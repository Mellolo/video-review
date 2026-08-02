"""
Shared validation and auto-fix logic for all storyboard generators.

Validates character references, location references, dialogue speaker
attribution, and line_type consistency.  Auto-fixes common issues like
alias mismatches and misattributed inner monologue.
"""

import re
from typing import List, Tuple, Optional


class ValidationIssue:
    __slots__ = ("scene_number", "issue_type", "detail", "auto_fixed")

    def __init__(self, scene_number: int, issue_type: str,
                 detail: str, auto_fixed: bool = False):
        self.scene_number = scene_number
        self.issue_type = issue_type
        self.detail = detail
        self.auto_fixed = auto_fixed

    def __repr__(self) -> str:
        tag = "已修复" if self.auto_fixed else "待处理"
        return f"Scene {self.scene_number} [{self.issue_type}] {self.detail} ({tag})"


def build_alias_map(canonical_names: set) -> dict:
    """Build common alias -> canonical name mapping for Chinese names.

    Examples: "张三丰" -> aliases "三丰", "张三";
              "灵月仙子" -> alias "灵月".
    """
    alias_map: dict[str, str] = {}
    for name in canonical_names:
        if len(name) >= 3:
            alias_map[name[1:]] = name
            alias_map[name[:2]] = name
        for title in ("长老", "大师", "仙子", "公主", "王子",
                      "大人", "将军", "小姐", "先生"):
            if name.endswith(title) and len(name) > len(title):
                alias_map[name[:-len(title)]] = name
            if name.startswith(title) and len(name) > len(title):
                alias_map[name[len(title):]] = name
    return alias_map


def dialogue_lines_to_string(lines: List[dict]) -> str:
    """Convert structured dialogue_lines to a readable string.

    Format: ``[speaker/type·emotion] text``
    """
    if not lines:
        return ""
    parts = []
    for dl in lines:
        speaker = dl.get("speaker", "旁白")
        line_type = dl.get("line_type", "dialogue")
        text = dl.get("text", "")
        emotion = dl.get("emotion", "")
        tag = f"{speaker}/{line_type}"
        if emotion:
            tag += f"·{emotion}"
        parts.append(f"[{tag}] {text}")
    return "\n".join(parts)


_STANDALONE_CONTINUITY_PATTERNS = (
    (re.compile(r"镜头承接上一段[^，。；！？]*?(?:的)?特写[，、\s]*"), ""),
    (re.compile(r"承接上一段[^，。；！？]*?(?:的)?特写[，、\s]*"), ""),
    (re.compile(r"(?:顺着|沿着)上一段[^，。；！？]*?视线[^，。；！？]*?切入"), "以"),
    (re.compile(r"(?:顺着|沿着)上一段[^，。；！？]*?视线方向[，、\s]*"), ""),
    (re.compile(r"利用上一段留出的[^，。；！？]*[，、\s]*"), ""),
    (re.compile(r"承接上一段的[^，。；！？]*[，、\s]*"), ""),
    (re.compile(r"承接上一段[^，。；！？]*[，、\s]*"), ""),
    (re.compile(r"上一段[^，。；！？]*?(?:结尾|末尾|画面|镜头|特写)[，、\s]*"), ""),
    (re.compile(r"前段[^，。；！？]*?(?:结尾|末尾|画面|镜头|特写)[，、\s]*"), ""),
    (re.compile(r"(?:上一镜头|前一镜头|上一幕|前一幕)[^，。；！？]*[，、\s]*"), ""),
)

_STANDALONE_CONTINUITY_LITERALS = (
    ("镜头承接上一段", ""),
    ("顺着上一段", ""),
    ("利用上一段", ""),
    ("承接上一段的", ""),
    ("承接上一段", ""),
    ("上一段的", ""),
    ("前段的", ""),
    ("上一镜头", ""),
    ("前一镜头", ""),
    ("上一幕", ""),
    ("前一幕", ""),
    ("为下一段做铺垫", "在结尾预留过桥锚点"),
    ("为下一段留下", "在结尾留下"),
    ("方便下一段开头承接", "方便后续镜头承接"),
    ("图片1", ""),
    ("图片2", ""),
    ("图片3", ""),
    ("图片4", ""),
    ("@图片1", ""),
    ("@图片2", ""),
    ("@图片3", ""),
    ("@图片4", ""),
)

