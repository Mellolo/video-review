"""
Video Storyboard Engine — analyse a reference video via Gemini VLM.

Two-step pipeline:
  Step 1: Video → Screenplay  (narrative: story, dialogue, mood)
  Step 2: Screenplay → Storyboard  (add camera, lighting, visual_description)

Supports two creative modes:
  - REPLICATE: faithfully reproduce the reference video's story
  - RECREATE:  same theme, adapt creatively to the user's direction (二创)
"""

import logging
import os
import re
from pathlib import Path
from typing import Dict, Any, Optional

from .base_engine import BaseStoryboardEngine
from .schemas import (
    StoryboardMode, ScreenplaySchema, AUTO_VIDEO_STYLE,
    DEFAULT_VIDEO_STYLE, VIDEO_STYLE_PRESETS, flatten_json_schema,
    normalize_style_choice,
)
from prompts.video_engine import build_replicate_screenplay_prompt, build_recreate_screenplay_prompt

_log = logging.getLogger("video_agent.storyboard_gen.video")


_STYLE_PREFIX_PATTERNS = [
    r"^(3D\s*CG\s*动画风格|3D\s*动画风格|中国国产3D\s*动漫风格|3d\s*国漫风格)[，。,:：\s]*",
    r"^(真人写实风格|真人风格|写实风格)[，。,:：\s]*",
    r"^(2D\s*手绘动漫风格|2D\s*动漫风格|2d\s*动漫风格|手绘动漫风格)[，。,:：\s]*",
    r"^(中国水墨动画风格|水墨风格|国风水墨风格)[，。,:：\s]*",
]


# ── Video helpers ─────────────────────────────────────────────────────

def _get_video_duration(video_path: str) -> Optional[float]:
    from utils.ffmpeg import get_video_duration_optional
    return get_video_duration_optional(video_path)


def _detect_mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".mp4": "video/mp4", ".mov": "video/mov", ".avi": "video/avi",
        ".webm": "video/webm", ".mpeg": "video/mpeg", ".mpg": "video/mpg",
        ".flv": "video/x-flv", ".wmv": "video/wmv", ".3gp": "video/3gpp",
    }.get(ext, "video/mp4")


