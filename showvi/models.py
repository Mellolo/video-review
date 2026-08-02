"""
Data models for the Video Director Agent.
Uses Pydantic for type safety and validation.
"""

import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum

from utils.io import load_json, save_embedded_versioned_json


class AttemptStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    IN_PROGRESS = "in_progress"


class GenerationAttempt(BaseModel):
    attempt_id: int
    tool_used: str
    timestamp: datetime = Field(default_factory=datetime.now)
    status: AttemptStatus
    input_params: Dict[str, Any] = Field(default_factory=dict)
    output_path: Optional[str] = None
    critique_result: Optional[Dict[str, Any]] = None
    critique_error: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    max_attempts_hint: Optional[int] = None

    class Config:
        use_enum_values = True


class WorkUnit(BaseModel):
    """
    Unified work unit — abstracts both Scene and SceneSegment.
    The agent only operates on WorkUnits, eliminating duplicate code paths.
    """
    unit_id: int
    prompt: str
    original_prompt: str
    duration_seconds: float
    scene_numbers: List[int] = Field(default_factory=list)
    attempts: List[GenerationAttempt] = Field(default_factory=list)
    escalation_level: int = 1
    is_completed: bool = False
    abandoned_no_video: bool = False  # max attempts exhausted, no usable video on disk
    final_video_path: Optional[str] = None
    reference_image_path: Optional[str] = None
    post_processed: bool = False
    post_process_result: Optional[Dict[str, Any]] = None

    # Manual regeneration / final selection state
    pending_extra_attempts: int = 0
    queued_manual_prompt: Optional[str] = None
    queued_manual_image_ref_assets: Dict[str, Any] = Field(default_factory=dict)
    skip_critic_rewrite_once: bool = False
    final_attempt_id: Optional[int] = None
    final_attempt_locked: bool = False

    # Optional storyboard metadata
    visual_description: Optional[str] = None
    plot_description: Optional[str] = None
    characters_in_scene: List[str] = Field(default_factory=list)
    camera_angle: Optional[str] = None
    mood: Optional[str] = None
    lighting: Optional[str] = None

    # Semantic group metadata (from LLM-based grouping)
    group_name: Optional[str] = None
    narrative_summary: Optional[str] = None

    # Grid image from predecessor segment (for visual continuity)
    prev_segment_grid_image: Optional[str] = None

    def add_attempt(self, attempt: GenerationAttempt):
        self.attempts.append(attempt)

    def get_latest_attempt(self) -> Optional[GenerationAttempt]:
        return self.attempts[-1] if self.attempts else None

    def get_failed_attempts_count(self) -> int:
        return sum(1 for a in self.attempts if a.status == AttemptStatus.FAILED)

    def get_next_attempt_id(self) -> int:
        if not self.attempts:
            return 1
        return max(a.attempt_id for a in self.attempts) + 1

    def consume_pending_extra_attempt(self) -> bool:
        if self.pending_extra_attempts > 0:
            self.pending_extra_attempts -= 1
            return True
        return False

    def has_locked_final_selection(self) -> bool:
        return bool(self.final_attempt_locked and self.final_attempt_id)


class VideoScript(BaseModel):
    title: str
    description: str
    work_units: List[WorkUnit] = Field(default_factory=list)

    def get_next_incomplete(self) -> Optional[WorkUnit]:
        for unit in self.work_units:
            if not unit.is_completed:
                return unit
        return None

    def is_complete(self) -> bool:
        return all(u.is_completed for u in self.work_units)

    def get_completion_progress(self) -> float:
        if not self.work_units:
            return 0.0
        completed = sum(1 for u in self.work_units if u.is_completed)
        return (completed / len(self.work_units)) * 100


class ProjectState(BaseModel):
    project_id: str
    script: VideoScript
    output_directory: str
    storyboard_path: Optional[str] = None
    generated_charsheets: Dict[str, str] = Field(default_factory=dict)
    pending_charsheet_tasks: Dict[str, str] = Field(default_factory=dict)
    """Maps charsheet key (e.g. 'char:陆瑶') → Nano Banana task ID.
    Saved to checkpoint so that on resume we can poll existing tasks
    instead of re-submitting."""
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    total_attempts: int = 0
    successful_units: int = 0

    def update_stats(self):
        self.total_attempts = sum(len(u.attempts) for u in self.script.work_units)
        self.successful_units = sum(1 for u in self.script.work_units if u.is_completed and u.final_video_path)

    def mark_complete(self):
        self.completed_at = datetime.now()
        self.update_stats()

    def get_status_report(self) -> Dict[str, Any]:
        self.update_stats()
        return {
            "project_id": self.project_id,
            "completion_percentage": self.script.get_completion_progress(),
            "total_units": len(self.script.work_units),
            "completed_units": self.successful_units,
            "total_attempts": self.total_attempts,
            "elapsed_time": (datetime.now() - self.started_at).total_seconds(),
            "is_complete": self.script.is_complete(),
        }

    def save_checkpoint(self, checkpoint_path: Optional[str] = None) -> str:
        if checkpoint_path is None:
            checkpoint_path = str(Path(self.output_directory) / "checkpoint.json")
        data = self.model_dump(mode="json")
        save_embedded_versioned_json(checkpoint_path, data, default=str)
        return checkpoint_path

    @classmethod
    def load_checkpoint(cls, checkpoint_path: str) -> "ProjectState":
        data = load_json(checkpoint_path, default={})
        if not isinstance(data, dict):
            raise ValueError(f"Invalid checkpoint payload: {checkpoint_path}")
        return cls.model_validate(data)

    @classmethod
    def find_latest_checkpoint(
        cls,
        output_base: str = "./output",
        storyboard_name: Optional[str] = None,
    ) -> Optional[str]:
        """Find the most recent checkpoint.json under output_base.

        If *storyboard_name* is given, only search inside
        ``output_base/<storyboard_name>/`` so that ``--resume latest``
        matches the storyboard supplied on the command line.
        """
        output_path = Path(output_base)
        if storyboard_name:
            output_path = output_path / storyboard_name
        if not output_path.exists():
            return None
        checkpoints = list(output_path.rglob("checkpoint.json"))
        if not checkpoints:
            return None
        checkpoints.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return str(checkpoints[0])


class AgentDecision(BaseModel):
    reasoning: str
    tool_to_use: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    expected_outcome: str
