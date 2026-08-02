"""
Low-level API clients (infrastructure layer).

These are NOT agent tools — they are the underlying SDK wrappers that tools
and other modules use to talk to external services (LLM, Seeddance, …).

Provider selection
------------------
LLM:
  LLM_PROVIDER=openai_compatible   (default) — any OpenAI-compatible API
  LLM_PROVIDER=google              — Google Gemini SDK (native)
  LLM_PROVIDER=custom:<name>       — plugin in clients/custom/<name>.py

Image:
  IMAGE_PROVIDER=openai_compatible — OpenAI images.generate / images.edit
  IMAGE_PROVIDER=google            — Gemini native image generation
  IMAGE_PROVIDER=custom:<name>     — plugin in clients/custom/<name>.py

Per-step model override (LLM only):
  LLM_MODEL_<STEP_ID>=<model>      — override model for a specific pipeline step
  LLM_PROVIDER_<STEP_ID>=<prov>    — override provider for a specific pipeline step

  Known step IDs: screenplay_gen, storyboard_gen, video_analysis, prompt_rewrite,
                  scene_rewrite, scene_edit, video_critique, video_select,
                  style_check, transition, metadata_sync
"""

import importlib
import logging
import os
import pkgutil
from pathlib import Path
from typing import Optional


def _env(key: str, default: str = "") -> str:
    """Read config from .env file first, fall back to os.environ.

    This ensures settings changed via the web UI take effect immediately
    without a server restart.
    """
    try:
        from dashboard.env_store import get_env_value
        return get_env_value(key, default)
    except Exception:
        return os.environ.get(key, default)

from .base import (
    ClientError,
    InsufficientCreditsError,
    ContentFilteredError,
    SessionExpiredError,
    GenerationTimeoutError,
    GenerationFailedError,
    VideoClient,
    LLMClient,
    ImageClient,
    call_with_timeout_retry,
)
from .llm_client import (
    LLMClient as _LLMClientImpl,
    LLMTimeoutError,
    ImageGenerationBlockedError,
    ProhibitedContentError,
    llm_cancel_scope,
    cancel_llm_scope,
    get_llm_client_by_backend,
)
# Backward-compatible aliases
GeminiClient = _LLMClientImpl
get_gemini_client = get_llm_client_by_backend
from .seeddance import SeeddanceClient, get_seeddance_client
from .openai_image import OpenAIImageClient, get_openai_image_client

_logger = logging.getLogger("video_agent.clients")

# ── Custom plugin registry ────────────────────────────────────────────────

_CUSTOM_PLUGIN_REGISTRY: dict[str, dict] = {}


def _discover_custom_plugins() -> None:
    """Scan clients/custom/ for modules that declare PLUGIN_TYPE + PLUGIN_CLASS."""
    custom_dir = Path(__file__).parent / "custom"
    if not custom_dir.exists():
        return
    for _, name, _ in pkgutil.iter_modules([str(custom_dir)]):
        if name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f".custom.{name}", package=__name__)
            if hasattr(mod, "PLUGIN_TYPE") and hasattr(mod, "PLUGIN_CLASS"):
                _CUSTOM_PLUGIN_REGISTRY[name] = {
                    "type": mod.PLUGIN_TYPE,
                    "class": mod.PLUGIN_CLASS,
                }
                _logger.debug("Registered custom plugin: %s (type=%s)", name, mod.PLUGIN_TYPE)
        except Exception as exc:
            _logger.warning("Failed to load custom plugin '%s': %s", name, exc)


_discover_custom_plugins()


def _load_custom_plugin(name: str, expected_type: str, **kwargs):
    """Instantiate a custom plugin by name."""
    entry = _CUSTOM_PLUGIN_REGISTRY.get(name)
    if not entry:
        raise ValueError(
            f"Custom plugin '{name}' not found. "
            f"Create clients/custom/{name}.py with PLUGIN_TYPE and PLUGIN_CLASS."
        )
    if entry["type"] != expected_type:
        raise ValueError(
            f"Plugin '{name}' is type '{entry['type']}', expected '{expected_type}'."
        )
    return entry["class"](**kwargs)


# ── Legacy image provider aliases ────────────────────────────────────────