_STYLE_LINE_PATTERN = re.compile(
    r"^\s*(?:风格|画面风格|整体画面风格|style)\s*[:：]\s*(.+?)\s*$",
    re.IGNORECASE,
)


def ensure_prompt_style_prefix(prompt: str, style: str, fallback: str = "") -> str:
    """Ensure prompt starts with a normalized ``风格：xxx`` line."""
    cleaned = (prompt or "").strip()
    style_text = (style or "").strip()

    if not cleaned:
        return f"风格：{style_text}" if style_text else fallback

    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return f"风格：{style_text}" if style_text else (fallback or cleaned)

    existing_style = ""
    body_lines = []
    style_removed = False
    for line in lines:
        match = _STYLE_LINE_PATTERN.match(line)
        if match and not style_removed:
            existing_style = match.group(1).strip()
            style_removed = True
            continue
        body_lines.append(line)

    resolved_style = style_text or existing_style
    body = "\n".join(body_lines).strip()

    if not resolved_style:
        return body or fallback or cleaned
    if not body:
        return f"风格：{resolved_style}"
    return f"风格：{resolved_style}\n{body}"


def prompt_has_body(prompt: str) -> bool:
    """Return True if *prompt* contains meaningful content beyond a style prefix line."""
    lines = [
        line.strip()
        for line in (prompt or "").strip().splitlines()
        if line.strip()
    ]
    body_lines = [
        line for line in lines
        if not _STYLE_LINE_PATTERN.match(line)
    ]
    if body_lines:
        return True
    # Edge case: style prefix and body content on the same single line.
    # e.g. "风格：3D CG动画风格。全程无背景音乐。画面开始于..."
    # _STYLE_LINE_PATTERN matches the whole line, but there IS body content.
    if lines and _STYLE_LINE_PATTERN.match(lines[0]):
        after_style = _STYLE_LINE_PATTERN.sub("", lines[0]).strip()
        if after_style:
            return True
        # Check if the captured style group itself contains body-like content
        # (sentences beyond the style description, indicated by 。)
        m = _STYLE_LINE_PATTERN.match(lines[0])
        if m:
            style_val = m.group(1).strip()
            # If the "style value" contains sentence-ending punctuation
            # followed by more text, there's body content mixed in
            parts = re.split(r"[。！？]", style_val, maxsplit=1)
            if len(parts) > 1 and parts[1].strip():
                return True
    return False


def sanitize_continuity_text(text: str, fallback: str = "") -> str:
    """Normalize continuity wording so each text stands alone without prior frames."""
    cleaned = (text or "").strip()
    if not cleaned:
        return fallback

    for pattern, replacement in _STANDALONE_CONTINUITY_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    for old, new in _STANDALONE_CONTINUITY_LITERALS:
        cleaned = cleaned.replace(old, new)

    cleaned = re.sub(r"^以[，、\s]+", "", cleaned)
    cleaned = re.sub(r"[，,]{2,}", "，", cleaned)
    cleaned = re.sub(r"[。！？]{2,}", "。", cleaned)
    cleaned = re.sub(r"，([。！？])", r"\1", cleaned)
    cleaned = re.sub(r"^[，。；、\s]+", "", cleaned)
    cleaned = re.sub(r"[，；、\s]+$", "", cleaned)
    cleaned = re.sub(r"[^\S\n]+", " ", cleaned).strip()

    return cleaned or fallback


def sanitize_scene_continuity(scene: dict) -> List[ValidationIssue]:
    """Sanitize prompt/transition fields so they do not depend on previous frames."""
    issues: List[ValidationIssue] = []
    sn = scene.get("scene_number") or scene.get("segment_id") or 0

    for field in ("seedance_prompt", "sora_prompt", "transition_strategy"):
        value = scene.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        cleaned = sanitize_continuity_text(value, fallback=value.strip())
        if cleaned != value:
            scene[field] = cleaned
            issues.append(ValidationIssue(
                sn,
                "standalone_transition_text",
                f"{field} 去除了对上一段/前一镜头/图片的直接引用",
                auto_fixed=True,
            ))

    anchor = scene.get("continuity_anchor")
    if isinstance(anchor, dict):
        for key, value in list(anchor.items()):
            if not isinstance(value, str) or not value.strip():
                continue
            cleaned = sanitize_continuity_text(value, fallback=value.strip())
            if cleaned != value:
                anchor[key] = cleaned
                issues.append(ValidationIssue(
                    sn,
                    "standalone_transition_text",
                    f"continuity_anchor.{key} 去除了对上一段/前一镜头/图片的直接引用",
                    auto_fixed=True,
                ))

    return issues


