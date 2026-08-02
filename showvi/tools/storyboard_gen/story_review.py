"""
Gemini-based story review and fix for storyboard quality assurance.

Two-phase process:
  Phase 1 (Review): Gemini analyzes the storyboard for issues like scene jumps,
      plot incoherence, unreasonable dialogue, dialogue misattribution, etc.
  Phase 2 (Fix): A second Gemini call takes the storyboard + issues and outputs
      corrected scenes. New scenes use "N_2", "N_3" naming for insertions.

Merge logic replaces/inserts scenes and renumbers sequentially.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

from pydantic import BaseModel, Field

from .schemas import DialogueLine, flatten_json_schema
from prompts.story_review import _REVIEW_SYSTEM_PROMPT, _FIX_SYSTEM_PROMPT

_log = logging.getLogger("video_agent.story_review")


# ═══════════════════════════════════════════════════════════════════════
#  Content-policy sanitisation (shared with base_engine)
# ═══════════════════════════════════════════════════════════════════════

_AGE_PATTERNS = re.compile(
    r"(?:\d{1,2}岁(?:左右)?(?:的)?)"
    r"|(?:年(?:龄|纪)(?:约|不过|大约)?\d{1,2}(?:岁|多岁)?(?:左右)?)"
    r"|(?:约?\d{1,2}(?:多)?岁(?:左右)?(?:的少[年女男])?)"
)

_YOUTH_REPLACEMENTS = [
    ("少女", "女子"), ("少年", "青年"),
    ("小小年纪", ""), ("稚嫩", ""),
    ("稚气未脱", ""), ("娇嫩", ""),
]


def _sanitize_ages(text: str) -> str:
    return _AGE_PATTERNS.sub("", text)


def _sanitize_youth(text: str) -> str:
    text = _sanitize_ages(text)
    for old, new in _YOUTH_REPLACEMENTS:
        text = text.replace(old, new)
    return text


def _call_llm_with_sanitize(
    client,
    user_msg: str,
    system_prompt: str,
    schema: dict,
    temperature: float,
    model: str,
    max_retries: int = 3,
) -> str:
    """Call LLM with progressive content-policy sanitisation on retry."""
    from clients.llm_client import ProhibitedContentError

    sanitize_level = 0
    last_err: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            result = client.generate_text(
                prompt=user_msg,
                system_instruction=system_prompt,
                temperature=temperature,
                response_schema=schema,
                model=model,
            )
            text = (result or "").strip()
            if text and len(text) >= 20:
                return text
            raise ValueError(f"LLM 返回过短 ({len(text)} chars)")

        except ProhibitedContentError:
            if sanitize_level == 0:
                print("    ⚠ PROHIBITED_CONTENT — 清洗年龄数字后重试...")
                user_msg = _sanitize_ages(user_msg)
                system_prompt = _sanitize_ages(system_prompt)
                sanitize_level = 1
                continue
            if sanitize_level == 1:
                print("    ⚠ PROHIBITED_CONTENT — 深度清洗少年/少女等词后重试...")
                user_msg = _sanitize_youth(user_msg)
                system_prompt = _sanitize_youth(system_prompt)
                sanitize_level = 2
                continue
            last_err = ProhibitedContentError(
                "Prompt still blocked after full sanitisation"
            )

        except Exception as e:
            last_err = e

        _log.warning("Gemini attempt %d/%d: %s", attempt, max_retries, last_err)
        print(f"    ⚠ Gemini 调用失败 (attempt {attempt}/{max_retries}): {last_err}")
        if attempt < max_retries:
            time.sleep(min(2 ** attempt, 30))

    raise last_err or ValueError("All Gemini attempts failed")


# ═══════════════════════════════════════════════════════════════════════
#  Pydantic schemas for Gemini structured output
# ═══════════════════════════════════════════════════════════════════════

class ReviewIssue(BaseModel):
    scene_numbers: List[int] = Field(description="涉及的分镜编号列表")
    issue_type: str = Field(
        description=(
            "问题类型：scene_jump（场景跳变）、plot_incoherent（剧情不连贯）、"
            "dialogue_unreasonable（对话不合理）、dialogue_mismatch（台词归属错误）、"
            "character_inconsistent（人物行为不一致）、pacing_issue（节奏问题）、"
            "missing_transition（缺少过渡）、"
            "character_missing（场景中缺失应出现的角色）、"
            "character_definition_missing（角色跨多场景出现但缺少顶层定义）、"
            "prop_definition_missing（道具跨多场景出现但缺少顶层定义）、"
            "scene_prop_conflict（场景描述中的物体被重复定义为道具）、"
            "appearance_in_scene（分镜描述中包含了外貌服饰等应属于角色定义的内容）、"
            "lighting_inconsistent（相邻分镜光照不合理突变）、"
            "weak_hook（开场钩子弱）、weak_conflict（冲突不够清晰或不够强）、"
            "no_escalation（缺少升级/反转/揭晓）、weak_climax（高潮/爆点不够强）、"
            "missing_payoff（结尾没有兑现或尾钩）、flat_emotion_curve（情绪曲线过平）、"
            "other（其他）"
        )
    )
    description: str = Field(description="问题的具体描述")
    severity: str = Field(
        description="严重程度：high（严重影响观看）、medium（建议修复）、low（轻微瑕疵）"
    )
    suggestion: str = Field(description="修复建议")


class StoryboardReview(BaseModel):
    has_issues: bool = Field(description="是否存在需要修复的问题")
    issues: List[ReviewIssue] = Field(description="发现的问题列表")
    overall_assessment: str = Field(description="整体评估，50字以内")


class FixedScene(BaseModel):
    scene_id: str = Field(
        description=(
            "分镜编号。修改现有分镜用原编号（如 \"5\"），"
            "在某个分镜后插入新分镜用 \"原编号_序号\"（如 \"5_2\" 表示在5号后插入的第1个新分镜，"
            "\"5_3\" 表示在第5号后插入的第2个新分镜）"
        )
    )
    plot_description: str = Field(description="情节描述")
    visual_description: str = Field(description="纯视觉画面描述")
    characters_in_scene: List[str] = Field(description="出场角色列表")
    props_in_scene: List[str] = Field(
        description="出现在画面中的关键道具名称列表", default_factory=list,
    )
    scene_location: str = Field(description="场景地点", default="")
    dialogue_lines: List[DialogueLine] = Field(
        description="台词列表", default_factory=list,
    )
    duration: str = Field(description="建议时长（如 1秒、2秒、3秒）")
    camera_angle: str = Field(description="镜头角度")
    mood: str = Field(description="情绪氛围")
    lighting: str = Field(
        description=(
            "光线描述，同一场景同一时间段内应与相邻分镜保持一致"
        )
    )


class NewCharacter(BaseModel):
    name: str = Field(description="角色名称")
    description: str = Field(
        description="角色的固定外观描述（年龄、性别、发型、服装等），不要写画面风格"
    )
    personality: str = Field(description="角色的性格特点")
    voice_description: str = Field(
        description="角色的固定声音特征（音色、语速、说话风格）",
        default="",
    )


class NewProp(BaseModel):
    name: str = Field(description="道具名称")
    description: str = Field(
        description="道具的固定外观描述（形状、材质、颜色、特效等），不要写画面风格"
    )


class StoryboardFix(BaseModel):
    new_characters: List[NewCharacter] = Field(
        description="需要新增的角色定义列表（仅当审查发现 character_definition_missing 时才填写，否则为空数组）",
        default_factory=list,
    )
    new_props: List[NewProp] = Field(
        description="需要新增的道具定义列表（仅当审查发现 prop_definition_missing 时才填写，否则为空数组）",
        default_factory=list,
    )
    removed_props: List[str] = Field(
        description=(
            "需要删除的道具名称列表（仅当审查发现 scene_prop_conflict 时才填写，"
            "将与场景描述重复的道具名放入此列表，否则为空数组）"
        ),
        default_factory=list,
    )
    fixed_scenes: List[FixedScene] = Field(description="修改或新增的分镜列表")
    fix_summary: str = Field(description="修复摘要，说明做了哪些改动")


# ═══════════════════════════════════════════════════════════════════════
#  Phase 1: Review
# ═══════════════════════════════════════════════════════════════════════

def _prepare_review_input(storyboard_data: Dict[str, Any]) -> Dict[str, Any]:
    """Strip heavy fields, keep what Gemini needs for review."""
    result: Dict[str, Any] = {
        "video_analysis": storyboard_data.get("video_analysis", {}),
        "story_arc": {
            "hook": storyboard_data.get("hook", ""),
            "core_conflict": storyboard_data.get("core_conflict", ""),
            "stakes": storyboard_data.get("stakes", ""),
            "turning_points": storyboard_data.get("turning_points", []),
            "climax": storyboard_data.get("climax", ""),
            "payoff": storyboard_data.get("payoff", ""),
            "emotional_curve": storyboard_data.get("emotional_curve", ""),
        },
        "narrative": storyboard_data.get("narrative", ""),
        "characters": [
            {
                "name": c["name"],
                "description": c.get("description", ""),
                "personality": c.get("personality", ""),
            }
            for c in storyboard_data.get("characters", [])
        ],
        "locations": [
            {"name": loc["name"], "description": loc.get("description", "")}
            for loc in storyboard_data.get("locations", [])
        ],
        "storyboard": storyboard_data.get("storyboard", []),
    }
    props = storyboard_data.get("props", [])
    if props:
        result["props"] = [
            {"name": p["name"], "description": p.get("description", "")}
            for p in props
        ]
    groups = storyboard_data.get("groups", [])
    if groups:
        result["groups"] = groups
    return result


def review_storyboard(
    storyboard_data: Dict[str, Any],
    client,
    model: str = "gemini-3-flash-preview",
) -> StoryboardReview:
    """Phase 1: Ask Gemini to review storyboard quality."""
    review_input = _prepare_review_input(storyboard_data)
    user_msg = (
        "请审查以下视频分镜剧本，找出其中的问题：\n\n"
        f"```json\n{json.dumps(review_input, ensure_ascii=False, indent=2)}\n```"
    )

    schema = flatten_json_schema(StoryboardReview.model_json_schema())

    print("[StoryReview] Phase 1: Gemini 剧情审查中...")
    result = _call_llm_with_sanitize(
        client, user_msg, _REVIEW_SYSTEM_PROMPT,
        schema=schema, temperature=0.2, model=model,
    )

    review = StoryboardReview.model_validate_json(result)

    if review.has_issues:
        print(f"[StoryReview] 发现 {len(review.issues)} 个问题:")
        for issue in review.issues:
            icon = (
                "🔴" if issue.severity == "high"
                else "🟡" if issue.severity == "medium"
                else "🟢"
            )
            print(
                f"  {icon} Scene {issue.scene_numbers} "
                f"[{issue.issue_type}] {issue.description}"
            )
    else:
        print("[StoryReview] 剧本质量良好，无需修复")

    print(f"[StoryReview] 整体评估: {review.overall_assessment}")
    return review


# ═══════════════════════════════════════════════════════════════════════
#  Phase 2: Fix
# ═══════════════════════════════════════════════════════════════════════

def fix_storyboard(
    storyboard_data: Dict[str, Any],
    review: StoryboardReview,
    client,
    model: str = "gemini-3-flash-preview",
) -> StoryboardFix:
    """Phase 2: Ask Gemini to fix the storyboard based on review issues."""
    review_input = _prepare_review_input(storyboard_data)
    issues_text = json.dumps(
        [i.model_dump() for i in review.issues],
        ensure_ascii=False,
        indent=2,
    )

    user_msg = (
        "以下是视频分镜剧本：\n\n"
        f"```json\n{json.dumps(review_input, ensure_ascii=False, indent=2)}\n```\n\n"
        f"审查发现的问题：\n\n```json\n{issues_text}\n```\n\n"
        "请根据以上问题修复剧本：\n"
        "- 需要修改或新增的分镜放在 fixed_scenes\n"
        "- 缺失的角色定义放在 new_characters\n"
        "- 缺失的道具定义放在 new_props\n"
        "- 与场景描述重复的道具名放在 removed_props"
    )

    schema = flatten_json_schema(StoryboardFix.model_json_schema())

    print("[StoryReview] Phase 2: Gemini 剧本修复中...")
    result = _call_llm_with_sanitize(
        client, user_msg, _FIX_SYSTEM_PROMPT,
        schema=schema, temperature=0.3, model=model,
    )

    fix = StoryboardFix.model_validate_json(result)

    n_replace = sum(1 for s in fix.fixed_scenes if "_" not in str(s.scene_id))
    n_insert = sum(1 for s in fix.fixed_scenes if "_" in str(s.scene_id))
    parts = [f"修改 {n_replace} 个分镜, 新增 {n_insert} 个分镜"]
    if fix.new_characters:
        names = [c.name for c in fix.new_characters]
        parts.append(f"注入 {len(fix.new_characters)} 个角色: {', '.join(names)}")
    if fix.new_props:
        names = [p.name for p in fix.new_props]
        parts.append(f"注入 {len(fix.new_props)} 个道具: {', '.join(names)}")
    if fix.removed_props:
        parts.append(f"删除 {len(fix.removed_props)} 个重复道具: {', '.join(fix.removed_props)}")
    print(f"[StoryReview] 修复结果: {'; '.join(parts)}")
    print(f"[StoryReview] 修复摘要: {fix.fix_summary}")

    return fix


# ═══════════════════════════════════════════════════════════════════════
#  Merge logic
# ═══════════════════════════════════════════════════════════════════════

_SCENE_ID_RE = re.compile(r"^(\d+)(?:_(\d+))?$")


def merge_fixes(
    original_scenes: List[dict],
    fix: StoryboardFix,
) -> List[dict]:
    """Merge fixed scenes into original storyboard.

    - scene_id = "N"       → replace scene N
    - scene_id = "N_2/N_3" → insert after scene N (sorted by suffix)

    After merge, renumber all scenes sequentially starting from 1.
    """
    replacements: Dict[int, dict] = {}
    insertions: Dict[int, List[Tuple[int, dict]]] = {}

    for fs in fix.fixed_scenes:
        m = _SCENE_ID_RE.match(str(fs.scene_id))
        if not m:
            _log.warning("Invalid scene_id format: %s, skipping", fs.scene_id)
            continue

        base_num = int(m.group(1))
        suffix = int(m.group(2)) if m.group(2) else None

        scene_dict = fs.model_dump()
        del scene_dict["scene_id"]

        if suffix is None:
            replacements[base_num] = scene_dict
        else:
            insertions.setdefault(base_num, []).append((suffix, scene_dict))

    merged: List[dict] = []
    for scene in original_scenes:
        sn = scene["scene_number"]

        if sn in replacements:
            merged.append(replacements[sn])
        else:
            merged.append(dict(scene))

        if sn in insertions:
            for _, new_scene in sorted(insertions[sn], key=lambda x: x[0]):
                merged.append(new_scene)

    for i, s in enumerate(merged, 1):
        s["scene_number"] = i

    n_replaced = len(replacements)
    n_inserted = sum(len(v) for v in insertions.values())
    print(
        f"[StoryReview] 合并完成: 替换 {n_replaced} 个, "
        f"插入 {n_inserted} 个, 共 {len(merged)} 个分镜"
    )

    return merged


# ═══════════════════════════════════════════════════════════════════════
#  Back-fill: scan scene text for newly injected characters / props
# ═══════════════════════════════════════════════════════════════════════

def _backfill_scenes(
    scenes: List[dict],
    char_names: set,
    prop_names: set,
) -> int:
    """Scan every scene's text fields; if a new character/prop name appears
    in the text but is missing from characters_in_scene / props_in_scene,
    add it automatically. Returns the number of scenes patched."""
    patched = 0
    for scene in scenes:
        text_blob = " ".join([
            scene.get("plot_description", ""),
            scene.get("visual_description", ""),
            *(dl.get("text", "") for dl in scene.get("dialogue_lines", [])),
            *(dl.get("speaker", "") for dl in scene.get("dialogue_lines", [])),
        ])

        changed = False

        for cname in char_names:
            if cname in text_blob:
                clist = scene.setdefault("characters_in_scene", [])
                if cname not in clist:
                    clist.append(cname)
                    changed = True

        for pname in prop_names:
            if pname in text_blob:
                plist = scene.setdefault("props_in_scene", [])
                if pname not in plist:
                    plist.append(pname)
                    changed = True

        if changed:
            patched += 1

    return patched


# ═══════════════════════════════════════════════════════════════════════
#  Orchestrator — called from base_engine
# ═══════════════════════════════════════════════════════════════════════

def run_story_review(
    storyboard_data: Dict[str, Any],
    client,
    review_dir: str,
    model: str = "gemini-3-flash-preview",
    allow_fix: bool = True,
) -> Dict[str, Any]:
    """Run story review and optionally apply fixes.

    Saves intermediate files to review_dir for debugging.
    Returns the (possibly modified) storyboard_data.
    """
    out = Path(review_dir)
    out.mkdir(parents=True, exist_ok=True)

    _save_json(storyboard_data, out / "01_before_review.json")

    # Phase 1: Review
    try:
        review = review_storyboard(storyboard_data, client, model=model)
    except Exception as e:
        _log.warning("Story review failed, skipping: %s", e)
        print(f"[StoryReview] ⚠ 审查失败，跳过: {e}")
        return storyboard_data

    _save_json(review.model_dump(), out / "02_review_issues.json")

    if not review.has_issues:
        return storyboard_data

    actionable = [i for i in review.issues if i.severity in ("high", "medium")]
    if not actionable:
        print("[StoryReview] 只有 low 级别问题，跳过修复")
        return storyboard_data

    if not allow_fix:
        print("[StoryReview] 仅执行轻量审查，不自动修复")
        storyboard_data.setdefault("_meta", {})["story_review_issues"] = [
            i.model_dump() for i in actionable
        ]
        return storyboard_data

    review_for_fix = StoryboardReview(
        has_issues=True,
        issues=actionable,
        overall_assessment=review.overall_assessment,
    )

    # Phase 2: Fix
    try:
        fix = fix_storyboard(
            storyboard_data, review_for_fix, client, model=model,
        )
    except Exception as e:
        _log.warning("Story fix failed, skipping: %s", e)
        print(f"[StoryReview] ⚠ 修复失败，跳过: {e}")
        return storyboard_data

    _save_json(fix.model_dump(), out / "03_fix_patches.json")

    has_changes = (
        fix.fixed_scenes or fix.new_characters
        or fix.new_props or fix.removed_props
    )
    if not has_changes:
        print("[StoryReview] Gemini 未返回任何修改")
        return storyboard_data

    # Remove conflicting props (scene_prop_conflict)
    if fix.removed_props:
        removed_set = set(fix.removed_props)
        orig_props = storyboard_data.get("props", [])
        storyboard_data["props"] = [
            p for p in orig_props if p["name"] not in removed_set
        ]
        n_removed = len(orig_props) - len(storyboard_data["props"])
        for pname in fix.removed_props:
            print(f"[StoryReview] - 道具「{pname}」(与场景描述重复)")

        for scene in storyboard_data.get("storyboard", []):
            plist = scene.get("props_in_scene", [])
            cleaned = [p for p in plist if p not in removed_set]
            if len(cleaned) != len(plist):
                scene["props_in_scene"] = cleaned

        print(f"[StoryReview] 已从顶层删除 {n_removed} 个重复道具，"
              f"并清理所有分镜的 props_in_scene")

    # Inject new characters
    if fix.new_characters:
        existing_names = {
            c["name"] for c in storyboard_data.get("characters", [])
        }
        for nc in fix.new_characters:
            if nc.name not in existing_names:
                storyboard_data.setdefault("characters", []).append(
                    nc.model_dump()
                )
                print(f"[StoryReview] + 角色「{nc.name}」")

    # Inject new props
    if fix.new_props:
        existing_names = {
            p["name"] for p in storyboard_data.get("props", [])
        }
        for np_ in fix.new_props:
            if np_.name not in existing_names:
                storyboard_data.setdefault("props", []).append(
                    np_.model_dump()
                )
                print(f"[StoryReview] + 道具「{np_.name}」")

    # Merge scenes
    if fix.fixed_scenes:
        merged_scenes = merge_fixes(
            storyboard_data.get("storyboard", []),
            fix,
        )
        storyboard_data["storyboard"] = merged_scenes

    # Back-fill: ensure new characters/props appear in relevant scenes
    new_char_names = {nc.name for nc in fix.new_characters}
    new_prop_names = {np_.name for np_ in fix.new_props}
    if new_char_names or new_prop_names:
        n_patched = _backfill_scenes(
            storyboard_data.get("storyboard", []),
            new_char_names,
            new_prop_names,
        )
        if n_patched:
            print(f"[StoryReview] 补漏: {n_patched} 个分镜的角色/道具列表已更新")

    _save_json(storyboard_data, out / "04_after_review.json")

    return storyboard_data


def _save_json(data, path: Path):
    from utils.io import save_json
    save_json(path, data)
