"""
Tools package — self-describing tools for the Video Agent system.

To register a new tool, create a BaseTool subclass in this package and add it
to ``ALL_TOOL_CLASSES``.  The agent auto-discovers capabilities from there.
"""

from .base import BaseTool, ToolResult, ExecutionContext, ToolCategory
from .seeddance import SeeddanceImageToVideo
from .image_gen import ImageGen
from .critic import GeminiCritic
from .prompt_rewriter import PromptRewriter
from .scene_rewriter import SceneRewriter
from .video_concat import VideoConcat
from .storyboard_gen import VideoStoryboardGen, NovelStoryboardGen, StoryboardMode
from .transition_bridge import TransitionBridge
from .style_consistency_checker import verify_image_matches_prompt

from .storyboard_parser import (
    Storyboard, StoryboardScene, Character, Location, load_storyboard,
)

ALL_TOOL_CLASSES = [
    # generators
    SeeddanceImageToVideo,
    ImageGen,
    # evaluators
    GeminiCritic,
    # rewriters
    PromptRewriter,
    SceneRewriter,
    # post-processing
    VideoConcat,
    # transition
    TransitionBridge,
    # analysis
    VideoStoryboardGen,
    NovelStoryboardGen,
]


def build_tool_registry() -> dict[str, BaseTool]:
    """Instantiate all tools and return a {name: instance} dict."""
    registry: dict[str, BaseTool] = {}
    for cls in ALL_TOOL_CLASSES:
        tool = cls()
        registry[tool.name] = tool
    return registry