# ── Character name strict matching / normalization in prompt text ─────────

# Regex to match quoted dialogue: Chinese quotes "…" or 「…」
_DIALOGUE_QUOTE_PATTERN = re.compile(
    r'[\u201c\u300c].*?[\u201d\u300d]'
)

# Common age/state qualifiers that should only appear when explicitly defined
# in canonical character names, e.g. "唐三（成年）" / "唐三（少年）".
_AGE_QUALIFIER_TERMS = (
    "成年", "少年", "青年", "幼年", "童年", "童年期", "童年版",
    "儿童", "孩童", "小孩", "少女", "男孩", "女孩",
    "中年", "老年", "老年版", "幼体", "幼体期",
)

_PAREN_NAME_PATTERN = re.compile(r'^(.+?)[（(](.+?)[）)]$')


def _get_canonical_plain_names(char_names: set) -> set:
    """Return canonical names that do not explicitly encode age/state variants."""
    plain_names = set()
    for name in char_names:
        m = _PAREN_NAME_PATTERN.match(name)
        if m and _contains_age_qualifier(m.group(2)):
            continue
        plain_names.add(name)
    return plain_names


def _build_short_to_full_map(char_names: set) -> dict:
    """Build a mapping from short/ambiguous names to canonical age/state variants.

    Only explicit age/state variants such as "唐三（成年）" and "唐三（少年）"
    participate in this mapping. Returns {short_name: [full_name1, ...]}.
    """
    base_to_full: dict = {}

    for name in char_names:
        m = _PAREN_NAME_PATTERN.match(name)
        if not m:
            continue
        base, qualifier = m.group(1), m.group(2)
        if _contains_age_qualifier(qualifier):
            base_to_full.setdefault(base, []).append(name)

    return base_to_full


def _contains_age_qualifier(text: str) -> bool:
    return any(term in (text or "") for term in _AGE_QUALIFIER_TERMS)


def _extract_name_base_and_qualifier(name: str) -> tuple[Optional[str], Optional[str]]:
    """Extract a canonical base name plus age/state qualifier if present.

    Supports:
    - 后缀括号：唐三（成年）
    - 前缀形态：成年唐三 / 少年唐三
    """
    if not name:
        return None, None

    m = _PAREN_NAME_PATTERN.match(name)
    if m:
        base, qualifier = m.group(1), m.group(2)
        if _contains_age_qualifier(qualifier):
            return base, qualifier
        return None, None

    for term in sorted(_AGE_QUALIFIER_TERMS, key=len, reverse=True):
        if name.startswith(term) and len(name) > len(term):
            return name[len(term):], term

    return None, None


def _build_name_normalization_rules(
    char_names: set,
    scene_chars: List[str],
) -> tuple[dict, dict, set]:
    """Build normalization rules for allowed and disallowed age/state variants.

    Returns:
      short_to_full: {base_name: [full_name, ...]}
      strip_to_base: {noncanonical_variant: canonical_base_name}
      all_full_names: set(full canonical names with qualifiers)
    """
    short_to_full = _build_short_to_full_map(char_names)
    all_full_names = {
        candidate
        for candidates in short_to_full.values()
        for candidate in candidates
    }

    canonical_plain_names = _get_canonical_plain_names(char_names)

    strip_to_base: dict[str, str] = {}

    # If a base name has no canonical qualified variants, then any generated
    # age/state-qualified forms should be stripped back to the canonical base.
    for base in canonical_plain_names:
        if base in short_to_full:
            continue
        for term in _AGE_QUALIFIER_TERMS:
            strip_to_base[f"{term}{base}"] = base
            strip_to_base[f"{base}（{term}）"] = base
            strip_to_base[f"{base}({term})"] = base

    # Scene characters may themselves contain a non-canonical age qualifier.
    # If so, normalize them back to canonical base names when the qualified form
    # is not explicitly defined.
    for scene_name in scene_chars or []:
        base, _qualifier = _extract_name_base_and_qualifier(scene_name)
        if not base:
            continue
        if scene_name not in char_names and base in canonical_plain_names and base not in short_to_full:
            strip_to_base[scene_name] = base

    return short_to_full, strip_to_base, all_full_names


