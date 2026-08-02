"""
Transition Bridge tool — seamless scene-to-scene bridging.

For every video segment after the first, this tool:
  1. Extracts the last frame from the previous segment's video (OpenCV).
  2. Asks Gemini to summarise all prior plot into a short paragraph.
  3. Asks Gemini (with the last-frame image) to write a transition prompt.
  4. Assembles the final Seeddance prompt with all @图片N references.

When no previous video exists (first segment), the tool is a transparent
pass-through — prompt and image_paths are returned unchanged.
"""

import logging
import re
from pathlib import Path
from typing import List, Optional

import cv2

from .base import BaseTool, ToolResult, ExecutionContext, ToolCategory
from .seeddance import _inject_image_refs

_log = logging.getLogger("video_agent.transition_bridge")


class TransitionBridge(BaseTool):
    """Prepare a transition-aware prompt that bridges two consecutive video segments."""

    @property
    def name(self) -> str:
        return "transition_bridge"

    @property
    def description(self) -> str:
        return (
            "Bridge consecutive video segments by extracting the previous "
            "segment's last frame, summarising prior plot via Gemini, and "
            "assembling a transition-aware Seeddance prompt."
        )

    @property
    def category(self) -> ToolCategory:
        return "rewriter"

    def execute(self, context: ExecutionContext, **params) -> ToolResult:
        image_paths = params.get("image_paths", [])
        if isinstance(image_paths, str):
            image_paths = [image_paths]
        image_paths = list(image_paths)

        characters = params.get("characters")
        locations = params.get("locations")
        props = params.get("props")

        prev_video_path = getattr(context, "prev_video_path", None)
        if not prev_video_path or not Path(prev_video_path).exists():
            _log.info("Unit %s: no previous video — pass-through", context.unit_id)
            print(f"[TRANSITION] Unit {context.unit_id}: first segment, pass-through")
            return ToolResult(
                success=True,
                metadata={
                    "image_paths": image_paths,
                    "assembled_prompt": context.prompt,
                    "characters": list(characters or []),
                    "locations": list(locations or []),
                    "props": list(props or []),
                },
            )

        _log.info(
            "Unit %s: bridging from prev video %s",
            context.unit_id, prev_video_path,
        )
        print(f"[TRANSITION] Unit {context.unit_id}: preparing scene transition...")

        try:
            last_frame_path = self._extract_last_frame(
                prev_video_path, context.output_dir, context.unit_id,
            )
        except Exception as exc:
            _log.error("Last frame extraction failed: %s — pass-through", exc, exc_info=True)
            print(f"[TRANSITION] Last frame extraction failed: {exc} — pass-through")
            return ToolResult(
                success=True,
                metadata={
                    "image_paths": image_paths,
                    "assembled_prompt": context.prompt,
                    "characters": list(characters or []),
                    "locations": list(locations or []),
                    "props": list(props or []),
                },
            )

        try:
            prev_summary = self._summarize_prev_plot(context)
            print(f"[TRANSITION] Prev plot summary: {prev_summary[:120]}")
        except Exception as exc:
            _log.warning("Plot summary failed: %s — using default", exc, exc_info=True)
            print(f"[TRANSITION] Plot summary failed: {exc} — using default")
            prev_summary = "（这是前序剧情：前面的故事刚刚开始。）"

        try:
            transition_text = self._generate_transition(
                context, last_frame_path, prev_summary,
            )
        except Exception as exc:
            _log.warning("Transition generation failed: %s — using default", exc, exc_info=True)
            print(f"[TRANSITION] Transition generation failed: {exc} — using default")
            transition_text = "镜头缓缓推进，场景自然过渡"

        try:
            assembled_prompt, merged_paths, filtered_characters, filtered_locations, filtered_props = self._assemble_prompt(
                original_prompt=context.prompt,
                prev_summary=prev_summary,
                transition_text=transition_text,
                last_frame_path=last_frame_path,
                image_paths=image_paths,
                characters=characters,
                locations=locations,
                props=props,
                context=context,
            )
        except Exception as exc:
            _log.error("Prompt assembly failed: %s — pass-through", exc, exc_info=True)
            print(f"[TRANSITION] Prompt assembly failed: {exc} — pass-through")
            return ToolResult(
                success=True,
                metadata={
                    "image_paths": image_paths,
                    "assembled_prompt": context.prompt,
                    "characters": list(characters or []),
                    "locations": list(locations or []),
                    "props": list(props or []),
                },
            )

        _log.info(
            "Transition assembled:\n"
            "  prev_summary: %s\n"
            "  transition: %s\n"
            "  merged images (%d): %s\n"
            "  prompt (first 300): %s",
            prev_summary[:120],
            transition_text,
            len(merged_paths),
            merged_paths,
            assembled_prompt[:300],
        )
        print(f"[TRANSITION] Summary: {prev_summary[:80]}...")
        print(f"[TRANSITION] Transition: {transition_text}")
        print(f"[TRANSITION] Images: {len(image_paths)} ref + 1 last-frame = {len(merged_paths)}")

        return ToolResult(
            success=True,
                metadata={
                    "image_paths": merged_paths,
                    "assembled_prompt": assembled_prompt,
                    "last_frame_path": last_frame_path,
                    "prev_summary": prev_summary,
                    "transition_text": transition_text,
                    "characters": filtered_characters,
                    "locations": filtered_locations,
                    "props": filtered_props,
                },
        )

    # ------------------------------------------------------------------
    # 1. Extract last frame
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_last_frame(
        video_path: str, output_dir: str, unit_id: int,
    ) -> str:
        out_path = str(Path(output_dir) / f"last_frame_unit{unit_id}.png")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)

        _log.info("Extracting last frame via OpenCV: %s", video_path)
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")

        try:
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total > 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, total - 1)

            ret, frame = cap.read()
            if not ret or frame is None:
                raise RuntimeError(
                    f"Failed to read last frame from {video_path} "
                    f"(total_frames={total})"
                )

            cv2.imwrite(out_path, frame)
        finally:
            cap.release()

        if not Path(out_path).exists():
            raise RuntimeError(f"Last frame file not created: {out_path}")

        print(f"[TRANSITION] Last frame extracted: {out_path}")
        return out_path

    # ------------------------------------------------------------------
    # 2. Summarise prior plot
    # ------------------------------------------------------------------

    @staticmethod
    def _summarize_prev_plot(context: ExecutionContext) -> str:
        storyboard = context.storyboard
        prev_scene_nums = set(getattr(context, "all_prev_scene_numbers", []))

        if not storyboard or not prev_scene_nums:
            _log.info(
                "Unit %s: _summarize_prev_plot — storyboard=%s prev_scene_nums=%s → using default",
                context.unit_id, bool(storyboard), prev_scene_nums,
            )
            return "（这是前序剧情：前面的故事刚刚开始。）"

        # Normalise to int so dict lookup works regardless of JSON type
        prev_scene_nums = {int(n) for n in prev_scene_nums}
        scene_map = {int(s.scene_number): s for s in storyboard.scenes}

        _log.info(
            "Unit %s: _summarize_prev_plot — prev_scene_nums=%s  scene_map_keys=%s",
            context.unit_id, sorted(prev_scene_nums), sorted(scene_map.keys()),
        )

        plot_parts = []
        for sn in sorted(prev_scene_nums):
            s = scene_map.get(sn)
            if s:
                plot_parts.append(f"场景{sn}: {s.plot_description}")
            else:
                _log.warning("Unit %s: scene %s not found in storyboard", context.unit_id, sn)

        if not plot_parts:
            _log.warning(
                "Unit %s: no plot_parts found for prev_scene_nums=%s → using default",
                context.unit_id, sorted(prev_scene_nums),
            )
            return "（这是前序剧情：前面的故事刚刚开始。）"

        from clients import get_llm_client
        from prompts.transition_bridge import PREV_PLOT_SUMMARY_SYSTEM

        client = get_llm_client(step="transition")
        user_msg = "以下是前序场景的剧情描述，请总结：\n\n" + "\n".join(plot_parts)

        _log.info("Summarising %d prior scenes for unit %s",
                   len(plot_parts), context.unit_id)

        raw = client.generate_text(
            prompt=user_msg,
            system_instruction=PREV_PLOT_SUMMARY_SYSTEM,
            temperature=0.4,
            model=context.model,
        )

        raw = raw.strip()
        if not raw.startswith("（"):
            raw = f"（这是前序剧情：{raw}）"
        if not raw.endswith("）"):
            raw = raw.rstrip("）") + "）"

        return raw

    # ------------------------------------------------------------------
    # 3. Generate transition prompt via Gemini VLM
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_transition(
        context: ExecutionContext,
        last_frame_path: str,
        prev_summary: str,
    ) -> str:
        from clients import get_llm_client
        from prompts.transition_bridge import TRANSITION_PROMPT_SYSTEM

        client = get_llm_client(step="transition")

        user_msg = (
            "以下内容是我为视频生成写的提示词：\n"
            f"{prev_summary}\n"
            "以下是后续剧情，从这里开始生成：\n"
            f"【】{context.prompt}\n"
            "【】之前的场景如我提供给你的图片，需要你联系前后情景的内容，"
            "需要你在【】中写一段简短的提示词，"
            "使得前后两个场景之间能够丝滑、逻辑合理地过渡。"
        )

        _log.info("Generating transition prompt for unit %s", context.unit_id)

        raw = client.generate_with_vision(
            text_prompt=user_msg,
            image_paths=[last_frame_path],
            system_instruction=TRANSITION_PROMPT_SYSTEM,
            temperature=0.5,
            model=context.model,
        )

        raw = raw.strip()
        match = re.search(r"【(.+?)】", raw)
        if match:
            return match.group(1)
        if raw:
            return raw.strip("【】")
        return "镜头缓缓推进，场景自然过渡"

    # ------------------------------------------------------------------
    # 4. Assemble the final prompt + merged image_paths
    # ------------------------------------------------------------------

    @staticmethod
    def _assemble_prompt(
        original_prompt: str,
        prev_summary: str,
        transition_text: str,
        last_frame_path: str,
        image_paths: List[str],
        characters: Optional[list],
        locations: Optional[list],
        props: Optional[list],
        context: ExecutionContext,
    ) -> tuple[str, List[str], List[str], List[str], List[str]]:
        characters = list(characters or [])
        locations = list(locations or [])
        props = list(props or [])

        characters_in_group = None
        locations_in_group = None
        props_in_group = None
        if context.storyboard and hasattr(context.storyboard, "scenes"):
            scene_numbers = set(context.scene_numbers or [])
            if scene_numbers:
                chars_set = set()
                locs_set = set()
                props_set = set()
                for s in context.storyboard.scenes:
                    if s.scene_number in scene_numbers:
                        chars_set.update(s.characters_in_scene)
                        loc = getattr(s, "scene_location", "")
                        if loc:
                            locs_set.add(loc)
                        for p in getattr(s, "props_in_scene", []):
                            if p:
                                props_set.add(p)
                characters_in_group = chars_set
                locations_in_group = locs_set
                props_in_group = props_set

        n_chars = len(characters)
        n_locs = len(locations)
        n_props = len(props)

        if characters_in_group is not None and characters:
            char_paths = image_paths[:n_chars]
            filtered_chars = [
                (name, path) for name, path in zip(characters, char_paths)
                if name in characters_in_group
            ]
            if filtered_chars:
                characters = [name for name, _ in filtered_chars]
                char_paths_filtered = [path for _, path in filtered_chars]
            else:
                characters = []
                char_paths_filtered = []
        else:
            char_paths_filtered = image_paths[:n_chars] if characters else []

        if locations_in_group is not None and locations:
            loc_paths = image_paths[n_chars:n_chars + n_locs]
            filtered_locs = [
                (name, path) for name, path in zip(locations, loc_paths)
                if name in locations_in_group
            ]
            if filtered_locs:
                locations = [name for name, _ in filtered_locs]
                loc_paths_filtered = [path for _, path in filtered_locs]
            else:
                locations = []
                loc_paths_filtered = []
        else:
            loc_paths_filtered = image_paths[n_chars:n_chars + n_locs] if locations else []

        if props_in_group is not None and props:
            prop_paths = image_paths[n_chars + n_locs:n_chars + n_locs + n_props]
            filtered_props = [
                (name, path) for name, path in zip(props, prop_paths)
                if name in props_in_group
            ]
            if filtered_props:
                props = [name for name, _ in filtered_props]
                prop_paths_filtered = [path for _, path in filtered_props]
            else:
                props = []
                prop_paths_filtered = []
        else:
            prop_paths_filtered = image_paths[n_chars + n_locs:n_chars + n_locs + n_props] if props else []

        filtered_image_paths = char_paths_filtered + loc_paths_filtered + prop_paths_filtered
        n_ref = len(filtered_image_paths)

        prompt_with_refs = _inject_image_refs(
            original_prompt, n_ref,
            characters=characters,
            characters_in_group=characters_in_group,
            locations=locations,
            locations_in_group=locations_in_group,
            props=props,
            props_in_group=props_in_group,
        )

        last_frame_idx = n_ref + 1
        merged_paths = filtered_image_paths + [last_frame_path]

        assembled = (
            f"{prev_summary}\n"
            f"以下是后续剧情，从这里开始生成：\n"
            f"剧情的第一幕要先从@图片{last_frame_idx}这个场景开始，"
            f"{transition_text} {prompt_with_refs}"
        )

        return assembled, merged_paths, characters, locations, props
