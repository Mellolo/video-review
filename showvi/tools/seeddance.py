"""
Seeddance video generation tool (Image-to-Video).
Self-describing BaseTool subclass for the Video Agent system.

Supports text prompt + single/multiple reference images via the
即梦(Jimeng) reverse-engineered API.

Prompt referencing:
  Use ``@图片1``, ``@图片2`` to reference uploaded images in the prompt.
  Example: "灵月@图片1 在街道上行走" — 灵月's appearance is taken from image 1.
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import List, Optional

from .base import BaseTool, ToolResult, ExecutionContext, ToolCategory
from clients.seeddance import (
    SeeddanceClient,
    SeeddanceError,
    ContentFilteredError,
    InsufficientCreditsError,
    SessionExpiredError,
    VideoGenerationFailed,
    VideoGenerationTimeout,
)
from clients.seedance_api import (
    SeedanceApiClient,
    SeedanceApiError,
    ContentFilteredError as ApiContentFilteredError,
    InsufficientCreditsError as ApiInsufficientCreditsError,
    SessionExpiredError as ApiSessionExpiredError,
    VideoGenerationFailed as ApiVideoGenerationFailed,
    VideoGenerationTimeout as ApiVideoGenerationTimeout,
)

_log = logging.getLogger("video_agent.seeddance")

MAX_TOOL_RETRIES = 5

_TEXT_KEYWORDS = ("文字", "文本", "文字不符合", "文本违规")
_IMAGE_KEYWORDS = ("图片", "人脸", "素材", "图片违规", "人脸信息")
_GENERIC_RETRY_KEYWORDS = ("未通过审核", "审核")


def _classify_content_error(error_msg: str) -> str:
    """根据 fail_starling_message 关键词判断内容过滤类型。

    Generic retry keywords (审核/未通过审核) are checked first — these indicate
    a transient review failure that should be retried as-is, regardless of
    whether the message also contains text/image keywords.

    Returns:
        "rewrite_prompt"  — 文字/文本问题，需要改写 prompt
        "mask_image"      — 图片/人脸问题，需要 mask 图片
        "retry"           — 未通过审核等，直接重试
    """
    if any(kw in error_msg for kw in _GENERIC_RETRY_KEYWORDS):
        _log.info("  content error classified as GENERIC retry: %s", error_msg[:120])
        return "retry"
    if any(kw in error_msg for kw in _TEXT_KEYWORDS):
        _log.info("  content error classified as TEXT issue: %s", error_msg[:120])
        return "rewrite_prompt"
    if any(kw in error_msg for kw in _IMAGE_KEYWORDS):
        _log.info("  content error classified as IMAGE issue: %s", error_msg[:120])
        return "mask_image"
    _log.info("  content error unclassified, defaulting to retry: %s", error_msg[:120])
    return "retry"


class VideoGenerationError(Exception):
    pass


def _mask_charsheet_face_closeup(image_path: str) -> Optional[str]:
    """White-out the top-left quadrant (face close-up) of a 2×2 charsheet.

    Charsheet layout:
        TL: face close-up (特写)  |  TR: front view (正面)
        BL: side view (侧面)      |  BR: back view (背面)

    The face close-up is the most likely trigger for Seedance's face
    detection filter.  Masking it keeps the other 3 views intact.

    Returns the path to the masked copy, or ``None`` on failure.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        _log.warning("Pillow not installed — cannot mask charsheet face")
        return None

    try:
        img = Image.open(image_path)
        w, h = img.size
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, w // 2, h // 2], fill=(255, 255, 255))

        stem, ext = os.path.splitext(image_path)
        safe_path = f"{stem}_masked{ext}"
        img.save(safe_path)
        _log.info("Masked face close-up: %s → %s", image_path, safe_path)
        print(f"[SEEDDANCE I2V] Masked face close-up: {os.path.basename(safe_path)}")
        return safe_path
    except Exception as e:
        _log.error("Failed to mask image %s: %s", image_path, e)
        return None


_GRID_BLUR_RADIUS = 6  # Gaussian blur radius applied per retry iteration


def _blur_grid_image(original_grid_path: str, current_grid_path: str, iteration: int) -> Optional[str]:
    """Apply one Gaussian blur pass to a grid image to obscure face details.

    Each call reads ``current_grid_path`` (which may already be blurred from a
    previous pass) and writes a new file named after ``original_grid_path`` so
    the output filenames stay clean: ``{original_stem}_blur1.png``,
    ``{original_stem}_blur2.png``, etc.

    Args:
        original_grid_path: the unblurred source grid (used for naming only)
        current_grid_path:  the image to actually blur (previous iteration's output)
        iteration:          1-based pass counter

    Returns:
        Path to the blurred copy, or None on failure.
    """
    try:
        from PIL import Image, ImageFilter
    except ImportError:
        _log.warning("Pillow not installed — cannot blur grid image")
        return None

    try:
        img = Image.open(current_grid_path)
        img = img.filter(ImageFilter.GaussianBlur(radius=_GRID_BLUR_RADIUS))

        stem, ext = os.path.splitext(original_grid_path)
        blurred_path = f"{stem}_blur{iteration}{ext}"
        img.save(blurred_path)
        _log.info(
            "Grid image blurred (iter %d, radius=%d): %s → %s",
            iteration, _GRID_BLUR_RADIUS, current_grid_path, blurred_path,
        )
        print(
            f"[SEEDDANCE I2V] Grid blurred (iter {iteration}, "
            f"radius={_GRID_BLUR_RADIUS}): {os.path.basename(blurred_path)}"
        )
        return blurred_path
    except Exception as e:
        _log.error("Failed to blur grid image %s: %s", current_grid_path, e)
        return None


def _get_seeddance_backend() -> str:
    return os.getenv("SEEDDANCE_BACKEND", "jimeng").lower()


def _get_seeddance_client(context: Optional[ExecutionContext] = None):
    """Return a client based on SEEDDANCE_BACKEND env var.

    - "api"    → SeedanceApiClient (官方 Bearer token API)
    - "jimeng" → SeeddanceClient   (即梦逆向接口，默认)
    """
    backend = _get_seeddance_backend()
    overrides = getattr(context, "runtime_overrides", {}) or {}

    if backend == "api":
        api_key = overrides.get("seedance_api_key") or os.getenv("SEEDANCE_API_KEY")
        if not api_key:
            raise VideoGenerationError("SEEDANCE_API_KEY not found in environment")
        return SeedanceApiClient(api_key=api_key)

    # 默认 jimeng 逆向
    session_id = overrides.get("seeddance_session_id") or os.getenv("SEEDDANCE_SESSION_ID")
    if not session_id:
        raise VideoGenerationError("SEEDDANCE_SESSION_ID not found in environment")
    return SeeddanceClient(session_id=session_id)


