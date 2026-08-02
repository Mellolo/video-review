"""
Reference image generation tool.

Supports multiple generation modes via ``image_mode`` param:
  - default     : single reference image
  - 9grid       : 九宫格 (3×3 grid showing 9 angles/compositions)
  - charsheet   : 人物四视图 — generates one sheet PER character
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import List, Tuple, Optional

from .base import BaseTool, ToolResult, ExecutionContext, ToolCategory
from prompts.image_gen import (
    _IMAGE_SAFETY_REWRITE_SYSTEM,
    _PROMPT_FAILURE_ANALYSIS_SYSTEM,
    CHARSHEET_TEMPLATE,
    LOCATION_SHEET_TEMPLATE,
    PROP_SHEET_TEMPLATE,
    DERIVED_CHARSHEET_TEMPLATE,
    DERIVED_LOCATION_SHEET_TEMPLATE,
    DERIVED_PROP_SHEET_TEMPLATE,
)

_log = logging.getLogger("video_agent.image_gen")

_IMAGE_SAFETY_REWRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "rewritten_prompt": {"type": "string"},
        "changes_made": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["rewritten_prompt", "changes_made", "rationale"],
}

MAX_SAFETY_REWRITES = 3


_REWRITE_MODEL_CANDIDATES = [
    "gemini-3-pro-preview",
    "gemini-2.5-flash-preview-05-20",
    "gemini-2.0-flash",
    "gemini-3-pro-image-preview",
]


def _rewrite_prompt_for_safety(
    original_prompt: str,
    fail_reason: str,
    rewrite_history: Optional[List[str]] = None,
) -> Optional[dict]:
    """Call Gemini LLM to rewrite a blocked prompt. Returns parsed dict or None."""
    from clients import get_llm_client

    history_block = ""
    if rewrite_history:
        history_block = (
            "\n# Previous rewrite attempts (also failed)\n"
            + "\n".join(f"- Attempt {i+1}: {p}" for i, p in enumerate(rewrite_history))
            + "\n\nYou must try a DIFFERENT strategy this time.\n"
        )

    user_msg = (
        f"# Original prompt (blocked)\n{original_prompt}\n\n"
        f"# Block reason\n{fail_reason}\n"
        f"{history_block}\n"
        "Rewrite this prompt so it passes safety filters while keeping the visual intent."
    )

    client = get_llm_client(step="prompt_rewrite")
    for model in _REWRITE_MODEL_CANDIDATES:
        try:
            resp = client.generate_text(
                prompt=user_msg,
                system_instruction=_IMAGE_SAFETY_REWRITE_SYSTEM,
                temperature=0.7,
                response_schema=_IMAGE_SAFETY_REWRITE_SCHEMA,
                model=model,
            )
            return json.loads(resp)
        except Exception as e:
            _log.warning("Safety rewrite failed with model %s: %s", model, e)
            continue

    _log.error("Safety rewrite failed with all candidate models")
    return None

_PROMPT_FAILURE_SCHEMA = {
    "type": "object",
    "properties": {
        "has_issues": {"type": "boolean"},
        "detected_issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["ip", "sexual", "violence", "minors_in_danger",
                                 "hate_extremism", "other"],
                    },
                    "original_text": {"type": "string"},
                    "detail": {"type": "string"},
                    "replacement": {"type": "string"},
                },
                "required": ["category", "original_text", "detail", "replacement"],
            },
        },
        "rewritten_prompt": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["has_issues", "detected_issues", "rewritten_prompt", "rationale"],
}


def analyze_and_rewrite_prompt_issues(
    prompt: str,
    error_messages: Optional[List[str]] = None,
    skip_categories: Optional[List[str]] = None,
    characters: Optional[list] = None,
) -> Optional[dict]:
    """
    Analyze a prompt for all potential issues (IP, safety, violence, etc.)
    that may cause repeated generation failures.

    *skip_categories*: list of category names to exclude from analysis,
    e.g. ``["ip"]`` to only fix sensitive words without touching IP names.

    Returns parsed dict with: has_issues, detected_issues, rewritten_prompt, rationale.
    Returns None if the LLM call fails entirely.
    """
    from clients import get_llm_client

    errors_block = ""
    if error_messages:
        errors_block = (
            "\n# Error messages from failed attempts\n"
            + "\n".join(f"- {e}" for e in error_messages[-5:])
            + "\n"
        )

    skip_block = ""
    if skip_categories:
        skip_block = (
            "\n# IMPORTANT: Skip these categories — do NOT check or modify them\n"
            + "\n".join(f"- {c}" for c in skip_categories)
            + "\nLeave any content in these categories completely unchanged.\n"
        )

    char_block = ""
    if characters:
        char_block = (
            "\n# Character names in this scene\n"
            + "\n".join(f"- {c}" for c in characters)
            + "\n\nNote: These EXACT character names will be replaced with @图片N references, "
            "so they are NOT sensitive and should be left unchanged.\n"
            "However, nicknames or shortened forms (like '薰儿' from '萧薰儿') that appear "
            "in dialogue or narrative text still need homophone replacement.\n"
        )

    user_msg = (
        f"# Prompt (failed {len(error_messages or [])} times)\n{prompt}\n\n"
        f"{errors_block}{char_block}{skip_block}\n"
        "Analyze this prompt for ALL potential issues (IP, safety, violence, "
        "sexual content, minors in danger, etc.) and rewrite if needed."
    )

    client = get_llm_client(step="prompt_rewrite")
    for model in _REWRITE_MODEL_CANDIDATES:
        try:
            resp = client.generate_text(
                prompt=user_msg,
                system_instruction=_PROMPT_FAILURE_ANALYSIS_SYSTEM,
                temperature=0.4,
                response_schema=_PROMPT_FAILURE_SCHEMA,
                model=model,
            )
            return json.loads(resp)
        except Exception as e:
            _log.warning("Prompt failure analysis failed with model %s: %s", model, e)
            continue

    _log.error("Prompt failure analysis failed with all candidate models")
    return None


# Keep old name as alias for backward compatibility
analyze_and_rewrite_ip = analyze_and_rewrite_prompt_issues


PROMPT_PREFIX_9GRID = (
    "Generate a 3x3 grid image (九宫格) with 9 cells arranged in a grid layout. "
    "Each cell shows a different camera angle or composition of the same scene. "
    "Include: wide shot, medium shot, close-up, low angle, high angle, "
    "over-the-shoulder, profile, dramatic, and establishing shot. "
    "All cells share the same style, lighting, and characters. "
    "The scene content is:\n\n"
)



def _reload_entity_desc_from_disk(
    output_dir: str, name: str, stype: str,
) -> str | None:
    """Re-read entity description from storyboard JSON files on disk.

    Checks both the run-local storyboard copy and the source storyboard
    (in the storyboards/ directory), preferring whichever was modified more
    recently.  This picks up dashboard edits (e.g. "重新生图" with a new
    prompt) without requiring an agent process restart.
    """
    category = {"character": "characters", "location": "locations", "prop": "props"}.get(stype)
    if not category:
        return None

    def _read_desc(path: Path) -> str | None:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                sb_data = json.load(fh)
            for entity in sb_data.get(category, []):
                if entity.get("name") == name:
                    desc = entity.get("description", "")
                    personality = entity.get("personality", "")
                    if stype == "character" and personality:
                        return f"{desc}，性格：{personality}"
                    return desc
        except Exception:
            pass
        return None

    # Collect candidate storyboard files
    candidates: list[Path] = []

    # Run-local copies: {output_dir}/*_storyboard.json
    out_path = Path(output_dir)
    candidates.extend(sorted(out_path.glob("*_storyboard.json")))

    # Source storyboard: data/users/{uid}/storyboards/{sb_name}.json
    try:
        from main import _find_source_storyboard
        run_sb_candidates = list(out_path.glob("*_storyboard.json"))
        for rsb in run_sb_candidates:
            source = _find_source_storyboard(str(rsb), output_dir)
            if source:
                candidates.append(source)
    except Exception:
        pass

    # Deduplicate and sort by mtime descending — most recently edited first
    seen: set[str] = set()
    unique: list[Path] = []
    for c in candidates:
        key = str(c.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(c)
    try:
        unique.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        pass

    for path in unique:
        result = _read_desc(path)
        if result:
            return result
    return None


def _extract_characters(context: ExecutionContext) -> List[Tuple[str, str, str]]:
    """
    Extract (name, description, image_path) triples from the storyboard.
    ``image_path`` is an empty string when no pre-existing image is available.
    For characters, personality is appended to description.
    """
    if not context.storyboard:
        return []

    characters = []
    sb = context.storyboard
    if hasattr(sb, "characters") and sb.characters:
        for name, char in sb.characters.items():
            desc = getattr(char, "description", "")
            personality = getattr(char, "personality", "")
            if personality:
                desc = f"{desc}，性格：{personality}"
            img = getattr(char, "image_path", "")
            characters.append((name, desc, img))

    return characters


def _extract_locations(context: ExecutionContext) -> List[Tuple[str, str, str]]:
    """
    Extract (name, description, image_path) triples for locations from the storyboard.
    """
    if not context.storyboard:
        return []

    locations = []
    sb = context.storyboard
    if hasattr(sb, "locations") and sb.locations:
        for name, loc in sb.locations.items():
            desc = getattr(loc, "description", "")
            img = getattr(loc, "image_path", "")
            locations.append((name, desc, img))

    return locations


def _extract_props(context: ExecutionContext) -> List[Tuple[str, str, str]]:
    """
    Extract (name, description, image_path) triples for props from the storyboard.
    """
    if not context.storyboard:
        return []

    props = []
    sb = context.storyboard
    if hasattr(sb, "props") and sb.props:
        for name, prop in sb.props.items():
            desc = getattr(prop, "description", "")
            img = getattr(prop, "image_path", "")
            props.append((name, desc, img))

    return props


def _get_style_reference_image(context: ExecutionContext) -> str:
    """Return the style reference image path from the storyboard, if available."""
    sb = context.storyboard
    if not sb:
        return ""
    ref = getattr(sb, "style_reference_image", "")
    if ref and Path(ref).exists():
        return ref
    return ""


class ImageGen(BaseTool):
    """Generate a reference image for I2V pipeline."""

    def __init__(self):
        super().__init__()
        self.config = None  # Will be set by agent if available

    @property
    def name(self) -> str:
        return "image_gen"

    @property
    def description(self) -> str:
        return (
            "Generate a reference image for I2V pipeline. "
            "Supports modes: default (single image), 9grid (九宫格), "
            "charsheet (per-character 四视图)."
        )

    @property
    def category(self) -> ToolCategory:
        return "generator"

    def execute(self, context: ExecutionContext, **params) -> ToolResult:
        image_mode = params.get("image_mode", "default")

        if image_mode == "charsheet":
            return self._execute_charsheet(context, **params)
        return self._execute_single(context, **params)

    def _execute_single(self, context: ExecutionContext, **params) -> ToolResult:
        manual_assets = getattr(context, "manual_image_ref_assets", {}) or {}
        if manual_assets:
            valid_manual_paths = [
                str(Path(asset.get("path")))
                for asset in manual_assets.values()
                if isinstance(asset, dict) and asset.get("path") and Path(asset.get("path")).exists()
            ]
            if valid_manual_paths:
                first_manual_path = valid_manual_paths[0]
                print(f"[IMAGE GEN] Reusing manual reference image: {first_manual_path}")
                _log.info("─── _execute_single (manual reuse) ───\n  path: %s", first_manual_path)
                return ToolResult(
                    success=True,
                    output_path=first_manual_path,
                    metadata={"image_mode": "manual_reuse", "reused": True},
                )

        characters = _extract_characters(context)
        existing = [(n, p) for n, _, p in characters if p and Path(p).exists()]
        if existing:
            name, path = existing[0]
            print(f"[IMAGE GEN] Reusing existing image for {name}: {path}")
            _log.info("─── _execute_single (reuse) ───\n  character: %s  path: %s", name, path)
            return ToolResult(
                success=True,
                output_path=path,
                metadata={"image_mode": "reuse", "character": name, "reused": True},
            )

        prompt = context.prompt
        image_mode = params.get("image_mode", "default")
        prompt_prefix = params.get("prompt_prefix", "")
        prompt_suffix = params.get("prompt_suffix", "")
        aspect_ratio = params.get("aspect_ratio", "16:9")
        image_size = params.get("image_size", "2K")

        suffix = f"_{image_mode}" if image_mode != "default" else ""
        output_path = (
            f"{context.output_dir}/segment_{context.unit_id}"
            f"_ref_{context.attempt_number}{suffix}.png"
        )

        if image_mode == "9grid":
            prompt = PROMPT_PREFIX_9GRID + prompt
            aspect_ratio = "1:1"

        if prompt_prefix:
            prompt = prompt_prefix + prompt
        if prompt_suffix:
            prompt = prompt + prompt_suffix

        mode_label = {"9grid": "九宫格"}.get(image_mode, "")
        if mode_label:
            print(f"[IMAGE GEN] Generating {mode_label} image: {prompt[:100]}...")
        else:
            print(f"[IMAGE GEN] Generating reference image: {prompt[:100]}...")

        style_ref = _get_style_reference_image(context)
        ref_images = [style_ref] if style_ref else None

        _log.info(
            "─── _execute_single ───\n"
            "  unit_id: %s  attempt: %s  mode: %s\n"
            "  aspect_ratio: %s  image_size: %s\n"
            "  style_ref: %s\n"
            "  prompt:\n%s",
            context.unit_id, context.attempt_number, image_mode,
            aspect_ratio, image_size, style_ref or "(none)", prompt,
        )

        return self._generate_with_safety_rewrite(
            prompt=prompt,
            output_path=output_path,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            image_mode=image_mode,
            reference_images=ref_images,
        )

    # ------------------------------------------------------------------
    # Core generation helper with automatic safety-rewrite retry
    # ------------------------------------------------------------------

    def _generate_with_safety_rewrite(
        self,
        prompt: str,
        output_path: str,
        aspect_ratio: str = "16:9",
        image_size: str = "2K",
        image_mode: str = "default",
        reference_images: Optional[List[str]] = None,
    ) -> ToolResult:
        """Try to generate an image; on safety block, rewrite the prompt and retry."""
        from clients import get_image_client
        from clients.llm_client import ImageGenerationBlockedError

        current_prompt = prompt
        rewrite_history: List[str] = []

        for attempt in range(1 + MAX_SAFETY_REWRITES):
            try:
                client = get_image_client()
                image_data = client.generate_image(
                    prompt=current_prompt,
                    aspect_ratio=aspect_ratio,
                    image_size=image_size,
                    reference_images=reference_images,
                )

                out = Path(output_path)
                out.parent.mkdir(parents=True, exist_ok=True)
                with open(out, "wb") as f:
                    f.write(image_data)

                rewritten = current_prompt != prompt
                print(f"[IMAGE GEN] Image saved to: {out}"
                      + (f" (after {attempt} rewrite(s))" if rewritten else ""))
                _log.info("  ✓ output: %s  size: %d bytes  rewrites: %d",
                          out, len(image_data), attempt)
                return ToolResult(
                    success=True,
                    output_path=str(out),
                    metadata={
                        "prompt": current_prompt[:500],
                        "original_prompt": prompt[:500] if rewritten else None,
                        "image_mode": image_mode,
                        "aspect_ratio": aspect_ratio,
                        "safety_rewrites": attempt,
                    },
                )

            except ImageGenerationBlockedError as e:
                _log.warning("  ⚠ blocked (attempt %d/%d): %s",
                             attempt + 1, 1 + MAX_SAFETY_REWRITES, e.reason)
                print(f"[IMAGE GEN] ⚠ Image blocked by safety filter: {e.reason}")

                if attempt >= MAX_SAFETY_REWRITES:
                    print(f"[IMAGE GEN] ✗ Exhausted {MAX_SAFETY_REWRITES} safety rewrites")
                    _log.error("  ✗ all safety rewrites exhausted for prompt: %s",
                               prompt[:200])
                    return ToolResult(
                        success=False,
                        error=f"Image blocked after {MAX_SAFETY_REWRITES} rewrites: {e.reason}",
                    )

                print(f"[IMAGE GEN] Rewriting prompt to bypass safety filter "
                      f"(rewrite {attempt + 1}/{MAX_SAFETY_REWRITES})...")
                result = _rewrite_prompt_for_safety(
                    current_prompt, e.reason, rewrite_history or None,
                )
                if not result:
                    _log.error("  ✗ safety rewrite LLM failed")
                    return ToolResult(success=False, error=f"Safety rewrite failed: {e.reason}")

                rewrite_history.append(current_prompt)
                current_prompt = result["rewritten_prompt"]
                print(f"[IMAGE GEN]   Rewritten prompt: {current_prompt[:120]}...")
                print(f"[IMAGE GEN]   Changes: {', '.join(result['changes_made'])}")
                _log.info("  rewrite %d → %s\n  changes: %s\n  rationale: %s",
                          attempt + 1, current_prompt,
                          result["changes_made"], result["rationale"])

            except Exception as e:
                _log.error("  ✗ failed: %s", e)
                return ToolResult(success=False, error=str(e))

        return ToolResult(success=False, error="Unexpected: fell through rewrite loop")

    def _check_and_fix_style_consistency(
        self,
        image_paths: List[str],
        image_names: List[str],
        characters: List[Tuple[str, str, str]],
        locations: List[Tuple[str, str, str]],
        context: ExecutionContext,
        aspect_ratio: str,
        image_size: str,
        max_retries: int = 2,
    ) -> List[str]:
        """
        Check style consistency and regenerate inconsistent images.
        
        Returns:
            List of regenerated image names (empty if all consistent)
        """
        from clients import get_image_client
        from clients.llm_client import ImageGenerationBlockedError
        
        print(f"[STYLE CHECK] Analyzing {len(image_paths)} image(s) for style consistency...")
        
        try:
            from tools.style_consistency_checker import check_image_style_consistency
            result = check_image_style_consistency(
                image_paths=image_paths,
                image_names=image_names,
                threshold="medium",
                model=self.config.llm_model if (self.config and hasattr(self.config, 'llm_model')) else "gemini-3-pro-preview",
            )
        except Exception as e:
            _log.error("Style consistency check failed: %s", e)
            print(f"[STYLE CHECK] ✗ Check failed: {e}, proceeding without check")
            return []
        
        if result["is_consistent"]:
            print(f"[STYLE CHECK] ✓ All images are style-consistent")
            return []
        
        # Found inconsistent images — regenerate them
        inconsistent = result["inconsistent_images"]
        consistent = result.get("consistent_images", [])
        
        print(f"[STYLE CHECK] ✗ Found {len(inconsistent)} inconsistent image(s)")
        print(f"[STYLE CHECK] Dominant style: {result['dominant_style']}")
        
        # Select reference images (style-consistent images to guide regeneration)
        reference_images = []
        if consistent:
            print(f"[STYLE CHECK] Using {len(consistent)} reference image(s) for style guidance:")
            for ref in consistent[:3]:  # Use up to 3 reference images
                idx = ref["index"]
                if 0 <= idx < len(image_paths):
                    reference_images.append(image_paths[idx])
                    quality = ref.get("style_quality", "风格参考")
                    print(f"[STYLE CHECK]   • {image_names[idx]}: {quality}")
        
        if not reference_images:
            # Fallback: use all consistent images (non-inconsistent ones)
            inconsistent_indices = {item["index"] for item in inconsistent}
            reference_images = [
                path for i, path in enumerate(image_paths)
                if i not in inconsistent_indices
            ][:3]
            if reference_images:
                print(f"[STYLE CHECK] Using {len(reference_images)} non-inconsistent image(s) as reference")
        
        regenerated = []
        
        for item in inconsistent:
            idx = item["index"]
            name = item["name"]
            issue = item["issue"]
            
            if idx >= len(image_names):
                _log.warning("Invalid index %d for image_names (len=%d)", idx, len(image_names))
                continue
            
            print(f"[STYLE CHECK] Regenerating '{name}': {issue}")
            
            # Find the subject in characters or locations
            subject_desc = None
            subject_type = None
            
            for char_name, char_desc, _ in characters:
                if char_name == name:
                    subject_desc = char_desc
                    subject_type = "character"
                    break
            
            if subject_desc is None:
                for loc_name, loc_desc, _ in locations:
                    if loc_name == name:
                        subject_desc = loc_desc
                        subject_type = "location"
                        break
            
            if subject_desc is None:
                _log.warning("Cannot find subject '%s' in characters or locations", name)
                continue
            
            # Build style-guided prompt
            prompt_name = name.replace("[", "（").replace("]", "）")
            clean_desc = subject_desc.replace("。。", "。")
            if subject_type == "location":
                base_prompt = LOCATION_SHEET_TEMPLATE.format(
                    name=prompt_name, description=clean_desc
                )
            else:
                base_prompt = CHARSHEET_TEMPLATE.format(
                    name=prompt_name, description=clean_desc
                )
            
            # Add style guidance from dominant style
            style_guidance = (
                f"\n\nIMPORTANT: Generate in the following style to match other images:\n"
                f"{result['dominant_style']}\n"
                f"Avoid: {issue}"
            )
            guided_prompt = base_prompt + style_guidance
            
            # Regenerate with style guidance and reference images
            success = False
            for retry in range(max_retries):
                try:
                    new_path = self._regenerate_single_image(
                        prompt=guided_prompt,
                        subject_name=name,
                        subject_type=subject_type,
                        context=context,
                        aspect_ratio=aspect_ratio,
                        image_size=image_size,
                        retry_num=retry + 1,
                        reference_images=reference_images if reference_images else None,
                    )
                    
                    if new_path:
                        if subject_type == "character" and context.storyboard:
                            if hasattr(context.storyboard, "characters") and name in context.storyboard.characters:
                                context.storyboard.characters[name].image_path = new_path
                        elif subject_type == "location" and context.storyboard:
                            if hasattr(context.storyboard, "locations") and name in context.storyboard.locations:
                                context.storyboard.locations[name].image_path = new_path
                        elif subject_type == "prop" and context.storyboard:
                            if hasattr(context.storyboard, "props") and name in context.storyboard.props:
                                context.storyboard.props[name].image_path = new_path
                        
                        regenerated.append(name)
                        success = True
                        break
                
                except Exception as e:
                    _log.error("Regeneration failed for '%s' (retry %d/%d): %s",
                              name, retry + 1, max_retries, e)
                    if retry < max_retries - 1:
                        print(f"[STYLE CHECK]   Retry {retry + 1}/{max_retries} failed, retrying...")
                        time.sleep(2)
            
            if not success:
                print(f"[STYLE CHECK] ✗ Failed to regenerate '{name}' after {max_retries} retries")
        
        if regenerated:
            print(f"[STYLE CHECK] ✓ Regenerated {len(regenerated)} image(s): {', '.join(regenerated)}")
        
        return regenerated
    
    def _regenerate_single_image(
        self,
        prompt: str,
        subject_name: str,
        subject_type: str,
        context: ExecutionContext,
        aspect_ratio: str,
        image_size: str,
        retry_num: int,
        reference_images: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Regenerate a single image with style guidance and optional reference images."""
        from clients import get_image_client
        from clients.llm_client import ImageGenerationBlockedError
        
        tag = "loc" if subject_type == "location" else "char"
        output_path = (
            f"{context.output_dir}/segment_{context.unit_id}"
            f"_ref_{context.attempt_number}_{tag}_regen_{subject_name}.png"
        )
        
        # Build enhanced prompt with reference image guidance
        current_prompt = prompt
        if reference_images:
            ref_guidance = (
                f"\n\nStyle Reference: Match the visual style, color palette, lighting, "
                f"and artistic approach shown in the reference images. "
                f"Maintain consistency in detail level, texture quality, and overall aesthetic."
            )
            current_prompt = prompt + ref_guidance
        
        rewrite_history: List[str] = []
        
        for attempt in range(1, 6):  # Max 5 attempts per regeneration
            try:
                client = get_image_client()
                image_data = client.generate_image(
                    prompt=current_prompt,
                    aspect_ratio=aspect_ratio,
                    image_size=image_size,
                    reference_images=reference_images,
                )
                
                out = Path(output_path)
                out.parent.mkdir(parents=True, exist_ok=True)
                with open(out, "wb") as f:
                    f.write(image_data)
                
                print(f"[STYLE CHECK]   ✓ {subject_name} regenerated → {out}")
                _log.info("  regenerated %s → %s (%d bytes)", subject_name, out, len(image_data))
                return str(out)
            
            except ImageGenerationBlockedError as e:
                _log.warning("  regeneration blocked for %s: %s", subject_name, e.reason)
                print(f"[STYLE CHECK]   ⚠ {subject_name} blocked: {e.reason}")
                
                if len(rewrite_history) >= MAX_SAFETY_REWRITES:
                    print(f"[STYLE CHECK]   ✗ {subject_name}: exhausted safety rewrites")
                    return None
                
                rw = _rewrite_prompt_for_safety(current_prompt, e.reason, rewrite_history or None)
                if not rw:
                    return None
                
                rewrite_history.append(current_prompt)
                current_prompt = rw["rewritten_prompt"]
                print(f"[STYLE CHECK]   ↻ {subject_name} rewritten for safety")
            
            except Exception as e:
                _log.error("  regeneration error for %s (attempt %d): %s",
                          subject_name, attempt, e)
                if attempt < 5:
                    time.sleep(min(2 ** attempt, 10))
                else:
                    return None
        
        return None
    
    def _generate_with_reference_images(
        self,
        client,
        prompt: str,
        reference_images: List[str],
        aspect_ratio: str,
        image_size: str,
    ) -> bytes:
        """
        Generate image with reference images for style guidance.
        
        Uses Gemini's multi-modal capability to understand reference styles.
        """
        from google.genai import types
        from pathlib import Path

        http_options = client._http_options(client.IMAGE_TIMEOUT_SECONDS)
        
        # Build multi-modal prompt with reference images
        parts = []
        
        _MIME = {".png": "image/png", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}

        for i, ref_path in enumerate(reference_images[:3], 1):
            if not Path(ref_path).exists():
                _log.warning("Reference image not found: %s", ref_path)
                continue
            
            try:
                mime = _MIME.get(Path(ref_path).suffix.lower(), "image/png")
                with open(ref_path, "rb") as f:
                    ref_data = f.read()
                parts.append(types.Part(
                    inline_data=types.Blob(data=ref_data, mime_type=mime)
                ))
            except Exception as e:
                _log.warning("Failed to load reference image %s: %s", ref_path, e)
        
        # Add text prompt
        enhanced_prompt = (
            f"The above {len(parts)} image(s) show the target visual style. "
            f"Generate a new image that matches this style exactly.\n\n"
            f"{prompt}"
        )
        parts.append(types.Part(text=enhanced_prompt))
        
        # Generate with reference context
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=aspect_ratio,
                image_size=image_size
            ),
            http_options=http_options,
        )
        
        response = client._call_with_timeout_retry(
            lambda: client.client.models.generate_content(
                model="gemini-3-pro-image-preview",
                contents=[types.Content(parts=parts)],
                config=config,
            ),
            timeout_seconds=client.IMAGE_TIMEOUT_SECONDS,
            max_retries=1,
            action_label="Gemini reference image generation (gemini-3-pro-image-preview)",
        )
        
        # Extract image data
        if response.candidates:
            candidate = response.candidates[0]
            finish_reason = getattr(candidate, "finish_reason", None)
            fr_str = str(finish_reason) if finish_reason else ""
            blocked_reasons = {"IMAGE_SAFETY", "SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT"}
            
            if any(reason in fr_str for reason in blocked_reasons):
                from clients.llm_client import ImageGenerationBlockedError
                raise ImageGenerationBlockedError(f"Image generation blocked: {finish_reason}")
            
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if hasattr(part, "inline_data") and part.inline_data:
                        return part.inline_data.data
        
        raise RuntimeError("No image data in response")

    def _execute_charsheet(self, context: ExecutionContext, **params) -> ToolResult:
        """Generate one sheet per character + one per location + one per prop, return all paths."""
        aspect_ratio = "1:1"
        image_size = params.get("image_size", "2K")

        characters = _extract_characters(context)
        locations = _extract_locations(context)
        props = _extract_props(context)

        if not characters and not locations and not props:
            print("[IMAGE GEN] No characters, locations, or props found, falling back to single image")
            return self._execute_single(context, **params)

        char_existing = {n: p for n, _, p in characters if p and Path(p).exists()}
        loc_existing = {n: p for n, _, p in locations if p and Path(p).exists()}
        prop_existing = {n: p for n, _, p in props if p and Path(p).exists()}
        all_cached = (
            len(char_existing) == len(characters)
            and len(loc_existing) == len(locations)
            and len(prop_existing) == len(props)
        )

        manual_assets = getattr(context, "manual_image_ref_assets", {}) or {}
        manual_paths = [
            str(Path(asset.get("path")))
            for asset in manual_assets.values()
            if isinstance(asset, dict) and asset.get("path") and Path(asset.get("path")).exists()
        ]

        if all_cached and (characters or locations or props):
            all_paths = ([char_existing[n] for n, _, _ in characters]
                         + [loc_existing[n] for n, _, _ in locations]
                         + [prop_existing[n] for n, _, _ in props]
                         + manual_paths)
            char_names = [n for n, _, _ in characters]
            loc_names = [n for n, _, _ in locations]
            prop_names = [n for n, _, _ in props]
            names_str = ", ".join(char_names + loc_names + prop_names)
            print(f"[IMAGE GEN] Reusing {len(all_paths)} existing sheet image(s): {names_str}")
            _log.info(
                "─── _execute_charsheet (reuse all) ───\n  paths: %s\n"
                "  char_names: %s\n  loc_names: %s\n  prop_names: %s",
                all_paths, char_names, loc_names, prop_names,
            )
            return ToolResult(
                success=True,
                output_path=all_paths[0],
                metadata={
                    "image_mode": "charsheet",
                    "all_image_paths": all_paths,
                    "character_names": char_names,
                    "location_names": loc_names,
                    "prop_names": prop_names,
                    "character_count": len(char_names),
                    "location_count": len(loc_names),
                    "prop_count": len(prop_names),
                    "reused": True,
                },
            )

        total = len(characters) + len(locations) + len(props)
        char_list_str = ", ".join(c[0] for c in characters)
        loc_list_str = ", ".join(loc[0] for loc in locations)
        prop_list_str = ", ".join(p[0] for p in props)
        _log.info(
            "─── _execute_charsheet ───\n"
            "  unit_id: %s  attempt: %s\n"
            "  characters (%d): %s\n"
            "  locations  (%d): %s\n"
            "  props      (%d): %s",
            context.unit_id, context.attempt_number,
            len(characters), char_list_str,
            len(locations), loc_list_str,
            len(props), prop_list_str,
        )

        # ── Collect existing sheets only (no generation — that's handled by
        #    agent._prepare_charsheets).  Missing sheets are simply skipped. ──
        char_paths: List[str] = []
        char_names: List[str] = []
        loc_paths: List[str] = []
        loc_names: List[str] = []
        prop_paths: List[str] = []
        prop_names: List[str] = []

        for name, desc, img in characters:
            path = char_existing.get(name)
            if path:
                char_paths.append(path)
                char_names.append(name)
                _log.info("  %s — reusing existing: %s", name, path)
            else:
                _log.warning("  %s — no charsheet found, skipping", name)
                print(f"[IMAGE GEN]   ⚠ {name}: no charsheet found, skipping")

        for name, desc, img in locations:
            path = loc_existing.get(name)
            if path:
                loc_paths.append(path)
                loc_names.append(name)
                _log.info("  %s — reusing existing: %s", name, path)
            else:
                _log.warning("  %s — no location sheet found, skipping", name)
                print(f"[IMAGE GEN]   ⚠ {name}: no location sheet found, skipping")

        for name, desc, img in props:
            path = prop_existing.get(name)
            if path:
                prop_paths.append(path)
                prop_names.append(name)
                _log.info("  %s — reusing existing: %s", name, path)
            else:
                _log.warning("  %s — no prop sheet found, skipping", name)
                print(f"[IMAGE GEN]   ⚠ {name}: no prop sheet found, skipping")

        all_paths = char_paths + loc_paths + prop_paths + manual_paths
        if not all_paths:
            return ToolResult(success=False, error="No sheet images available (all missing)")

        print(f"[IMAGE GEN] Collected {len(char_paths)}/{len(characters)} charsheet(s), "
              f"{len(loc_paths)}/{len(locations)} location sheet(s), "
              f"{len(prop_paths)}/{len(props)} prop sheet(s)")
        _log.info(
            "  sheet result: chars %d/%d, locs %d/%d, props %d/%d\n"
            "  char_paths: %s\n  loc_paths: %s\n  prop_paths: %s",
            len(char_paths), len(characters),
            len(loc_paths), len(locations),
            len(prop_paths), len(props),
            char_paths, loc_paths, prop_paths,
        )
        
        # ── Style consistency check ──────────────────────────────────────
        style_check_enabled = params.get("check_style_consistency", True)
        max_style_retries = params.get("max_style_retries", 2)
        
        if style_check_enabled and len(all_paths) >= 2:
            all_names = char_names + loc_names + prop_names
            inconsistent = self._check_and_fix_style_consistency(
                image_paths=all_paths,
                image_names=all_names,
                characters=characters,
                locations=locations,
                context=context,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                max_retries=max_style_retries,
            )
            
            if inconsistent:
                print(f"[STYLE CHECK] Rebuilding image paths after regeneration...")
                char_paths_new = []
                char_names_new = []
                loc_paths_new = []
                loc_names_new = []
                prop_paths_new = []
                prop_names_new = []
                
                if context.storyboard:
                    if hasattr(context.storyboard, "characters"):
                        for name, _, _ in characters:
                            if name in context.storyboard.characters:
                                img_path = context.storyboard.characters[name].image_path
                                if img_path and Path(img_path).exists():
                                    char_paths_new.append(img_path)
                                    char_names_new.append(name)
                    
                    if hasattr(context.storyboard, "locations"):
                        for name, _, _ in locations:
                            if name in context.storyboard.locations:
                                img_path = context.storyboard.locations[name].image_path
                                if img_path and Path(img_path).exists():
                                    loc_paths_new.append(img_path)
                                    loc_names_new.append(name)

                    if hasattr(context.storyboard, "props"):
                        for name, _, _ in props:
                            if name in context.storyboard.props:
                                img_path = context.storyboard.props[name].image_path
                                if img_path and Path(img_path).exists():
                                    prop_paths_new.append(img_path)
                                    prop_names_new.append(name)
                    
                    if char_paths_new or loc_paths_new or prop_paths_new:
                        char_paths = char_paths_new
                        char_names = char_names_new
                        loc_paths = loc_paths_new
                        loc_names = loc_names_new
                        prop_paths = prop_paths_new
                        prop_names = prop_names_new
                        all_paths = char_paths + loc_paths + prop_paths
                        
                        print(f"[STYLE CHECK] Updated paths: {len(all_paths)} total "
                              f"({len(char_paths)} chars, {len(loc_paths)} locs, "
                              f"{len(prop_paths)} props)")
        
        return ToolResult(
            success=True,
            output_path=all_paths[0],
            metadata={
                "image_mode": "charsheet",
                "all_image_paths": all_paths,
                "character_names": char_names,
                "location_names": loc_names,
                "prop_names": prop_names,
                "character_count": len(char_paths),
                "location_count": len(loc_paths),
                "prop_count": len(prop_paths),
            },
        )