def _replace_names_outside_quotes(
    text: str,
    replacements: dict,
    protected_full_names: Optional[set] = None,
) -> str:
    """Replace names outside quoted dialogue, optionally protecting full names."""
    if not replacements:
        return text

    quoted_spans = []
    for m in _DIALOGUE_QUOTE_PATTERN.finditer(text):
        quoted_spans.append((m.start(), m.end()))

    def _in_quoted(pos: int, length: int) -> bool:
        for qs, qe in quoted_spans:
            if pos >= qs and pos + length <= qe:
                return True
        return False

    def _inside_protected_name(pos: int, target: str) -> bool:
        if not protected_full_names:
            return False
        for full in protected_full_names:
            if target in full and full != target:
                idx = pos - len(full) + len(target)
                for start in range(max(0, idx), min(pos + 1, len(text))):
                    if text[start:start + len(full)] == full:
                        return True
        return False

    for source in sorted(replacements.keys(), key=len, reverse=True):
        replacement = replacements[source]
        result = []
        i = 0
        while i < len(text):
            if text[i:i + len(source)] == source:
                if _in_quoted(i, len(source)) or _inside_protected_name(i, source):
                    result.append(source)
                    i += len(source)
                else:
                    result.append(replacement)
                    i += len(source)
            else:
                result.append(text[i])
                i += 1
        text = "".join(result)
        quoted_spans = [(m.start(), m.end()) for m in _DIALOGUE_QUOTE_PATTERN.finditer(text)]

    return text


def _normalize_character_names_outside_quotes(
    text: str,
    short_to_full: dict,
    strip_to_base: dict,
    scene_chars: List[str],
    all_full_names: set,
) -> str:
    """Normalize character names outside quotes.

    Rules:
    1. If canonical definitions explicitly distinguish variants like
       "唐三（成年）" / "唐三（少年）", expand short names to canonical full names.
    2. If canonical definitions only contain plain names like "李平安",
       strip generated non-canonical forms like "李平安（成年）" / "少年李平安"
       back to the base name.
    """
    if not short_to_full and not strip_to_base:
        return text

    # Phase 1: strip unsupported generated variants back to base names.
    text = _replace_names_outside_quotes(text, strip_to_base)

    if not short_to_full:
        return text

    # Build prefix-to-full mapping only for explicitly defined canonical variants.
    prefix_replacements: dict = {}
    for candidates in short_to_full.values():
        for full_name in candidates:
            pm = _PAREN_NAME_PATTERN.match(full_name)
            if pm:
                base = pm.group(1)
                suffix = pm.group(2)
                prefix_replacements[suffix + base] = full_name

    # Phase 2: replace canonical prefix forms like "少年唐三" -> "唐三（少年）".
    text = _replace_names_outside_quotes(
        text,
        prefix_replacements,
        protected_full_names=all_full_names,
    )

    # Phase 3: replace short names with the correct canonical variant when
    # canonical variants are explicitly defined.
    short_replacements: dict = {}
    for short in sorted(short_to_full.keys(), key=len, reverse=True):
        candidates = short_to_full[short]
        if len(candidates) == 1:
            short_replacements[short] = candidates[0]
            continue

        in_scene = [c for c in candidates if c in scene_chars]
        if len(in_scene) == 1:
            short_replacements[short] = in_scene[0]

    return _replace_names_outside_quotes(
        text,
        short_replacements,
        protected_full_names=all_full_names,
    )


