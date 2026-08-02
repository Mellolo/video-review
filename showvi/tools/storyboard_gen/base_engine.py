"""
Base storyboard engine — shared post-processing, validation, I/O logic,
and the **narrative → storyboard** conversion step.

Pipeline:
    Source (video / novel / prompt)
       │
       ▼
    核心剧情 (narrative)  ← 所有来源都先经过这一步
       │
       ▼
    分镜 (storyboard)     ← 同时参考 source_context + narrative

Subclasses implement ``generate_screenplay()`` for their specific input type.
The shared ``screenplay_to_storyboard()`` converts prose narrative into
structured storyboard scenes, optionally referencing the original source.
"""

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable

from .validation import (
    validate_and_fix, dialogue_lines_to_string, ValidationIssue,
    sanitize_continuity_text, sanitize_scene_continuity,
    fix_character_names_in_prompts, ensure_prompt_style_prefix,
    prompt_has_body,
)
from .schemas import (
    flatten_json_schema,
    NarrativeSegmentation, ContinuityRewriteOutput,
    BatchFluentSeedanceOutput,
    NarrativeStateOutput, StateAssignmentOutput,
    SegmentGroupingOutput,
)
from prompts.storyboard_gen import (
    build_segment_narrative_prompt,
    build_batch_fluent_prompt_system,
    build_fluent_continuity_director_prompt,
    build_state_tracker_prompt,
    build_state_validation_prompt,
    build_segment_grouping_prompt,
)

_log = logging.getLogger("video_agent.storyboard_gen")


class GenerationStoppedError(RuntimeError):
    """Raised when a creation job is stopped by the user."""


