"""
Novel Storyboard Engine — convert a novel chapter into a structured storyboard
using a multi-phase pipeline:

  Phase 1: Global Analysis    — extract characters, locations, narrative segments
  Phase 2: Per-Segment Narrative — condense each segment into prose narrative
  Phase 3: Narrative → Storyboard — split narrative into shots (SHARED)
"""

import json
import logging
from typing import Dict, Any, Optional, List

from .base_engine import BaseStoryboardEngine
from .schemas import (
    ChapterAnalysis, NarrativeSegment, SegmentNarrative,
    ScreenplayNarrativeOutput, AUTO_VIDEO_STYLE, DEFAULT_VIDEO_STYLE,
    flatten_json_schema, Prop, resolve_video_style,
)
from prompts.novel_engine import (
    build_analyze_chapter_prompt,
    build_full_narrative_prompt,
    build_segment_narrative_prompt,
)

_log = logging.getLogger("video_agent.storyboard_gen.novel")


class NovelStoryboardEngine(BaseStoryboardEngine):
    """Multi-phase pipeline: Analyze → Narrative → Storyboard."""

    # ── Step 1: Novel → Screenplay (prose narrative) ──────────────

    def generate_screenplay(
        self,
        chapter_text: str,
        output_path: str,
        video_style: str = AUTO_VIDEO_STYLE,
        style_hint: str = "",
        target_duration: Optional[float] = None,
        title: str = "",
        save: bool = True,
    ) -> Dict[str, Any]:
        """Generate a prose narrative screenplay from a novel chapter.

        When *save* is True (default), writes .json and .txt files.
        Returns the screenplay dict.
        """
        chapter_text = chapter_text.strip()
        self._last_story_arc = {}
        if len(chapter_text) < 50:
            raise ValueError("章节文本过短，无法生成剧本")

        resolved_style_key, resolved_style, style_from_user = resolve_video_style(
            explicit_style=video_style,
            text_candidates=[title, chapter_text[:4000]],
        )
        if style_hint:
            resolved_style = f"{resolved_style}。{style_hint}"

        char_count = len(chapter_text)
        if target_duration is None:
            target_duration = max(60.0, min(char_count / 30, 300.0))

        style_source = "用户指定" if style_from_user else (
            "文本解析" if resolved_style_key != DEFAULT_VIDEO_STYLE else "默认兜底"
        )
        print(f"[NovelStoryboardGen] 章节长度: {char_count} 字")
        print(f"[NovelStoryboardGen] 视频风格: {resolved_style_key or DEFAULT_VIDEO_STYLE} ({style_source}) → "
              f"{resolved_style[:60]}...")
        print(f"[NovelStoryboardGen] 目标时长: {target_duration:.0f}s")

        # Phase 1: Global analysis
        print(f"\n[Phase 1] 全局分析...")
        self._ensure_not_stopped()
        analysis = self._analyze_chapter(
            chapter_text, resolved_style, target_duration,
        )
        print(f"[Phase 1] 完成 — "
              f"角色 {len(analysis.characters)} · "
              f"场景 {len(analysis.locations)} · "
              f"道具 {len(analysis.props)} · "
              f"段落 {len(analysis.segments)}")
        for c in analysis.characters:
            print(f"  角色「{c.name}」声音: {c.voice_description[:40]}...")
        for p in analysis.props:
            print(f"  道具「{p.name}」: {p.description[:40]}...")
        for seg in analysis.segments:
            print(f"  [{seg.segment_id}] {seg.title} "
                  f"(~{seg.estimated_video_seconds:.0f}s, "
                  f"角色: {', '.join(seg.characters_involved)})")

        # Phase 2: Per-segment narrative generation
        print(f"\n[Phase 2] 逐段生成剧本叙述...")
        self._ensure_not_stopped()
        segment_narratives = self._generate_all_narratives(
            chapter_text, analysis,
        )
        full_narrative = "\n\n".join(segment_narratives)
        print(f"[Phase 2] 完成: {len(full_narrative)} 字叙述文本")

        # Assemble screenplay
        screenplay_data = self._assemble_screenplay(
            analysis, full_narrative, title,
        )
        screenplay_data.setdefault("_meta", {})
        screenplay_data["_meta"]["target_duration_seconds"] = round(target_duration, 1)

        if save:
            sp_json, sp_txt = self.screenplay_paths(output_path)
            self.save_screenplay(screenplay_data, sp_json, sp_txt)

        return screenplay_data

    # ── Step 2: Full pipeline ─────────────────────────────────────

    def generate(
        self,
        chapter_text: str,
        output_path: str,
        video_style: str = AUTO_VIDEO_STYLE,
        style_hint: str = "",
        target_duration: Optional[float] = None,
        title: str = "",
        screenplay_data: Optional[Dict[str, Any]] = None,
        style_reference_image: str = "",
    ) -> Dict[str, Any]:
        """Full pipeline: Novel → Narrative → Storyboard.

        If *screenplay_data* is provided, skips the narrative generation
        and goes directly to the storyboard conversion.
        """
        self._ensure_not_stopped()
        if screenplay_data is None:
            screenplay_data = self.generate_screenplay(
                chapter_text=chapter_text,
                output_path=output_path,
                video_style=video_style,
                style_hint=style_hint,
                target_duration=target_duration,
                title=title,
                save=False,
            )
        elif target_duration is not None and target_duration > 0:
            # 已有剧本时，用传入的 target_duration 覆盖 _meta
            screenplay_data.setdefault("_meta", {})["target_duration_seconds"] = round(target_duration, 1)

        print(f"\n[Phase 3] 核心剧情 + 小说原文 → 分镜转换...")
        storyboard = self.screenplay_to_storyboard(
            screenplay_data=screenplay_data,
            output_path=output_path,
            source_context=chapter_text,
            source_label="novel_storyboard_gen",
            style_reference_image=style_reference_image,
        )

        total_scenes = len(storyboard.get("storyboard", []))
        total_dur = storyboard.get("_meta", {}).get(
            "estimated_duration_seconds", 0,
        )
        print(f"\n[NovelStoryboardGen] 完成: {total_scenes} 个分镜, "
              f"~{total_dur:.0f}s")
        return storyboard

    # ═════════════════════════════════════════════════════════════════
    #  Phase 1: Global Analysis
    # ═════════════════════════════════════════════════════════════════

    def _analyze_chapter(
        self,
        chapter_text: str,
        resolved_style: str,
        target_duration: float,
    ) -> ChapterAnalysis:
        from clients import get_llm_client

        drama_requirements = BaseStoryboardEngine._screenplay_drama_requirements(target_duration)
        fewshot_examples = BaseStoryboardEngine._screenplay_fewshot_examples()
        system_prompt = build_analyze_chapter_prompt(
            resolved_style=resolved_style,
            drama_requirements=drama_requirements,
            fewshot_examples=fewshot_examples,
            target_duration=target_duration,
        )

        user_msg = f"请分析以下小说章节：\n\n{chapter_text}"

        client = get_llm_client(step="screenplay_gen")
        result = self._call_llm_with_retry(
            client, user_msg, system_prompt,
            schema=flatten_json_schema(ChapterAnalysis.model_json_schema()),
            temperature=0.2,
            max_retries=5,
        )

        analysis = ChapterAnalysis.model_validate_json(result)

        total_est = sum(s.estimated_video_seconds for s in analysis.segments)
        if total_est > 0 and abs(total_est - target_duration) > 10:
            ratio = target_duration / total_est
            for seg in analysis.segments:
                seg.estimated_video_seconds = round(
                    seg.estimated_video_seconds * ratio, 1,
                )

        return analysis

    # ═════════════════════════════════════════════════════════════════
    #  Phase 2: Narrative Generation
    # ═════════════════════════════════════════════════════════════════

    SINGLE_PASS_MAX_CHARS = 8000

    def _generate_all_narratives(
        self,
        chapter_text: str,
        analysis: ChapterAnalysis,
    ) -> List[str]:
        char_names = [c.name for c in analysis.characters]

        if len(chapter_text) > self.SINGLE_PASS_MAX_CHARS:
            raise ValueError(
                f"章节文本过长（{len(chapter_text)} 字），当前最大支持 {self.SINGLE_PASS_MAX_CHARS} 字。"
                "请将章节拆分后分别生成。"
            )

        print(f"\n  [单次生成模式] 章节 {len(chapter_text)} 字，一次性生成完整叙述...")
        narrative = self._generate_full_narrative(
            chapter_text=chapter_text,
            analysis=analysis,
            char_names=char_names,
        )
        print(f"  ✓ 生成 {len(narrative)} 字叙述")
        return [narrative]

        # ── 分段生成（暂不启用，保留备用） ────────────────────────────
        # all_narratives: List[str] = []
        # prev_tail: str = ""
        # for seg in analysis.segments:
        #     self._ensure_not_stopped()
        #     print(f"\n  [Segment {seg.segment_id}/{len(analysis.segments)}] {seg.title}...")
        #     seg_text = self._extract_segment_text(chapter_text, seg, analysis.segments)
        #     if not seg_text or len(seg_text.strip()) < 10:
        #         _log.warning("Segment %d: text too short, skipping", seg.segment_id)
        #         continue
        #     narrative = self._generate_segment_narrative(
        #         segment_text=seg_text,
        #         segment_info=seg,
        #         char_names=char_names,
        #         prev_tail=prev_tail,
        #     )
        #     all_narratives.append(narrative)
        #     prev_tail = narrative[-200:] if len(narrative) > 200 else narrative
        # return all_narratives

    def _generate_full_narrative(
        self,
        chapter_text: str,
        analysis: ChapterAnalysis,
        char_names: List[str],
    ) -> str:
        """Single-pass: convert the entire chapter into a condensed screenplay narrative."""
        from clients import get_llm_client

        seg_summary = "\n".join(
            f"  [{s.segment_id}] {s.title}：{s.summary}"
            for s in analysis.segments
        )

        drama_requirements = BaseStoryboardEngine._screenplay_drama_requirements(sum(s.estimated_video_seconds for s in analysis.segments))
        story_arc_output_rules = BaseStoryboardEngine._screenplay_story_arc_output_rules()
        fewshot_examples = BaseStoryboardEngine._screenplay_fewshot_examples()
        system_prompt = build_full_narrative_prompt(
            char_names=char_names,
            drama_requirements=drama_requirements,
            fewshot_examples=fewshot_examples,
            story_arc_output_rules=story_arc_output_rules,
        )

        user_msg = (
            f"【叙事段落参考（确保全部覆盖）】\n{seg_summary}\n\n"
            f"【小说原文】\n{chapter_text}\n\n"
            "请将上述完整章节改写为一段凝练连贯的剧本叙述。"
        )

        client = get_llm_client(step="screenplay_gen")
        result = self._call_llm_with_retry(
            client, user_msg, system_prompt,
            schema=flatten_json_schema(
                ScreenplayNarrativeOutput.model_json_schema(),
            ),
            temperature=0.3,
            max_retries=5,
        )

        parsed = ScreenplayNarrativeOutput.model_validate_json(result)
        self._last_story_arc = {
            "hook": parsed.hook,
            "core_conflict": parsed.core_conflict,
            "stakes": parsed.stakes,
            "turning_points": parsed.turning_points,
            "climax": parsed.climax,
            "payoff": parsed.payoff,
            "emotional_curve": parsed.emotional_curve,
        }
        return parsed.narrative

    def _generate_segment_narrative(
        self,
        segment_text: str,
        segment_info: NarrativeSegment,
        char_names: List[str],
        prev_tail: str = "",
    ) -> str:
        from clients import get_llm_client

        prev_context = ""
        if prev_tail:
            prev_context = (
                f"【上一段末尾（请确保本段开头自然衔接）】\n...{prev_tail}\n\n"
            )

        drama_requirements = BaseStoryboardEngine._screenplay_drama_requirements(
            segment_info.estimated_video_seconds,
        )
        fewshot_examples = BaseStoryboardEngine._screenplay_fewshot_examples()
        system_prompt = build_segment_narrative_prompt(
            char_names=char_names,
            drama_requirements=drama_requirements,
            fewshot_examples=fewshot_examples,
        )

        parts = []
        if prev_context:
            parts.append(prev_context)
        parts.append(f"段落标题：{segment_info.title}")
        parts.append(f"段落概要：{segment_info.summary}")
        parts.append(f"\n原文：\n{segment_text}")
        parts.append("\n请将上述原文改写为凝练的剧本叙述。")

        user_msg = "\n".join(parts)

        client = get_llm_client(step="screenplay_gen")
        result = self._call_llm_with_retry(
            client, user_msg, system_prompt,
            schema=flatten_json_schema(
                SegmentNarrative.model_json_schema(),
            ),
            temperature=0.3,
            max_retries=5,
        )

        parsed = SegmentNarrative.model_validate_json(result)
        return parsed.narrative

    # ── Segment text extraction ───────────────────────────────────

    def _extract_segment_text(
        self,
        full_text: str,
        segment: NarrativeSegment,
        all_segments: List[NarrativeSegment],
    ) -> str:
        start_hint = segment.start_hint.strip()
        end_hint = segment.end_hint.strip()

        start_idx = self._fuzzy_find(full_text, start_hint, 0)
        end_idx = self._fuzzy_find(
            full_text, end_hint,
            max(0, start_idx) if start_idx >= 0 else 0,
        )

        if start_idx >= 0 and end_idx >= 0:
            return full_text[start_idx:end_idx + len(end_hint)]

        _log.warning(
            "Segment %d: hint matching failed (start=%d, end=%d), "
            "using proportional fallback",
            segment.segment_id, start_idx, end_idx,
        )
        total_segs = len(all_segments)
        seg_idx = segment.segment_id - 1
        chunk_size = len(full_text) // max(total_segs, 1)
        fallback_start = seg_idx * chunk_size
        fallback_end = min((seg_idx + 1) * chunk_size + 200, len(full_text))
        return full_text[fallback_start:fallback_end]

    @staticmethod
    def _fuzzy_find(text: str, hint: str, search_from: int = 0) -> int:
        """Try exact match first, then progressively shorter prefixes."""
        if not hint:
            return -1
        idx = text.find(hint, search_from)
        if idx >= 0:
            return idx
        for trim in (0.7, 0.5, 0.35):
            length = max(6, int(len(hint) * trim))
            prefix = hint[:length]
            idx = text.find(prefix, search_from)
            if idx >= 0:
                return idx
            suffix = hint[-length:]
            idx = text.find(suffix, search_from)
            if idx >= 0:
                return idx
        return -1

    # ═════════════════════════════════════════════════════════════════
    #  Assembly
    # ═════════════════════════════════════════════════════════════════

    def _assemble_screenplay(
        self,
        analysis: ChapterAnalysis,
        narrative: str,
        title: str = "",
    ) -> dict:
        story_arc = getattr(self, "_last_story_arc", {}) or {}
        screenplay: Dict[str, Any] = {
            "video_analysis": {
                "style": analysis.style,
                "theme": analysis.theme,
                "tone": analysis.tone,
                "key_elements": analysis.key_elements,
            },
            "characters": [
                {
                    "name": c.name,
                    "description": c.description,
                    "personality": c.personality,
                    "voice_description": c.voice_description,
                }
                for c in analysis.characters
            ],
            "locations": [
                {
                    "name": loc.name,
                    "description": loc.description,
                }
                for loc in analysis.locations
            ],
            "props": [
                {
                    "name": p.name,
                    "description": p.description,
                }
                for p in analysis.props
            ],
            "hook": story_arc.get("hook", analysis.segments[0].summary if analysis.segments else narrative[:30]),
            "core_conflict": story_arc.get("core_conflict", analysis.theme),
            "stakes": story_arc.get("stakes", analysis.tone),
            "turning_points": story_arc.get("turning_points", [s.summary for s in analysis.segments[:3]]),
            "climax": story_arc.get("climax", analysis.segments[-1].summary if analysis.segments else narrative[-30:]),
            "payoff": story_arc.get("payoff", analysis.segments[-1].summary if analysis.segments else narrative[-30:]),
            "emotional_curve": story_arc.get("emotional_curve", analysis.tone),
            "narrative": narrative,
        }
        if title:
            screenplay["title"] = title
        return screenplay
