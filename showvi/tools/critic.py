"""
Video quality critic tool.
Uses Gemini VLM to evaluate video against the scene description.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict

from google.genai import types

from .base import BaseTool, ToolResult, ExecutionContext, ToolCategory
from clients import get_llm_client

_logger = logging.getLogger("video_agent.critic")

_CRITIQUE_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_score": {"type": "number", "minimum": 0, "maximum": 10},
        "visual_quality": {"type": "number", "minimum": 0, "maximum": 10},
        "content_accuracy": {"type": "number", "minimum": 0, "maximum": 10},
        "character_fidelity": {"type": "number", "minimum": 0, "maximum": 10},
        "artistic_merit": {"type": "number", "minimum": 0, "maximum": 10},
        "critical_issues": {"type": "array", "items": {"type": "string"}},
        "minor_issues": {"type": "array", "items": {"type": "string"}},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "recommendation": {"type": "string", "enum": ["ACCEPT", "REJECT", "RETRY"]},
        "feedback": {"type": "string"},
        "plot_missing": {"type": "array", "items": {"type": "string"}},
        "dialogue_errors": {"type": "array", "items": {"type": "string"}},
        "quality_issues": {"type": "array", "items": {"type": "string"}},
        "physics_errors": {"type": "array", "items": {"type": "string"}},
        "model_penetration": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "overall_score",
        "visual_quality",
        "content_accuracy",
        "character_fidelity",
        "artistic_merit",
        "critical_issues",
        "recommendation",
        "feedback",
    ],
}


class GeminiCritic(BaseTool):
    """Evaluate video quality using a Vision Language Model."""

    @property
    def name(self) -> str:
        return "critique_video"

    @property
    def description(self) -> str:
        return "Use VLM to evaluate generated video quality against the scene description."

    @property
    def category(self) -> ToolCategory:
        return "evaluator"

    def execute(self, context: ExecutionContext, **params) -> ToolResult:
        video_path = params.get("video_path", "")
        scene_description = params.get("scene_description", context.prompt)
        model = params.get("model", context.model)

        if not video_path:
            return ToolResult(success=False, error="No video_path provided for critique")

        _logger.info(
            "=" * 60 + "\nCritic Input\n  video: %s\n  desc : %s",
            video_path, scene_description,
        )
        print(f"[CRITIC] Analyzing video: {video_path}")

        try:
            video_file = Path(video_path)
            if not video_file.exists():
                raise FileNotFoundError(f"Video file not found: {video_path}")

            file_size_mb = video_file.stat().st_size / (1024 * 1024)

            from prompts.critic import build_critique_prompt
            system_prompt = build_critique_prompt(scene_description)

            user_message = (
                f"Please evaluate this video for the following scene:\n\n"
                f"Scene Description: {scene_description}\n\n"
                "Analyze the entire video and provide your assessment. "
                "Respond ONLY with a valid JSON object following the specified format."
            )

            raw_text = self._call_vlm(system_prompt, user_message, str(video_file), model, file_size_mb > 100)
            critique_data = self._parse(raw_text)

            _logger.info("Critic score=%s  rec=%s", critique_data["overall_score"], critique_data["recommendation"])
            print(f"[CRITIC] Score: {critique_data['overall_score']}/10 — {critique_data['recommendation']}")

            return ToolResult(success=True, metadata=critique_data)

        except Exception as e:
            _logger.error("Critique failed: %s", e)
            return ToolResult(success=False, error=str(e))

    # ── Private helpers ──────────────────────────────────────────────

    @staticmethod
    def _call_vlm(system_prompt: str, user_message: str, video_path: str, model: str, use_file_api: bool) -> str:
        client = get_llm_client(step="video_critique")

        if use_file_api:
            raw_text = GeminiCritic._call_vlm_with_file_api(
                client,
                system_prompt=system_prompt,
                user_message=user_message,
                video_path=video_path,
                model=model,
            )
        else:
            raw_text = client.generate_with_video(
                text_prompt=user_message,
                video_paths=[video_path],
                system_instruction=system_prompt,
                temperature=0.3,
                response_schema=_CRITIQUE_SCHEMA,
                model=model,
                timeout_seconds=client.VIDEO_TIMEOUT_SECONDS,
                max_retries=3,
                auto_adjust_fps=True,
                use_low_resolution=True,
            )

        _logger.info("VLM raw response:\n%s", raw_text)
        return raw_text

    @staticmethod
    def _call_vlm_with_file_api(
        client,
        *,
        system_prompt: str,
        user_message: str,
        video_path: str,
        model: str,
    ) -> str:
        uploaded = None
        try:
            uploaded = client.client.files.upload(file=video_path)
            uploaded = client.wait_for_file_processing(
                uploaded.name,
                timeout_seconds=client.VIDEO_FILE_PROCESSING_TIMEOUT_SECONDS,
            )

            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.3,
                response_mime_type="application/json",
                response_json_schema=_CRITIQUE_SCHEMA,
                http_options=client._http_options(client.VIDEO_TIMEOUT_SECONDS),
            )
            response = client._call_with_timeout_retry(
                lambda: client.client.models.generate_content(
                    model=model,
                    contents=[
                        types.Content(parts=[
                            types.Part(file_data=types.FileData(file_uri=uploaded.uri)),
                            types.Part(text=user_message),
                        ])
                    ],
                    config=config,
                ),
                timeout_seconds=client.VIDEO_TIMEOUT_SECONDS,
                max_retries=3,
                action_label=f"Gemini video critique ({model})",
            )
            return response.text
        finally:
            if uploaded is not None:
                try:
                    client.client.files.delete(name=uploaded.name)
                except Exception:
                    pass

    @staticmethod
    def _parse(response_text: str) -> Dict[str, Any]:
        data = json.loads(response_text)
        for field in ["overall_score", "visual_quality", "content_accuracy", "character_fidelity", "artistic_merit"]:
            v = data.get(field, 0)
            if not isinstance(v, (int, float)) or v < 0 or v > 10:
                raise ValueError(f"Invalid score for {field}: {v}")
        if data.get("recommendation") not in ("ACCEPT", "REJECT", "RETRY"):
            raise ValueError(f"Invalid recommendation: {data.get('recommendation')}")
        for key in ("critical_issues", "minor_issues", "strengths", "plot_missing",
                     "dialogue_errors", "quality_issues", "physics_errors", "model_penetration"):
            if not isinstance(data.get(key), list):
                data[key] = []
        return data