def _strip_sora_ids(prompt: str) -> str:
    """Remove Sora-style @username references (e.g. ``@buzhizu.serenewill``)."""
    return re.sub(r"@[A-Za-z0-9_.]+", "", prompt).strip()


def _replace_name_outside_quotes(text: str, name: str, replacement: str) -> str:
    """Replace all occurrences of 'name' with 'replacement', but skip those inside quotes.
    
    Supports Chinese quotes (「」『』""'') and English quotes ("" '').
    
    Example:
        Input:  灵月说："灵月，你要坚强"
        Replace: 灵月 → @图片1
        Output: @图片1说："灵月，你要坚强"
    """
    if not name or name not in text:
        return text
    
    # Define quote pairs: opening → closing
    # Using Unicode escape codes for Chinese quotes to avoid syntax issues
    quote_pairs = {
        '\u201c': '\u201d',  # Chinese double quotes ""
        '"': '"',            # English double quotes
        '\u2018': '\u2019',  # Chinese single quotes ''
        "'": "'",            # English single quotes
        '\u300c': '\u300d',  # Japanese-style quotes 「」
        '\u300e': '\u300f',  # Japanese-style quotes 『』
    }
    
    # Build a list of quoted ranges: [(start, end), ...]
    quoted_ranges = []
    i = 0
    while i < len(text):
        char = text[i]
        if char in quote_pairs:
            closing = quote_pairs[char]
            # Find the matching closing quote
            end_pos = text.find(closing, i + 1)
            if end_pos != -1:
                # Found a complete quoted section
                quoted_ranges.append((i, end_pos + 1))
                i = end_pos + 1
                continue
        i += 1
    
    # Helper function to check if a position is inside any quoted range
    def is_in_quotes(pos: int) -> bool:
        for start, end in quoted_ranges:
            if start < pos < end:
                return True
        return False
    
    # Find all occurrences of 'name' and replace only those outside quotes
    result = []
    last_pos = 0
    
    while True:
        pos = text.find(name, last_pos)
        if pos == -1:
            # No more occurrences, append the rest
            result.append(text[last_pos:])
            break
        
        # Check if this occurrence is inside quotes
        if is_in_quotes(pos):
            # Keep the original name
            result.append(text[last_pos:pos + len(name)])
        else:
            # Replace with the new value
            result.append(text[last_pos:pos])
            result.append(replacement)
        
        last_pos = pos + len(name)
    
    return ''.join(result)


def _extract_prompt_ref_numbers(prompt: str) -> list[int]:
    return [
        int(m.group(1))
        for m in re.finditer(r"@(?:图片?|image)(\d+)", prompt or "", re.IGNORECASE)
    ]


