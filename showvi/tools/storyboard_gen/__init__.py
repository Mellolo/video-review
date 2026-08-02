"""
Storyboard generation package — unified entry point for all storyboard
generators (video, novel, prompt, and future input types).

Pipeline:  Source (video / novel / prompt) → Screenplay → Storyboard

Architecture:
  schemas.py        — shared Pydantic models (incl. Screenplay + Storyboard schemas)
  validation.py     — shared validation & auto-fix logic
  base_engine.py    — abstract base engine with shared screenplay→storyboard conversion
  video_engine.py   — VideoStoryboardEngine   (video → screenplay → storyboard)
  novel_engine.py   — NovelStoryboardEngine   (novel text → screenplay → storyboard)
  prompt_engine.py  — PromptStoryboardEngine  (text prompt → screenplay → storyboard)
  tool.py           — BaseTool wrappers for the agent system
"""

from .tool import VideoStoryboardGen, NovelStoryboardGen, PromptStoryboardGen
from .schemas import (
    StoryboardMode,
    VIDEO_STYLE_PRESETS,
    DEFAULT_VIDEO_STYLE,
    ScreenplaySchema,
    SegmentNarrative,
)
from .video_engine import VideoStoryboardEngine
from .novel_engine import NovelStoryboardEngine
from .prompt_engine import PromptStoryboardEngine

# Backward-compatible aliases
StoryboardGenerationEngine = VideoStoryboardEngine
NovelStoryboardGenEngine = NovelStoryboardEngine

__all__ = [
    "VideoStoryboardGen",
    "NovelStoryboardGen",
    "PromptStoryboardGen",
    "StoryboardMode",
    "VIDEO_STYLE_PRESETS",
    "DEFAULT_VIDEO_STYLE",
    "ScreenplaySchema",
    "SegmentNarrative",
    "VideoStoryboardEngine",
    "NovelStoryboardEngine",
    "PromptStoryboardEngine",
    "StoryboardGenerationEngine",
    "NovelStoryboardGenEngine",
]
