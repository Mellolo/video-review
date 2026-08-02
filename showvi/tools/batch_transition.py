"""
Batch creation mode — transition unit generator.

Given a list of semantic groups (WorkUnits), generates additional "transition"
WorkUnits that bridge consecutive groups.  Each transition unit takes the
latter-half scenes of group[i] and the first-half scenes of group[i+1],
providing overlap footage for human editors.

Usage:
    transition_units = generate_transition_units(
        work_units=existing_units,
        storyboard=storyboard,
        model="gemini-3-flash-preview",
    )
    all_units = interleave_with_transitions(existing_units, transition_units)
"""

import logging
import math
from typing import List, Optional

from models import WorkUnit
from tools.storyboard_parser import Storyboard

_log = logging.getLogger("video_agent.batch_transition")


def _half_split(scene_numbers: List[int]) -> tuple[List[int], List[int]]:
    """Split scene list into first-half and latter-half with overlap at the middle.

    For [1,2,3,4,5]:
        first_half  = [1,2,3]   (ceil(5/2) = 3 items)
        latter_half = [3,4,5]   (from floor(5/2) onwards)

    The middle scene is included in both halves for smooth overlap.
    """
    n = len(scene_numbers)
    if n <= 1:
        return scene_numbers[:], scene_numbers[:]
    mid = math.ceil(n / 2)
    first_half = scene_numbers[:mid]
    latter_half = scene_numbers[mid - 1:]  # overlap: include mid-1
    return first_half, latter_half


def _compute_duration(
    scene_numbers: List[int],
    storyboard: Storyboard,
) -> float:
    """Sum parsed durations for the given scene numbers."""
    total = 0.0
    scene_map = {s.scene_number: s for s in storyboard.scenes}
    for sn in scene_numbers:
        s = scene_map.get(sn)
        if s:
            total += s.parse_duration_to_seconds()
    return total


def _generate_transition_prompt(
    transition_scenes: List[int],
    storyboard: Storyboard,
    prev_group_name: str = "",
    next_group_name: str = "",
    model: str = "gemini-3-flash-preview",
) -> str:
    """Generate a video prompt for a transition unit using the storyboard's LLM flow."""
    scene_objs = []
    for sn in transition_scenes:
        s = storyboard.get_scene_by_number(sn)
        if s:
            scene_objs.append(s)

    if not scene_objs:
        return ""

    try:
        prompt = storyboard.generate_multi_scene_sora_prompt(
            scene_objs, use_llm=True,
        )
        return prompt
    except Exception as e:
        _log.warning("Transition prompt LLM failed: %s, using template", e)
        return storyboard.generate_multi_scene_sora_prompt(
            scene_objs, use_llm=False,
        )


def generate_transition_units(
    work_units: List[WorkUnit],
    storyboard: Storyboard,
    model: str = "gemini-3-flash-preview",
) -> List[WorkUnit]:
    """Create transition WorkUnits between each consecutive pair of groups.

    Transition unit for (group_i, group_i+1):
        scenes = latter_half(group_i.scenes) + first_half(group_i+1.scenes)

    Returns a list of transition WorkUnits.  The unit_id uses a 1000+ offset
    to avoid collisions with original units.
    """
    if len(work_units) < 2:
        _log.info("Less than 2 groups, no transitions to generate")
        return []

    transitions: List[WorkUnit] = []
    max_existing_id = max(u.unit_id for u in work_units)
    tid_offset = max_existing_id + 1

    for i in range(len(work_units) - 1):
        prev_unit = work_units[i]
        next_unit = work_units[i + 1]

        _, prev_latter = _half_split(prev_unit.scene_numbers)
        next_first, _ = _half_split(next_unit.scene_numbers)

        existing = set()
        transition_scenes = []
        for sn in prev_latter + next_first:
            if sn not in existing:
                transition_scenes.append(sn)
                existing.add(sn)

        if not transition_scenes:
            continue

        duration = _compute_duration(transition_scenes, storyboard)
        duration = max(4.0, min(15.0, duration))

        prev_name = prev_unit.group_name or f"Group {prev_unit.unit_id}"
        next_name = next_unit.group_name or f"Group {next_unit.unit_id}"

        print(f"  [BATCH] Transition {prev_unit.unit_id}→{next_unit.unit_id}: "
              f"scenes {transition_scenes} ({duration:.1f}s)")

        prompt = _generate_transition_prompt(
            transition_scenes, storyboard,
            prev_group_name=prev_name,
            next_group_name=next_name,
            model=model,
        )

        tid = tid_offset + i
        unit = WorkUnit(
            unit_id=tid,
            prompt=prompt,
            original_prompt=prompt,
            duration_seconds=duration,
            scene_numbers=transition_scenes,
            group_name=f"过渡 {prev_name}→{next_name}",
            narrative_summary=f"过渡片段：从「{prev_name}」到「{next_name}」",
        )
        transitions.append(unit)

    _log.info("Generated %d transition unit(s)", len(transitions))
    return transitions


def interleave_with_transitions(
    original_units: List[WorkUnit],
    transition_units: List[WorkUnit],
) -> List[WorkUnit]:
    """Interleave original groups and transition units in narrative order.

    Result: [group1, trans1→2, group2, trans2→3, group3, ...]
    """
    if not transition_units:
        return list(original_units)

    result: List[WorkUnit] = []
    for i, unit in enumerate(original_units):
        result.append(unit)
        if i < len(transition_units):
            result.append(transition_units[i])

    return result