def _filter_scene_assets(
    image_paths: list[str],
    characters: list | None,
    locations: list | None,
    props: list | None,
    characters_in_group: set | None,
    locations_in_group: set | None,
    props_in_group: set | None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    characters = list(characters or [])
    locations = list(locations or [])
    props = list(props or [])

    n_chars = len(characters)
    n_locs = len(locations)
    n_props = len(props)

    if characters_in_group is not None and characters:
        char_paths = image_paths[:n_chars]
        filtered_chars = [
            (name, path) for name, path in zip(characters, char_paths)
            if name in characters_in_group
        ]
        characters = [name for name, _ in filtered_chars]
        char_paths_filtered = [path for _, path in filtered_chars]
    else:
        char_paths_filtered = image_paths[:n_chars] if characters else []

    if locations_in_group is not None and locations:
        loc_paths = image_paths[n_chars:n_chars + n_locs]
        filtered_locs = [
            (name, path) for name, path in zip(locations, loc_paths)
            if name in locations_in_group
        ]
        locations = [name for name, _ in filtered_locs]
        loc_paths_filtered = [path for _, path in filtered_locs]
    else:
        loc_paths_filtered = image_paths[n_chars:n_chars + n_locs] if locations else []

    if props_in_group is not None and props:
        prop_paths = image_paths[n_chars + n_locs:n_chars + n_locs + n_props]
        filtered_props = [
            (name, path) for name, path in zip(props, prop_paths)
            if name in props_in_group
        ]
        props = [name for name, _ in filtered_props]
        prop_paths_filtered = [path for _, path in filtered_props]
    else:
        prop_paths_filtered = image_paths[n_chars + n_locs:n_chars + n_locs + n_props] if props else []

    extra_paths = image_paths[n_chars + n_locs + n_props:]
    filtered_image_paths = char_paths_filtered + loc_paths_filtered + prop_paths_filtered + extra_paths
    return filtered_image_paths, characters, locations, props


def _build_image_ref_assets(
    image_paths: list[str],
    characters: list | None,
    locations: list | None,
    props: list | None,
) -> dict[str, dict]:
    assets: dict[str, dict] = {}
    ordered_labels: list[str] = []
    ordered_types: list[str] = []

    for name in characters or []:
        ordered_labels.append(name)
        ordered_types.append("character")
    for name in locations or []:
        ordered_labels.append(name)
        ordered_types.append("location")
    for name in props or []:
        ordered_labels.append(name)
        ordered_types.append("prop")

    extra_count = max(0, len(image_paths) - len(ordered_labels))
    if extra_count == 1:
        ordered_labels.append("上一镜头画面")
        ordered_types.append("transition_frame")
    else:
        for i in range(extra_count):
            ordered_labels.append(f"参考图{i + 1}")
            ordered_types.append("reference")

    for idx, path in enumerate(image_paths, start=1):
        key = f"@图片{idx}"
        assets[key] = {
            "label": ordered_labels[idx - 1] if idx - 1 < len(ordered_labels) else f"参考图{idx}",
            "type": ordered_types[idx - 1] if idx - 1 < len(ordered_types) else "reference",
            "path": path,
        }

    return assets


def _build_readable_prompt(prompt: str, image_ref_assets: dict[str, dict]) -> str:
    if not prompt:
        return prompt

    def _replace(m: re.Match) -> str:
        idx = int(m.group(1))
        key = f"@图片{idx}"
        asset = image_ref_assets.get(key) or {}
        return asset.get("label") or key

    return re.sub(r"@(?:图片?|image)(\d+)", _replace, prompt, flags=re.IGNORECASE)


def _validate_prompt_refs(prompt: str, image_count: int) -> Optional[str]:
    refs = _extract_prompt_ref_numbers(prompt)
    if not refs:
        return None

    invalid = sorted({ref for ref in refs if ref <= 0 or ref > image_count})
    if invalid:
        bad = ", ".join(f"@图片{n}" for n in invalid)
        return f"Prompt 引用了不存在的图片编号: {bad}（当前仅有 {image_count} 张参考图）"
    return None


def _replace_prompt_refs(prompt: str, ref_map: dict[int, int]) -> str:
    if not prompt or not ref_map:
        return prompt

    def _replace(m: re.Match) -> str:
        old_ref = int(m.group(1))
        new_ref = ref_map.get(old_ref)
        if not new_ref:
            return m.group(0)
        return f"@图片{new_ref}"

    return re.sub(r"@(?:图片?|image)(\d+)", _replace, prompt, flags=re.IGNORECASE)


def _build_manual_prompt_assets(
    prompt: str,
    manual_assets: dict | None,
    fallback_assets: dict[str, dict] | None = None,
) -> tuple[str, list[str], list[str], list[str], list[str]]:
    """Build referenced assets for a manual prompt and compact ref indices.

    Priority:
    1) manual_assets[@图片N]
    2) fallback_assets[@图片N] (from current unit's default refs)
    """
    refs = sorted({ref for ref in _extract_prompt_ref_numbers(prompt) if ref > 0})
    if not refs:
        return prompt, [], [], [], []

    manual_assets = dict(manual_assets or {})
    fallback_assets = dict(fallback_assets or {})

    resolved_assets: list[dict] = []
    old_to_new_ref: dict[int, int] = {}
    unresolved_refs: list[int] = []

    for ref in refs:
        key = f"@图片{ref}"
        asset = manual_assets.get(key) or fallback_assets.get(key) or {}
        path = str(asset.get("path") or "").strip()
        if not path:
            unresolved_refs.append(ref)
            continue
        resolved_assets.append({
            "path": path,
            "label": str(asset.get("label") or key).strip() or key,
            "type": str(asset.get("type") or "reference").strip() or "reference",
        })
        old_to_new_ref[ref] = len(resolved_assets)

    compact_prompt = _replace_prompt_refs(prompt, old_to_new_ref)
    image_paths: list[str] = []
    characters: list[str] = []
    locations: list[str] = []
    props: list[str] = []

    for asset in resolved_assets:
        image_paths.append(asset["path"])
        atype = asset["type"]
        label = asset["label"]
        if atype == "character":
            characters.append(label)
        elif atype == "location":
            locations.append(label)
        elif atype == "prop":
            props.append(label)

    if unresolved_refs:
        _log.warning(
            "Manual prompt refs unresolved: %s",
            ", ".join(f"@图片{n}" for n in unresolved_refs),
        )

    return compact_prompt, image_paths, characters, locations, props


def _inject_image_refs_replace(
    prompt: str,
    duration: str,
    image_count: int,
    characters: list | None = None,
    characters_in_group: set | None = None,
    locations: list | None = None,
    locations_in_group: set | None = None,
    props: list | None = None,
    props_in_group: set | None = None,
) -> str:
    """Image injection for segment_direct mode: replace names with @图片N only.

    All character, prop, and location names are **replaced** (not appended)
    to prevent IP infringement.  Format:
        场景：@图片{loc_idx}，时长：{duration}。{prompt_with_names_replaced}
    """
    if re.search(r"@(?:图片?|image)\d+", prompt, re.IGNORECASE):
        return prompt

    n_chars = len(characters) if characters else 0
    n_locs = len(locations) if locations else 0

    char_refs: dict[str, int] = {}
    img_idx = 1
    if characters:
        for char_name in characters:
            if img_idx > image_count:
                break
            if characters_in_group is not None and char_name not in characters_in_group:
                img_idx += 1
                continue
            char_refs[char_name] = img_idx
            img_idx += 1

    loc_refs: dict[str, int] = {}
    if locations:
        loc_start = n_chars + 1
        for i, loc_name in enumerate(locations):
            loc_idx = loc_start + i
            if loc_idx > image_count:
                break
            if locations_in_group is not None and loc_name not in locations_in_group:
                continue
            loc_refs[loc_name] = loc_idx

    prop_refs: dict[str, int] = {}
    if props:
        prop_start = n_chars + n_locs + 1
        for i, prop_name in enumerate(props):
            prop_idx = prop_start + i
            if prop_idx > image_count:
                break
            if props_in_group is not None and prop_name not in props_in_group:
                continue
            prop_refs[prop_name] = prop_idx

    modified = prompt
    all_refs = list(char_refs.items()) + list(prop_refs.items()) + list(loc_refs.items())
    all_refs.sort(key=lambda x: len(x[0]), reverse=True)
    for name, idx in all_refs:
        modified = _replace_name_outside_quotes(modified, name, f"@图片{idx}")

    loc_prefix = ""
    for loc_name, loc_idx in loc_refs.items():
        loc_prefix += f"场景：@图片{loc_idx}，"

    result = f"{loc_prefix}时长：{duration}。{modified}"
    return result


def _inject_image_refs(prompt: str, image_count: int,
                       characters: list | None = None,
                       characters_in_group: set | None = None,
                       locations: list | None = None,
                       locations_in_group: set | None = None,
                       props: list | None = None,
                       props_in_group: set | None = None) -> str:
    """
    Auto-inject ``@图片N`` placeholders for Seeddance (普通模式).

    All character, prop, and location names are **replaced** (not appended)
    with @图片N references, but names inside quotes are preserved.
    
    Format: 场景名@图片N <prompt_with_names_replaced>

    Image index convention (matches image_paths order):
      characters first (1 … n_chars), then locations (n_chars+1 … n_chars+n_locs),
      then props (n_chars+n_locs+1 …).
    """
    if re.search(r"@(?:图片?|image)\d+", prompt, re.IGNORECASE):
        return prompt

    n_chars = len(characters) if characters else 0
    n_locs = len(locations) if locations else 0

    # --- character refs: name → img_idx ---
    char_refs: dict[str, int] = {}
    img_idx = 1
    if characters:
        for char_name in characters:
            if img_idx > image_count:
                break
            if characters_in_group is not None and char_name not in characters_in_group:
                img_idx += 1
                continue
            char_refs[char_name] = img_idx
            img_idx += 1

    # --- location refs: name → img_idx ---
    loc_refs: dict[str, int] = {}
    if locations:
        loc_start = n_chars + 1
        for i, loc_name in enumerate(locations):
            loc_idx = loc_start + i
            if loc_idx > image_count:
                break
            if locations_in_group is not None and loc_name not in locations_in_group:
                continue
            loc_refs[loc_name] = loc_idx

    # --- prop refs: name → img_idx ---
    prop_refs: dict[str, int] = {}
    if props:
        prop_start = n_chars + n_locs + 1
        for i, prop_name in enumerate(props):
            prop_idx = prop_start + i
            if prop_idx > image_count:
                break
            if props_in_group is not None and prop_name not in props_in_group:
                continue
            prop_refs[prop_name] = prop_idx

    # --- replace all names with @图片N (outside quotes only) ---
    modified = prompt
    all_refs = list(char_refs.items()) + list(prop_refs.items()) + list(loc_refs.items())
    all_refs.sort(key=lambda x: len(x[0]), reverse=True)
    for name, idx in all_refs:
        modified = _replace_name_outside_quotes(modified, name, f"@图片{idx}")

    # --- prepend location prefix ---
    loc_prefix = ""
    for loc_name, loc_idx in loc_refs.items():
        loc_prefix += f"场景：@图片{loc_idx}，"

    if loc_prefix:
        return f"{loc_prefix}{modified}"
    
    return modified


def _rewrite_prompt_for_content_issue(
    prompt: str,
    error_history: List[str],
    skip_categories: Optional[List[str]] = None,
    characters: Optional[list] = None,
) -> Optional[str]:
    """Use LLM to rewrite a prompt that was flagged by content filters.

    *skip_categories*: forwarded to ``analyze_and_rewrite_prompt_issues``
    to exclude certain issue categories (e.g. ``["ip"]``).
    *characters*: list of character names to help identify which names will be
    replaced by @图片N (so they don't need IP rewriting).

    Returns the rewritten prompt string, or ``None`` on LLM failure.
    """
    try:
        from tools.image_gen import analyze_and_rewrite_prompt_issues

        result = analyze_and_rewrite_prompt_issues(
            prompt, error_history, 
            skip_categories=skip_categories,
            characters=characters,
        )
        if result and result.get("rewritten_prompt"):
            new_prompt = result["rewritten_prompt"]
            detected = result.get("detected_issues", [])
            if detected:
                for d in detected:
                    _log.info(
                        "  content issue [%s]: \"%s\" → \"%s\"",
                        d.get("category", "?"),
                        d.get("original_text", ""),
                        d.get("replacement", ""),
                    )
            return new_prompt
    except Exception as e:
        _log.warning("Prompt content rewrite failed: %s", e)
    return None


class SeeddanceImageToVideo(BaseTool):
    """Generate video from reference image(s) + text prompt using Seeddance (即梦)."""

    @property
    def name(self) -> str:
        return "seeddance_image_to_video"

    @property
    def description(self) -> str:
        return (
            "Generate video from one or more reference images + text prompt "
            "(即梦 Seeddance). Use @图片1, @图片2 to reference images in prompt. "
            "Default model: seedance-2.0-fast (4-15s duration)."
        )

    @property
    def category(self) -> ToolCategory:
        return "generator"

    @staticmethod
    def _get_group_characters(context: ExecutionContext) -> set[str] | None:
        """
        Return the set of character names that appear in this group's scenes.
        Returns None only when the info is unavailable; otherwise returns a
        possibly empty set so callers can distinguish “no characters in group”
        from “unknown, keep all characters”.
        """
        sb = context.storyboard
        if not sb or not hasattr(sb, "scenes"):
            return None

        scene_numbers = set(getattr(context, "scene_numbers", None) or [])
        if not scene_numbers:
            return None

        chars = set()
        for s in sb.scenes:
            if s.scene_number in scene_numbers:
                chars.update(s.characters_in_scene)
        return chars

    @staticmethod
    def _get_group_locations(context: ExecutionContext) -> set[str] | None:
        """
        Return the set of location names that appear in this group's scenes.
        Returns None only when the info is unavailable; otherwise returns a
        possibly empty set so callers can distinguish “no locations in group”
        from “unknown, keep all locations”.
        """
        sb = context.storyboard
        if not sb or not hasattr(sb, "scenes"):
            return None

        scene_numbers = set(getattr(context, "scene_numbers", None) or [])
        if not scene_numbers:
            return None

        locs = set()
        for s in sb.scenes:
            if s.scene_number in scene_numbers:
                loc = getattr(s, "scene_location", "")
                if loc:
                    locs.add(loc)
        return locs

    @staticmethod
    def _get_group_props(context: ExecutionContext) -> set[str] | None:
        """
        Return the set of prop names that appear in this group's scenes.
        Returns None only when the info is unavailable; otherwise returns a
        possibly empty set so callers can distinguish “no props in group”
        from “unknown, keep all props”.
        """
        sb = context.storyboard
        if not sb or not hasattr(sb, "scenes"):
            return None

        scene_numbers = set(getattr(context, "scene_numbers", None) or [])
        if not scene_numbers:
            return None

        prop_set = set()
        for s in sb.scenes:
            if s.scene_number in scene_numbers:
                for p in getattr(s, "props_in_scene", []):
                    if p:
                        prop_set.add(p)
        return prop_set

    def execute(self, context: ExecutionContext, **params) -> ToolResult:
        """
        Params (via context and **params):
            context.prompt              — text prompt
            context.duration_seconds    — video duration
            context.reference_image_path — default first image (if no image_paths)
            context.output_dir          — output directory
            context.unit_id             — segment id
            context.attempt_number      — attempt number

            image_paths    : list[str] | str — image file paths (overrides context)
            model          : str       — model version (default "seedance-2.0-fast")
            duration       : int       — override duration
            aspect_ratio   : str       — "16:9", "9:16", etc.
            auto_ref       : bool      — auto-inject @图片N refs (default True)
            characters     : list[str] — character names for @图片N refs
            locations      : list[str] — location names for @图片N refs (after characters)
            props          : list[str] — prop names for @图片N refs (after locations)
        """
        prompt = params.get("prompt", context.prompt)
        raw_duration = int(params.get("duration", context.duration_seconds or 5))
        duration = max(4, min(15, raw_duration))
        aspect_ratio = params.get("aspect_ratio", "16:9")
        model = params.get("model", "seedance-2.0-fast")
        # 优先使用 runtime_overrides 中的模型设置（来自前端用户选择）
        overrides = getattr(context, "runtime_overrides", {}) or {}
        if overrides.get("seeddance_model"):
            model = overrides["seeddance_model"]
        # 再 fallback 到环境变量（新任务无 run_dir 时通过环境变量传递）
        elif os.environ.get("SEEDDANCE_MODEL"):
            model = os.environ["SEEDDANCE_MODEL"]
        auto_ref = params.get("auto_ref", True)
        characters = params.get("characters")
        locations = params.get("locations")
        props = params.get("props")
        output_path = (
            f"{context.output_dir}/segment_{context.unit_id}"
            f"_attempt_{context.attempt_number}.mp4"
        )

        image_paths = params.get("image_paths")
        if not image_paths and context.reference_image_path:
            image_paths = [context.reference_image_path]
        if not image_paths:
            return ToolResult(success=False, error="No image_paths provided for Seeddance I2V")

        if isinstance(image_paths, str):
            image_paths = [image_paths]

        prompt = _strip_sora_ids(prompt)
        manual_image_ref_assets = getattr(context, "manual_image_ref_assets", {}) or {}

        # ── Dependency group: append grid image + prefix prompt ───
        grid_image = getattr(context, "prev_segment_grid_image", None)
        grid_already_in_paths = False
        _saved_characters_for_mask: list | None = None
        if grid_image and Path(grid_image).exists():
            _log.info("Unit %s: injecting prev-segment grid image: %s",
                       context.unit_id, grid_image)

        _log.info(
            "─── input (before filter) ───\n"
            "  unit_id: %s  attempt: %s  scenes: %s\n"
            "  image_paths (%d):\n%s\n"
            "  characters: %s\n"
            "  locations: %s\n"
            "  props: %s",
            context.unit_id, context.attempt_number,
            getattr(context, "scene_numbers", []),
            len(image_paths),
            "\n".join(f"    [{i}] {p}" for i, p in enumerate(image_paths)),
            characters,
            locations,
            props,
        )

        already_has_refs = bool(
            re.search(r"@(?:图片?|image)\d+", prompt, re.IGNORECASE)
        )

        characters_in_group = None
        locations_in_group = None
        props_in_group = None
        if context.storyboard and hasattr(context, "unit_id"):
            characters_in_group = self._get_group_characters(context)
            locations_in_group = self._get_group_locations(context)
            props_in_group = self._get_group_props(context)

        _log.info(
            "  characters_in_group: %s\n"
            "  locations_in_group: %s\n"
            "  props_in_group: %s",
            characters_in_group,
            locations_in_group,
            props_in_group,
        )

        image_paths, characters, locations, props = _filter_scene_assets(
            image_paths,
            characters,
            locations,
            props,
            characters_in_group,
            locations_in_group,
            props_in_group,
        )

        # _original_prompt: the prompt as received (original @图片N numbering).
        # Used for metadata.prompt so frontend always sees the same numbering
        # as input_params.prompt / unit.prompt.
        _original_prompt = prompt

        if already_has_refs and manual_image_ref_assets:
            fallback_ref_assets = _build_image_ref_assets(
                image_paths,
                characters=characters,
                locations=locations,
                props=props,
            )
            compact_prompt, manual_paths, manual_characters, manual_locations, manual_props = _build_manual_prompt_assets(
                prompt,
                manual_image_ref_assets,
                fallback_ref_assets,
            )
            if manual_paths:
                prompt = compact_prompt
                image_paths = manual_paths
                characters = None
                locations = None
                props = None
                characters_in_group = None
                locations_in_group = None
                props_in_group = None
                # Build image_ref_assets using ORIGINAL numbering (from
                # manual_image_ref_assets + fallback), not compact numbering.
                # This ensures metadata.image_ref_assets always matches the
                # numbering in input_params.prompt / unit.prompt.
                _override_ref_assets = {
                    **fallback_ref_assets,
                    **manual_image_ref_assets,
                }
                # If the grid image is already referenced in the prompt (e.g.
                # user included "参考前序剧情：@图片5" in their edit), mark it so
                # the auto-inject block below doesn't add a duplicate prefix.
                _grid_src = getattr(context, "prev_segment_grid_image", None)
                if _grid_src and any(
                    str(Path(p)) == str(Path(_grid_src))
                    for p in manual_paths
                ):
                    grid_already_in_paths = True
                _log.info(
                    "Using manual prompt ref assets for unit %s attempt %s: %d image(s)",
                    context.unit_id,
                    context.attempt_number,
                    len(image_paths),
                )
            else:
                _override_ref_assets = None
        elif already_has_refs and not manual_image_ref_assets:
            # Prompt contains @图片 refs (e.g. from a regen request) but no
            # manual_image_ref_assets were provided.  Build a fallback that
            # includes the grid image (if any) so all refs can be resolved.
            _fallback_paths = list(image_paths)
            if grid_image and Path(grid_image).exists():
                _fallback_paths.append(grid_image)
            fallback_ref_assets = _build_image_ref_assets(
                _fallback_paths,
                characters=characters,
                locations=locations,
                props=props,
            )
            compact_prompt, resolved_paths, _, _, _ = _build_manual_prompt_assets(
                prompt,
                {},           # no manual overrides
                fallback_ref_assets,  # use fallback to resolve all refs
            )
            if resolved_paths:
                prompt = compact_prompt
                image_paths = resolved_paths
                _saved_characters_for_mask = list(characters or [])
                characters = None
                locations = None
                props = None
                characters_in_group = None
                locations_in_group = None
                props_in_group = None
                # Use fallback_ref_assets directly (original numbering).
                _override_ref_assets = fallback_ref_assets
                # Grid is already included in resolved image_paths — set flag
                # so the auto-inject block below skips appending it again,
                # but keep grid_image alive for retry logic (rewrite restore
                # and grid face masking).
                grid_already_in_paths = True
                # Compute grid_idx and prefix so retry rewrite-restore works.
                _grid_src = getattr(context, "prev_segment_grid_image", None)
                if _grid_src:
                    try:
                        grid_idx = next(
                            i for i, p in enumerate(image_paths, 1)
                            if str(Path(p)) == str(Path(_grid_src))
                        )
                    except StopIteration:
                        grid_idx = len(image_paths)
                else:
                    grid_idx = len(image_paths)
                prefix = (
                    f"参考前序剧情：@图片{grid_idx}，"
                    "继续生成后续的剧情："
                )
                _log.info(
                    "Resolved @图片 refs via fallback for unit %s attempt %s: %d image(s)",
                    context.unit_id,
                    context.attempt_number,
                    len(image_paths),
                )
            else:
                _override_ref_assets = None
        else:
            _override_ref_assets = None

        _log.info(
            "─── after filter ───\n"
            "  kept characters: %s\n"
            "  kept locations: %s\n"
            "  kept props: %s\n"
            "  final image_paths (%d):\n%s",
            characters,
            locations,
            props,
            len(image_paths),
            "\n".join(f"    [{i}] {p}" for i, p in enumerate(image_paths)),
        )

        if already_has_refs:
            _log.info(
                "Prompt already contains @图片 refs — skipping auto_ref injection only"
            )
        elif auto_ref:
            is_direct_mode = (
                context.storyboard
                and getattr(context.storyboard, "meta", {}).get(
                    "scene_granularity") == "segment_direct"
            )
            if is_direct_mode:
                dur_str = f"{duration}秒"
                prompt = _inject_image_refs_replace(
                    prompt, dur_str, len(image_paths),
                    characters=characters,
                    characters_in_group=characters_in_group,
                    locations=locations,
                    locations_in_group=locations_in_group,
                    props=props,
                    props_in_group=props_in_group,
                )
            else:
                prompt = _inject_image_refs(
                    prompt, len(image_paths),
                    characters=characters,
                    characters_in_group=characters_in_group,
                    locations=locations,
                    locations_in_group=locations_in_group,
                    props=props,
                    props_in_group=props_in_group,
                )

        ref_error = _validate_prompt_refs(prompt, len(image_paths))
        if ref_error:
            return ToolResult(success=False, error=ref_error)

        # ── Dependency group: append grid image + prefix prompt ───
        if grid_image and Path(grid_image).exists() and not grid_already_in_paths:
            image_paths = list(image_paths) + [grid_image]
            grid_idx = len(image_paths)  # 1-based index of the grid image
            prefix = (
                f"参考前序剧情：@图片{grid_idx}"
                "继续生成后续的剧情："
            )
            prompt = prefix + prompt
            _log.info("Grid image appended as @图片%d, prefix added", grid_idx)

        _log.info(
            "─── submit to seedance ───\n"
            "  unit_id: %s  attempt: %s\n"
            "  model: %s  duration: %ds  aspect_ratio: %s\n"
            "  image_paths (%d):\n%s\n"
            "  characters: %s\n"
            "  locations: %s\n"
            "  props: %s\n"
            "  prompt:\n%s",
            context.unit_id, context.attempt_number,
            model, duration, aspect_ratio,
            len(image_paths),
            "\n".join(f"    [{i}] {p}" for i, p in enumerate(image_paths)),
            characters,
            locations,
            props,
            prompt,
        )

        print(
            f"[SEEDDANCE I2V] Generating video: model={model}, "
            f"{len(image_paths)} image(s), {duration}s"
        )
        print(f"[SEEDDANCE I2V] Prompt: {prompt[:150]}...")

        current_prompt = prompt
        current_image_paths = list(image_paths)
        error_history: List[str] = []
        faces_masked = False
        grid_blur_count = 0   # number of blur passes applied to the grid image so far
        text_error_count = 0

        if _override_ref_assets:
            image_ref_assets = _override_ref_assets
        else:
            image_ref_assets = _build_image_ref_assets(
                current_image_paths,
                characters,
                locations,
                props,
            )
        image_ref_map = {
            key: value.get("label", key)
            for key, value in image_ref_assets.items()
        }

        # Base metadata — always attached to results (even failures) so the
        # dashboard can display @图片N labels and thumbnails.
        _base_meta = {
            "image_ref_map": image_ref_map,
            "image_ref_assets": image_ref_assets,
        }

        # ── 预扣款 / 退款闭包 ──────────────────────────────────────
        # 预扣模式：第一次 submit_video 成功后立即扣费，只扣一次。
        # 如果所有 retry 最终都失败（segment 被跳过），退还预扣款。
        _pre_deducted = False
        _billing_uid = os.environ.get("VIDEO_AGENT_OWNER_USER_ID")
        # 用户自己带了 cookie（VIDEO_AGENT_PROXY_MODE=1）才不扣平台积分；
        # 走账号池分配的 session_id 消耗的是平台账号，需要扣平台积分。
        _billing_proxy = bool(os.environ.get("VIDEO_AGENT_PROXY_MODE"))
        _need_billing = bool(_billing_uid) and not _billing_proxy
        _log.info("billing check: uid=%s proxy=%s need_billing=%s secret_set=%s",
                  _billing_uid, _billing_proxy, _need_billing,
                  bool(os.environ.get("VIDEO_AGENT_INTERNAL_SECRET")))

        def _try_pre_deduct():
            """首次 submit 成功后预扣，只扣一次。扣费失败时返回 False。"""
            nonlocal _pre_deducted
            if _pre_deducted or not _need_billing:
                return True
            try:
                from dashboard.credits import COST_VIDEO_GEN, deduct_credits_standalone
                _cost = duration * COST_VIDEO_GEN
                ok = deduct_credits_standalone(
                    int(_billing_uid), _cost, "video_gen",
                    f"视频片段生成（segment {context.unit_id}，{duration}秒）",
                )
                if ok:
                    _pre_deducted = True
                    return True
                _log.error("扣费失败（余额不足或服务异常）: uid=%s cost=%s", _billing_uid, _cost)
                return False
            except Exception as exc:
                _log.error("扣费异常: uid=%s — %s", _billing_uid, exc)
                return False

        def _try_refund():
            """所有 retry 失败后退还预扣款。"""
            nonlocal _pre_deducted
            if not _pre_deducted:
                return
            try:
                from dashboard.credits import COST_VIDEO_GEN, refund_credits_standalone
                _cost = duration * COST_VIDEO_GEN
                ok = refund_credits_standalone(
                    int(_billing_uid), _cost, "video_gen_refund",
                    f"视频片段生成失败退款（segment {context.unit_id}，{duration}秒）",
                )
                if ok:
                    _pre_deducted = False
                else:
                    _log.error("退款失败: uid=%s cost=%s", _billing_uid, _cost)
            except Exception as exc:
                _log.error("退款异常: uid=%s — %s", _billing_uid, exc)

        for retry in range(1, MAX_TOOL_RETRIES + 1):
            try:
                result = self._submit_and_wait(
                    current_prompt, current_image_paths, duration, aspect_ratio,
                    model, output_path, context,
                )
                result.metadata["prompt"] = _original_prompt
                result.metadata["compact_prompt"] = current_prompt
                result.metadata["image_ref_map"] = image_ref_map
                result.metadata["image_ref_assets"] = image_ref_assets
                result.metadata["readable_prompt"] = _build_readable_prompt(
                    _original_prompt,
                    image_ref_assets,
                )
                # ── submit + wait 都成功 → 预扣（如果还没扣过），保留 ──
                if not _try_pre_deduct():
                    _log.error("视频生成成功但扣费失败，仍返回结果: uid=%s unit=%s", _billing_uid, context.unit_id)
                return result

            except (InsufficientCreditsError,
                    SessionExpiredError) as e:
                # submit 阶段就失败了，不预扣
                _log.error("  ✗ unrecoverable: %s", e)
                return ToolResult(success=False, error=str(e), metadata=dict(_base_meta))

            except (VideoGenerationTimeout,) as e:
                # submit 成功但 wait 超时 → 预扣，不重试直接返回失败
                _try_pre_deduct()
                _log.error("  ✗ timeout: %s", e)
                _try_refund()
                return ToolResult(success=False, error=str(e), metadata=dict(_base_meta))

            except (ContentFilteredError,) as e:
                # submit 成功但内容审核失败 → 预扣
                _try_pre_deduct()
                error_msg = str(e)
                error_history.append(error_msg)
                _log.warning(
                    "  ✗ attempt %d/%d content filtered: %s",
                    retry, MAX_TOOL_RETRIES, error_msg,
                )
                if retry >= MAX_TOOL_RETRIES:
                    print(f"[SEEDDANCE I2V] All {MAX_TOOL_RETRIES} attempts content filtered")
                    _try_refund()
                    return ToolResult(success=False, error=error_msg, metadata=dict(_base_meta))

                action = _classify_content_error(error_msg)
                if action == "rewrite_prompt":
                    text_error_count += 1
                    if text_error_count == 1:
                        print(
                            f"[SEEDDANCE I2V] Attempt {retry}/{MAX_TOOL_RETRIES} "
                            f"text content filter — retrying as-is first..."
                        )
                    else:
                        print(
                            f"[SEEDDANCE I2V] Attempt {retry}/{MAX_TOOL_RETRIES} "
                            f"text filter again — full rewrite (including IP)..."
                        )
                        # Strip grid prefix before rewriting so the rewriter
                        # only sees the creative prompt, not the dependency
                        # preamble.  The prefix is restored afterwards.
                        _rewrite_input = current_prompt
                        _had_grid_prefix = False
                        if grid_image and Path(grid_image).exists():
                            grid_prefix_marker = f"@图片{grid_idx}"
                            _pfx_end = current_prompt.find(prefix) + len(prefix) if prefix in current_prompt else -1
                            if _pfx_end > 0:
                                _rewrite_input = current_prompt[_pfx_end:]
                                _had_grid_prefix = True

                        _rewrite_input = self._try_rewrite_prompt(
                            _rewrite_input, error_history, retry,
                            characters=characters,
                        )

                        # Restore grid prefix
                        if grid_image and Path(grid_image).exists():
                            grid_prefix_marker = f"@图片{grid_idx}"
                            if _had_grid_prefix and grid_prefix_marker not in _rewrite_input:
                                current_prompt = prefix + _rewrite_input
                            else:
                                current_prompt = _rewrite_input
                        else:
                            current_prompt = _rewrite_input
                elif action == "mask_image":
                    if not faces_masked:
                        masked = self._mask_character_faces(
                            current_image_paths, characters or _saved_characters_for_mask,
                        )
                        if masked:
                            current_image_paths = masked
                            faces_masked = True
                        else:
                            _log.warning("  face masking not applicable, retrying as-is")
                            print("[SEEDDANCE I2V] Image filter — no maskable charsheets, retrying...")

                    # ── Blur grid image progressively on each face-error retry ──
                    # Each retry applies one more Gaussian blur pass on top of the
                    # previous result, so faces become less recognisable over time.
                    if grid_image and Path(grid_image).exists():
                        grid_path_in_use = current_image_paths[-1] if current_image_paths else None
                        if grid_path_in_use and (
                            grid_path_in_use == grid_image
                            or "_blur" in os.path.basename(grid_path_in_use)
                            or os.path.basename(grid_path_in_use).startswith("grid_seg")
                        ):
                            grid_blur_count += 1
                            blurred = _blur_grid_image(grid_image, grid_path_in_use, grid_blur_count)
                            if blurred:
                                current_image_paths[-1] = blurred
                                _log.info(
                                    "  grid image blurred (pass %d): %s",
                                    grid_blur_count, blurred,
                                )
                else:
                    print(
                        f"[SEEDDANCE I2V] Attempt {retry}/{MAX_TOOL_RETRIES} "
                        f"generic content filter — retrying..."
                    )

                time.sleep(min(2 ** retry, 30))

            except Exception as e:
                error_msg = str(e)
                error_history.append(error_msg)
                _log.warning(
                    "  ✗ attempt %d/%d failed: %s",
                    retry, MAX_TOOL_RETRIES, error_msg,
                )
                if retry >= MAX_TOOL_RETRIES:
                    print(f"[SEEDDANCE I2V] All {MAX_TOOL_RETRIES} attempts failed")
                    _try_refund()
                    return ToolResult(success=False, error=error_msg, metadata=dict(_base_meta))

                print(
                    f"[SEEDDANCE I2V] Attempt {retry}/{MAX_TOOL_RETRIES} "
                    f"failed: {error_msg[:100]} — retrying..."
                )
                time.sleep(min(2 ** retry, 30))

        _try_refund()
        return ToolResult(success=False, error="Exhausted all retries", metadata=dict(_base_meta))

    @staticmethod
    def _try_rewrite_prompt(
        current_prompt: str,
        error_history: List[str],
        retry: int,
        skip_categories: Optional[List[str]] = None,
        characters: Optional[list] = None,
    ) -> str:
        """Attempt to rewrite a prompt that hit content filters. Returns new or original prompt."""
        new_prompt = _rewrite_prompt_for_content_issue(
            current_prompt, error_history, 
            skip_categories=skip_categories,
            characters=characters,
        )
        if new_prompt and new_prompt != current_prompt:
            _log.info(
                "  prompt rewritten:\n    OLD: %s\n    NEW: %s",
                current_prompt[:200], new_prompt[:200],
            )
            print(f"[SEEDDANCE I2V] Prompt rewritten: {new_prompt[:120]}...")
            return new_prompt
        print("[SEEDDANCE I2V] Prompt rewrite unchanged, retrying as-is")
        return current_prompt

    @staticmethod
    def _mask_character_faces(
        image_paths: List[str],
        characters: list | None,
    ) -> Optional[List[str]]:
        """Mask face close-ups in character charsheets (top-left quadrant → white).

        Only character images (first ``len(characters)`` entries) are masked;
        location images are left untouched.  Returns a new path list with
        masked copies, or ``None`` if nothing changed.
        """
        n_chars = len(characters) if characters else 0
        if n_chars == 0:
            return None

        new_paths = list(image_paths)
        changed = False

        for i in range(min(n_chars, len(image_paths))):
            masked = _mask_charsheet_face_closeup(image_paths[i])
            if masked:
                new_paths[i] = masked
                changed = True

        if not changed:
            print("[SEEDDANCE I2V] Face masking failed, continuing with original images")
            return None

        _log.info("  masked %d character charsheet(s)",
                  sum(1 for a, b in zip(new_paths, image_paths) if a != b))
        return new_paths

    @staticmethod
    def _submit_and_wait(
        prompt: str,
        image_paths: list,
        duration: int,
        aspect_ratio: str,
        model: str,
        output_path: str,
        context: ExecutionContext,
    ) -> ToolResult:
        """Submit video, poll for completion, download. Raises on any failure."""
        client = _get_seeddance_client(context)
        backend = _get_seeddance_backend()

        if backend == "api":
            # 官方 API：不需要上传图片，直接用 prompt 提交
            # image_paths 在官方 API 模式下暂不支持（官方接口用 reference_videos URL）
            history_id = client.submit_video(
                prompt=prompt,
                ratio=aspect_ratio,
                duration=duration,
                model=model,
            )
        else:
            history_id = client.submit_video(
                image_path=image_paths,
                prompt=prompt,
                duration=duration,
                aspect_ratio=aspect_ratio,
                model=model,
            )

        if not history_id:
            raise VideoGenerationError("Failed to submit Seeddance video generation task")

        _log.info("  submitted: history_id=%s", history_id)

        # ── 提交流水记录：每次 submit 成功都追加一条 JSONL ──
        # 用于服务器端自查对账，防止漏扣/多扣积分。
        try:
            import json as _json
            from datetime import datetime, timezone
            _uid = os.environ.get("VIDEO_AGENT_OWNER_USER_ID")
            if _uid:
                _base = Path(__file__).resolve().parent.parent / "data" / "users" / _uid / ".dashboard"
                _base.mkdir(parents=True, exist_ok=True)
                _ledger = _base / "submit_ledger.jsonl"
                _record = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "history_id": history_id,
                    "backend": _get_seeddance_backend(),
                    "unit_id": context.unit_id,
                    "attempt": context.attempt_number,
                    "duration": duration,
                    "model": model,
                    "proxy_mode": bool(os.environ.get("VIDEO_AGENT_PROXY_MODE")),
                    "prompt_preview": prompt[:80],
                }
                with open(_ledger, "a", encoding="utf-8") as _f:
                    _f.write(_json.dumps(_record, ensure_ascii=False) + "\n")
        except Exception as _ledger_err:
            _log.warning("submit ledger write failed: %s", _ledger_err)

        # Persist history_id immediately for crash recovery
        if context.on_task_submitted:
            try:
                task_info = {
                    "history_id": history_id,
                    "backend": _get_seeddance_backend(),
                }
                context.on_task_submitted(task_info)
            except Exception as cb_err:
                _log.warning("on_task_submitted callback failed: %s", cb_err)

        # on_progress 回调：把 queue 信息写入 attempt metadata 并保存 checkpoint
        def _on_progress(info: dict):
            if context.on_task_submitted:
                try:
                    context.on_task_submitted({
                        "history_id": history_id,
                        "backend": _get_seeddance_backend(),
                        "queue_status": info.get("queue_status"),
                        "queue_idx": info.get("queue_idx"),
                        "queue_length": info.get("queue_length"),
                        "estimated_time": info.get("estimated_time"),
                    })
                except Exception as cb_err:
                    _log.warning("on_progress->on_task_submitted failed: %s", cb_err)

        result = client.wait_for_video(history_id, on_progress=_on_progress)
        if not result or not result.get("url"):
            raise VideoGenerationError("Video generation timed out or returned no URL")

        video_url = result["url"]
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        success = client.download_video_file(video_url, str(out))
        if not success:
            raise VideoGenerationError("Failed to download video")

        print(f"[SEEDDANCE I2V] Video saved to: {out}")
        _log.info("  ✓ output: %s  video_url: %s", out, video_url)
        return ToolResult(
            success=True,
            output_path=str(out),
            metadata={
                "image_paths": image_paths,
                "duration": duration,
                "model": model,
                "aspect_ratio": aspect_ratio,
                "history_id": history_id,
                "video_url": video_url,
            },
        )