def fix_character_names_in_prompts(
    scene: dict,
    char_names: set,
) -> List[ValidationIssue]:
    """Normalize character names in prompt-like text fields.

    - If canonical character definitions explicitly include qualified variants
      such as "唐三（成年）", prompts are normalized toward those canonical names.
    - Otherwise, generated qualifiers like "李平安（成年）" are stripped back to
      the canonical base name "李平安".
    - Quoted dialogue is preserved.
    """
    issues: List[ValidationIssue] = []
    sn = scene.get("scene_number") or scene.get("segment_id") or 0
    scene_chars = scene.get("characters_in_scene", [])
    short_to_full, strip_to_base, all_full_names = _build_name_normalization_rules(
        char_names,
        scene_chars,
    )

    if not short_to_full and not strip_to_base:
        return issues

    for field in ("seedance_prompt", "sora_prompt", "narrative_summary",
                  "transition_strategy"):
        value = scene.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        fixed = _normalize_character_names_outside_quotes(
            value,
            short_to_full,
            strip_to_base,
            scene_chars,
            all_full_names,
        )
        if fixed != value:
            scene[field] = fixed
            issues.append(ValidationIssue(
                sn,
                "character_name_strict_match",
                f"{field} 中的角色名已规范为角色定义中的合法名称",
                auto_fixed=True,
            ))

    anchor = scene.get("continuity_anchor")
    if isinstance(anchor, dict):
        for key, value in list(anchor.items()):
            if not isinstance(value, str) or not value.strip():
                continue
            fixed = _normalize_character_names_outside_quotes(
                value,
                short_to_full,
                strip_to_base,
                scene_chars,
                all_full_names,
            )
            if fixed != value:
                anchor[key] = fixed
                issues.append(ValidationIssue(
                    sn,
                    "character_name_strict_match",
                    f"continuity_anchor.{key} 中的角色名已规范为角色定义中的合法名称",
                    auto_fixed=True,
                ))

    return issues


