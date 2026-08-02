"""
Modular scene editing utilities for storyboard scenes.

Provides two core functions:
  - regenerate_seedance_prompt(): re-generate seedance_prompt from narrative
  - refine_scene_with_chat(): refine scene fields via user chat feedback

These are standalone functions (no class instantiation needed) that can be
called from:
  - Dashboard API endpoints (pipeline review, editor)
  - CLI scripts
  - Post-video-generation rework flows
"""

import copy
import json
import logging
import time
from typing import Dict, Any, List, Optional

from prompts.scene_editor import (
    build_seedance_system_prompt,
    build_refine_scene_system_prompt,
)
from tools.storyboard_gen.schemas import (
    flatten_json_schema,
    SingleSceneRewriteOutput,
)
from tools.storyboard_gen.validation import (
    sanitize_continuity_text, sanitize_scene_continuity,
    fix_character_names_in_prompts, ensure_prompt_style_prefix,
)

_log = logging.getLogger("video_agent.scene_editor")


# ═══════════════════════════════════════════════════════════════════════
#  Internal helpers
# ═══════════════════════════════════════════════════════════════════════

def _get_llm_client():
    from clients import get_llm_client
    return get_llm_client(step="scene_edit")


def _call_llm(
    prompt: str,
    system_instruction: str,
    schema: Optional[dict] = None,
    temperature: float = 0.4,
    model: str = "gemini-3-flash-preview",
    max_retries: int = 3,
) -> str:
    """Call LLM with retry logic. Returns raw text response."""
    from tools.storyboard_gen.schemas import flatten_json_schema

    client = _get_llm_client()
    retry_delays = [2, 5, 10]
    last_err = None

    for attempt in range(1, max_retries + 1):
        try:
            kwargs = dict(
                prompt=prompt,
                system_instruction=system_instruction,
                temperature=temperature,
                model=model,
            )
            if schema:
                kwargs["response_schema"] = schema
            result = client.generate_text(**kwargs)
            text = (result or "").strip()
            if text and len(text) >= 10:
                return text
            raise ValueError(f"LLM returned too short ({len(text)} chars)")
        except Exception as e:
            last_err = e
            _log.warning("Gemini attempt %d/%d: %s", attempt, max_retries, e)
            if attempt < max_retries:
                time.sleep(retry_delays[min(attempt - 1, len(retry_delays) - 1)])

    raise RuntimeError(f"Gemini failed after {max_retries} attempts: {last_err}")


def _extract_context(storyboard: Dict[str, Any]) -> dict:
    """Extract characters, locations, props, style from a storyboard dict."""
    return {
        "characters": storyboard.get("characters", []),
        "locations": storyboard.get("locations", []),
        "props": storyboard.get("props", []),
        "style": storyboard.get("video_analysis", {}).get("style", ""),
    }


def _get_neighbor_scenes(storyboard: Dict[str, Any], scene: dict) -> tuple[Optional[dict], Optional[dict]]:
    """Get previous / next scene dict by current scene_number ordering."""
    scenes = storyboard.get("storyboard", []) or []
    if not scenes:
        return None, None

    current_num = scene.get("scene_number")
    idx = None
    for i, item in enumerate(scenes):
        if item.get("scene_number") == current_num:
            idx = i
            break
    if idx is None:
        return None, None

    prev_scene = scenes[idx - 1] if idx > 0 else None
    next_scene = scenes[idx + 1] if idx + 1 < len(scenes) else None
    return prev_scene, next_scene


def _build_neighbor_anchor_payload(scene: Optional[dict]) -> str:
    """Serialize minimal neighbor continuity info for prompts."""
    if not scene:
        return "无"
    raw_anchor = scene.get("continuity_anchor", {}) or {}
    anchor = {
        key: sanitize_continuity_text(value, value)
        if isinstance(value, str) else value
        for key, value in raw_anchor.items()
    }
    payload = {
        "scene_number": scene.get("scene_number"),
        "narrative_summary": scene.get("narrative_summary", ""),
        "transition_strategy": sanitize_continuity_text(scene.get("transition_strategy", "")),
        "continuity_anchor": anchor,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_duration_seconds(dur_str: str) -> float:
    """Parse '10秒' or '10s' to float."""
    import re
    m = re.search(r"(\d+(?:\.\d+)?)", str(dur_str))
    return float(m.group(1)) if m else 10.0


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).lower()


