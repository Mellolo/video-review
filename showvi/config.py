"""
Centralized configuration for the Video Director Agent.
All settings can be overridden via environment variables.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AppConfig:
    """Application-level configuration, loaded from environment / CLI."""

    # --- LLM Provider ---
    # Options: "openai_compatible" (default) | "google" | "custom:<name>"
    llm_provider: str = "openai_compatible"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gemini-3-flash-preview"

    # --- LLM (Google-specific, used when LLM_PROVIDER=google) ---
    gemini_api_key: str = ""
    llm_model: str = "gemini-3-flash-preview"

    # --- Image Generation Provider ---
    # Options: "openai_compatible" | "google"
    image_provider: str = "google"
    image_base_url: str = ""
    image_api_key: str = ""
    image_model: str = "gpt-image-2"

    # --- Per-step LLM model overrides ---
    # Format: llm_model_<step_id> — overrides LLM_MODEL for a specific pipeline step
    # Known steps: screenplay_gen, storyboard_gen, video_analysis, prompt_rewrite,
    #              scene_rewrite, scene_edit, video_critique, video_select,
    #              style_check, transition, metadata_sync
    llm_model_overrides: dict = field(default_factory=dict)

    # --- Video Generation (Seeddance) ---
    seeddance_session_id: str = ""
    seeddance_backend: str = "jimeng"

    # --- Agent Behaviour ---
    max_attempts_per_scene: int = 1
    max_attempts_per_level: int = 2
    auto_escalate: bool = True
    accept_score_threshold: float = 7.0

    # --- Parallelism ---
    parallel_mode: bool = False
    runtime_overrides: dict = field(default_factory=dict)
    max_parallel_tasks: int = 3

    # --- Paths ---
    output_dir: str = "./output"
    asset_library_dir: str = "./asset_library"

    # --- Evaluator ---
    evaluator_name: str = "critique_video"

    # --- Parallel pipeline ---
    parallel_max_attempts: int = 3

    # --- Post-generation video selection ---
    enable_video_selection: bool = True

    # --- Batch creation mode ---
    batch_mode: bool = False

    # --- Transition bridge (scene-to-scene bridging) ---
    enable_transition_bridge: bool = False
    

    @classmethod
    def from_env(cls) -> "AppConfig":
        # Collect per-step model overrides from env
        step_ids = [
            "screenplay_gen", "storyboard_gen", "video_analysis", "prompt_rewrite",
            "scene_rewrite", "scene_edit", "video_critique", "video_select",
            "style_check", "transition", "metadata_sync",
        ]
        overrides = {}
        for step in step_ids:
            val = os.getenv(f"LLM_MODEL_{step.upper()}")
            if val:
                overrides[step] = val

        return cls(
            llm_provider=os.getenv("LLM_PROVIDER", ""),
            llm_base_url=os.getenv("LLM_BASE_URL", ""),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            image_provider=os.getenv("IMAGE_PROVIDER", "google"),
            image_base_url=os.getenv("IMAGE_BASE_URL", ""),
            image_api_key=os.getenv("IMAGE_API_KEY", ""),
            image_model=os.getenv("IMAGE_MODEL", "gpt-image-2"),
            llm_model_overrides=overrides,
            seeddance_session_id=os.getenv("SEEDDANCE_SESSION_ID", ""),
            seeddance_backend=os.getenv("SEEDDANCE_BACKEND", "jimeng"),
            output_dir=os.getenv("OUTPUT_DIR", "./output"),
        )
