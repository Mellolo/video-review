"""
Storyboard Parser and Prompt Generator.
Handles reading storyboard.json and converting scenes to Sora-compatible prompts.

P1: Context-aware prompt generation (prev_context, next_context).

⚠️ 开发注意 — Prompt 字段映射：
  seedance_prompt（JSON）→ visual_description（StoryboardScene 属性）→ sora_prompt
  三个名字是同一份数据。如果 JSON 中同时存在 visual_description 和 seedance_prompt，
  优先读 visual_description（见 load_storyboard 的 fallback 链）。
P2: Segment-type-specific prompt templates (action / montage / closeup / transition / general).
"""

import json
from typing import Dict, List, Any, Optional
from pathlib import Path

from tools.storyboard_gen.validation import ensure_prompt_style_prefix


class Character:
    def __init__(self, name: str, description: str, personality: str,
                 character_id: str, image_path: str = "",
                 voice_description: str = "",
                 _derived_from: str = "",
                 _change_description: str = ""):
        self.name = name
        self.description = description
        self.personality = personality
        self.id = character_id.strip()
        self.image_path = image_path.strip()
        self.voice_description = voice_description.strip()
        self._derived_from = _derived_from
        self._change_description = _change_description

    def get_id_tag(self) -> str:
        if self.id:
            return f"<{self.id}>"
        return ""


class Location:
    def __init__(self, name: str, description: str, image_path: str = "",
                 _derived_from: str = "",
                 _change_description: str = ""):
        self.name = name
        self.description = description
        self.image_path = image_path.strip()
        self._derived_from = _derived_from
        self._change_description = _change_description


class Prop:
    def __init__(self, name: str, description: str, image_path: str = "",
                 _derived_from: str = "",
                 _change_description: str = ""):
        self.name = name
        self.description = description
        self.image_path = image_path.strip()
        self._derived_from = _derived_from
        self._change_description = _change_description


class StoryboardScene:
    def __init__(
        self,
        scene_number: int,
        plot_description: str,
        visual_description: str,
        characters_in_scene: List[str],
        dialogue: str,
        duration: str,
        camera_angle: str,
        mood: str,
        lighting: str,
        scene_location: str = "",
        dialogue_lines: Optional[List[Dict[str, str]]] = None,
        props_in_scene: Optional[List[str]] = None,
    ):
        self.scene_number = scene_number
        self.plot_description = plot_description
        self.visual_description = visual_description
        self.characters_in_scene = characters_in_scene
        self.props_in_scene: List[str] = props_in_scene or []
        self.scene_location = scene_location
        self.dialogue = dialogue
        self.dialogue_lines: List[Dict[str, str]] = dialogue_lines or []
        self.duration = duration
        self.camera_angle = camera_angle
        self.mood = mood
        self.lighting = lighting
        self.sora_prompt = ""

    def parse_duration_to_seconds(self) -> float:
        try:
            s = self.duration.replace("秒", "").replace("seconds", "").replace("second", "").strip()
            return float(s)
        except Exception:
            return 5.0