class BaseStoryboardEngine(ABC):
    """Abstract base for all storyboard generation engines."""

    SCREENPLAY_PARAGRAPH_TRANSITION_REQUIREMENT = (
        "按自然段组织叙事，遇到叙事边界时换段；遇到时空/场景切换，或某一段以人物说话收束时，"
        "必须在该段结尾明确写出至少 1 秒的过渡余量（如人物说完后的停顿、反应镜头、环境空镜、"
        "呼吸/脚步/风声延续、镜头延续），避免对白、动作和场景戛然而止。"
    )
    SEGMENT_END_BUFFER_REQUIREMENT = (
        "当段尾即将发生时空/场景切换，或最后一个强节拍是人物说话时，必须把至少 1 秒的"
        "反应或环境余韵算在当前段里，不能在最后一个字、最后一个动作或最后一个画面点上硬切。"
    )
    DRAMA_HIGH_PRIORITY_KEYWORDS = (
        "高潮", "爆点", "反转", "逆转", "决战", "对峙", "反杀", "揭晓", "真相",
        "终于", "突然", "猛地", "轰然", "崩塌", "爆炸", "降临", "觉醒", "决绝",
        "一剑", "致命", "质问", "逼近", "威胁", "撕开", "现身", "抹除", "主宰",
    )
    DRAMA_PAUSE_KEYWORDS = (
        "停顿", "沉默", "凝视", "愣住", "屏住呼吸", "余韵", "空镜", "静止", "缓缓",
        "半晌", "片刻", "呼吸", "风声", "回望", "目光", "无言", "怔住",
    )

    @staticmethod
    def _screenplay_drama_requirements(target_duration: float) -> str:
        hook_window = "前 3-5 秒" if target_duration and target_duration <= 30 else "前 3-8 秒"
        beat_hint = "3-4 个强节拍" if target_duration and target_duration <= 30 else "4-6 个强节拍"
        return (
            f"- {hook_window} 必须抛出 hook：异常、羞辱、危机、诱惑、谜团或反常信息，立刻让观众想继续看\n"
            "- 必须明确主角目标、阻碍者/阻碍机制，以及失败代价，不能只写发生了什么\n"
            "- 中段至少出现一次局势升级、真相揭晓或反转，不能平铺直叙\n"
            "- 高潮必须是全片最强动作、最强情绪或最强信息爆点，不能一笔带过\n"
            "- 结尾必须有 payoff：回收前文钩子、兑现反击结果、给出代价或留下更强尾钩\n"
            f"- 整体节奏尽量压缩成 {beat_hint}，不要平均分配事件，不要流水账\n"
        )

    @staticmethod
    def _screenplay_story_arc_output_rules() -> str:
        return (
            "- **hook**：一句话说明故事开场 3-8 秒内最抓人的钩子\n"
            "- **core_conflict**：一句话说明主角目标与核心阻碍\n"
            "- **stakes**：一句话说明失败代价，必须具体\n"
            "- **turning_points**：按时间顺序列出 2-5 个关键升级/反转/揭晓\n"
            "- **climax**：一句话概括最终高潮/爆点\n"
            "- **payoff**：一句话概括高潮后的兑现/尾钩\n"
            "- **emotional_curve**：一句话概括全片情绪曲线\n"
            "- **narrative**：把以上戏剧骨架真正写进连贯叙述里，而不是只在字段里概括\n"
        )

    @staticmethod
    def _screenplay_fewshot_examples() -> str:
        return (
            "【节奏模板示例 A：30 秒逆袭向】\n"
            "hook：婚宴上，赘婿被逼下跪签离婚书。\n"
            "core_conflict：主角必须在被彻底踩死前证明自己不是废物，但全场都站在岳家一边。\n"
            "stakes：若失败，他会失去尊严、婚姻和唯一翻身机会。\n"
            "turning_points：\n"
            "- 他拿出的证据被当众撕毁，羞辱升级。\n"
            "- 所有人都以为他完了时，真正的大人物进门认主。\n"
            "climax：主角当众反杀，把逼他下跪的人按回地上。\n"
            "payoff：先前嘲笑他的人集体失声，妻子第一次正眼看他。\n"
            "emotional_curve：压抑→受辱→绝望→反杀→扬眉吐气。\n"
            "narrative 写法特征：开场立刻受辱，中段连续加压，高潮一击翻盘，结尾给身份/关系回收。\n\n"
            "【节奏模板示例 B：60 秒惊悚反转向】\n"
            "hook：新弟子半夜听见墙里有人喊自己的名字。\n"
            "core_conflict：主角想逃出宗门，却发现所有师长都在把他养成祭品。\n"
            "stakes：若失败，他会失去身体、记忆和最后的人性。\n"
            "turning_points：\n"
            "- 他以为唯一可信的人，其实是引他入局的帮凶。\n"
            "- 他逃到出口才发现出口本身就是献祭阵眼。\n"
            "climax：主角自断一臂毁掉阵眼，在血雾中强行杀出一条路。\n"
            "payoff：三年后看似平静归隐，但体内残留的邪物再次苏醒。\n"
            "emotional_curve：好奇→不安→惊恐→决绝→余悸。\n"
            "narrative 写法特征：先立异样，再层层揭开真相，高潮必须是代价巨大的逃生或反击，结尾留下更长的阴影。\n"
        )

    @classmethod
    def _story_beat_text(cls, payload: Dict[str, Any]) -> str:
        text_parts = [
            payload.get("segment_goal", ""),
            payload.get("segment_conflict", ""),
            payload.get("segment_turn", ""),
            payload.get("segment_end_beat", ""),
            payload.get("story_function", ""),
            payload.get("narrative_summary", ""),
            payload.get("plot_description", ""),
            payload.get("visual_description", ""),
            payload.get("seedance_prompt", ""),
            payload.get("mood", ""),
        ]
        text_parts.extend(
            dl.get("text", "")
            for dl in payload.get("dialogue_lines", []) or []
            if isinstance(dl, dict)
        )
        return " ".join(part for part in text_parts if part)

    @classmethod
    def _story_duration_priority(cls, payload: Dict[str, Any]) -> float:
        text = cls._story_beat_text(payload)
        priority = 1.0
        if any(keyword in text for keyword in cls.DRAMA_HIGH_PRIORITY_KEYWORDS):
            priority += 0.8
        if any(keyword in text for keyword in cls.DRAMA_PAUSE_KEYWORDS):
            priority += 0.5
        if payload.get("segment_turn") or payload.get("segment_end_beat"):
            priority += 0.5
        return round(priority, 2)

    def __init__(
        self,
        llm_model: str = "gemini-3-flash-preview",
        stop_checker: Optional[Callable[[], bool]] = None,
        progress_callback: Optional[Callable[[str, Optional[Dict[str, Any]]], None]] = None,
    ):
        self.llm_model = llm_model
        self.stop_checker = stop_checker
        self.progress_callback = progress_callback

    @staticmethod
    def _ensure_debug_log(output_path: str) -> None:
        """Ensure _log has a FileHandler pointing to *output_path*'s directory.

        Writes to ``storyboard_gen.log`` next to *output_path*.
        Always updates the handler to the current run's directory so that
        multiple runs in the same process each write to their own log file.
        """
        log_dir = Path(output_path).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "storyboard_gen.log"

        # Remove any existing FileHandlers so we always point to the current run
        for h in list(_log.handlers):
            if isinstance(h, logging.FileHandler):
                h.close()
                _log.removeHandler(h)

        _log.setLevel(logging.DEBUG)
        _log.propagate = False
        handler = logging.FileHandler(str(log_file), encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
        _log.addHandler(handler)
        _log.debug("storyboard_gen debug log initialized → %s", log_file)

    @abstractmethod
    def generate(self, **kwargs) -> Dict[str, Any]:
        """Run the full pipeline (screenplay → storyboard) and return a storyboard dict."""
        ...

    @abstractmethod
    def generate_screenplay(self, **kwargs) -> Dict[str, Any]:
        """Generate a screenplay (no technical shot details) and return it."""
        ...

    # ═════════════════════════════════════════════════════════════════
    #  Screenplay metadata sync — keep characters/locations/props
    #  consistent with an edited narrative.
    # ═════════════════════════════════════════════════════════════════

    def sync_screenplay_metadata(
        self,
        screenplay_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Re-extract characters, locations, and props from the narrative
        so they stay consistent after the user edits the narrative text.

        Returns the *mutated* screenplay_data dict (same reference).
        If the LLM call fails, the original metadata is kept unchanged.
        """
        from clients import get_llm_client
        from .schemas import ScreenplayMetadataSync, flatten_json_schema

        narrative = (screenplay_data.get("narrative") or "").strip()
        if not narrative:
            return screenplay_data

        old_characters = screenplay_data.get("characters", [])
        old_locations = screenplay_data.get("locations", [])
        old_props = screenplay_data.get("props", [])

        # Build a concise representation of the existing definitions
        def _fmt_chars(chars):
            return "\n".join(
                f"- {c.get('name', '?')}: {c.get('description', '')[:120]}"
                for c in chars
            ) or "(无)"

        def _fmt_locs(locs):
            return "\n".join(
                f"- {l.get('name', '?')}: {l.get('description', '')[:120]}"
                for l in locs
            ) or "(无)"

        def _fmt_props(props):
            return "\n".join(
                f"- {p.get('name', '?')}: {p.get('description', '')[:120]}"
                for p in props
            ) or "(无)"

        style = screenplay_data.get("video_analysis", {}).get("style", "")

        style_rule = f"8. 画面风格：{style}\n" if style else ""
        system_prompt = (
            "你是一个专业的剧本元数据同步助手。用户编辑了剧本的叙述文本（narrative），"
            "你需要根据最新的叙述内容，重新输出完整的角色（characters）、场景（locations）和道具（props）定义。\n\n"
            "规则：\n"
            "1. 仔细阅读最新的叙述文本，提取所有出现的角色、场景和道具\n"
            "2. 如果叙述中出现了新的角色/场景/道具（原定义中没有），为其创建新的定义\n"
            "3. 如果原有定义中的某个角色/场景/道具在最新叙述中完全不再出现，则移除它\n"
            "4. 角色描述必须是固定外观（禁止情绪、动作、剧情变化）；"
            "禁止在 description 中描述后期/变身后/受伤后/换装后等任何非初始状态的外观，所有角色只定义最初登场时的样子；"
            "同一角色有明显不同外观形态（如变身前后、换装后）必须作为两个独立角色分别定义\n"
            "5. 场景描述必须是固定视觉特征；场景只要初始场景，不要中间态和结尾的场景；不要把动态变化的道具和人物定义在环境中\n"
            "6. 道具只保留横跨10秒以上不同段落反复出现的重要道具\n"
            + style_rule
        )

        user_prompt = (
            f"## 最新叙述文本\n{narrative}\n\n"
            f"## 原有角色定义\n{_fmt_chars(old_characters)}\n\n"
            f"## 原有场景定义\n{_fmt_locs(old_locations)}\n\n"
            f"## 原有道具定义\n{_fmt_props(old_props)}\n\n"
            "请根据最新叙述文本，输出更新后的完整 characters、locations 和 props。"
        )

        try:
            client = get_llm_client(step="metadata_sync")
            result = self._call_llm_with_retry(
                client, user_prompt, system_prompt,
                schema=flatten_json_schema(
                    ScreenplayMetadataSync.model_json_schema(),
                ),
                temperature=0.1,
                max_retries=2,
                step_name="metadata_sync",
            )
            parsed = ScreenplayMetadataSync.model_validate_json(result)
            new_characters = [c.model_dump() for c in parsed.characters]
            new_locations = [l.model_dump() for l in parsed.locations]
            new_props = [p.model_dump() for p in parsed.props]

            # Log changes for debugging
            old_char_names = {c.get("name") for c in old_characters}
            new_char_names = {c.get("name") for c in new_characters}
            old_loc_names = {l.get("name") for l in old_locations}
            new_loc_names = {l.get("name") for l in new_locations}

            added_chars = new_char_names - old_char_names
            removed_chars = old_char_names - new_char_names
            added_locs = new_loc_names - old_loc_names
            removed_locs = old_loc_names - new_loc_names

            if added_chars or removed_chars or added_locs or removed_locs:
                changes = []
                if added_chars:
                    changes.append(f"新增角色: {', '.join(added_chars)}")
                if removed_chars:
                    changes.append(f"移除角色: {', '.join(removed_chars)}")
                if added_locs:
                    changes.append(f"新增场景: {', '.join(added_locs)}")
                if removed_locs:
                    changes.append(f"移除场景: {', '.join(removed_locs)}")
                print(f"[metadata_sync] 角色/场景定义已同步: {'; '.join(changes)}")
            else:
                print("[metadata_sync] 角色/场景定义无变化")

            screenplay_data["characters"] = new_characters
            screenplay_data["locations"] = new_locations
            screenplay_data["props"] = new_props

        except Exception as e:
            # If sync fails, keep original metadata — don't block the pipeline
            print(f"[metadata_sync] 同步失败，保留原有定义: {e}")

        return screenplay_data

    # ═════════════════════════════════════════════════════════════════
    #  Screenplay → Storyboard conversion (SHARED)
    # ═════════════════════════════════════════════════════════════════

    def _ensure_not_stopped(self):
        if self.stop_checker and self.stop_checker():
            raise GenerationStoppedError("用户已停止生成")

    def _report_progress(self, phase: str, data: Optional[Dict[str, Any]] = None):
        if not self.progress_callback:
            return
        try:
            self.progress_callback(phase, data or {})
        except Exception as e:
            _log.warning("Progress callback failed for %s: %s", phase, e)

    def screenplay_to_storyboard(
        self,
        screenplay_data: Dict[str, Any],
        output_path: str,
        source_context: str = "",
        character_ids: Optional[Dict[str, str]] = None,
        source_label: str = "storyboard_gen",
        style_reference_image: str = "",
    ) -> Dict[str, Any]:
        """Convert a screenplay (prose narrative) to a full storyboard.

        Uses the **direct-prompt** pipeline:
        narrative → LLM segmentation → parallel LLM → seedance prompts.
        """
        self._ensure_not_stopped()
        return self._screenplay_to_direct_storyboard(
            screenplay_data=screenplay_data,
            output_path=output_path,
            source_context=source_context,
            character_ids=character_ids,
            source_label=source_label,
            style_reference_image=style_reference_image,
        )

    # ── Direct-prompt segment mode ─────────────────────────────────

    def _screenplay_to_direct_storyboard(
        self,
        screenplay_data: Dict[str, Any],
        output_path: str,
        source_context: str = "",
        character_ids: Optional[Dict[str, str]] = None,
        source_label: str = "storyboard_gen",
        style_reference_image: str = "",
    ) -> Dict[str, Any]:
        """New segment mode: narrative → LLM segmentation → parallel LLM
        → direct seedance prompts.  No story review."""
        from clients import get_llm_client

        # Ensure storyboard_gen logger has a file handler for debug output
        self._ensure_debug_log(output_path)

        style = screenplay_data.get("video_analysis", {}).get("style", "")
        characters = screenplay_data.get("characters", [])
        locations = screenplay_data.get("locations", [])
        props = screenplay_data.get("props", [])
        narrative = screenplay_data.get("narrative", "")

        if not narrative:
            raise ValueError("Screenplay has no narrative text")

        print(f"[{source_label}] 段落直出模式 (优先12-15s，尽量接近15s) — "
              f"{len(narrative)} 字叙述")

        self._ensure_not_stopped()
        client = get_llm_client(step="storyboard_gen")
        target_total = self._extract_target_duration_seconds(screenplay_data)

        # ── Step 1: LLM splits narrative into target-aware segments ─
        self._report_progress("storyboard_step", {
            "step_index": 1,
            "total_steps": 6,
            "step_label": "剧本拆分",
            "status": "running",
        })
        segments = self._segment_narrative(
            client,
            narrative,
            characters,
            locations,
            props,
            source_label,
            target_total=target_total,
        )
        if target_total and target_total > 0 and segments:
            min_segment_seconds = min(
                4.0,
                max(1.0, round((target_total / len(segments)) * 0.5, 1)),
            )
            segment_values = [max(0.1, float(seg.get("duration_seconds") or 0.0)) for seg in segments]
            segment_minimums = [
                self._segment_duration_floor_seconds(seg, min_segment_seconds)
                for seg in segments
            ]
            normalized = self._normalize_duration_values(
                segment_values,
                target_total,
                minimums=segment_minimums,
                maximums=[15.0] * len(segments),
                priorities=[self._story_duration_priority(seg) for seg in segments],
            )
            for seg, seconds in zip(segments, normalized):
                seg["duration_seconds"] = seconds
        print(f"[{source_label}] Step 1 完成: {len(segments)} 个段落")
        for seg in segments:
            print(f"  段落 {seg['segment_id']}: "
                  f"{seg['duration_seconds']:.0f}s — "
                  f"{seg['narrative'][:40]}...")
        self._report_progress("storyboard_step", {
            "step_index": 1,
            "total_steps": 6,
            "step_label": "剧本拆分",
            "status": "done",
        })

        # ── Step 2: segment dependency grouping (先于 Step 3，供延续性检测使用) ──
        self._ensure_not_stopped()
        dependency_groups = None
        self._report_progress("storyboard_step", {
            "step_index": 2,
            "total_steps": 6,
            "step_label": "剧本分组",
            "status": "running",
        })
        try:
            dependency_groups = self._group_segments(
                client, segments, source_label,
            )
            self._report_progress("storyboard_step", {
                "step_index": 2,
                "total_steps": 6,
                "step_label": "剧本分组",
                "status": "done",
            })
        except Exception as e:
            _log.warning("Step 2 failed (non-fatal): %s", e)
            print(f"[{source_label}] Step 2 分组失败 (非致命错误): {e}")

        # ── Step 3: narrative state tracking & transition gaps ───
        self._ensure_not_stopped()
        self._report_progress("storyboard_step", {
            "step_index": 3,
            "total_steps": 6,
            "step_label": "缺失实体分析",
            "status": "running",
        })
        try:
            self._track_narrative_states(
                client, segments, screenplay_data, source_label,
                groups=dependency_groups,
            )
            # Re-read characters/locations/props (may have been mutated)
            characters = screenplay_data.get("characters", [])
            locations = screenplay_data.get("locations", [])
            props = screenplay_data.get("props", [])
            # NOTE: 衍生实体和缺失场景的生图不在这里做。
            # 它们的 image_path 留空，由后续 agent._prepare_charsheets() 统一生图。
            self._report_progress("storyboard_step", {
                "step_index": 3,
                "total_steps": 6,
                "step_label": "缺失实体分析",
                "status": "done",
            })
        except Exception as e:
            _log.warning("Step 3 failed (non-fatal): %s", e)
            print(f"[{source_label}] Step 3 跳过 (非致命错误): {e}")

        # ── Step 4: prune low-frequency entities ────────────────
        # Remove characters/locations/props that appear in <=1 segment,
        # unless they are referenced by _text_state_overlays spanning
        # multiple segments (to avoid breaking continuity state tracking).
        self._report_progress("storyboard_step", {
            "step_index": 4,
            "total_steps": 6,
            "step_label": "冗余实体清理",
            "status": "running",
        })
        try:
            self._prune_rare_entities(segments, screenplay_data, source_label)
            characters = screenplay_data.get("characters", [])
            locations = screenplay_data.get("locations", [])
            props = screenplay_data.get("props", [])
            self._report_progress("storyboard_step", {
                "step_index": 4,
                "total_steps": 6,
                "step_label": "冗余实体清理",
                "status": "done",
            })
        except Exception as e:
            _log.warning("Step 4 prune failed (non-fatal): %s", e)
            print(f"[{source_label}] Step 4 清理跳过 (非致命错误): {e}")

        # ── Step 5: generate seedance prompts ────────────────────
        char_names = [c["name"] for c in characters]
        loc_names = [loc["name"] for loc in locations]
        prop_names = [p["name"] for p in props]

        print(f"[{source_label}] Step 5: 批量生成 {len(segments)} 个 seedance prompt（fluent 连贯叙述模式）...")
        self._report_progress("storyboard_step", {
            "step_index": 5,
            "total_steps": 6,
            "step_label": "提示词生成",
            "status": "running",
            "total_segments": len(segments),
        })
        results = self._generate_fluent_prompts_batch(
            client, segments, characters, locations, props, style,
        )
        # Verify all segments got results
        missing = [s["segment_id"] for s in segments if s["segment_id"] not in results]
        if missing:
            raise RuntimeError(f"Step 5 batch 模式缺少段落 {missing}")
        # Check for empty prompts (LLM returned valid JSON but no content)
        empty_prompt_ids = [
            sid for sid, r in results.items()
            if not prompt_has_body(r.get("seedance_prompt", ""))
        ]
        if empty_prompt_ids:
            raise RuntimeError(
                f"Step 5 batch 模式段落 {empty_prompt_ids} 的 seedance_prompt 为空，"
                "可能是 structured output schema 不兼容"
            )
        print(f"[{source_label}] Step 5 完成: batch 模式成功生成 {len(results)} 个段落")
        for sid in sorted(results):
            plen = len(results[sid].get("seedance_prompt", ""))
            print(f"  段落 {sid}: seedance_prompt {plen} 字")
        self._report_progress("storyboard_step", {
            "step_index": 5,
            "total_steps": 6,
            "step_label": "提示词生成",
            "status": "done",
        })

        # ── Assemble ordered prompt scenes ─────────────────────────
        ordered_prompt_scenes = []
        for seg in segments:
            sid = seg["segment_id"]
            r = results[sid]
            resolved_duration = f"{self._format_duration_seconds(seg['duration_seconds'])}秒"
            if not (target_total and target_total > 0):
                resolved_duration = r.get("duration", resolved_duration)
            scene_entry = {
                "segment_id": sid,
                "seedance_prompt": r["seedance_prompt"],
                "duration": resolved_duration,
                "characters_in_scene": r.get("characters_in_scene", []),
                "scene_location": r.get("scene_location", "") or (seg.get("locations_involved") or [""])[0],
                "props_in_scene": r.get("props_in_scene", []),
                "narrative_summary": seg["narrative"],
            }
            # Carry over batch-mode extras (transition_strategy, continuity_anchor)
            if r.get("transition_strategy"):
                scene_entry["transition_strategy"] = r["transition_strategy"]
            if r.get("continuity_anchor"):
                scene_entry["continuity_anchor"] = r["continuity_anchor"]
            ordered_prompt_scenes.append(scene_entry)

        # ── Step 6: global continuity polish ───────────────────────
        # Always run fluent continuity to ensure smooth transitions between segments
        run_step_c = True
        if run_step_c:
            self._ensure_not_stopped()
            self._report_progress("storyboard_step", {
                "step_index": 6,
                "total_steps": 6,
                "step_label": "连续性增强",
                "status": "running",
                "total_segments": len(ordered_prompt_scenes),
            })
            continuity_result = self._rewrite_fluent_prompts_for_continuity(
                client=client,
                prompt_scenes=ordered_prompt_scenes,
                screenplay_data=screenplay_data,
                style=style,
                source_label=source_label,
            )
            if continuity_result:
                continuity_map = {
                    item["segment_id"]: item for item in continuity_result.get("segments", [])
                }
                for scene_data in ordered_prompt_scenes:
                    rewritten = continuity_map.get(scene_data["segment_id"])
                    if not rewritten:
                        continue
                    new_prompt = sanitize_continuity_text(
                        (rewritten.get("seedance_prompt") or "").strip(),
                        fallback=scene_data["seedance_prompt"],
                    )
                    _log.debug("[Step 6 apply] segment %d: len=%d, preview=%.120s",
                               scene_data["segment_id"], len(new_prompt), new_prompt[:120])
                    if new_prompt:
                        scene_data["seedance_prompt"] = new_prompt
                    if rewritten.get("transition_strategy"):
                        scene_data["transition_strategy"] = sanitize_continuity_text(
                            rewritten["transition_strategy"],
                            fallback=scene_data.get("transition_strategy", ""),
                        )
                    if rewritten.get("continuity_anchor"):
                        scene_data["continuity_anchor"] = rewritten["continuity_anchor"]
                    sanitize_scene_continuity(scene_data)
            self._report_progress("storyboard_step", {
                "step_index": 6,
                "total_steps": 6,
                "step_label": "连续性增强",
                "status": "done",
            })
        else:
            print(f"[{source_label}] Step 6 跳过: batch 模式已内置连续性处理")

        for scene_data in ordered_prompt_scenes:
            sanitize_scene_continuity(scene_data)

        # ── Fix short character names in prompt text ──────────────
        char_name_set = {c["name"] for c in characters}
        for scene_data in ordered_prompt_scenes:
            fix_character_names_in_prompts(scene_data, char_name_set)
            scene_data["seedance_prompt"] = ensure_prompt_style_prefix(
                scene_data.get("seedance_prompt", ""),
                style,
                fallback=scene_data.get("seedance_prompt", ""),
            )
            if not prompt_has_body(scene_data["seedance_prompt"]):
                sid = scene_data["segment_id"]
                raise RuntimeError(
                    f"段落 {sid} 的 seedance_prompt 在所有处理后仍为空"
                )

        # ── Build storyboard scenes ─────────────────────────────
        all_scenes: List[dict] = []

        for scene_data in ordered_prompt_scenes:
            self._ensure_not_stopped()
            sid = scene_data["segment_id"]
            dur = scene_data["duration"]

            scene = {
                "scene_number": sid,
                "seedance_prompt": scene_data["seedance_prompt"],
                "duration": dur,
                "characters_in_scene": scene_data.get("characters_in_scene", []),
                "scene_location": scene_data.get("scene_location", ""),
                "props_in_scene": scene_data.get("props_in_scene", []),
                "narrative_summary": scene_data["narrative_summary"],
            }
            if scene_data.get("transition_strategy"):
                scene["transition_strategy"] = scene_data["transition_strategy"]
            if scene_data.get("continuity_anchor"):
                scene["continuity_anchor"] = scene_data["continuity_anchor"]
            all_scenes.append(scene)

        # ── Assemble & save ───────────────────────────────────────
        storyboard_data: Dict[str, Any] = {
            "video_analysis": screenplay_data.get("video_analysis", {}),
            "characters": characters,
            "locations": locations,
            "props": props,
            "hook": screenplay_data.get("hook", ""),
            "core_conflict": screenplay_data.get("core_conflict", ""),
            "stakes": screenplay_data.get("stakes", ""),
            "turning_points": screenplay_data.get("turning_points", []),
            "climax": screenplay_data.get("climax", ""),
            "payoff": screenplay_data.get("payoff", ""),
            "emotional_curve": screenplay_data.get("emotional_curve", ""),
            "narrative": narrative,
            "storyboard": all_scenes,
        }

        ref_img = style_reference_image or screenplay_data.get("style_reference_image", "")
        if ref_img:
            storyboard_data["style_reference_image"] = ref_img
        if "title" in screenplay_data:
            storyboard_data["title"] = screenplay_data["title"]

        self.inject_defaults(storyboard_data, character_ids)
        actual_dur = round(
            sum(self._parse_duration_seconds(s.get("duration", "0")) for s in all_scenes),
            1,
        )

        meta = dict(storyboard_data.get("_meta", {}))
        meta.update({
            "source": source_label,
            "total_scenes": len(all_scenes),
            "total_characters": len(characters),
            "total_locations": len(locations),
            "estimated_duration_seconds": round(actual_dur, 1),
            "scene_granularity": "segment_direct",
        })
        if target_total and target_total > 0:
            meta["target_duration_seconds"] = round(target_total, 1)

        # ── Store Step 2 grouping result ────────────────────────
        if dependency_groups:
            meta["dependency_groups"] = dependency_groups
        storyboard_data["_meta"] = meta

        self.save_json(storyboard_data, output_path)
        print(f"[{source_label}] Generated {len(all_scenes)} direct-prompt segments, "
              f"~{actual_dur:.1f}s total")
        print(f"[{source_label}] Saved: {output_path}")
        return storyboard_data

    # ── Step 3: narrative state tracking & transition gap detection ──

    def _track_narrative_states(
        self,
        client,
        segments: List[dict],
        screenplay_data: Dict[str, Any],
        source_label: str,
        groups: Optional[List[dict]] = None,
    ) -> None:
        """Step 3 — 两轮 LLM 识别 + 验证缺失场景和外观变化。

        修改 screenplay_data 和 segments（in-place）：
        - 补充缺失的 location 定义
        - 注册衍生实体（角色/场景/道具的状态变体）
        - 更新 segment 的 characters_involved / locations_involved / props_involved
        失败时静默跳过，不阻断主流程。

        Args:
            groups: Step 2 的分组结果。用于判断状态变化是组内还是跨组：
                    组内变化强制 requires_sheet=False（仅文本注入），
                    跨组变化强制 requires_sheet=True（生成衍生实体设定图）。
        """
        characters = screenplay_data.get("characters", [])
        locations = screenplay_data.get("locations", [])
        props = screenplay_data.get("props", [])
        style = screenplay_data.get("video_analysis", {}).get("style", "")

        char_defs = json.dumps(
            [{"name": c["name"], "description": c.get("description", "")}
             for c in characters], ensure_ascii=False, indent=2,
        )
        loc_defs = json.dumps(
            [{"name": loc["name"], "description": loc.get("description", "")}
             for loc in locations], ensure_ascii=False, indent=2,
        )
        prop_defs = json.dumps(
            [{"name": p["name"], "description": p.get("description", "")}
             for p in props], ensure_ascii=False, indent=2,
        )

        # Build segment summary for LLM
        seg_summary = json.dumps([
            {"segment_id": s["segment_id"], "narrative": s["narrative"]}
            for s in segments
        ], ensure_ascii=False, indent=2)

        valid_seg_ids = {s["segment_id"] for s in segments}
        max_seg_id = max(valid_seg_ids) if valid_seg_ids else 0

        # ── Pass 1: LLM 识别 ─────────────────────────────────────
        print(f"[{source_label}] Step 3 Pass 1: 识别缺失场景 + 外观变化...")

        system_prompt_1 = build_state_tracker_prompt(
            char_defs, loc_defs, prop_defs, style,
        )
        user_msg_1 = f"请分析以下叙事段落：\n\n{seg_summary}"

        result_1 = self._call_llm_with_retry(
            client, user_msg_1, system_prompt_1,
            schema=flatten_json_schema(
                NarrativeStateOutput.model_json_schema(),
            ),
            temperature=0.1,
            max_retries=2,
            step_name="step_3_state_tracker_pass1",
        )
        pass1 = NarrativeStateOutput.model_validate_json(result_1)
        _log.debug("[Step 3 Pass 1] missing_locations=%d, state_changes=%d",
                   len(pass1.missing_locations), len(pass1.state_changes))
        for ml in pass1.missing_locations:
            _log.debug("  missing_location: %s (segments: %s)", ml.name, ml.mentioned_in_segments)
        for sc in pass1.state_changes:
            _log.debug("  state_change: %s [%s] %s (seg %d-%d, sheet=%s)",
                       sc.original_name, sc.entity_type, sc.state_label,
                       sc.first_segment_id, sc.last_segment_id, sc.requires_sheet)

        # ── Code pre-filter (before Pass 2) ───────────────────────
        char_name_set = {c["name"] for c in characters}
        loc_name_set = {loc["name"] for loc in locations}
        prop_name_set = {p["name"] for p in props}
        all_name_sets = {
            "character": char_name_set,
            "location": loc_name_set,
            "prop": prop_name_set,
        }

        filtered_changes = []
        involved_key_for_type = {
            "character": "characters_involved",
            "location": "locations_involved",
            "prop": "props_involved",
        }
        for sc in pass1.state_changes:
            name_set = all_name_sets.get(sc.entity_type)
            if name_set is None:
                print(f"  [pre-filter] 丢弃: entity_type={sc.entity_type!r} 无效")
                continue
            if sc.original_name not in name_set:
                print(f"  [pre-filter] 丢弃: {sc.original_name!r} 不在 {sc.entity_type} 定义中")
                continue
            if sc.first_segment_id not in valid_seg_ids:
                print(f"  [pre-filter] 丢弃: first_segment_id={sc.first_segment_id} 无效")
                continue
            if sc.last_segment_id != -1 and sc.last_segment_id not in valid_seg_ids:
                print(f"  [pre-filter] 丢弃: last_segment_id={sc.last_segment_id} 无效")
                continue
            # Check: entity must appear in at least one segment AFTER first_segment_id
            inv_key = involved_key_for_type[sc.entity_type]
            appears_later = any(
                sc.original_name in seg.get(inv_key, [])
                for seg in segments
                if seg["segment_id"] > sc.first_segment_id
            )
            if not appears_later:
                print(f"  [pre-filter] 丢弃: {sc.original_name!r} 在 segment {sc.first_segment_id} 之后不再出现")
                continue
            # Check: skip if the changed form is already covered by an
            # independently defined entity whose name contains original_name
            # AND that entity appears in the affected segment range.
            # e.g. "剑仙林曦" covers "林曦" 的变身, so no derived entity needed.
            last_id = sc.last_segment_id if sc.last_segment_id != -1 else max_seg_id
            variant_names = [
                other_name for other_name in name_set
                if other_name != sc.original_name and sc.original_name in other_name
            ]
            if variant_names:
                # Check if any variant is actually used in the affected segments
                variant_used = any(
                    vn in seg.get(inv_key, [])
                    for seg in segments
                    if sc.first_segment_id <= seg["segment_id"] <= last_id
                    for vn in variant_names
                )
                if variant_used:
                    print(f"  [pre-filter] 丢弃: {sc.original_name!r} 的变化形态已有独立定义 "
                          f"{variant_names} 覆盖")
                    continue
            filtered_changes.append(sc)

        filtered_locations = []
        sorted_seg_ids = sorted(valid_seg_ids)
        adjacent_pairs = set()
        for i in range(len(sorted_seg_ids) - 1):
            adjacent_pairs.add((sorted_seg_ids[i], sorted_seg_ids[i + 1]))

        for ml in pass1.missing_locations:
            if ml.name in loc_name_set:
                print(f"  [pre-filter] 丢弃: 场景 {ml.name!r} 已在定义中")
                continue
            valid_segs = sorted(sid for sid in ml.mentioned_in_segments if sid in valid_seg_ids)
            if not valid_segs:
                print(f"  [pre-filter] 丢弃: 场景 {ml.name!r} 无有效 segment_id")
                continue
            # Must appear in at least two adjacent segments
            has_adjacent = any(
                (valid_segs[i], valid_segs[i + 1]) in adjacent_pairs
                for i in range(len(valid_segs) - 1)
            )
            if not has_adjacent:
                print(f"  [pre-filter] 丢弃: 场景 {ml.name!r} 未横跨相邻段落 (segments: {valid_segs})")
                continue
            ml.mentioned_in_segments = valid_segs
            filtered_locations.append(ml)

        if not filtered_changes and not filtered_locations:
            print(f"[{source_label}] Step 3: 无缺失场景或外观变化")
            return

        print(f"  Pass 1 结果: {len(filtered_locations)} 个缺失场景, "
              f"{len(filtered_changes)} 个外观变化 (预过滤后)")

        # ── Pass 2: LLM 确认过滤 ─────────────────────────────────
        print(f"[{source_label}] Step 3 Pass 2: 确认过滤...")

        pass1_summary = json.dumps({
            "missing_locations": [
                {"index": i, "name": ml.name, "description": ml.description,
                 "mentioned_in_segments": ml.mentioned_in_segments}
                for i, ml in enumerate(filtered_locations)
            ],
            "state_changes": [
                {"index": i, "original_name": sc.original_name,
                 "entity_type": sc.entity_type, "state_label": sc.state_label,
                 "change_description": sc.change_description,
                 "first_segment_id": sc.first_segment_id,
                 "last_segment_id": sc.last_segment_id,
                 "requires_sheet": sc.requires_sheet}
                for i, sc in enumerate(filtered_changes)
            ],
        }, ensure_ascii=False, indent=2)

        system_prompt_2 = build_state_validation_prompt(
            char_defs, loc_defs, prop_defs,
        )
        user_msg_2 = (
            f"## 叙事段落\n{seg_summary}\n\n"
            f"## 第一轮分析结果\n{pass1_summary}\n\n"
            "请逐条审核以上结果。"
        )

        result_2 = self._call_llm_with_retry(
            client, user_msg_2, system_prompt_2,
            schema=flatten_json_schema(
                StateAssignmentOutput.model_json_schema(),
            ),
            temperature=0.1,
            max_retries=2,
            step_name="step_3_state_validation_pass2",
        )
        pass2 = StateAssignmentOutput.model_validate_json(result_2)
        _log.debug("[Step 3 Pass 2] location_validations=%d, change_assignments=%d",
                   len(pass2.location_validations), len(pass2.change_assignments))
        for v in pass2.location_validations:
            _log.debug("  loc_validation[%d]: confirmed=%s", v.index, v.confirmed)
        for v in pass2.change_assignments:
            _log.debug("  change_assignment[%d]: confirmed=%s", v.index, v.confirmed)

        # ── Apply validated results ───────────────────────────────
        # 1. Confirmed missing locations
        confirmed_locs = []
        for v in pass2.location_validations:
            if 0 <= v.index < len(filtered_locations) and v.confirmed:
                confirmed_locs.append(filtered_locations[v.index])

        for ml in confirmed_locs:
            new_loc = {"name": ml.name, "description": ml.description, "image_path": ""}
            screenplay_data.setdefault("locations", []).append(new_loc)
            for seg in segments:
                if seg["segment_id"] in ml.mentioned_in_segments:
                    involved = seg.setdefault("locations_involved", [])
                    if ml.name not in involved:
                        involved.append(ml.name)
            print(f"  ✓ 补充场景: {ml.name} (segments: {ml.mentioned_in_segments})")

        # 2. Confirmed state changes → derived entities
        confirmed_changes = []
        for v in pass2.change_assignments:
            if 0 <= v.index < len(filtered_changes) and v.confirmed:
                confirmed_changes.append(filtered_changes[v.index])

        # Build lookup for original entities
        entity_lookup: Dict[str, Dict[str, dict]] = {
            "character": {c["name"]: c for c in characters},
            "location": {loc["name"]: loc for loc in locations},
            "prop": {p["name"]: p for p in props},
        }
        list_key_map = {
            "character": "characters",
            "location": "locations",
            "prop": "props",
        }
        involved_key_map = {
            "character": "characters_involved",
            "location": "locations_involved",
            "prop": "props_involved",
        }

        sheet_count = 0
        text_count = 0

        # Build segment → group mapping for cross-group detection
        seg_to_group: Dict[int, int] = {}
        if groups:
            for g in groups:
                for sid in g["segment_ids"]:
                    seg_to_group[sid] = g["group_id"]

        for sc in confirmed_changes:
            original = entity_lookup.get(sc.entity_type, {}).get(sc.original_name)
            if not original:
                continue

            last_id = sc.last_segment_id if sc.last_segment_id != -1 else max_seg_id

            # Override requires_sheet based on group info:
            # cross-group → always True (need sheet), intra-group → always False (text only)
            if seg_to_group:
                affected_groups = {
                    seg_to_group[sid]
                    for sid in range(sc.first_segment_id, last_id + 1)
                    if sid in seg_to_group
                }
                is_cross_group = len(affected_groups) > 1
                sc.requires_sheet = is_cross_group

            if sc.requires_sheet:
                # ── Path A: 需要生成设定图 → 创建衍生实体 ──
                derived_name = f"{sc.original_name}[{sc.state_label}]"
                derived = dict(original)
                derived["name"] = derived_name
                orig_desc = original.get('description', '').rstrip('。')
                derived["description"] = (
                    f"{orig_desc}。"
                    f"变化：{sc.change_description}"
                )
                derived["image_path"] = ""
                derived["_derived_from"] = sc.original_name
                derived["_change_description"] = sc.change_description

                list_key = list_key_map[sc.entity_type]
                involved_key = involved_key_map[sc.entity_type]
                screenplay_data.setdefault(list_key, []).append(derived)

                # Update segment involved lists:
                # - first_segment_id: keep original + add derived (变化发生在段落中途)
                # - first_segment_id+1 to last_id: replace original with derived
                for seg in segments:
                    sid = seg["segment_id"]
                    if sid == sc.first_segment_id:
                        involved = seg.get(involved_key, [])
                        # 保留原始名，同时加入衍生名（段落中途发生变化，两者都需要出现）
                        if derived_name not in involved:
                            involved.append(derived_name)
                    elif sc.first_segment_id < sid <= last_id:
                        involved = seg.get(involved_key, [])
                        if sc.original_name in involved:
                            idx = involved.index(sc.original_name)
                            involved[idx] = derived_name
                        elif derived_name not in involved:
                            involved.append(derived_name)

                sheet_count += 1
                print(f"  ✓ 衍生实体(设定图): {derived_name} "
                      f"(segments {sc.first_segment_id}-"
                      f"{'end' if sc.last_segment_id == -1 else sc.last_segment_id})")
            else:
                # ── Path B: 仅文本描述 → 写入 _text_state_overlays ──
                overlay = {
                    "entity_name": sc.original_name,
                    "entity_type": sc.entity_type,
                    "state_label": sc.state_label,
                    "change_description": sc.change_description,
                }
                for seg in segments:
                    sid = seg["segment_id"]
                    if sc.first_segment_id <= sid <= last_id:
                        seg.setdefault("_text_state_overlays", []).append(overlay)

                text_count += 1
                print(f"  ✓ 文本状态: {sc.original_name}[{sc.state_label}] "
                      f"(segments {sc.first_segment_id}-"
                      f"{'end' if sc.last_segment_id == -1 else sc.last_segment_id})")

        total = len(confirmed_locs) + sheet_count + text_count
        print(f"[{source_label}] Step 3 完成: "
              f"{len(confirmed_locs)} 个场景补充, "
              f"{sheet_count} 个衍生实体(设定图), "
              f"{text_count} 个文本状态注入")
        return

    # ── Step 4: prune rare entities ─────────────────────────────

    @staticmethod
    def _prune_rare_entities(
        segments: List[dict],
        screenplay_data: Dict[str, Any],
        source_label: str,
    ) -> None:
        """Remove characters/locations/props that appear in <= 1 segment.

        Also removes the entity name from the corresponding segment's
        ``*_involved`` list.  Entities referenced by ``_text_state_overlays``
        that span multiple segments are protected from pruning.

        Modifies *segments* and *screenplay_data* in-place.
        """
        entity_types = [
            ("characters", "characters_involved"),
            ("locations", "locations_involved"),
            ("props", "props_involved"),
        ]

        # 1. Collect names protected by multi-segment _text_state_overlays.
        #    An overlay entity that appears in overlays across >1 segment
        #    must NOT be pruned even if its *_involved count is <=1.
        overlay_seg_count: Dict[str, set] = {}  # entity_name → set of segment_ids
        for seg in segments:
            for ov in seg.get("_text_state_overlays", []):
                ov_name = ov.get("entity_name", "")
                if ov_name:
                    overlay_seg_count.setdefault(ov_name, set()).add(seg["segment_id"])
        protected_names = {
            name for name, sids in overlay_seg_count.items() if len(sids) > 1
        }

        # 收集有衍生实体的原始实体名（用于后续按 count 条件保护）
        has_derived: set = set()
        for list_key, _ in entity_types:
            for defn in screenplay_data.get(list_key, []):
                derived_from = defn.get("_derived_from", "")
                if derived_from:
                    has_derived.add(derived_from)

        total_pruned = 0

        for list_key, involved_key in entity_types:
            definitions = screenplay_data.get(list_key, [])
            if not definitions:
                continue

            # Count how many segments each entity appears in
            name_seg_count: Dict[str, int] = {}
            for defn in definitions:
                name_seg_count[defn["name"]] = 0
            for seg in segments:
                for name in seg.get(involved_key, []):
                    if name in name_seg_count:
                        name_seg_count[name] += 1

            # Decide which to prune
            to_prune: set = set()
            for name, count in name_seg_count.items():
                if name in protected_names:
                    continue
                # 有衍生实体的原始实体：只要自身还出现在 >=1 个 segment 就保留
                # （count==0 说明剧本已完全不需要原图，仍可删除）
                if count >= 1 and name in has_derived:
                    continue
                if count <= 1:
                    to_prune.add(name)

            if not to_prune:
                continue

            # Remove from screenplay_data definitions
            screenplay_data[list_key] = [
                d for d in definitions if d["name"] not in to_prune
            ]

            # Remove from each segment's *_involved list
            for seg in segments:
                involved = seg.get(involved_key)
                if involved:
                    seg[involved_key] = [n for n in involved if n not in to_prune]

            # Also clean up single-segment _text_state_overlays for pruned entities
            for seg in segments:
                overlays = seg.get("_text_state_overlays")
                if overlays:
                    seg["_text_state_overlays"] = [
                        ov for ov in overlays
                        if ov.get("entity_name", "") not in to_prune
                    ]

            type_label = {"characters": "角色", "locations": "场景", "props": "道具"}[list_key]
            for name in sorted(to_prune):
                count = name_seg_count[name]
                print(f"  [prune] 移除{type_label}: {name!r} (仅出现在 {count} 个段落)")
            total_pruned += len(to_prune)

        if total_pruned:
            print(f"[{source_label}] Step 4 完成: 清理了 {total_pruned} 个低频实体")
        else:
            print(f"[{source_label}] Step 4: 无需清理")

    # ── Step 2: segment dependency grouping ─────────────────────

    def _group_segments(
        self,
        client,
        segments: List[dict],
        source_label: str,
    ) -> List[dict]:
        """Step 2 — LLM 判断相邻 segment 的空间连续性依赖，输出分组。

        返回 list[dict]，每个 dict 含 group_id, segment_ids, reason。
        失败时回退为每段独立一组。
        """
        valid_seg_ids = sorted(s["segment_id"] for s in segments)

        # Build segment summary for LLM
        seg_summary = json.dumps([
            {
                "segment_id": s["segment_id"],
                "narrative": s["narrative"],
                "characters_involved": s.get("characters_involved", []),
                "locations_involved": s.get("locations_involved", []),
            }
            for s in segments
        ], ensure_ascii=False, indent=2)

        system_prompt = build_segment_grouping_prompt()
        user_msg = f"请分析以下 {len(segments)} 个叙事段落的空间连续性依赖：\n\n{seg_summary}"

        def _parse_grouping_result(raw: str) -> SegmentGroupingOutput:
            """Parse LLM result, handling bare-array responses."""
            stripped = raw.strip()
            if stripped.startswith("["):
                raw = json.dumps({"groups": json.loads(stripped)})
            return SegmentGroupingOutput.model_validate_json(raw)

        output = None
        last_exc: Optional[Exception] = None
        for attempt in range(1, 4):  # up to 3 attempts total
            try:
                result = self._call_llm_with_retry(
                    client, user_msg, system_prompt,
                    schema=flatten_json_schema(
                        SegmentGroupingOutput.model_json_schema(),
                    ),
                    temperature=0.1,
                    max_retries=2,
                    step_name="step_2_segment_grouping",
                )
                output = _parse_grouping_result(result)
                _log.debug("[Step 2] groups=%d: %s",
                           len(output.groups),
                           [(g.group_id, g.segment_ids) for g in output.groups])
                break
            except Exception as e:
                last_exc = e
                _log.warning("Step 2 attempt %d/3 failed: %s", attempt, e)
                print(f"[{source_label}] Step 2 第 {attempt}/3 次失败 ({e})"
                      + ("，重试..." if attempt < 3 else "，回退为全并行"))

        if output is None:
            return self._default_groups(valid_seg_ids)

        # ── Code validation ───────────────────────────────────────
        seen_ids: set[int] = set()
        validated_groups: List[dict] = []

        for g in output.groups:
            # Filter to valid segment_ids only
            filtered = [sid for sid in g.segment_ids if sid in valid_seg_ids]
            if not filtered:
                continue

            # Check for duplicates
            dups = seen_ids & set(filtered)
            if dups:
                print(f"  [A.6 validate] group {g.group_id}: "
                      f"重复 segment_ids {dups}，跳过重复")
                filtered = [sid for sid in filtered if sid not in seen_ids]
                if not filtered:
                    continue

            # Check contiguity: segment_ids must be consecutive
            is_contiguous = all(
                filtered[i] + 1 == filtered[i + 1]
                for i in range(len(filtered) - 1)
            )
            if not is_contiguous:
                print(f"  [A.6 validate] group {g.group_id}: "
                      f"segment_ids {filtered} 不连续，拆为独立组")
                for sid in filtered:
                    if sid not in seen_ids:
                        validated_groups.append({
                            "group_id": len(validated_groups) + 1,
                            "segment_ids": [sid],
                            "reason": "拆分：原组不连续",
                        })
                        seen_ids.add(sid)
                continue

            seen_ids.update(filtered)
            validated_groups.append({
                "group_id": len(validated_groups) + 1,
                "segment_ids": filtered,
                "reason": g.reason,
            })

        # Add any missing segments as independent groups
        missing = set(valid_seg_ids) - seen_ids
        for sid in sorted(missing):
            validated_groups.append({
                "group_id": len(validated_groups) + 1,
                "segment_ids": [sid],
                "reason": "独立段落",
            })

        # Re-number group_ids
        for i, g in enumerate(validated_groups, 1):
            g["group_id"] = i

        # Log
        serial_groups = [g for g in validated_groups if len(g["segment_ids"]) > 1]
        print(f"[{source_label}] Step 2 完成: "
              f"{len(validated_groups)} 组 "
              f"({len(serial_groups)} 组串行, "
              f"{len(validated_groups) - len(serial_groups)} 组独立)")
        for g in validated_groups:
            if len(g["segment_ids"]) > 1:
                print(f"  组 {g['group_id']}: segments {g['segment_ids']} — {g['reason']}")

        return validated_groups

    @staticmethod
    def _default_groups(seg_ids: List[int]) -> List[dict]:
        """每段独立一组（全并行回退）。"""
        return [
            {"group_id": i, "segment_ids": [sid], "reason": "独立段落"}
            for i, sid in enumerate(sorted(seg_ids), 1)
        ]

    # ── Direct-prompt helpers ─────────────────────────────────────

    def _segment_narrative(
        self,
        client,
        narrative: str,
        characters: List[dict],
        locations: List[dict],
        props: List[dict],
        source_label: str,
        target_total: Optional[float] = None,
    ) -> List[dict]:
        """Use LLM to split narrative into target-aware segments."""
        char_names = json.dumps([c["name"] for c in characters], ensure_ascii=False)
        loc_names = json.dumps([loc["name"] for loc in locations], ensure_ascii=False)
        prop_names = json.dumps([p["name"] for p in props], ensure_ascii=False)

        target_hint = ""
        if target_total and target_total > 0:
            approx_segments = max(1, round(target_total / 14.0))
            target_hint = (
                f"11. 所有段落的 duration_seconds 总和必须尽量接近 {target_total:.1f} 秒，允许误差不超过 2 秒\n"
                f"12. 优先按总时长倒推段落数量，建议约 {approx_segments} 段，使单段尽量落在 12-15 秒并优先接近 15 秒，但以叙事完整为准\n"
                "13. 如果目标总时长较短，可以输出短于 8 秒的段落，但要保证剧情完整、不过度切碎\n\n"
            )

        system_prompt = build_segment_narrative_prompt(
            self.SEGMENT_END_BUFFER_REQUIREMENT,
            target_hint,
            char_names,
            loc_names,
            prop_names,
        )
        if target_total and target_total > 0:
            user_msg = (
                f"请将以下故事叙述拆分为多个总时长约 {target_total:.1f} 秒的段落，"
                "并为每段填写合理的 duration_seconds。优先让单段接近 15 秒，以保证一次生成更连贯；仅在叙事需要时再缩短：\n\n"
                f"{narrative}"
            )
        else:
            user_msg = (
                "请将以下故事叙述拆分为多个 8-15 秒、但尽量接近 15 秒的段落，"
                "优先保证单段内容完整和一次生成连贯性：\n\n"
                f"{narrative}"
            )

        print(f"[{source_label}] Step 1: LLM 拆分 narrative 为段落...")
        result = self._call_llm_with_retry(
            client, user_msg, system_prompt,
            schema=flatten_json_schema(
                NarrativeSegmentation.model_json_schema(),
            ),
            temperature=0.2,
            max_retries=3,
            step_name="step_1_segment_narrative",
        )

        parsed = NarrativeSegmentation.model_validate_json(result)
        _log.debug("[Step 1] parsed %d segments: %s",
                   len(parsed.segments),
                   [(s.segment_id, f"{s.duration_seconds}s", s.narrative[:60]) for s in parsed.segments])
        return [s.model_dump() for s in parsed.segments]

    def _generate_fluent_prompts_batch(
        self,
        client,
        segments: List[dict],
        characters: List[dict],
        locations: List[dict],
        props: List[dict],
        style: str,
        segment_ids: Optional[List[int]] = None,
    ) -> Dict[int, dict]:
        """Generate fluent (连贯叙述) seedance prompts for multiple segments in one LLM call.

        Unlike _generate_direct_prompts_batch which produces structured shots (镜头1/镜头2),
        this method generates a single coherent narrative prompt per segment.

        Args:
            segments: All narrative segments (full list, for context).
            characters / locations / props: Screenplay definitions.
            style: Visual style string.
            segment_ids: Which segment IDs to generate prompts for.
                         If None, generates for all segments.

        Returns:
            Dict mapping segment_id → scene dict with seedance_prompt as a fluent string.
        """
        if segment_ids is not None:
            target_ids = set(segment_ids)
            target_segments = [s for s in segments if s["segment_id"] in target_ids]
        else:
            target_segments = segments

        if not target_segments:
            return {}

        char_defs = json.dumps(
            [{"name": c["name"], "description": c.get("description", "")[:100]}
             for c in characters],
            ensure_ascii=False, indent=2,
        )
        loc_defs = json.dumps(
            [{"name": loc["name"], "description": loc.get("description", "")[:100]}
             for loc in locations],
            ensure_ascii=False, indent=2,
        )
        prop_defs = json.dumps(
            [{"name": p["name"], "description": p.get("description", "")[:100]}
             for p in props],
            ensure_ascii=False, indent=2,
        )

        seg_payloads = []
        for seg in target_segments:
            dur = seg["duration_seconds"]
            max_words = int(dur * 6)
            is_last = seg["segment_id"] == segments[-1]["segment_id"]
            seg_payloads.append({
                "segment_id": seg["segment_id"],
                "duration_seconds": dur,
                "max_dialogue_words": max_words,
                "is_last_segment": is_last,
                "segment_goal": seg.get("segment_goal", ""),
                "segment_conflict": seg.get("segment_conflict", ""),
                "segment_turn": seg.get("segment_turn", ""),
                "segment_end_beat": seg.get("segment_end_beat", ""),
                "narrative": seg["narrative"],
                "characters_involved": seg.get("characters_involved", []),
                "locations_involved": seg.get("locations_involved", []),
                "props_involved": seg.get("props_involved", []),
                **({"text_state_overlays": seg["_text_state_overlays"]}
                   if seg.get("_text_state_overlays") else {}),
            })

        segments_json = json.dumps(seg_payloads, ensure_ascii=False, indent=2)

        system_prompt = build_batch_fluent_prompt_system(style)

        user_msg = (
            f"角色定义：\n{char_defs}\n\n"
            f"场景定义：\n{loc_defs}\n\n"
            f"道具定义：\n{prop_defs}\n\n"
            f"以下是需要生成连贯 seedance prompt 的段落（共 {len(target_segments)} 段）：\n\n"
            f"{segments_json}\n\n"
            "请按 segment_id 顺序，为每个段落生成一段连贯流畅的 seedance prompt，"
            "同时输出 transition_strategy 和 continuity_anchor。"
        )

        result = self._call_llm_with_retry(
            client, user_msg, system_prompt,
            schema=flatten_json_schema(
                BatchFluentSeedanceOutput.model_json_schema(),
            ),
            temperature=0.4,
            max_retries=3,
            step_name="step_5_batch_fluent_prompts",
        )

        parsed = BatchFluentSeedanceOutput.model_validate_json(result)

        results: Dict[int, dict] = {}
        for batch_scene in parsed.segments:
            scene_dict = batch_scene.model_dump()
            seg_id = scene_dict.pop("segment_id")
            # Fluent mode: seedance_prompt is already a string, just prepend style
            prompt = scene_dict["seedance_prompt"]
            _log.debug("[Step 5] segment %d raw seedance_prompt (%d chars):\n%s",
                       seg_id, len(prompt), prompt)
            if not prompt.startswith(f"风格：{style}"):
                prompt = f"风格：{style}\n{prompt}"
            scene_dict["seedance_prompt"] = prompt
            results[seg_id] = scene_dict

        return results

    def _rewrite_fluent_prompts_for_continuity(
        self,
        client,
        prompt_scenes: List[dict],
        screenplay_data: Dict[str, Any],
        style: str,
        source_label: str,
    ) -> Optional[dict]:
        """Rewrite fluent (连贯叙述) segment prompts as a sequence to reduce jumps.

        Same logic as _rewrite_prompts_for_continuity but uses the fluent
        continuity prompt that preserves natural narrative format instead of
        requiring structured shot format (镜头N：Xs).
        """
        if len(prompt_scenes) <= 1:
            return None

        characters = screenplay_data.get("characters", [])
        locations = screenplay_data.get("locations", [])
        props = screenplay_data.get("props", [])

        char_defs = json.dumps(
            [{"name": c["name"], "description": c.get("description", "")[:100]}
             for c in characters],
            ensure_ascii=False, indent=2,
        )
        loc_defs = json.dumps(
            [{"name": loc["name"], "description": loc.get("description", "")[:100]}
             for loc in locations],
            ensure_ascii=False, indent=2,
        )
        prop_defs = json.dumps(
            [{"name": p["name"], "description": p.get("description", "")[:100]}
             for p in props],
            ensure_ascii=False, indent=2,
        )
        sequence_payload = json.dumps(prompt_scenes, ensure_ascii=False, indent=2)

        system_prompt = build_fluent_continuity_director_prompt(style)

        user_msg = (
            f"角色定义：\n{char_defs}\n\n"
            f"场景定义：\n{loc_defs}\n\n"
            f"道具定义：\n{prop_defs}\n\n"
            "以下是按时间顺序排列的段落信息。每段包含 narrative_summary、原始 seedance_prompt（连贯叙述格式）、"
            "时长和涉及元素。请你先为每一段提炼 continuity_anchor，再从全局统一连续性的角度逐段重写，"
            "保持连贯自然语言叙述格式：\n\n"
            f"{sequence_payload}\n\n"
            "请返回所有段落的重写结果。"
        )

        print(f"[{source_label}] Step 6 (fluent): 分析 continuity anchors 并全局重写连贯 seedance prompts，增强段落衔接...")
        try:
            result = self._call_llm_with_retry(
                client, user_msg, system_prompt,
                schema=flatten_json_schema(
                    ContinuityRewriteOutput.model_json_schema(),
                ),
                temperature=0.45,
                max_retries=3,
                step_name="step_6_continuity_rewrite",
            )
            parsed = ContinuityRewriteOutput.model_validate_json(result)
            print(f"[{source_label}] Step 6 (fluent) 完成: 已统一优化 {len(parsed.segments)} 个段落的连续性")
            for seg in parsed.segments:
                _log.debug("[Step 6] segment %d raw seedance_prompt (%d chars):\n%s",
                           seg.segment_id, len(seg.seedance_prompt), seg.seedance_prompt)
            return parsed.model_dump()
        except Exception as e:
            _log.warning("Fluent continuity rewrite failed: %s", e)
            print(f"[{source_label}] Step 6 (fluent) 跳过: 连续性重写失败 ({e})")
            return None

    # ═════════════════════════════════════════════════════════════════
    #  Screenplay → human-readable text
    # ═════════════════════════════════════════════════════════════════

    @staticmethod
    def screenplay_to_text(screenplay_data: Dict[str, Any]) -> str:
        """Convert screenplay dict to a human-readable text file."""
        lines: List[str] = []
        va = screenplay_data.get("video_analysis", {})
        meta = screenplay_data.get("_meta", {}) or {}

        lines.append("═" * 50)
        lines.append("  剧 本")
        lines.append("═" * 50)
        if "title" in screenplay_data:
            lines.append(f"  标题：{screenplay_data['title']}")
        target_duration = meta.get("target_duration_seconds") or meta.get("requested_duration_seconds")
        if target_duration:
            lines.append(f"  目标时长：{target_duration}s")
        lines.append(f"  风格：{va.get('style', '')}")
        lines.append(f"  主题：{va.get('theme', '')}")
        lines.append(f"  基调：{va.get('tone', '')}")
        elements = va.get("key_elements", [])
        if elements:
            lines.append(f"  关键元素：{', '.join(elements)}")
        story_arc_fields = [
            ("开场钩子", screenplay_data.get("hook", "")),
            ("核心冲突", screenplay_data.get("core_conflict", "")),
            ("失败代价", screenplay_data.get("stakes", "")),
            ("高潮爆点", screenplay_data.get("climax", "")),
            ("结尾兑现", screenplay_data.get("payoff", "")),
            ("情绪曲线", screenplay_data.get("emotional_curve", "")),
        ]
        for label, value in story_arc_fields:
            if value:
                lines.append(f"  {label}：{value}")
        turning_points = screenplay_data.get("turning_points", []) or []
        if turning_points:
            lines.append("  关键转折：")
            for tp in turning_points:
                lines.append(f"    - {tp}")
        lines.append("")

        chars = screenplay_data.get("characters", [])
        if chars:
            lines.append("─" * 50)
            lines.append("  角色表")
            lines.append("─" * 50)
            for c in chars:
                lines.append(f"\n▌ {c['name']}")
                lines.append(f"  外观：{c.get('description', '')}")
                lines.append(f"  性格：{c.get('personality', '')}")
                voice = c.get("voice_description", "")
                if voice:
                    lines.append(f"  声音：{voice}")
            lines.append("")

        locs = screenplay_data.get("locations", [])
        if locs:
            lines.append("─" * 50)
            lines.append("  场景表")
            lines.append("─" * 50)
            for loc in locs:
                lines.append(f"\n▌ {loc['name']}")
                lines.append(f"  {loc.get('description', '')}")
            lines.append("")

        narrative = screenplay_data.get("narrative", "")
        if narrative:
            lines.append("─" * 50)
            lines.append("  故事正文")
            lines.append("─" * 50)
            lines.append("")
            lines.append(narrative)

        lines.append(f"\n{'═' * 50}")
        return "\n".join(lines)

    # ═════════════════════════════════════════════════════════════════
    #  Path helpers
    # ═════════════════════════════════════════════════════════════════

    @staticmethod
    def screenplay_paths(storyboard_output_path: str) -> tuple[str, str]:
        """Derive screenplay .json and .txt paths from storyboard output path."""
        p = Path(storyboard_output_path)
        stem = p.stem.replace("_storyboard", "")
        return (
            str(p.parent / f"{stem}_screenplay.json"),
            str(p.parent / f"{stem}_screenplay.txt"),
        )

    def save_screenplay(self, screenplay_data: Dict[str, Any],
                        json_path: str, txt_path: str):
        """Save screenplay as both JSON and human-readable .txt."""
        self.save_json(screenplay_data, json_path)
        txt = self.screenplay_to_text(screenplay_data)
        Path(txt_path).parent.mkdir(parents=True, exist_ok=True)
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(txt)
        print(f"[Screenplay] JSON: {json_path}")
        print(f"[Screenplay] TXT:  {txt_path}")

    # ═════════════════════════════════════════════════════════════════
    #  LLM call helper (shared by novel engine + conversion step)
    # ═════════════════════════════════════════════════════════════════

    # ── Age-reference sanitisation (for PROHIBITED_CONTENT workaround) ──

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

    @staticmethod
    def _strip_markdown_fences(text: str) -> str:
        """Remove ```json ... ``` or ``` ... ``` wrappers that LLMs sometimes add."""
        import re
        stripped = text.strip()
        m = re.match(r'^```(?:json|JSON)?\s*\n(.*?)```\s*$', stripped, re.DOTALL)
        if m:
            return m.group(1).strip()
        return stripped

    @staticmethod
    def _extract_json_object(text: str) -> str:
        """Try to extract the first top-level JSON object/array from *text*.

        LLMs sometimes wrap valid JSON inside prose (e.g. "作为导演，我已根据…
        ```json { … } ```").  This helper finds the outermost { … } or [ … ]
        and returns it, falling back to the original text if nothing is found.

        Uses full bracket/brace tracking ({}, []) to avoid premature
        truncation when inner JSON is malformed (e.g. a ``}`` that closes
        an object too early throws off single-char depth counting).
        """
        s = text.strip()
        start = -1
        for i, ch in enumerate(s):
            if ch in ('{', '['):
                start = i
                break
        if start == -1:
            return s

        # Track a stack of expected closers so we handle both {} and []
        stack: list[str] = []
        in_string = False
        escape = False
        _MATCH = {'{': '}', '[': ']'}
        _CLOSERS = set(']}')

        for i in range(start, len(s)):
            ch = s[i]
            if escape:
                escape = False
                continue
            if ch == '\\' and in_string:
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in _MATCH:
                stack.append(_MATCH[ch])
            elif ch in _CLOSERS:
                if stack and stack[-1] == ch:
                    stack.pop()
                # If stack is empty, this is the outermost closer
                if not stack:
                    return s[start:i + 1]

        return s[start:]

    @classmethod
    def _sanitize_ages(cls, text: str) -> str:
        """Strip explicit numeric age references that trigger Gemini's
        PROHIBITED_CONTENT filter."""
        return cls._AGE_PATTERNS.sub("", text)

    @classmethod
    def _sanitize_youth(cls, text: str) -> str:
        """Aggressive sanitisation: also replace youth-indicating words to
        avoid Gemini's PROHIBITED_CONTENT on minor-related content."""
        text = cls._sanitize_ages(text)
        for old, new in cls._YOUTH_REPLACEMENTS:
            text = text.replace(old, new)
        return text

    # ── LLM call helper ──────────────────────────────────────────────

    def _call_llm_with_retry(
        self,
        client,
        user_msg: str,
        system_prompt: str,
        schema: dict,
        temperature: float = 0.3,
        max_retries: int = 5,
        step_name: str = "",
        response_model: Optional[type] = None,
    ) -> str:
        from clients.llm_client import ProhibitedContentError, LLMTimeoutError

        last_err: Optional[Exception] = None
        sanitize_level = 0          # 0=none, 1=ages, 2=youth words

        def _escape_inner_quotes(text: str) -> str:
            """Escape unescaped ASCII double-quotes inside JSON string values.

            LLMs often emit raw " for Chinese dialogue quotes inside long
            narrative strings instead of using \\\" or the proper Unicode
            quotation marks (\u201c/\u201d).  This pass detects those
            by checking whether a " actually terminates the JSON string
            (i.e. the next non-whitespace char is a JSON structural token)
            or is an embedded literal that needs escaping.
            """
            _STRUCT_AFTER_VALUE = frozenset(',}]:')
            out: list[str] = []
            i = 0
            n = len(text)

            while i < n:
                ch = text[i]
                if ch != '"':
                    out.append(ch)
                    i += 1
                    continue

                # Opening quote of a JSON string — find the *real* closing quote
                out.append('"')
                i += 1
                while i < n:
                    c = text[i]
                    if c == '\\':
                        out.append(c)
                        i += 1
                        if i < n:
                            out.append(text[i])
                            i += 1
                        continue
                    if c != '"':
                        out.append(c)
                        i += 1
                        continue
                    # c == '"' — decide: real close or embedded?
                    # Peek ahead past whitespace for a structural token
                    j = i + 1
                    while j < n and text[j] in ' \t\r\n':
                        j += 1
                    if j >= n or text[j] in _STRUCT_AFTER_VALUE:
                        # Looks like a real closing quote
                        out.append('"')
                        i = j  # skip over the whitespace we peeked
                        break
                    # Otherwise it's an embedded quote — escape it
                    out.append('\\"')
                    i += 1

            return ''.join(out)

        def _fix_premature_close(text: str) -> str:
            r"""Fix the common LLM pattern where an object ``}`` is placed
            too early and subsequent fields leak out at the wrong level.

            Example broken JSON (4th item closes early)::

                [{"a":"1","b":"2"},{"a":"3"},"b":"4"}]

            Fixed::

                [{"a":"1","b":"2"},{"a":"3","b":"4"}]

            The heuristic: outside a string, if we see ``}`` followed by
            ``,`` then a bare key ``"…":`` (not preceded by ``{``), the
            closer was premature — remove it so the fields merge back into
            the open object.  Only applies to ``}`` (not ``]``), because
            ``],"key":`` is normal JSON (closing array, next sibling key).
            """
            _KEY_COLON = re.compile(r'\s*"[^"]*"\s*:')
            out: list[str] = list(text)
            length = len(out)
            in_str = False
            esc = False
            i = 0
            while i < length:
                ch = out[i]
                if esc:
                    esc = False
                    i += 1
                    continue
                if ch == '\\' and in_str:
                    esc = True
                    i += 1
                    continue
                if ch == '"':
                    in_str = not in_str
                    i += 1
                    continue
                if in_str:
                    i += 1
                    continue
                if ch == '}':
                    j = i + 1
                    while j < length and out[j] in ' \t\r\n':
                        j += 1
                    if j < length and out[j] == ',':
                        k = j + 1
                        rest = ''.join(out[k:k + 80])
                        m = _KEY_COLON.match(rest)
                        if m:
                            out[i] = ','
                            out[j] = ''
                            text_new = ''.join(out)
                            out = list(text_new)
                            length = len(out)
                            continue
                i += 1
            return ''.join(out)

        def _repair_json(text: str) -> str:
            """Best-effort repair of truncated / malformed LLM JSON.

            Handles: unescaped quotes in string values, trailing commas,
            unterminated strings, unbalanced braces/brackets, and premature
            object closes caused by LLM structural mistakes.
            """
            s = text.rstrip()

            # 0. Escape unescaped double-quotes inside string values
            s = _escape_inner_quotes(s)

            # 0.5. Fix premature object/array closes
            s = _fix_premature_close(s)

            # 1. Close unterminated string literals
            in_str = False
            esc = False
            last_quote = -1
            for i, ch in enumerate(s):
                if esc:
                    esc = False
                    continue
                if ch == '\\':
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    last_quote = i
            if in_str:
                s += '"'

            # 2. Remove trailing commas before } or ]
            s = re.sub(r',\s*([}\]])', r'\1', s)

            # 3. Remove trailing incomplete key-value pairs or dangling commas
            s = re.sub(r',\s*"[^"]*"\s*:\s*("([^"\\]|\\.)*)?$', '', s)
            s = re.sub(r',\s*$', '', s)

            # 4. Remove unmatched closers and append any missing ones.
            #    Matched closers pop the stack; unmatched closers (LLM typo)
            #    are dropped from the output.
            stack4: list = []
            out4: list[str] = []
            in_str2 = False
            esc2 = False
            for ch in s:
                if esc2:
                    esc2 = False
                    out4.append(ch)
                    continue
                if ch == '\\' and in_str2:
                    esc2 = True
                    out4.append(ch)
                    continue
                if ch == '"':
                    in_str2 = not in_str2
                    out4.append(ch)
                    continue
                if in_str2:
                    out4.append(ch)
                    continue
                if ch == '{':
                    stack4.append('}')
                    out4.append(ch)
                elif ch == '[':
                    stack4.append(']')
                    out4.append(ch)
                elif ch in ('}', ']'):
                    if stack4 and stack4[-1] == ch:
                        stack4.pop()
                        out4.append(ch)
                    # else: unmatched closer — drop it silently
                else:
                    out4.append(ch)
            s = ''.join(out4)

            if stack4:
                s += ''.join(reversed(stack4))

            return s

        def _try_parse_or_repair(text: str, label: str = "") -> Optional[str]:
            """Try json.loads, then repair+json.loads. Return valid JSON or None."""
            if not text or len(text) < 2:
                return None
            try:
                json.loads(text)
                return text
            except json.JSONDecodeError:
                pass
            try:
                repaired = _repair_json(text)
                json.loads(repaired)
                _log.info("JSON auto-repair succeeded (%s, len=%d)", label, len(repaired))
                return repaired
            except (json.JSONDecodeError, Exception):
                return None

        def _clean_and_validate(raw: str) -> str:
            """Strip fences, extract JSON, and validate it parses as JSON.
            Returns the cleaned JSON string or raises ValueError.

            Tries multiple strategies in order:
            1. fence-strip → extract → parse (or repair)
            2. fence-strip → repair full text (skip extract, avoids truncation)
            3. raw → repair (last resort)
            """
            stripped = (raw or "").strip()
            defenced = self._strip_markdown_fences(stripped)
            extracted = self._extract_json_object(defenced)

            if not extracted or len(extracted) < 20:
                preview = (raw or "")[:200].replace('\n', ' ')
                raise ValueError(f"LLM 返回过短 ({len(extracted)} chars), preview: {preview}")

            # Strategy 1: extracted text (ideal path)
            result = _try_parse_or_repair(extracted, "extracted")
            if result:
                if result != extracted:
                    print("    ✓ JSON 自动修复成功")
                return result

            # Strategy 2: full defenced text (extraction may have truncated)
            if len(defenced) > len(extracted) + 10:
                result = _try_parse_or_repair(defenced, "full-defenced")
                if result:
                    print("    ✓ JSON 自动修复成功 (跳过截断提取)")
                    return result

            # Strategy 3: raw text (fence stripping may have broken things)
            if stripped != defenced:
                result = _try_parse_or_repair(stripped, "raw")
                if result:
                    print("    ✓ JSON 自动修复成功 (原始文本)")
                    return result

            # All strategies failed — dump debug info
            repaired = _repair_json(extracted)
            try:
                last_err = None
                json.loads(repaired)
            except json.JSONDecodeError as e:
                last_err = e

            dump_path = Path("/tmp/llm_invalid_json_debug.txt")
            try:
                dump_path.write_text(
                    f"=== RAW LLM RESPONSE (len={len(raw or '')}) ===\n"
                    f"{raw}\n\n"
                    f"=== AFTER FENCE STRIP + EXTRACT (len={len(extracted)}) ===\n"
                    f"{extracted}\n\n"
                    f"=== AFTER REPAIR ATTEMPT (len={len(repaired)}) ===\n"
                    f"{repaired}\n\n"
                    f"=== JSON ERROR ===\n{last_err}\n",
                    encoding="utf-8",
                )
                _log.info("Dumped invalid JSON debug info to %s", dump_path)
                print(f"    📄 已保存原始响应到 {dump_path}")
            except OSError:
                pass
            preview = extracted[:300].replace('\n', ' ')
            raise ValueError(
                f"LLM 返回非法 JSON: {last_err}. preview: {preview}"
            ) from last_err

        # OpenAI-compatible backends only set response_format: json_object,
        # which many proxied models ignore. Append explicit JSON instructions
        # to the system prompt so the model reliably outputs valid JSON.
        if schema and getattr(client, "backend", "") == "openai_compatible":
            compact_schema = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
            system_prompt = (
                system_prompt
                + "\n\n【输出格式——严格遵守】\n"
                "1. 你必须且只能输出合法的 JSON 对象，禁止输出 markdown、解释性文字、```json``` 包裹。\n"
                "2. 数组中每个对象的所有字段必须写在同一对花括号 {} 内，不要提前关闭 }。\n"
                "3. 字符串值内的引号请用中文引号（""）而非 ASCII 双引号。\n"
                "4. 确保所有括号配对完整，输出完整的 JSON 后停止。\n"
                "JSON Schema：\n"
                + compact_schema
            )

        for attempt in range(1, max_retries + 1):
            self._ensure_not_stopped()
            try:
                result = client.generate_text(
                    prompt=user_msg,
                    system_instruction=system_prompt,
                    temperature=temperature,
                    response_schema=schema,
                    model=self.llm_model,
                    timeout_seconds=getattr(client, "DEFAULT_TIMEOUT_SECONDS", 120),
                    max_retries=1,
                    max_tokens=16384,
                )
                self._ensure_not_stopped()
                _log.debug("[LLM raw response] len=%d, preview=%.500s",
                           len(result or ""), (result or "")[:500])
                try:
                    cleaned = _clean_and_validate(result)
                    if response_model is not None:
                        try:
                            response_model.model_validate_json(cleaned)
                        except Exception as ve:
                            if attempt == max_retries:
                                raise ValueError(
                                    f"LLM 返回 JSON 缺少必要字段 (已重试 {max_retries} 次): {ve}"
                                ) from ve
                            _log.warning(
                                "LLM attempt %d/%d schema validation failed: %s",
                                attempt, max_retries, ve,
                            )
                            print(f"    ⚠ LLM 返回缺少必要字段 (attempt {attempt}/{max_retries}): {ve}")
                            retry_delays = [2, 5, 10, 10, 10]
                            delay = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                            time.sleep(delay)
                            continue
                    _log.debug(
                        "[LLM IO] step=%s attempt=%d\n"
                        "=== SYSTEM PROMPT ===\n%s\n"
                        "=== USER MSG ===\n%s\n"
                        "=== OUTPUT ===\n%s",
                        step_name or "unknown", attempt,
                        system_prompt, user_msg, cleaned,
                    )
                    return cleaned
                except (ValueError, json.JSONDecodeError) as je:
                    # JSON invalid — try fallback on last attempt, otherwise retry
                    if attempt == max_retries:
                        raise ValueError(
                            f"LLM 返回非法 JSON (已重试 {max_retries} 次): {je}"
                        ) from je
                    # Not last attempt — log and let the loop retry
                    last_err = je
                    _log.warning("LLM attempt %d/%d returned invalid JSON: %s",
                                 attempt, max_retries, je)
                    print(f"    ⚠ LLM 返回非法 JSON (attempt {attempt}/{max_retries}): {je}")
                    retry_delays = [2, 5, 10, 10, 10]
                    delay = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                    time.sleep(delay)
                    continue

            except ProhibitedContentError:
                if sanitize_level == 0:
                    print("    ⚠ PROHIBITED_CONTENT — 清洗年龄数字后重试...")
                    user_msg = self._sanitize_ages(user_msg)
                    system_prompt = self._sanitize_ages(system_prompt)
                    sanitize_level = 1
                    continue
                if sanitize_level == 1:
                    print("    ⚠ PROHIBITED_CONTENT — 深度清洗少年/少女等词后重试...")
                    user_msg = self._sanitize_youth(user_msg)
                    system_prompt = self._sanitize_youth(system_prompt)
                    sanitize_level = 2
                    continue
                last_err = ProhibitedContentError(
                    "Prompt still blocked after full sanitisation"
                )
                _log.warning("LLM attempt %d/%d: %s", attempt, max_retries,
                             last_err)
                print(f"    ⚠ LLM 调用失败 "
                      f"(attempt {attempt}/{max_retries}): {last_err}")
                if attempt < max_retries:
                    retry_delays = [2, 5, 10, 10, 10]
                    delay = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                    time.sleep(delay)

            except LLMTimeoutError as e:
                last_err = e
                _log.warning("LLM attempt %d/%d timeout: %s", attempt, max_retries, e)
                print(f"    ⚠ LLM 调用超时 "
                      f"(attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    retry_delays = [2, 5, 10, 10, 10]
                    delay = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                    time.sleep(delay)

            except Exception as e:
                last_err = e
                _log.warning("LLM attempt %d/%d: %s", attempt, max_retries, e)
                print(f"    ⚠ LLM 调用失败 "
                      f"(attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    retry_delays = [2, 5, 10, 10, 10]
                    delay = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                    time.sleep(delay)

        raise last_err or ValueError("All LLM attempts failed")

    # ═════════════════════════════════════════════════════════════════
    #  Shared post-processing helpers
    # ═════════════════════════════════════════════════════════════════

    @staticmethod
    def _format_duration_seconds(value: float) -> str:
        value = round(float(value) * 10) / 10
        if abs(value - int(value)) < 1e-9:
            return str(int(value))
        return f"{value:.1f}".rstrip("0").rstrip(".")

    @staticmethod
    def _extract_target_duration_seconds(data: Dict[str, Any]) -> Optional[float]:
        meta = (data or {}).get("_meta") or {}
        for key in ("target_duration_seconds", "requested_duration_seconds"):
            raw = meta.get(key)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None

    @classmethod
    def _scene_duration_floor_seconds(cls, scene: dict) -> float:
        total_chars = sum(
            len(dl.get("text", ""))
            for dl in scene.get("dialogue_lines", []) or []
        )
        base_floor = 1.0
        if total_chars > 0:
            base_floor = max(base_floor, round(total_chars / cls.CHARS_PER_SECOND, 1))

        priority = cls._story_duration_priority(scene)
        if priority >= 2.4:
            base_floor = max(base_floor, 2.5)
        elif priority >= 1.9:
            base_floor = max(base_floor, 2.0)
        elif priority >= 1.4:
            base_floor = max(base_floor, 1.5)
        return round(base_floor, 1)

    @classmethod
    def _segment_duration_floor_seconds(
        cls,
        segment: dict,
        default_floor: float,
    ) -> float:
        base_floor = max(1.0, round(default_floor, 1))
        priority = cls._story_duration_priority(segment)
        if priority >= 2.4:
            base_floor = max(base_floor, round(default_floor + 2.0, 1))
        elif priority >= 1.9:
            base_floor = max(base_floor, round(default_floor + 1.2, 1))
        elif priority >= 1.4:
            base_floor = max(base_floor, round(default_floor + 0.6, 1))
        return round(base_floor, 1)

    @staticmethod
    def _normalize_duration_values(
        values: List[float],
        target_total: float,
        minimums: Optional[List[float]] = None,
        maximums: Optional[List[float]] = None,
        priorities: Optional[List[float]] = None,
    ) -> List[float]:
        if not values:
            return []

        precision = 0.1
        target = float(target_total)
        if target <= 0:
            return [round(v, 1) for v in values]

        minimums = list(minimums or [precision] * len(values))
        minimums = [
            max(precision, round(float(v) / precision) * precision)
            for v in minimums
        ]
        # Enforce maximums: each max must be >= its corresponding minimum
        if maximums is not None:
            maximums = [
                max(minimums[i], round(float(v) / precision) * precision)
                for i, v in enumerate(maximums)
            ]
        priorities = [float(v) for v in (priorities or [1.0] * len(values))]
        target = max(
            round(target / precision) * precision,
            round(sum(minimums) / precision) * precision,
        )

        current_total = sum(values)
        if current_total <= 0:
            scaled = [
                max(
                    minimums[i],
                    round((target / len(values)) / precision) * precision,
                )
                for i in range(len(values))
            ]
        else:
            ratio = target / current_total
            scaled = [
                max(
                    minimums[i],
                    round((values[i] * ratio) / precision) * precision,
                )
                for i in range(len(values))
            ]

        # Apply upper bounds after initial scaling
        if maximums is not None:
            scaled = [min(scaled[i], maximums[i]) for i in range(len(scaled))]

        increase_order = sorted(
            range(len(scaled)),
            key=lambda i: (priorities[i], values[i]),
            reverse=True,
        )
        decrease_order = sorted(
            range(len(scaled)),
            key=lambda i: (priorities[i], values[i]),
        )

        delta = round(target - sum(scaled), 1)
        guard = 0
        while abs(delta) >= precision and guard < 10000:
            guard += 1
            if delta > 0:
                # Respect upper bound when increasing
                candidates_up = [
                    i for i in increase_order
                    if maximums is None or scaled[i] + precision <= maximums[i] + 1e-9
                ]
                if not candidates_up:
                    break
                idx = candidates_up[(guard - 1) % len(candidates_up)]
                scaled[idx] = round(scaled[idx] + precision, 1)
                delta = round(delta - precision, 1)
                continue

            candidates = [
                i for i in decrease_order
                if scaled[i] - precision >= minimums[i] - 1e-9
            ]
            if not candidates:
                break
            idx = candidates[(guard - 1) % len(candidates)]
            scaled[idx] = round(scaled[idx] - precision, 1)
            delta = round(delta + precision, 1)

        return [round(v, 1) for v in scaled]

    @classmethod
    def _apply_target_duration_to_storyboard_scenes(
        cls,
        scenes: List[dict],
        target_total: Optional[float],
    ) -> float:
        if not scenes:
            return 0.0
        if target_total is None or target_total <= 0:
            return cls.calculate_duration(scenes)

        values: List[float] = []
        minimums: List[float] = []
        for scene in scenes:
            dur_key = "duration" if "duration" in scene else "estimated_duration"
            current = cls._parse_duration_seconds(scene.get(dur_key, "2秒"))
            values.append(max(0.1, current))
            minimums.append(cls._scene_duration_floor_seconds(scene))

        adjusted = cls._normalize_duration_values(
            values,
            target_total,
            minimums=minimums,
            maximums=[15.0] * len(scenes),
            priorities=[cls._story_duration_priority(scene) for scene in scenes],
        )
        for scene, new_value in zip(scenes, adjusted):
            dur_key = "duration" if "duration" in scene else "estimated_duration"
            scene[dur_key] = f"{cls._format_duration_seconds(new_value)}秒"

        return round(sum(adjusted), 1)

    @staticmethod
    def run_validation(
        scenes: List[dict],
        char_names: set,
        loc_names: set,
        prop_names: Optional[set] = None,
        label: str = "StoryboardGen",
    ) -> tuple[List[dict], List[ValidationIssue]]:
        """Run validate_and_fix and print a human-readable summary."""
        print(f"[{label}] Recheck: validating consistency...")
        scenes, issues = validate_and_fix(
            scenes, char_names, loc_names, prop_names=prop_names or set(),
        )

        if issues:
            for issue in issues:
                tag = "✔" if issue.auto_fixed else "⚠"
                print(f"  {tag} {issue}")
            fixed = sum(1 for i in issues if i.auto_fixed)
            unfixed = len(issues) - fixed
            print(f"[{label}] Recheck: {len(issues)} issues, "
                  f"auto-fixed {fixed}, remaining {unfixed}")
        else:
            print(f"[{label}] Recheck: all clear")

        return scenes, issues

    @staticmethod
    def inject_defaults(
        storyboard_data: Dict[str, Any],
        character_ids: Optional[Dict[str, str]] = None,
    ):
        """Inject default fields (id, image_path) into characters and locations."""
        for char in storyboard_data.get("characters", []):
            char["id"] = (character_ids or {}).get(char["name"], "")
            char.setdefault("image_path", "")
        for loc in storyboard_data.get("locations", []):
            loc.setdefault("image_path", "")

    CHARS_PER_SECOND = 5.5

    @staticmethod
    def _parse_duration_seconds(raw: str) -> float:
        """Parse a Chinese duration string like '2秒' / '3s' into float seconds."""
        try:
            d = (raw.replace("秒", "").replace("seconds", "")
                 .replace("s", "").strip())
            return float(d.split("-")[0])
        except Exception:
            return 2.0

    @classmethod
    def adjust_duration_by_dialogue(cls, scenes: List[dict]) -> int:
        """Ensure every scene's duration is at least long enough for its dialogue.

        Returns the number of scenes whose duration was bumped up.
        """
        adjusted = 0
        for s in scenes:
            dur_key = "duration" if "duration" in s else "estimated_duration"
            current_sec = cls._parse_duration_seconds(s.get(dur_key, "2秒"))

            total_chars = sum(
                len(dl.get("text", ""))
                for dl in s.get("dialogue_lines", [])
            )
            if total_chars == 0:
                continue

            text_sec = total_chars / cls.CHARS_PER_SECOND
            needed = max(current_sec, text_sec)

            if needed > current_sec:
                import math
                new_dur = math.ceil(needed)
                s[dur_key] = f"{new_dur}秒"
                adjusted += 1

        return adjusted

    @staticmethod
    def calculate_duration(scenes: List[dict]) -> float:
        """Sum up durations from scene dicts, parsing Chinese '秒' etc."""
        total = 0.0
        for s in scenes:
            dur_key = "duration" if "duration" in s else "estimated_duration"
            try:
                d = (s[dur_key]
                     .replace("秒", "").replace("seconds", "")
                     .replace("s", "").strip())
                total += float(d.split("-")[0])
            except Exception:
                total += 2.0
        return total

    @staticmethod
    def convert_dialogue(scenes: List[dict]):
        """Add a ``dialogue`` string field to each scene from ``dialogue_lines``."""
        for s in scenes:
            s["dialogue"] = dialogue_lines_to_string(
                s.get("dialogue_lines", [])
            )

    @staticmethod
    def save_json(data: Dict[str, Any], output_path: str):
        """Save dict to JSON file."""
        from utils.io import save_json
        save_json(output_path, data)
