"""
BaseTool wrappers for storyboard generation engines.

These thin wrappers adapt the engine classes to the project's tool system,
handling parameter extraction from ``**params`` and returning ``ToolResult``.
"""

import json
import logging
import os
import re
from pathlib import Path

from ..base import BaseTool, ToolResult, ExecutionContext, ToolCategory
from .schemas import StoryboardMode, AUTO_VIDEO_STYLE
from .video_engine import VideoStoryboardEngine
from .novel_engine import NovelStoryboardEngine
from .prompt_engine import PromptStoryboardEngine

_log = logging.getLogger("video_agent.storyboard_gen.tool")


class VideoStoryboardGen(BaseTool):
    """Analyse a reference video and generate a structured storyboard JSON."""

    @property
    def name(self) -> str:
        return "video_storyboard_gen"

    @property
    def description(self) -> str:
        return (
            "Analyse a reference video using Gemini VLM and generate a full "
            "storyboard JSON via a two-step pipeline: first produce a "
            "narrative screenplay, then convert it to technical storyboard "
            "with camera/lighting/visual details."
        )

    @property
    def category(self) -> ToolCategory:
        return "editor"

    def execute(self, context: ExecutionContext, **params) -> ToolResult:
        """
        Params:
            video_path      : str   — path to the reference video
            output_path     : str   — (optional) where to save the JSON
            num_scenes      : int   — (optional) desired scene count
            total_duration  : float — (optional) target duration in seconds
            character_ids   : dict  — (optional) {name: sora_id} mapping
            llm_model    : str   — (optional) model name
            mode            : str   — "replicate" (default) or "recreate"
            screenplay_data : dict  — (optional) pre-generated screenplay;
                                      skips Step 1 if provided
            screenplay_only : bool  — if True, only generate screenplay (no storyboard)
        """
        video_path = params.get("video_path")
        if not video_path:
            return ToolResult(success=False, error="video_path is required")

        output_path = params.get("output_path")
        if not output_path:
            output_dir = context.output_dir or "."
            video_stem = Path(video_path).stem
            output_path = str(
                Path(output_dir) / f"{video_stem}_storyboard.json"
            )

        num_scenes = params.get("num_scenes")
        total_duration = params.get("total_duration")
        character_ids = params.get("character_ids")
        llm_model = params.get(
            "llm_model", context.model or "gemini-3-flash-preview",
        )

        mode_str = params.get("mode", "replicate")
        try:
            mode = StoryboardMode(mode_str)
        except ValueError:
            return ToolResult(
                success=False,
                error=f"Invalid mode '{mode_str}'. "
                      f"Must be 'replicate' or 'recreate'.",
            )

        screenplay_data = params.get("screenplay_data")
        screenplay_only = params.get("screenplay_only", False)

        try:
            engine = VideoStoryboardEngine(llm_model=llm_model)

            if screenplay_only:
                screenplay = engine.generate_screenplay(
                    video_path=video_path,
                    output_path=output_path,
                    num_scenes=num_scenes,
                    total_duration=total_duration,
                    mode=mode,
                    video_style=params.get("video_style", AUTO_VIDEO_STYLE),
                    style_hint=params.get("style_hint", ""),
                )
                sp_json, sp_txt = engine.screenplay_paths(output_path)
                return ToolResult(
                    success=True,
                    output_path=sp_json,
                    metadata={
                        "mode": mode.value,
                        "step": "screenplay_only",
                        "screenplay_json": sp_json,
                        "screenplay_txt": sp_txt,
                        "narrative_length": len(screenplay.get("narrative", "")),
                    },
                )

            storyboard_data = engine.generate(
                video_path=video_path,
                output_path=output_path,
                num_scenes=num_scenes,
                total_duration=total_duration,
                character_ids=character_ids,
                mode=mode,
                screenplay_data=screenplay_data,
                style_reference_image=params.get("style_reference_image", ""),
                video_style=params.get("video_style", AUTO_VIDEO_STYLE),
                style_hint=params.get("style_hint", ""),
            )

            scenes = storyboard_data.get("storyboard", [])
            characters = storyboard_data.get("characters", [])
            locations = storyboard_data.get("locations", [])

            return ToolResult(
                success=True,
                output_path=output_path,
                metadata={
                    "mode": mode.value,
                    "total_scenes": len(scenes),
                    "total_characters": len(characters),
                    "total_locations": len(locations),
                    "theme": storyboard_data.get(
                        "video_analysis", {},
                    ).get("theme", ""),
                    "style": storyboard_data.get(
                        "video_analysis", {},
                    ).get("style", ""),
                },
            )
        except Exception as e:
            _log.error("VideoStoryboardGen failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=str(e))


class NovelStoryboardGen(BaseTool):
    """Convert a novel chapter into a structured storyboard JSON."""

    @property
    def name(self) -> str:
        return "novel_storyboard_gen"

    @property
    def description(self) -> str:
        return (
            "Read a novel chapter text and generate a full storyboard JSON "
            "via a multi-phase pipeline: global analysis → screenplay → "
            "storyboard with technical details → validate.  Intermediate "
            "screenplay is saved as .json and .txt for review."
        )

    @property
    def category(self) -> ToolCategory:
        return "editor"

    def execute(self, context: ExecutionContext, **params) -> ToolResult:
        """
        Params:
            chapter_text    : str   — the novel chapter content
            chapter_file    : str   — (alt) path to a .txt file
            output_path     : str   — (optional) where to save the JSON
            video_style     : str   — "3d国漫" (default), "真人", etc.
            style_hint      : str   — (optional) additional style notes
            target_duration : float — (optional) target seconds
            title           : str   — (optional) chapter title
            llm_model    : str   — (optional) model override
            screenplay_data : dict  — (optional) pre-generated screenplay
            screenplay_only : bool  — if True, only generate screenplay
        """
        chapter_text = params.get("chapter_text", "")
        chapter_file = params.get("chapter_file", "")

        if not chapter_text and chapter_file:
            if not os.path.exists(chapter_file):
                return ToolResult(
                    success=False,
                    error=f"File not found: {chapter_file}",
                )
            with open(chapter_file, "r", encoding="utf-8") as f:
                chapter_text = f.read()

        if not chapter_text:
            return ToolResult(
                success=False,
                error="chapter_text or chapter_file is required",
            )

        output_path = params.get("output_path")
        if not output_path:
            output_dir = context.output_dir or "."
            raw_title = params.get("title", "novel")
            safe_title = re.sub(r'[^\w\u4e00-\u9fff]', '_', raw_title)[:30]
            output_path = str(
                Path(output_dir) / f"{safe_title}_storyboard.json"
            )

        llm_model = params.get(
            "llm_model",
            context.model or "gemini-3-flash-preview",
        )
        screenplay_data = params.get("screenplay_data")
        screenplay_only = params.get("screenplay_only", False)

        try:
            engine = NovelStoryboardEngine(llm_model=llm_model)

            if screenplay_only:
                screenplay = engine.generate_screenplay(
                    chapter_text=chapter_text,
                    output_path=output_path,
                    video_style=params.get("video_style", AUTO_VIDEO_STYLE),
                    style_hint=params.get("style_hint", ""),
                    target_duration=params.get("target_duration"),
                    title=params.get("title", ""),
                )
                sp_json, sp_txt = engine.screenplay_paths(output_path)
                return ToolResult(
                    success=True,
                    output_path=sp_json,
                    metadata={
                        "step": "screenplay_only",
                        "screenplay_json": sp_json,
                        "screenplay_txt": sp_txt,
                        "narrative_length": len(screenplay.get("narrative", "")),
                    },
                )

            storyboard = engine.generate(
                chapter_text=chapter_text,
                output_path=output_path,
                video_style=params.get("video_style", AUTO_VIDEO_STYLE),
                style_hint=params.get("style_hint", ""),
                target_duration=params.get("target_duration"),
                title=params.get("title", ""),
                screenplay_data=screenplay_data,
                style_reference_image=params.get("style_reference_image", ""),
            )

            meta = storyboard.get("_meta", {})
            return ToolResult(
                success=True,
                output_path=output_path,
                metadata={
                    "total_scenes": meta.get("total_scenes", 0),
                    "total_characters": meta.get("total_characters", 0),
                    "total_locations": meta.get("total_locations", 0),
                    "estimated_duration": meta.get(
                        "estimated_duration_seconds", 0,
                    ),
                    "style": storyboard.get(
                        "video_analysis", {},
                    ).get("style", ""),
                },
            )
        except Exception as e:
            _log.error("NovelStoryboardGen failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=str(e))


class PromptStoryboardGen(BaseTool):
    """Generate a storyboard from a short creative text prompt."""

    @property
    def name(self) -> str:
        return "prompt_storyboard_gen"

    @property
    def description(self) -> str:
        return (
            "Generate a complete storyboard from a short creative description. "
            "Two-step pipeline: the prompt is first expanded into a narrative "
            "screenplay, then converted to a technical storyboard."
        )

    @property
    def category(self) -> ToolCategory:
        return "editor"

    def execute(self, context: ExecutionContext, **params) -> ToolResult:
        """
        Params:
            prompt_text     : str   — the creative description / story idea
            output_path     : str   — (optional) where to save the JSON
            video_style     : str   — "3d国漫" (default), "真人", etc.
            style_hint      : str   — (optional) additional style notes
            target_duration : float — (optional) target seconds (default 60)
            title           : str   — (optional) title
            num_scenes      : int   — (optional) suggested scene count
            llm_model    : str   — (optional) model override
            screenplay_data : dict  — (optional) pre-generated screenplay
            screenplay_only : bool  — if True, only generate screenplay
        """
        prompt_text = params.get("prompt_text", "")
        if not prompt_text:
            return ToolResult(
                success=False,
                error="prompt_text is required",
            )

        output_path = params.get("output_path")
        if not output_path:
            output_dir = context.output_dir or "."
            raw_title = params.get("title", "prompt")
            safe_title = re.sub(r'[^\w\u4e00-\u9fff]', '_', raw_title)[:30]
            output_path = str(
                Path(output_dir) / f"{safe_title}_storyboard.json"
            )

        llm_model = params.get(
            "llm_model",
            context.model or "gemini-3-flash-preview",
        )
        screenplay_data = params.get("screenplay_data")
        screenplay_only = params.get("screenplay_only", False)

        try:
            engine = PromptStoryboardEngine(llm_model=llm_model)

            if screenplay_only:
                screenplay = engine.generate_screenplay(
                    prompt_text=prompt_text,
                    output_path=output_path,
                    video_style=params.get("video_style", AUTO_VIDEO_STYLE),
                    style_hint=params.get("style_hint", ""),
                    target_duration=params.get("target_duration"),
                    title=params.get("title", ""),
                    num_scenes=params.get("num_scenes"),
                )
                sp_json, sp_txt = engine.screenplay_paths(output_path)
                return ToolResult(
                    success=True,
                    output_path=sp_json,
                    metadata={
                        "step": "screenplay_only",
                        "screenplay_json": sp_json,
                        "screenplay_txt": sp_txt,
                        "narrative_length": len(screenplay.get("narrative", "")),
                    },
                )

            storyboard = engine.generate(
                prompt_text=prompt_text,
                output_path=output_path,
                video_style=params.get("video_style", AUTO_VIDEO_STYLE),
                style_hint=params.get("style_hint", ""),
                target_duration=params.get("target_duration"),
                title=params.get("title", ""),
                num_scenes=params.get("num_scenes"),
                screenplay_data=screenplay_data,
                style_reference_image=params.get("style_reference_image", ""),
            )

            meta = storyboard.get("_meta", {})
            return ToolResult(
                success=True,
                output_path=output_path,
                metadata={
                    "total_scenes": meta.get("total_scenes", 0),
                    "total_characters": meta.get("total_characters", 0),
                    "total_locations": meta.get("total_locations", 0),
                    "estimated_duration": meta.get(
                        "estimated_duration_seconds", 0,
                    ),
                    "style": storyboard.get(
                        "video_analysis", {},
                    ).get("style", ""),
                },
            )
        except Exception as e:
            _log.error("PromptStoryboardGen failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=str(e))