def _replace_style_prefix(text: str, style_desc: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return style_desc
    cleaned = raw
    # Loop until no more style prefixes are found (handles consecutive ones
    # like "真人风格。3D CG 动画风格。...")
    changed = True
    while changed:
        changed = False
        for pattern in _STYLE_PREFIX_PATTERNS:
            new = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
            if new != cleaned:
                cleaned = new.lstrip("，。,:： ")
                changed = True
    cleaned = cleaned.lstrip("，。,:： ")
    return f"{style_desc}。{cleaned}" if cleaned else style_desc


# ── Engine ────────────────────────────────────────────────────────────

class VideoStoryboardEngine(BaseStoryboardEngine):
    """Watches a video via Gemini VLM and produces a structured storyboard."""

    # ── Step 1: Video → Screenplay ────────────────────────────────

    def generate_screenplay(
        self,
        video_path: str,
        output_path: str,
        num_scenes: Optional[int] = None,
        total_duration: Optional[float] = None,
        mode: StoryboardMode = StoryboardMode.REPLICATE,
        recreate_direction: str = "",
        video_style: str = AUTO_VIDEO_STYLE,
        style_hint: str = "",
        save: bool = True,
    ) -> Dict[str, Any]:
        """Analyse a video and produce a narrative screenplay (no technical
        shot details).  When *save* is True (default), writes .json and .txt.

        Returns the screenplay dict.
        """
        from clients import get_llm_client

        video_path = str(video_path)
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video not found: {video_path}")

        detected_duration = _get_video_duration(video_path)
        if total_duration is None:
            total_duration = detected_duration or 60.0

        mode_label = "二创" if mode == StoryboardMode.RECREATE else "复刻"
        _log.info("Video: %s  duration=%.1fs  target_scenes=%s  mode=%s",
                   video_path, total_duration, num_scenes or "auto", mode.value)
        print(f"[StoryboardGen] Video: {Path(video_path).name}")
        print(f"[StoryboardGen] Mode: {mode_label} ({mode.value})")
        print(f"[StoryboardGen] Duration: {total_duration:.1f}s")
        print(f"[StoryboardGen] Scenes: {num_scenes or 'auto'}")

        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        if file_size_mb > 100:
            raise ValueError(f"Video too large ({file_size_mb:.1f}MB > 100MB limit)")

        with open(video_path, "rb") as f:
            video_bytes = f.read()

        mime_type = _detect_mime(video_path)
        prompt = self._build_screenplay_prompt(
            num_scenes,
            total_duration,
            mode,
            recreate_direction=recreate_direction,
            source_duration=detected_duration,
        )

        print("[StoryboardGen] Step 1: 生成剧本...")
        self._ensure_not_stopped()

        client = get_llm_client(step="video_analysis")
        last_video_err = None
        for video_attempt in range(1, 4):
            self._ensure_not_stopped()
            result = client.generate_with_video(
                text_prompt=prompt,
                video_paths=[video_path],
                temperature=0.2,
                response_schema=flatten_json_schema(
                    ScreenplaySchema.model_json_schema()
                ),
                model=self.llm_model,
                timeout_seconds=client.VIDEO_TIMEOUT_SECONDS,
                max_retries=3,
                auto_adjust_fps=True,
                use_low_resolution=True,
            )
            self._ensure_not_stopped()
            try:
                parsed = ScreenplaySchema.model_validate_json(result)
                break
            except Exception as ve:
                last_video_err = ve
                if video_attempt < 3:
                    print(f"    ⚠ 剧本缺少必要字段 (attempt {video_attempt}/3): {ve}")
                    import time as _time
                    _time.sleep(5)
                    continue
                raise ValueError(
                    f"视频分析返回剧本缺少必要字段 (已重试 3 次): {ve}"
                ) from ve
        screenplay_data = parsed.model_dump()
        explicit_style = normalize_style_choice(video_style)
        analyzed_style = (screenplay_data.get("video_analysis", {}) or {}).get("style", "").strip()
        resolved_style_key = explicit_style or (DEFAULT_VIDEO_STYLE if not analyzed_style else "")
        resolved_style_desc = VIDEO_STYLE_PRESETS.get(resolved_style_key, resolved_style_key) if resolved_style_key else analyzed_style
        if style_hint:
            resolved_style_desc = f"{resolved_style_desc}。{style_hint}" if resolved_style_desc else style_hint

        if explicit_style:
            screenplay_data.setdefault("video_analysis", {})["style"] = resolved_style_desc
            for item in screenplay_data.get("characters", []) or []:
                item["description"] = _replace_style_prefix(item.get("description", ""), resolved_style_desc)
            for item in screenplay_data.get("locations", []) or []:
                item["description"] = _replace_style_prefix(item.get("description", ""), resolved_style_desc)
            for item in screenplay_data.get("props", []) or []:
                item["description"] = _replace_style_prefix(item.get("description", ""), resolved_style_desc)
        elif not analyzed_style:
            screenplay_data.setdefault("video_analysis", {})["style"] = resolved_style_desc

        if not screenplay_data.get("title"):
            screenplay_data["title"] = "untitled"
        screenplay_data.setdefault("_meta", {})
        screenplay_data["_meta"]["source_video_duration_seconds"] = (
            round(detected_duration, 1) if detected_duration else None
        )
        screenplay_data["_meta"]["target_duration_seconds"] = round(total_duration, 1)
        screenplay_data["_meta"]["mode"] = mode.value if isinstance(mode, StoryboardMode) else mode
        screenplay_data["_meta"]["style_source"] = (
            "user_selected" if explicit_style else ("video_analysis" if analyzed_style else "default_fallback")
        )

        if save:
            sp_json, sp_txt = self.screenplay_paths(output_path)
            self.save_screenplay(screenplay_data, sp_json, sp_txt)

        narrative = screenplay_data.get("narrative", "")
        print(f"[StoryboardGen] Step 1 完成: {len(narrative)} 字叙述")

        return screenplay_data

    # ── Step 2: Full pipeline ─────────────────────────────────────

    def generate(
        self,
        video_path: str,
        output_path: str,
        num_scenes: Optional[int] = None,
        total_duration: Optional[float] = None,
        character_ids: Optional[Dict[str, str]] = None,
        mode: StoryboardMode = StoryboardMode.REPLICATE,
        screenplay_data: Optional[Dict[str, Any]] = None,
        style_reference_image: str = "",
        recreate_direction: str = "",
        video_style: str = AUTO_VIDEO_STYLE,
        style_hint: str = "",
    ) -> Dict[str, Any]:
        """Full pipeline: Video → Narrative → Storyboard.

        Step 1 uses multimodal VLM to watch the video and produce narrative.
        Step 2 converts narrative → storyboard.  For video sources, the
        visual context is already captured in video_analysis / characters /
        locations from Step 1.  source_context is left empty because the
        original source is video bytes, not text.

        If *screenplay_data* is provided, skips Step 1.
        """
        self._ensure_not_stopped()
        if screenplay_data is None:
            screenplay_data = self.generate_screenplay(
                video_path=video_path,
                output_path=output_path,
                num_scenes=num_scenes,
                total_duration=total_duration,
                mode=mode,
                recreate_direction=recreate_direction,
                video_style=video_style,
                style_hint=style_hint,
                save=False,
            )

        generated_title = screenplay_data.get("title", "")
        if generated_title and generated_title != "untitled":
            p = Path(output_path)
            output_path = str(p.parent / f"{generated_title}_storyboard.json")
            print(f"[StoryboardGen] 使用 LLM 生成的标题: {generated_title}")

        print(f"\n[StoryboardGen] Step 2: 核心剧情 → 分镜...")

        meta_extra = {}
        if mode:
            meta_extra["mode"] = mode.value if isinstance(mode, StoryboardMode) else mode
        if total_duration and total_duration > 0:
            meta_extra["target_duration_seconds"] = round(total_duration, 1)

        storyboard = self.screenplay_to_storyboard(
            screenplay_data=screenplay_data,
            output_path=output_path,
            source_context="",
            character_ids=character_ids,
            source_label="video_storyboard_gen",
            style_reference_image=style_reference_image,
        )

        if meta_extra:
            storyboard.setdefault("_meta", {}).update(meta_extra)
            self.save_json(storyboard, output_path)

        return storyboard

    # ═════════════════════════════════════════════════════════════════
    #  Screenplay prompt builders
    # ═════════════════════════════════════════════════════════════════

    def _build_screenplay_prompt(
        self,
        num_scenes: Optional[int],
        total_duration: float,
        mode: StoryboardMode = StoryboardMode.REPLICATE,
        recreate_direction: str = "",
        source_duration: Optional[float] = None,
    ) -> str:
        if mode == StoryboardMode.RECREATE:
            return self._build_recreate_screenplay_prompt(
                num_scenes,
                total_duration,
                recreate_direction=recreate_direction,
                source_duration=source_duration,
            )
        return self._build_replicate_screenplay_prompt(
            num_scenes,
            total_duration,
            source_duration=source_duration,
        )

    def _build_replicate_screenplay_prompt(
        self,
        num_scenes: Optional[int],
        total_duration: float,
        source_duration: Optional[float] = None,
    ) -> str:
        drama_requirements = BaseStoryboardEngine._screenplay_drama_requirements(total_duration)
        story_arc_output_rules = BaseStoryboardEngine._screenplay_story_arc_output_rules()
        fewshot_examples = BaseStoryboardEngine._screenplay_fewshot_examples()
        return build_replicate_screenplay_prompt(
            total_duration=total_duration,
            drama_requirements=drama_requirements,
            story_arc_output_rules=story_arc_output_rules,
            fewshot_examples=fewshot_examples,
            source_duration=source_duration or 0.0,
        )

    def _build_recreate_screenplay_prompt(
        self,
        num_scenes: Optional[int],
        total_duration: float,
        recreate_direction: str = "",
        source_duration: Optional[float] = None,
    ) -> str:
        drama_requirements = BaseStoryboardEngine._screenplay_drama_requirements(total_duration)
        story_arc_output_rules = BaseStoryboardEngine._screenplay_story_arc_output_rules()
        fewshot_examples = BaseStoryboardEngine._screenplay_fewshot_examples()
        return build_recreate_screenplay_prompt(
            total_duration=total_duration,
            drama_requirements=drama_requirements,
            story_arc_output_rules=story_arc_output_rules,
            fewshot_examples=fewshot_examples,
            recreate_direction=recreate_direction,
            source_duration=source_duration or 0.0,
        )