def _tokenize_name(name: str) -> List[str]:
    raw = str(name or "").strip()
    if not raw:
        return []

    tokens = {raw.lower()}
    for ch in ["（", "）", "(", ")", "、", "，", ",", "。", ".", "·", "-", "_", "/", "：", ":", "；", ";", "“", "”", '"', "'", "？", "！", "!", "?"]:
        raw = raw.replace(ch, " ")
    for part in raw.split():
        part = part.strip().lower()
        if len(part) >= 2:
            tokens.add(part)
    return sorted(tokens, key=len, reverse=True)


def _match_entity_in_text(name: str, text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    for token in _tokenize_name(name):
        if token and token in normalized:
            return True
    return False


def _scene_text_blob(scene: dict) -> str:
    dialogue_bits = []
    for line in scene.get("dialogue_lines", []) or []:
        speaker = line.get("speaker", "")
        text = line.get("text", "")
        emotion = line.get("emotion", "")
        dialogue_bits.append(f"{speaker} {text} {emotion}".strip())
    parts = [
        scene.get("narrative_summary", ""),
        scene.get("plot_description", ""),
        scene.get("visual_description", ""),
        scene.get("seedance_prompt", ""),
        scene.get("dialogue", ""),
        " ".join(dialogue_bits),
        scene.get("scene_location", ""),
        " ".join(scene.get("characters_in_scene", []) or []),
        " ".join(scene.get("props_in_scene", []) or []),
    ]
    return "\n".join(str(p or "") for p in parts if p)


def _dedupe_preserve_order(values: List[str]) -> List[str]:
    seen = set()
    result = []
    for value in values or []:
        v = str(value or "").strip()
        if not v or v in seen:
            continue
        seen.add(v)
        result.append(v)
    return result


def _find_referenced_scene_indices(name: str, storyboard: Dict[str, Any], *, field: str) -> List[int]:
    scenes = storyboard.get("storyboard", []) or []
    matches: List[int] = []
    for idx, scene in enumerate(scenes):
        if field == "scene_location":
            existing = str(scene.get("scene_location", "") or "").strip()
            if existing == name or _match_entity_in_text(name, _scene_text_blob(scene)):
                matches.append(idx)
        else:
            existing_list = scene.get(field, []) or []
            if name in existing_list or _match_entity_in_text(name, _scene_text_blob(scene)):
                matches.append(idx)
    return matches


def _sync_storyboard_entities(storyboard: Dict[str, Any], edits: Dict[str, Any]) -> Dict[str, Any]:
    synced = copy.deepcopy(storyboard)
    scenes = synced.get("storyboard", []) or []

    type_map = {
        "character": {
            "collection_key": "characters",
            "scene_field": "characters_in_scene",
            "single_value": False,
        },
        "location": {
            "collection_key": "locations",
            "scene_field": "scene_location",
            "single_value": True,
        },
        "prop": {
            "collection_key": "props",
            "scene_field": "props_in_scene",
            "single_value": False,
        },
    }

    edits_by_type = (edits or {}).get("edits", {}) or {}
    sync_summary = {
        "added": {"character": [], "location": [], "prop": []},
        "deleted": {"character": [], "location": [], "prop": []},
        "updated": {"character": [], "location": [], "prop": []},
    }

    for entity_type, config in type_map.items():
        collection_key = config["collection_key"]
        scene_field = config["scene_field"]
        single_value = config["single_value"]

        collection = synced.setdefault(collection_key, [])
        operations = edits_by_type.get(entity_type, []) or []

        for op in operations:
            action = (op.get("action") or "update").strip().lower()
            name = str(op.get("name") or "").strip()
            previous_name = str(op.get("previous_name") or "").strip()
            scene_indices = sorted({
                int(i) for i in (op.get("scene_indices") or [])
                if isinstance(i, int) or (isinstance(i, str) and i.isdigit())
            })

            target_name = name or previous_name
            if not target_name and action != "add":
                continue

            existing_idx = next((i for i, item in enumerate(collection) if item.get("name") == previous_name or item.get("name") == name), None)

            if action == "delete":
                if existing_idx is not None:
                    collection.pop(existing_idx)
                auto_scene_indices = _find_referenced_scene_indices(target_name, synced, field=scene_field)
                all_indices = sorted(set(scene_indices or auto_scene_indices))
                removed_from = []
                for idx in all_indices:
                    if idx < 0 or idx >= len(scenes):
                        continue
                    scene = scenes[idx]
                    if single_value:
                        current = str(scene.get(scene_field, "") or "").strip()
                        if current == target_name:
                            scene[scene_field] = ""
                            removed_from.append(idx)
                    else:
                        current_list = scene.get(scene_field, []) or []
                        next_list = [item for item in current_list if item != target_name]
                        if len(next_list) != len(current_list):
                            scene[scene_field] = next_list
                            removed_from.append(idx)
                sync_summary["deleted"][entity_type].append({
                    "name": target_name,
                    "scene_indices": removed_from,
                })
                continue

            payload = {
                k: v for k, v in op.items()
                if k not in {"action", "previous_name", "scene_indices"}
            }
            payload["name"] = name

            if existing_idx is not None:
                existing = collection[existing_idx]
                preserved_image = existing.get("image_path", "")
                existing.update(payload)
                if not existing.get("image_path"):
                    existing["image_path"] = preserved_image
                updated_entity = existing
            else:
                payload.setdefault("image_path", "")
                collection.append(payload)
                updated_entity = collection[-1]

            if action == "rename" and previous_name and name and previous_name != name:
                renamed_in = []
                for idx, scene in enumerate(scenes):
                    if single_value:
                        current = str(scene.get(scene_field, "") or "").strip()
                        if current == previous_name:
                            scene[scene_field] = name
                            renamed_in.append(idx)
                    else:
                        current_list = scene.get(scene_field, []) or []
                        replaced = [name if item == previous_name else item for item in current_list]
                        replaced = _dedupe_preserve_order(replaced)
                        if replaced != current_list:
                            scene[scene_field] = replaced
                            renamed_in.append(idx)
                sync_summary["updated"][entity_type].append({
                    "action": "rename",
                    "name": name,
                    "previous_name": previous_name,
                    "scene_indices": renamed_in,
                })
                continue

            if action == "add":
                auto_scene_indices = _find_referenced_scene_indices(name, synced, field=scene_field)
                target_scene_indices = sorted(set(scene_indices or auto_scene_indices))
                injected_into = []
                for idx in target_scene_indices:
                    if idx < 0 or idx >= len(scenes):
                        continue
                    scene = scenes[idx]
                    if single_value:
                        current = str(scene.get(scene_field, "") or "").strip()
                        if not current:
                            scene[scene_field] = name
                            injected_into.append(idx)
                    else:
                        current_list = scene.get(scene_field, []) or []
                        if name not in current_list:
                            scene[scene_field] = _dedupe_preserve_order(current_list + [name])
                            injected_into.append(idx)
                sync_summary["added"][entity_type].append({
                    "name": name,
                    "scene_indices": injected_into,
                    "auto_detected": scene_indices != target_scene_indices,
                })
                continue

            sync_summary["updated"][entity_type].append({
                "action": action,
                "name": updated_entity.get("name", name),
                "previous_name": previous_name,
                "scene_indices": scene_indices,
            })

    return {
        "storyboard": synced,
        "sync_summary": sync_summary,
    }


def _build_seedance_system_prompt(style: str, duration_seconds: float) -> str:
    """Build the system prompt for seedance prompt generation.
    Mirrors base_engine._generate_direct_prompt() prompt template."""
    return build_seedance_system_prompt(style, duration_seconds)


def _build_seedance_user_msg(
    narrative: str,
    duration_seconds: float,
    characters: List[dict],
    locations: List[dict],
    props: List[dict],
    scene: dict,
) -> str:
    """Build the user message for seedance prompt generation."""
    seg_chars = scene.get("characters_in_scene", [])
    seg_locs = [scene.get("scene_location", "")] if scene.get("scene_location") else []
    seg_props = scene.get("props_in_scene", [])

    char_defs = json.dumps(
        [{"name": c["name"], "description": c.get("description", "")[:100]}
         for c in characters if c["name"] in seg_chars],
        ensure_ascii=False, indent=2,
    )
    loc_defs = json.dumps(
        [{"name": loc["name"], "description": loc.get("description", "")[:100]}
         for loc in locations if loc["name"] in seg_locs],
        ensure_ascii=False, indent=2,
    )
    prop_defs = json.dumps(
        [{"name": p["name"], "description": p.get("description", "")[:100]}
         for p in props if p["name"] in seg_props],
        ensure_ascii=False, indent=2,
    )

    return (
        f"角色定义：\n{char_defs}\n\n"
        f"场景定义：\n{loc_defs}\n\n"
        f"道具定义：\n{prop_defs}\n\n"
        f"本段时长：{duration_seconds:.0f} 秒\n\n"
        f"本段叙事：\n{narrative}\n\n"
        "请为该段落撰写一个直接可用的视频生成 prompt。"
    )


# ═══════════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════════

def sync_storyboard_entities(
    storyboard: Dict[str, Any],
    edits: Dict[str, Any],
) -> Dict[str, Any]:
    """Sync storyboard entity cards and scene references after editor mutations."""
    return _sync_storyboard_entities(storyboard, edits)


def regenerate_seedance_prompt(
    narrative_summary: str,
    scene: dict,
    storyboard_context: Dict[str, Any],
    model: str = "gemini-3-flash-preview",
) -> Dict[str, Any]:
    """Re-generate seedance_prompt for a single scene based on its narrative.

    This version also considers previous/next scene anchors so local edits
    still preserve cross-segment continuity.
    """
    ctx = _extract_context(storyboard_context)
    dur = _parse_duration_seconds(scene.get("duration", "10秒"))
    prev_scene, next_scene = _get_neighbor_scenes(storyboard_context, scene)

    system_prompt = (
        _build_seedance_system_prompt(ctx["style"], dur)
        + "\n你还需要兼顾与前后段的连续性。"
        + "\n- 可以参考上一段的 continuity_anchor 和 transition_strategy 来设计本段开场，但最终文字必须让当前段独立成立"
        + "\n- 当前段开头不要重复上一段末帧的同一表情、同一姿态、同一特写构图；应写成下一拍的新动作、新反应或新主体入画"
        + "\n- 可以参考下一段的 continuity_anchor 来设计本段结尾留口，但不要写成‘下一段如何承接’"
        + "\n- 严禁输出‘承接上一段’‘镜头承接上一段图片1’‘上一镜头/前一幕’等依赖前序画面的表述"
        + "\n- 返回结构化结果：新的 seedance_prompt、transition_strategy、continuity_anchor"
    )
    user_msg = (
        _build_seedance_user_msg(
            narrative=narrative_summary,
            duration_seconds=dur,
            characters=ctx["characters"],
            locations=ctx["locations"],
            props=ctx["props"],
            scene=scene,
        )
        + "\n\n"
        + f"上一段信息：\n{_build_neighbor_anchor_payload(prev_scene)}\n\n"
        + f"下一段信息：\n{_build_neighbor_anchor_payload(next_scene)}\n\n"
        + "请在保留本段核心剧情的前提下，输出最利于前后衔接的重写结果。"
    )

    result = _call_llm(
        prompt=user_msg,
        system_instruction=system_prompt,
        schema=flatten_json_schema(SingleSceneRewriteOutput.model_json_schema()),
        temperature=0.4,
        model=model,
    )

    parsed = SingleSceneRewriteOutput.model_validate_json(result)
    payload = parsed.model_dump()
    if payload.get("seedance_prompt"):
        payload["seedance_prompt"] = sanitize_continuity_text(payload["seedance_prompt"], payload["seedance_prompt"])
    if payload.get("transition_strategy"):
        payload["transition_strategy"] = sanitize_continuity_text(payload["transition_strategy"], payload["transition_strategy"])
    sanitize_scene_continuity(payload)
    char_names = {c["name"] for c in ctx["characters"]}
    fix_character_names_in_prompts(payload, char_names)
    payload["seedance_prompt"] = ensure_prompt_style_prefix(
        payload.get("seedance_prompt", ""),
        ctx["style"],
        fallback=payload.get("seedance_prompt", ""),
    )
    return payload


def refine_scene_with_chat(
    user_feedback: str,
    scene: dict,
    storyboard_context: Dict[str, Any],
    field: str = "seedance",
    chat_history: Optional[List[dict]] = None,
    model: str = "gemini-3-flash-preview",
) -> Dict[str, Any]:
    """Refine a scene's seedance_prompt based on user feedback.

    narrative_summary is read-only and always returned unchanged.
    The rewrite also considers neighbor anchors so edited scenes continue to
    stitch cleanly with adjacent segments.
    """
    ctx = _extract_context(storyboard_context)
    current_narrative = scene.get("narrative_summary", "")
    current_seedance = scene.get("seedance_prompt", "")
    dur = _parse_duration_seconds(scene.get("duration", "10秒"))
    prev_scene, next_scene = _get_neighbor_scenes(storyboard_context, scene)

    char_info = ", ".join(c["name"] for c in ctx["characters"])
    loc_info = ", ".join(loc["name"] for loc in ctx["locations"])

    system_prompt = build_refine_scene_system_prompt(
        style=ctx["style"], dur=dur, char_info=char_info, loc_info=loc_info
    )
    history_text = ""
    if chat_history:
        history_text = "之前的对话：\n" + "\n".join(
            f"{'用户' if m.get('role') == 'user' else 'AI'}: {m.get('content', '')}"
            for m in (chat_history or [])[-4:]
        ) + "\n\n"

    user_msg = (
        f"{history_text}"
        f"叙事内容（只读，不要修改）：\n{current_narrative}\n\n"
        f"当前 seedance_prompt：\n{current_seedance}\n\n"
        f"上一段信息：\n{_build_neighbor_anchor_payload(prev_scene)}\n\n"
        f"下一段信息：\n{_build_neighbor_anchor_payload(next_scene)}\n\n"
        f"用户修改意见：{user_feedback}\n\n"
        "请返回修改后的结构化结果。"
    )

    result = _call_llm(
        prompt=user_msg,
        system_instruction=system_prompt,
        schema=flatten_json_schema(SingleSceneRewriteOutput.model_json_schema()),
        temperature=0.4,
        model=model,
    )

    parsed = SingleSceneRewriteOutput.model_validate_json(result)
    payload = parsed.model_dump()
    if payload.get("seedance_prompt"):
        payload["seedance_prompt"] = sanitize_continuity_text(payload["seedance_prompt"], payload["seedance_prompt"])
    if payload.get("transition_strategy"):
        payload["transition_strategy"] = sanitize_continuity_text(payload["transition_strategy"], payload["transition_strategy"])
    sanitize_scene_continuity(payload)
    char_names = {c["name"] for c in ctx["characters"]}
    fix_character_names_in_prompts(payload, char_names)
    payload["seedance_prompt"] = ensure_prompt_style_prefix(
        payload.get("seedance_prompt", ""),
        ctx["style"],
        fallback=payload.get("seedance_prompt", ""),
    )
    payload["narrative_summary"] = current_narrative
    return payload
