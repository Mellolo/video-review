"""
Skill Planner — selects the best Skill for a given WorkUnit.

Two modes:
  - Rule-based (default): filter by constraints, sort by priority.
  - LLM-based (optional): send skill descriptions to LLM for selection.
"""

import logging
from typing import Dict, List, Optional

from models import WorkUnit
from pipeline import SkillDefinition
from tools.base import BaseTool

_log = logging.getLogger("video_agent.planner")


class Planner:

    def __init__(
        self,
        skills: Dict[str, SkillDefinition],
        tools: Dict[str, BaseTool],
        use_llm: bool = False,
    ):
        self.skills = skills
        self.tools = tools
        self.use_llm = use_llm

    # ── public API ────────────────────────────────────────────────

    def select_skill(
        self,
        unit: WorkUnit,
        excluded_skills: Optional[List[str]] = None,
    ) -> Optional[SkillDefinition]:
        """
        Pick the best available skill for *unit*.

        1. Filter out skills whose env-vars are missing.
        2. Filter out skills that don't support the unit's duration.
        3. Filter out skills whose tools aren't registered.
        4. Filter out explicitly excluded skills (already failed).
        5. Sort by priority (lower = better).
        """
        excluded = set(excluded_skills or [])
        candidates: List[SkillDefinition] = []

        for skill in self.skills.values():
            if skill.name in excluded:
                _log.debug("  skip %s (excluded)", skill.name)
                continue
            if not skill.is_available():
                _log.debug("  skip %s (missing env vars)", skill.name)
                continue
            if not skill.supports_duration(unit.duration_seconds):
                _log.debug("  skip %s (duration %.1fs out of range)",
                           skill.name, unit.duration_seconds)
                continue
            if not skill.has_required_tools(self.tools):
                _log.debug("  skip %s (missing tools)", skill.name)
                continue
            candidates.append(skill)

        if not candidates:
            _log.warning("No eligible skill for unit %s (%.1fs)",
                         unit.unit_id, unit.duration_seconds)
            return None

        if self.use_llm:
            return self._select_with_llm(unit, candidates)

        candidates.sort(key=lambda s: s.priority)
        selected = candidates[0]
        _log.info("Planner selected: %s (priority=%d) for unit %s",
                  selected.name, selected.priority, unit.unit_id)
        print(f"[PLANNER] Selected: {selected.display_name}")
        return selected

    def get_fallback_skill(
        self,
        current_skill: SkillDefinition,
    ) -> Optional[SkillDefinition]:
        """Return the fallback skill declared by *current_skill*, if available."""
        fallback_name = current_skill.fallback.get("skill")
        if not fallback_name:
            return None
        fallback = self.skills.get(fallback_name)
        if fallback is None:
            _log.warning("Fallback skill '%s' not found", fallback_name)
            return None
        if not fallback.is_available():
            _log.warning("Fallback skill '%s' unavailable (env)", fallback_name)
            return None
        if not fallback.has_required_tools(self.tools):
            _log.warning("Fallback skill '%s' unavailable (tools)", fallback_name)
            return None
        _log.info("Fallback: %s → %s", current_skill.name, fallback.name)
        print(f"[PLANNER] Fallback: {current_skill.display_name} → {fallback.display_name}")
        return fallback

    def get_prompt_tips(self, skill: SkillDefinition) -> str:
        """Return prompt optimization tips for the selected skill."""
        return skill.prompt_tips.strip() if skill.prompt_tips else ""

    # ── LLM mode (placeholder) ───────────────────────────────────

    def _select_with_llm(
        self,
        unit: WorkUnit,
        candidates: List[SkillDefinition],
    ) -> SkillDefinition:
        """Use an LLM to pick the best skill. Falls back to rule-based."""
        # TODO: build prompt from candidate descriptions + unit context,
        #       call Gemini, parse response.
        _log.info("LLM planner not yet implemented, falling back to rules")
        candidates.sort(key=lambda s: s.priority)
        return candidates[0]