_IMAGE_PROVIDER_ALIASES = {
    "gemini": "google",
    "google": "google",
    "gpt-image-2": "openai_compatible",
    "gpt_image_2": "openai_compatible",
    "gptimage2": "openai_compatible",
}


def normalize_image_provider(provider: Optional[str] = None) -> str:
    raw_value = (provider or _env("IMAGE_PROVIDER", "google")).strip().lower()
    return _IMAGE_PROVIDER_ALIASES.get(raw_value, raw_value)


# ── LLM client factory ────────────────────────────────────────────────────

def get_llm_client(
    provider: Optional[str] = None,
    *,
    step: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> LLMClient:
    """Get an LLM client for the given provider and optional pipeline step.

    Priority for provider: explicit arg > LLM_PROVIDER_<STEP> env > LLM_PROVIDER env
    Priority for model:    explicit arg > LLM_MODEL_<STEP> env    > LLM_MODEL env

    Args:
        provider: Force a specific provider ("openai_compatible", "google", "custom:<name>").
        step:     Pipeline step ID for per-step overrides (e.g. "video_critique").
        model:    Override the model name.
        api_key:  Override the API key.
        base_url: Override the base URL (openai_compatible only).
    """
    step_upper = step.upper() if step else None

    resolved_provider = (
        provider
        or (step_upper and _env(f"LLM_PROVIDER_{step_upper}"))
        or _env("LLM_PROVIDER")
        or _legacy_llm_provider_from_env()
    )

    resolved_model = (
        model
        or (step_upper and _env(f"LLM_MODEL_{step_upper}"))
        or _env("LLM_MODEL")
        or _env("GEMINI_MODEL", "gemini-3-flash-preview")
    )

    resolved_provider = (resolved_provider or "openai_compatible").strip().lower()

    if resolved_provider == "google":
        return _LLMClientImpl(
            api_key=api_key or _env("GEMINI_API_KEY") or _env("LLM_API_KEY"),
            model=resolved_model,
            backend="api_key",
        )

    if resolved_provider == "openai_compatible":
        return _LLMClientImpl(
            api_key=api_key or _env("LLM_API_KEY"),
            model=resolved_model,
            backend="openai_compatible",
            base_url=base_url or _env("LLM_BASE_URL"),
        )

    if resolved_provider.startswith("custom:"):
        plugin_name = resolved_provider.split(":", 1)[1]
        return _load_custom_plugin(plugin_name, "llm", api_key=api_key, model=resolved_model)

    # Legacy: treat as backend name for LLMClient
    return _LLMClientImpl(api_key=api_key, model=resolved_model, backend=resolved_provider)


def _legacy_llm_provider_from_env() -> Optional[str]:
    """Map old GEMINI_BACKEND env var to new LLM_PROVIDER values."""
    backend = _env("GEMINI_BACKEND").strip().lower()
    if backend == "api_key":
        return "google"
    if backend == "openai_compatible":
        return "openai_compatible"
    return None


# ── Image client factory ──────────────────────────────────────────────────

def get_image_client(
    provider: Optional[str] = None,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> ImageClient:
    """Get an image generation client.

    Args:
        provider: "openai_compatible", "google", "custom:<name>".
                  Defaults to IMAGE_PROVIDER env var.
        api_key:  Override the API key.
        model:    Override the model name.
        base_url: Override the base URL (openai_compatible only).
    """
    normalized = normalize_image_provider(provider)

    if normalized == "google":
        return _LLMClientImpl(
            api_key=api_key or _env("GEMINI_API_KEY") or _env("IMAGE_API_KEY"),
            model=model or _env("IMAGE_MODEL", "gemini-3-pro-image-preview"),
            backend="api_key",
        )

    if normalized == "openai_compatible":
        resolved_model = model or _env("IMAGE_MODEL", "gpt-image-2")
        resolved_base = base_url or _env("IMAGE_BASE_URL")
        resolved_key = api_key or _env("IMAGE_API_KEY")
        return OpenAIImageClient(api_key=resolved_key, model=resolved_model, base_url=resolved_base)

    if normalized.startswith("custom:"):
        plugin_name = normalized.split(":", 1)[1]
        return _load_custom_plugin(plugin_name, "image", api_key=api_key, model=model)

    raise ValueError(
        f"Unsupported IMAGE_PROVIDER: {normalized!r}. "
        "Expected one of: openai_compatible, google, custom:<name>"
    )