class Storyboard:
    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self.video_analysis: Dict[str, Any] = {}
        self.characters: Dict[str, Character] = {}
        self.locations: Dict[str, Location] = {}
        self.props: Dict[str, Prop] = {}
        self.scenes: List[StoryboardScene] = []
        self.style_reference_image: str = ""
        self.meta: Dict[str, Any] = {}
        self._load()

    def _load(self):
        with open(self.filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.video_analysis = data.get("video_analysis", {})
        self.style_reference_image = data.get("style_reference_image", "")

        for char_data in data.get("characters", []):
            char = Character(
                name=char_data["name"],
                description=char_data.get("description", ""),
                personality=char_data.get("personality", ""),
                character_id=char_data.get("id", ""),
                image_path=char_data.get("image_path", ""),
                voice_description=char_data.get("voice_description", ""),
                _derived_from=char_data.get("_derived_from", ""),
                _change_description=char_data.get("_change_description", ""),
            )
            self.characters[char.name] = char

        for loc_data in data.get("locations", []):
            loc = Location(
                name=loc_data["name"],
                description=loc_data.get("description", ""),
                image_path=loc_data.get("image_path", ""),
                _derived_from=loc_data.get("_derived_from", ""),
                _change_description=loc_data.get("_change_description", ""),
            )
            self.locations[loc.name] = loc

        for prop_data in data.get("props", []):
            prop = Prop(
                name=prop_data["name"],
                description=prop_data.get("description", ""),
                image_path=prop_data.get("image_path", ""),
                _derived_from=prop_data.get("_derived_from", ""),
                _change_description=prop_data.get("_change_description", ""),
            )
            self.props[prop.name] = prop

        for scene_data in data.get("storyboard", []):
            scene = StoryboardScene(
                scene_number=scene_data["scene_number"],
                plot_description=scene_data.get("plot_description", scene_data.get("narrative_summary", "")),
                visual_description=scene_data.get("visual_description", scene_data.get("seedance_prompt", "")),
                characters_in_scene=scene_data.get("characters_in_scene", []),
                props_in_scene=scene_data.get("props_in_scene", []),
                scene_location=scene_data.get("scene_location", ""),
                dialogue=scene_data.get("dialogue", ""),
                dialogue_lines=scene_data.get("dialogue_lines"),
                duration=scene_data.get("duration", "5秒"),
                camera_angle=scene_data.get("camera_angle", ""),
                mood=scene_data.get("mood", ""),
                lighting=scene_data.get("lighting", ""),
            )
            self.scenes.append(scene)

        self.meta = data.get("_meta", {})

    def save(self):
        data = {
            "video_analysis": self.video_analysis,
            "characters": [
                {
                    "name": c.name, "description": c.description,
                    "personality": c.personality,
                    **({"voice_description": c.voice_description} if c.voice_description else {}),
                    "id": c.id, "image_path": c.image_path,
                }
                for c in self.characters.values()
            ],
            "locations": [
                {"name": loc.name, "description": loc.description, "image_path": loc.image_path}
                for loc in self.locations.values()
            ],
            "props": [
                {"name": p.name, "description": p.description, "image_path": p.image_path}
                for p in self.props.values()
            ],
            "storyboard": [
                {
                    "scene_number": s.scene_number,
                    "plot_description": s.plot_description,
                    "visual_description": s.visual_description,
                    "characters_in_scene": s.characters_in_scene,
                    **({"props_in_scene": s.props_in_scene} if s.props_in_scene else {}),
                    "scene_location": s.scene_location,
                    "dialogue": s.dialogue,
                    **({"dialogue_lines": s.dialogue_lines} if s.dialogue_lines else {}),
                    "duration": s.duration,
                    "camera_angle": s.camera_angle,
                    "mood": s.mood,
                    "lighting": s.lighting,
                }
                for s in self.scenes
            ],
        }
        if self.style_reference_image:
            data["style_reference_image"] = self.style_reference_image
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_scene_by_number(self, scene_number: int) -> Optional[StoryboardScene]:
        for s in self.scenes:
            if s.scene_number == scene_number:
                return s
        return None

    def update_scene(self, scene_number: int, new_data: Dict[str, Any]):
        scene = self.get_scene_by_number(scene_number)
        if scene:
            for key, value in new_data.items():
                if hasattr(scene, key):
                    setattr(scene, key, value)

    # ── Prompt generation ─────────────────────────────────────────────

    def generate_sora_prompt(
        self,
        scene: StoryboardScene,
        use_llm: bool = True,
        prev_context: str = "",
        segment_type: str = "general",
    ) -> str:
        # --- LLM rewrite 已禁用，直接使用原始 seedance_prompt ---
        raw = scene.visual_description
        if raw and len(raw) >= 20:
            scene.sora_prompt = raw
            return raw
        return self._build_template_prompt([scene], segment_type=segment_type)

        # --- 以下为原 LLM rewrite 逻辑（已注释） ---
        # if not use_llm:
        #     return self._build_template_prompt([scene], segment_type=segment_type)
        #
        # from clients import get_llm_client
        #
        # shot_desc = self._build_shot_description(scene, 1)
        # char_replacements = self._build_character_replacements([scene])
        # state_changes = self._detect_state_changes([scene])
        # prompt_text = self._build_rewrite_prompt(
        #     shot_desc, char_replacements,
        #     prev_context=prev_context,
        #     state_changes=state_changes,
        #     segment_type=segment_type,
        # )
        #
        # try:
        #     client = get_llm_client(step="storyboard_gen")
        #     for attempt in range(3):
        #         try:
        #             response = client.generate_text(
        #                 prompt=prompt_text, temperature=0.7, max_tokens=1000, model="gemini-3-flash-preview"
        #             )
        #             result = ensure_prompt_style_prefix(
        #                 response.strip(),
        #                 self.video_analysis.get("style", ""),
        #                 fallback=response.strip(),
        #             )
        #             if result and len(result) >= 20:
        #                 scene.sora_prompt = result
        #                 return result
        #             raise ValueError("Prompt too short")
        #         except Exception as e:
        #             if attempt < 2:
        #                 print(f"[WARNING] LLM error (attempt {attempt + 1}/3): {e}, retrying...")
        #                 import time; time.sleep(1)
        #             else:
        #                 raise
        # except Exception as e:
        #     print(f"[WARNING] LLM failed: {e}, using template")
        #     return self._build_template_prompt([scene], segment_type=segment_type)

    def generate_multi_scene_sora_prompt(
        self,
        scenes: List[StoryboardScene],
        use_llm: bool = True,
        prev_context: str = "",
        next_context: str = "",
        segment_type: str = "general",
    ) -> str:
        # --- LLM rewrite 已禁用，直接拼接原始 seedance_prompt ---
        raw_parts = [s.visual_description for s in scenes if s.visual_description]
        if raw_parts:
            combined = "\n\n".join(raw_parts)
            for s in scenes:
                s.sora_prompt = combined
            return combined
        return self._build_template_prompt(scenes, segment_type=segment_type)

        # --- 以下为原 LLM rewrite 逻辑（已注释） ---
        # if not use_llm:
        #     return self._build_template_prompt(scenes, segment_type=segment_type)
        #
        # from clients import get_llm_client
        #
        # shot_descriptions = [self._build_shot_description(s, i + 1) for i, s in enumerate(scenes)]
        # combined_shots = "\n\n".join(shot_descriptions)
        # char_replacements = self._build_character_replacements(scenes)
        # state_changes = self._detect_state_changes(scenes)
        # prompt_text = self._build_rewrite_prompt(
        #     combined_shots, char_replacements,
        #     prev_context=prev_context,
        #     next_context=next_context,
        #     state_changes=state_changes,
        #     segment_type=segment_type,
        # )
        #
        # try:
        #     client = get_llm_client(step="storyboard_gen")
        #     for attempt in range(3):
        #         try:
        #             response = client.generate_text(
        #                 prompt=prompt_text, temperature=0.7, max_tokens=2000, model="gemini-3-flash-preview"
        #             )
        #             result = ensure_prompt_style_prefix(
        #                 response.strip(),
        #                 self.video_analysis.get("style", ""),
        #                 fallback=response.strip(),
        #             )
        #             if result and len(result) >= 20:
        #                 for s in scenes:
        #                     s.sora_prompt = result
        #                 return result
        #             raise ValueError("Prompt too short")
        #         except Exception as e:
        #             if attempt < 2:
        #                 print(f"[WARNING] LLM error (attempt {attempt + 1}/3): {e}, retrying...")
        #                 import time; time.sleep(1)
        #             else:
        #                 raise
        # except Exception as e:
        #     print(f"[WARNING] Multi-scene LLM failed: {e}, using template")
        #     return self._build_template_prompt(scenes, segment_type=segment_type)

    # ── Private helpers ───────────────────────────────────────────────

    def _get_segment_type_instructions(self, segment_type: str) -> str:
        """Return type-specific instructions for the rewrite prompt."""
        instructions = {
            "action": (
                "【动作段落特别要求】\n"
                "   - 强调速度感和动态模糊，动作流畅连贯\n"
                "   - 镜头跟随人物快速移动，手持摄影感\n"
                "   - 保留冲击力强的视觉关键帧\n"
                "   - 全程无背景音乐，保留动作音效"
            ),
            "montage": (
                "【蒙太奇段落特别要求】\n"
                "   - 将多个短镜头无缝串联成流畅的视觉流\n"
                "   - 每个动作之间用极短的硬切衔接\n"
                "   - 保持整体节奏感，不要让单个镜头过长\n"
                "   - 全程无背景音乐"
            ),
            "closeup": (
                "【特写段落特别要求】\n"
                "   - 聚焦细节：表情、手部动作、道具质感\n"
                "   - 景深浅，背景虚化突出主体\n"
                "   - 光线质感细腻，体现情绪\n"
                "   - 全程无背景音乐"
            ),
            "transition": (
                "【转场段落特别要求】\n"
                "   - 自然流畅的场景过渡，避免突兀\n"
                "   - 可用光效、运动模糊辅助转场\n"
                "   - 衔接前后场景的视觉色调\n"
                "   - 全程无背景音乐"
            ),
            "general": (
                "【通用要求】\n"
                "   - 强调一下全程无背景音乐\n"
                "   - 保持画面风格与整体剧情一致"
            ),
        }
        return instructions.get(segment_type, instructions["general"])

    def _build_rewrite_prompt(
        self,
        combined_shots: str,
        char_replacements: str,
        prev_context: str = "",
        next_context: str = "",
        state_changes: str = "",
        segment_type: str = "general",
    ) -> str:
        context_section = ""
        if prev_context or next_context:
            context_section = "\n【上下文衔接信息（P1）】\n"
            if prev_context:
                context_section += f"{prev_context}\n"
            if next_context:
                context_section += f"{next_context}\n"
            context_section += "请在 Prompt 中体现以上状态，确保角色造型、场景氛围与前后段落衔接自然。\n"

        state_section = ""
        if state_changes:
            state_section = (
                "\n【本段内状态变化（重要）】\n"
                + state_changes
                + "\n请在 Prompt 中明确体现上述变化过程。\n"
            )

        type_instructions = self._get_segment_type_instructions(segment_type)

        return f"""你是一个专业的视频生成 Prompt 专家，擅长将分镜脚本改写为 Sora2 风格的详细 Prompt。
{context_section}{state_section}
【原始分镜脚本】
{combined_shots}

【改写要求】
1. **角色替换规则（非常重要）：**
{char_replacements}

2. **融合镜头逻辑：**
   - 将上述多个镜头的内容自然融合成一个连贯的场景
   - 保留关键的动作序列和视觉细节
   - 按时间顺序组织动作流程

3. {type_instructions}

4. **Sora2 风格要求：**
【参考示例】
示例1:
场景： 悬浮于云海之上的中式杂货铺。摊主卖各种中式早点，无围栏，
主角： @buzhizu.serenewill 长发，全程无音乐。
动作： 主角从云边飞入，因为起晚了的原因，拿了个馒头，没给钱（店主着急喊她给钱），女主着急跳起来就飞走了（没御剑），然后给女主脸部特写，她大喊一声剑来，然后御剑飞行（青剑，大小比例合适。姿势帅气，双脚稳在剑上，双手背负，剑尖朝前），速度顿时提升，手持镜头的感觉，飞到远处。

示例2:
主体：女主@buzhizu.serenewill 长发。男主@buzhizu.silentjunb 全程无音乐。
场景：男主是修仙界的交通执法，在云海之上的亭子里面睡觉，突然女主超速御剑飞驰而过(非常快速飞）。两人出现在同镜头（超音速，青剑，姿势帅气，背负双手，仙子的感觉，双脚稳在剑上，剑尖朝前），飞过后，男主腰带上灵界警报响了两秒，惊醒，男主@buzhizu.silentjunb 生气说到"又是你！"，
女主@buzhizu.serenewill 长发边飞远边调皮回应"记账上"。
男主@buzhizu.silentjunb 马上回应"你分早没了！"
女主听了反而一个超级加速御剑飞走。

现在请改写上述分镜脚本，直接输出改写后的 Sora2 Prompt，不要任何额外解释。
"""

    def _build_template_prompt(
        self, scenes: List[StoryboardScene], segment_type: str = "general"
    ) -> str:
        parts = []
        for i, scene in enumerate(scenes, 1):
            shot_parts = []
            if scene.camera_angle:
                shot_parts.append(scene.camera_angle)
            visual = scene.visual_description
            for char_name in scene.characters_in_scene:
                if char_name in self.characters:
                    tag = self.characters[char_name].get_id_tag()
                    if tag:
                        visual = visual.replace(char_name, f"{char_name} {tag}")
            shot_parts.append(visual)
            if scene.dialogue:
                shot_parts.append(f'dialogue: "{scene.dialogue}"')
            parts.append(f"Shot {i} ({scene.duration}): {', '.join(shot_parts)}")

        type_hints = {
            "action": " Fast cuts, dynamic motion blur, action-focused.",
            "montage": " Quick montage editing, seamless transitions between shots.",
            "closeup": " Shallow depth of field, focus on details and emotions.",
            "transition": " Smooth scene transition, matching color tones.",
        }
        suffix = type_hints.get(segment_type, " No background music.")

        result = ". Then ".join(parts) if len(scenes) > 1 else (parts[0] if parts else "")
        final_prompt = result + suffix if result else ""
        return ensure_prompt_style_prefix(
            final_prompt,
            self.video_analysis.get("style", ""),
            fallback=final_prompt,
        )


def load_storyboard(filepath: str) -> Storyboard:
    return Storyboard(filepath)
