"""
Prompt Storyboard Engine — generate a screenplay + storyboard from a
free-form text description / creative brief.

Two-step pipeline:
  Step 1: User prompt → Screenplay  (characters, locations, narrative scenes)
  Step 2: Screenplay → Storyboard   (add camera, lighting, visual_description)

Unlike the video and novel engines which adapt existing material, this engine
creates original content entirely from a short creative prompt.
"""

import logging
from typing import Dict, Any, Optional

from .base_engine import BaseStoryboardEngine
from .schemas import (
    ScreenplaySchema, ScreenplaySchemaWithDuration,
    AUTO_VIDEO_STYLE, DEFAULT_VIDEO_STYLE,
    flatten_json_schema, resolve_video_style,
)
from prompts.prompt_engine import build_prompt_engine_system_prompt, build_quickchat_system_prompt

_log = logging.getLogger("video_agent.storyboard_gen.prompt")


class PromptStoryboardEngine(BaseStoryboardEngine):
    """Generates a complete screenplay from a short creative description."""

    # ── Step 1: Prompt → Screenplay ───────────────────────────────

    def generate_screenplay(
        self,
        prompt_text: str,
        output_path: str,
        video_style: str = AUTO_VIDEO_STYLE,
        style_hint: str = "",
        target_duration: Optional[float] = None,
        title: str = "",
        num_scenes: Optional[int] = None,
        save: bool = True,
    ) -> Dict[str, Any]:
        """Generate a narrative screenplay from a creative brief.

        Args:
            prompt_text: The user's story idea / creative description.
            output_path: Storyboard output path (screenplay paths are derived).
            video_style: Style preset key or free-form description.
            style_hint: Additional style notes appended to the preset.
            target_duration: Target video length in seconds (default 60).
            title: Optional title for the screenplay.
            num_scenes: Suggested scene count (default: auto).
            save: Whether to write intermediate .json / .txt files.

        Returns:
            The screenplay dict.
        """
        from clients import get_llm_client

        prompt_text = prompt_text.strip()
        if len(prompt_text) < 5:
            raise ValueError("创意描述过短，至少5个字")

        resolved_style_key, resolved_style, style_from_user = resolve_video_style(
            explicit_style=video_style,
            text_candidates=[prompt_text, title],
        )
        if style_hint:
            resolved_style = f"{resolved_style}。{style_hint}"

        # 是否为一句话成片模式：调用方没有传 target_duration（None）
        quickchat_mode = target_duration is None
        if quickchat_mode:
            target_duration = 60.0

        style_source = "用户指定" if style_from_user else (
            "文本解析" if resolved_style_key != DEFAULT_VIDEO_STYLE else "默认兜底"
        )
        print(f"[PromptGen] 创意: {prompt_text[:80]}{'...' if len(prompt_text) > 80 else ''}")
        print(f"[PromptGen] 风格: {resolved_style_key or DEFAULT_VIDEO_STYLE} ({style_source}) → {resolved_style[:60]}...")
        print(f"[PromptGen] 时长: {target_duration:.0f}s {'(默认，将由LLM解析)' if quickchat_mode else '(用户指定)'}")

        if quickchat_mode:
            system_prompt = self._build_quickchat_system_prompt(
                resolved_style=resolved_style,
                default_duration=target_duration,
                num_scenes=num_scenes,
            )
            active_schema = ScreenplaySchemaWithDuration
        else:
            system_prompt = self._build_system_prompt(
                resolved_style=resolved_style,
                target_duration=target_duration,
                num_scenes=num_scenes,
            )
            active_schema = ScreenplaySchema

        user_msg = f"请根据以下创意描述，创作一个完整的剧本：\n\n{prompt_text}"

        print("[PromptGen] Step 1: 生成剧本...")
        self._ensure_not_stopped()
        client = get_llm_client(step="screenplay_gen")
        result = self._call_llm_with_retry(
            client, user_msg, system_prompt,
            schema=flatten_json_schema(active_schema.model_json_schema()),
            temperature=0.7,
            max_retries=3,
            response_model=active_schema,
        )

        parsed = active_schema.model_validate_json(result)
        screenplay_data = parsed.model_dump()
        if title:
            screenplay_data["title"] = title
        elif not screenplay_data.get("title"):
            screenplay_data["title"] = "untitled"
        screenplay_data.setdefault("_meta", {})

        # 一句话成片模式：用 LLM 解析出的时长覆盖默认值
        if quickchat_mode:
            llm_duration = screenplay_data.get("requested_duration_seconds")
            if llm_duration and isinstance(llm_duration, (int, float)) and llm_duration > 0:
                print(f"[PromptGen] LLM 从文本解析到时长: {llm_duration:.0f}s（覆盖默认 {target_duration:.0f}s）")
                target_duration = float(llm_duration)

        screenplay_data["_meta"]["target_duration_seconds"] = round(target_duration, 1)

        if save:
            sp_json, sp_txt = self.screenplay_paths(output_path)
            self.save_screenplay(screenplay_data, sp_json, sp_txt)

        narrative = screenplay_data.get("narrative", "")
        print(f"[PromptGen] Step 1 完成: "
              f"{len(screenplay_data.get('characters', []))} 角色, "
              f"{len(screenplay_data.get('locations', []))} 场景, "
              f"{len(narrative)} 字叙述")

        return screenplay_data

    # ── Step 2: Full pipeline ─────────────────────────────────────

    def generate(
        self,
        prompt_text: str,
        output_path: str,
        video_style: str = AUTO_VIDEO_STYLE,
        style_hint: str = "",
        target_duration: Optional[float] = None,
        title: str = "",
        num_scenes: Optional[int] = None,
        screenplay_data: Optional[Dict[str, Any]] = None,
        style_reference_image: str = "",
    ) -> Dict[str, Any]:
        """Full pipeline: Prompt → Screenplay → Storyboard.

        If *screenplay_data* is provided, skips Step 1.
        """
        self._ensure_not_stopped()
        if screenplay_data is None:
            screenplay_data = self.generate_screenplay(
                prompt_text=prompt_text,
                output_path=output_path,
                video_style=video_style,
                style_hint=style_hint,
                target_duration=target_duration,
                title=title,
                num_scenes=num_scenes,
                save=False,
            )
        elif target_duration is not None and target_duration > 0:
            # 已有剧本时，用传入的 target_duration 覆盖 _meta
            screenplay_data.setdefault("_meta", {})["target_duration_seconds"] = round(target_duration, 1)

        generated_title = screenplay_data.get("title", "")
        if generated_title and generated_title != "untitled":
            from pathlib import Path
            p = Path(output_path)
            output_path = str(p.parent / f"{generated_title}_storyboard.json")
            print(f"[PromptGen] 使用 LLM 生成的标题: {generated_title}")

        print(f"\n[PromptGen] Step 2: 核心剧情 + 用户创意 → 分镜...")
        storyboard = self.screenplay_to_storyboard(
            screenplay_data=screenplay_data,
            output_path=output_path,
            source_context=prompt_text,
            source_label="prompt_storyboard_gen",
            style_reference_image=style_reference_image,
        )

        total_scenes = len(storyboard.get("storyboard", []))
        total_dur = storyboard.get("_meta", {}).get(
            "estimated_duration_seconds", 0,
        )
        print(f"\n[PromptGen] 完成: {total_scenes} 个分镜, ~{total_dur:.0f}s")
        return storyboard

    # ═════════════════════════════════════════════════════════════════
    #  Prompt builder
    # ═════════════════════════════════════════════════════════════════

    @staticmethod
    def _build_system_prompt(
        resolved_style: str,
        target_duration: float,
        num_scenes: Optional[int] = None,
    ) -> str:
        drama_requirements = BaseStoryboardEngine._screenplay_drama_requirements(target_duration)
        story_arc_output_rules = BaseStoryboardEngine._screenplay_story_arc_output_rules()
        fewshot_examples = BaseStoryboardEngine._screenplay_fewshot_examples()
        return build_prompt_engine_system_prompt(
            resolved_style=resolved_style,
            target_duration=target_duration,
            drama_requirements=drama_requirements,
            story_arc_output_rules=story_arc_output_rules,
            fewshot_examples=fewshot_examples,
        )

    @staticmethod
    def _build_quickchat_system_prompt(
        resolved_style: str,
        default_duration: float,
        num_scenes: Optional[int] = None,
    ) -> str:
        """一句话成片模式专用 system prompt，让 LLM 自行解析时长。"""
        drama_requirements = BaseStoryboardEngine._screenplay_drama_requirements(default_duration)
        story_arc_output_rules = BaseStoryboardEngine._screenplay_story_arc_output_rules()
        fewshot_examples = BaseStoryboardEngine._screenplay_fewshot_examples()
        return build_quickchat_system_prompt(
            resolved_style=resolved_style,
            default_duration=default_duration,
            drama_requirements=drama_requirements,
            story_arc_output_rules=story_arc_output_rules,
            fewshot_examples=fewshot_examples,
        )
