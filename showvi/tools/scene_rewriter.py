"""
Scene rewriter tool.
Heavyweight fix: modifies the storyboard scene itself.
"""

import json
from collections import Counter
from typing import Dict, Any, List

from .base import BaseTool, ToolResult, ExecutionContext, ToolCategory
from clients import get_llm_client


class SceneRewriter(BaseTool):
    """Modify the storyboard scene itself to make it achievable for current AI video models."""

    @property
    def name(self) -> str:
        return "rewrite_scene"

    @property
    def description(self) -> str:
        return "Rewrite the storyboard scene (simplify/replace/split) when the scene is fundamentally too complex."

    @property
    def category(self) -> ToolCategory:
        return "rewriter"

    def execute(self, context: ExecutionContext, **params) -> ToolResult:
        scene_data = params.get("scene_data", {})
        failure_history = params.get("failure_history", [])
        action = params.get("action", "simplify")
        model = params.get("model", context.model)

        print(f"[SCENE REWRITER] Rewriting storyboard scene (action: {action})...")

        try:
            client = get_llm_client(step="scene_rewrite")
            failure_context = _build_failure_context(failure_history)

            from prompts.rewriter import SCENE_REWRITER_SYSTEM

            user_message = (
                f"{failure_context}\n\n"
                f"# Current Scene (Failing Repeatedly)\n"
                f"Scene Number: {scene_data.get('scene_number')}\n"
                f"Plot: {scene_data.get('plot_description')}\n"
                f"Visual: {scene_data.get('visual_description')}\n"
                f"Characters: {', '.join(scene_data.get('characters_in_scene', []))}\n"
                f"Duration: {scene_data.get('duration')}\n"
                f"Camera: {scene_data.get('camera_angle')}\n\n"
                f"# Your Task\nAction: {action}\n\n"
                "Please rewrite this scene to make it more achievable."
            )

            scene_rewrite_schema = {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["simplify", "replace", "split"]},
                    "scenes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "scene_number": {"type": "integer"},
                                "plot_description": {"type": "string"},
                                "visual_description": {"type": "string"},
                                "characters_in_scene": {"type": "array", "items": {"type": "string"}},
                                "dialogue": {"type": "string"},
                                "duration": {"type": "string"},
                                "camera_angle": {"type": "string"},
                                "mood": {"type": "string"},
                                "lighting": {"type": "string"},
                            },
                            "required": ["scene_number", "plot_description", "visual_description"],
                        },
                    },
                    "changes_made": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                },
                "required": ["operation", "scenes", "changes_made", "rationale"],
            }

            response_text = client.generate_text(
                prompt=user_message,
                system_instruction=SCENE_REWRITER_SYSTEM,
                temperature=0.7,
                response_schema=scene_rewrite_schema,
                model=model,
            )

            result = json.loads(response_text)
            print(f"[SCENE REWRITER] Done ({result['operation']}), {len(result['scenes'])} scene(s)")

            return ToolResult(success=True, metadata=result)

        except Exception as e:
            return ToolResult(success=False, error=str(e))


def _build_failure_context(failure_history: list) -> str:
    if not failure_history:
        return "No specific failure information available."

    parts = ["# Previous Failures\n"]
    for i, attempt in enumerate(failure_history[-5:], 1):
        parts.append(f"\n## Attempt {i}")
        parts.append(f"Tool used: {attempt.get('tool_used', 'unknown')}")
        critique = attempt.get("critique_result", {})
        if critique:
            parts.append(f"Quality score: {critique.get('overall_score', 'N/A')}/10")
            issues = critique.get("critical_issues", [])
            if issues:
                parts.append(f"Critical issues: {', '.join(issues)}")
            fb = critique.get("feedback", "")
            if fb:
                parts.append(f"Feedback: {fb[:200]}")
        err = attempt.get("error_message")
        if err:
            parts.append(f"Error: {err}")

    all_issues: List[str] = []
    for attempt in failure_history:
        all_issues.extend(attempt.get("critique_result", {}).get("critical_issues", []))
    if all_issues:
        parts.append("\n# Recurring Problems")
        for issue, count in Counter(all_issues).most_common(3):
            parts.append(f"- {issue} (occurred {count}x)")

    return "\n".join(parts)
