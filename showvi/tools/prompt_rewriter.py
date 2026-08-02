"""
Prompt rewriter tool.
Lightweight fix: refines the Sora prompt based on critic feedback.
"""

import json
import logging
from typing import Dict, Any

from .base import BaseTool, ToolResult, ExecutionContext, ToolCategory
from clients import get_llm_client

_logger = logging.getLogger("video_agent.rewriter")


class PromptRewriter(BaseTool):
    """Refine the Sora prompt based on critic feedback without changing the storyboard."""

    @property
    def name(self) -> str:
        return "rewrite_prompt"

    @property
    def description(self) -> str:
        return "Refine the Sora prompt based on critic feedback (lightweight fix, no storyboard change)."

    @property
    def category(self) -> ToolCategory:
        return "rewriter"

    def execute(self, context: ExecutionContext, **params) -> ToolResult:
        original_prompt = params.get("original_prompt", context.prompt)
        critique_feedback = params.get("critique_feedback", {})
        failure_history = params.get("failure_history", [])
        scene_context = params.get("scene_context", "")
        model = params.get("model", context.model)

        score = critique_feedback.get("overall_score", 0)
        issues = critique_feedback.get("critical_issues", []) + critique_feedback.get("minor_issues", [])
        feedback_text = critique_feedback.get("feedback", "")

        _logger.info(
            "=" * 60
            + "\nRewriter Input"
            "\n  unit_id  : %s"
            "\n  attempt  : %s"
            "\n  score    : %s/10"
            "\n  issues   : %s"
            "\n  feedback : %s"
            "\n  prev_attempts: %d"
            "\n  prompt   : %s",
            context.unit_id,
            context.attempt_number,
            score,
            issues,
            feedback_text,
            len(failure_history),
            original_prompt,
        )
        if scene_context:
            _logger.info("Scene context:\n%s", scene_context)

        print("[PROMPT REWRITER] Refining prompt based on critique...")

        try:
            client = get_llm_client(step="prompt_rewrite")

            from prompts.rewriter import PROMPT_REWRITER_SYSTEM

            scene_block = ""
            if scene_context:
                scene_block = (
                    f"# Original Storyboard (本组对应的原始剧本)\n"
                    f"{scene_context}\n\n"
                )

            user_message = (
                f"{scene_block}"
                f"# Original Prompt\n{original_prompt}\n\n"
                f"# Critique Feedback (Score: {score}/10)\n"
                f"Issues: {', '.join(issues)}\n\n"
                f"Detailed Feedback: {feedback_text}\n\n"
                f"# Previous Attempts\n{len(failure_history)} previous attempts have failed.\n\n"
                "Please refine this prompt to fix the issues while keeping the scene concept intact. "
                "Remember to preserve character ID tags. "
                "Use the original storyboard as ground truth for the scene's intent, "
                "characters, mood, and camera direction."
            )

            _logger.info("LLM request (model=%s):\n%s", model, user_message)

            rewrite_schema = {
                "type": "object",
                "properties": {
                    "original_prompt": {"type": "string"},
                    "rewritten_prompt": {"type": "string"},
                    "changes_made": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                },
                "required": ["original_prompt", "rewritten_prompt", "changes_made", "rationale"],
            }

            response_text = client.generate_text(
                prompt=user_message,
                system_instruction=PROMPT_REWRITER_SYSTEM,
                temperature=0.6,
                response_schema=rewrite_schema,
                model=model,
            )

            _logger.info("LLM raw response:\n%s", response_text)

            result = json.loads(response_text)

            _logger.info(
                "Rewrite result"
                "\n  changes  : %s"
                "\n  rationale: %s"
                "\n  new prompt: %s",
                result["changes_made"],
                result["rationale"],
                result["rewritten_prompt"],
            )
            print(f"[PROMPT REWRITER] Changes: {', '.join(result['changes_made'][:3])}")

            return ToolResult(
                success=True,
                metadata={
                    "rewritten_prompt": result["rewritten_prompt"],
                    "changes_made": result["changes_made"],
                    "rationale": result["rationale"],
                },
            )
        except Exception as e:
            _logger.error("Rewrite failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=str(e))