def validate_and_fix(
    scenes: List[dict],
    char_names: set,
    loc_names: set,
    prop_names: set = frozenset(),
) -> Tuple[List[dict], List[ValidationIssue]]:
    """Validate and auto-fix character/location/prop/dialogue consistency.

    Returns the (possibly modified) scenes list and a list of issues found.
    """
    issues: List[ValidationIssue] = []
    char_alias = build_alias_map(char_names)
    loc_alias = build_alias_map(loc_names)
    prop_alias = build_alias_map(prop_names) if prop_names else {}

    for scene in scenes:
        sn = scene.get("scene_number", 0)

        canonical_plain_names = _get_canonical_plain_names(char_names)
        short_to_full_map = _build_short_to_full_map(char_names)

        # ── Check characters_in_scene ─────────────────────────────
        fixed_chars = []
        for cname in scene.get("characters_in_scene", []):
            normalized_cname = cname
            base_name, _qualifier = _extract_name_base_and_qualifier(cname)
            if (
                cname not in char_names
                and base_name
                and base_name in canonical_plain_names
                and base_name not in short_to_full_map
            ):
                normalized_cname = base_name
                issues.append(ValidationIssue(
                    sn, "character_name_normalized",
                    f"角色 '{cname}' → '{base_name}'",
                    auto_fixed=True,
                ))

            if normalized_cname in char_names:
                fixed_chars.append(normalized_cname)
            elif normalized_cname in char_alias:
                fixed_chars.append(char_alias[normalized_cname])
                issues.append(ValidationIssue(
                    sn, "character_alias",
                    f"角色 '{normalized_cname}' → '{char_alias[normalized_cname]}'",
                    auto_fixed=True,
                ))
            else:
                issues.append(ValidationIssue(
                    sn, "unknown_character",
                    f"角色 '{normalized_cname}' 不在定义中",
                ))
                fixed_chars.append(normalized_cname)
        scene["characters_in_scene"] = fixed_chars

        # ── Check scene_location ──────────────────────────────────
        loc = scene.get("scene_location", "")
        if loc and loc not in loc_names:
            if loc in loc_alias:
                scene["scene_location"] = loc_alias[loc]
                issues.append(ValidationIssue(
                    sn, "location_alias",
                    f"场景 '{loc}' → '{loc_alias[loc]}'",
                    auto_fixed=True,
                ))
            else:
                issues.append(ValidationIssue(
                    sn, "unknown_location",
                    f"场景 '{loc}' 不在定义中",
                ))

        # ── Check props_in_scene ──────────────────────────────────
        if prop_names:
            fixed_props = []
            for pname in scene.get("props_in_scene", []):
                if pname in prop_names:
                    fixed_props.append(pname)
                elif pname in prop_alias:
                    fixed_props.append(prop_alias[pname])
                    issues.append(ValidationIssue(
                        sn, "prop_alias",
                        f"道具 '{pname}' → '{prop_alias[pname]}'",
                        auto_fixed=True,
                    ))
                else:
                    issues.append(ValidationIssue(
                        sn, "unknown_prop",
                        f"道具 '{pname}' 不在定义中",
                    ))
                    fixed_props.append(pname)
            scene["props_in_scene"] = fixed_props

        # ── Check dialogue speakers & line_type ───────────────────
        valid_line_types = {"dialogue", "inner", "narration", "crowd"}
        dialogue_lines = scene.get("dialogue_lines", [])
        for dl in dialogue_lines:
            speaker = dl.get("speaker", "")
            line_type = dl.get("line_type", "")

            if speaker not in ("", "旁白", "路人"):
                base_name, _qualifier = _extract_name_base_and_qualifier(speaker)
                if (
                    speaker not in char_names
                    and base_name
                    and base_name in canonical_plain_names
                    and base_name not in short_to_full_map
                ):
                    dl["speaker"] = base_name
                    issues.append(ValidationIssue(
                        sn, "dialogue_speaker_normalized",
                        f"说话者 '{speaker}' → '{base_name}'",
                        auto_fixed=True,
                    ))
                    speaker = base_name

            if line_type not in valid_line_types:
                if speaker == "旁白":
                    dl["line_type"] = "narration"
                elif speaker == "路人":
                    dl["line_type"] = "crowd"
                else:
                    dl["line_type"] = "dialogue"
                issues.append(ValidationIssue(
                    sn, "invalid_line_type",
                    f"line_type '{line_type}' 无效，已修正为 '{dl['line_type']}'",
                    auto_fixed=True,
                ))

            if not speaker:
                dl["speaker"] = "旁白"
                dl["line_type"] = "narration"
                issues.append(ValidationIssue(
                    sn, "empty_speaker",
                    "台词缺少说话者，已设为旁白/narration",
                    auto_fixed=True,
                ))
                continue

            if speaker == "旁白" and dl.get("line_type") == "narration":
                text = dl.get("text", "")
                first_person = any(
                    m in text for m in ("我", "自己", "本座", "老子", "吾")
                )
                has_question_to_self = (
                    ("吗" in text or "么" in text or "？" in text)
                    and len(text) < 80
                )
                if first_person or has_question_to_self:
                    scene_chars = scene.get("characters_in_scene", [])
                    if len(scene_chars) == 1:
                        dl["speaker"] = scene_chars[0]
                        dl["line_type"] = "inner"
                        issues.append(ValidationIssue(
                            sn, "narrator_to_inner",
                            f"旁白疑似内心独白 → 已改为 "
                            f"'{scene_chars[0]}'/inner: '{text[:30]}…'",
                            auto_fixed=True,
                        ))
                    elif scene_chars:
                        issues.append(ValidationIssue(
                            sn, "suspect_inner_monologue",
                            f"旁白疑似某角色的内心独白: '{text[:40]}…' "
                            f"(场景角色: {', '.join(scene_chars)})",
                        ))

            if speaker in ("旁白", "路人"):
                continue

            if speaker not in char_names:
                if speaker in char_alias:
                    dl["speaker"] = char_alias[speaker]
                    issues.append(ValidationIssue(
                        sn, "dialogue_speaker_alias",
                        f"说话者 '{speaker}' → '{char_alias[speaker]}'",
                        auto_fixed=True,
                    ))
                    speaker = dl["speaker"]
                else:
                    issues.append(ValidationIssue(
                        sn, "dialogue_speaker_unknown",
                        f"说话者 '{speaker}' 不在角色定义中",
                    ))

            if (speaker not in ("旁白", "路人")
                    and speaker not in scene.get("characters_in_scene", [])):
                scene["characters_in_scene"].append(speaker)
                issues.append(ValidationIssue(
                    sn, "speaker_not_in_scene",
                    f"说话者 '{speaker}' 不在 characters_in_scene，已添加",
                    auto_fixed=True,
                ))

        issues.extend(sanitize_scene_continuity(scene))
        issues.extend(fix_character_names_in_prompts(scene, char_names))

        # ── Check for empty content ───────────────────────────────
        if not scene.get("visual_description", "").strip():
            issues.append(ValidationIssue(
                sn, "empty_visual", "visual_description 为空",
            ))
        if not scene.get("plot_description", "").strip():
            issues.append(ValidationIssue(
                sn, "empty_plot", "plot_description 为空",
            ))

    return scenes, issues
