"""
Base classes for self-describing tools.

Every tool carries its own metadata (name, description, category) so the
agent can auto-discover capabilities and dynamically build LLM prompts.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, Any, Optional, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    pass

ToolCategory = Literal[
    "generator",
    "evaluator",
    "rewriter",
    "editor",
    "post_processor",
]


@dataclass
class ToolResult:
    """Standardized result returned by every tool."""
    success: bool
    output_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class ExecutionContext:
    """Shared context passed to tools during execution.

    For per-unit tools (generator / evaluator / rewriter), fields like
    ``unit_id`` and ``attempt_number`` are meaningful.  Project-level tools
    (editor / post_processor) may ignore them and receive their real
    parameters through ``**params`` in ``execute()``.
    """
    output_dir: str
    unit_id: int = 0
    attempt_number: int = 0
    prompt: str = ""
    duration_seconds: float = 0.0
    reference_image_path: Optional[str] = None
    manual_image_ref_assets: Dict[str, Any] = field(default_factory=dict)
    storyboard: Any = None
    model: str = "gemini-3-pro-preview"
    scene_numbers: list = field(default_factory=list)
    config: Any = None  # AppConfig instance for accessing global settings
    prev_video_path: Optional[str] = None
    all_prev_scene_numbers: list = field(default_factory=list)
    runtime_overrides: Dict[str, Any] = field(default_factory=dict)
    prev_segment_grid_image: Optional[str] = None
    on_task_submitted: Optional[Callable[[dict], None]] = None  # callback({"history_id":..., ...}) for crash recovery


class BaseTool(ABC):
    """
    Abstract base for all tools.

    Subclasses must define ``name``, ``description``, ``category`` and
    ``execute``.  The agent uses these properties to auto-build the LLM
    system prompt and decision schema — no manual sync required.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        ...

    @property
    @abstractmethod
    def category(self) -> ToolCategory:
        ...

    @abstractmethod
    def execute(self, context: ExecutionContext, **params) -> ToolResult:
        ...

    def to_prompt_description(self) -> str:
        return f"`{self.name}` - {self.description}"
