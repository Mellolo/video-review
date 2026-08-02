"""Shared base classes, exceptions, and utilities for all API clients."""

import logging
import time
from typing import Dict, List, Protocol, runtime_checkable, Callable, Any, Optional, Sequence

logger = logging.getLogger("video_agent.clients")


# ── Shared Exception Hierarchy ────────────────────────────────────────────

class ClientError(Exception):
    """Base exception for all client errors."""

class InsufficientCreditsError(ClientError):
    """Account has insufficient credits/quota."""

class ContentFilteredError(ClientError):
    """Content was blocked by safety/content filters."""

class SessionExpiredError(ClientError):
    """Session/authentication has expired."""

class GenerationTimeoutError(ClientError):
    """Generation timed out."""

class GenerationFailedError(ClientError):
    """Generation failed for non-specific reasons."""


# ── Shared Retry Utility ─────────────────────────────────────────────────

DEFAULT_RETRY_DELAYS: Sequence[float] = (2, 5, 10)

def call_with_timeout_retry(
    fn: Callable[..., Any],
    *,
    max_retries: int = 3,
    retry_delays: Sequence[float] = DEFAULT_RETRY_DELAYS,
    timeout_exceptions: tuple = (),
    logger_name: str = "video_agent.clients",
) -> Any:
    """Generic retry wrapper for API calls that may timeout.

    Calls fn() and retries on timeout_exceptions with exponential backoff.
    """
    _logger = logging.getLogger(logger_name)
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except timeout_exceptions as e:
            last_err = e
            if attempt < max_retries:
                delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                _logger.warning(f"Timeout (attempt {attempt + 1}/{max_retries + 1}), retrying in {delay}s: {e}")
                time.sleep(delay)
            else:
                _logger.error(f"All {max_retries + 1} attempts failed: {e}")
                raise
    raise last_err  # should not reach here


# ── Video Client Protocol ─────────────────────────────────────────────────

@runtime_checkable
class VideoClient(Protocol):
    """Protocol defining the interface for video generation clients."""

    def get_capabilities(self) -> dict:
        """Return client capabilities (supported features, limits, etc.)."""
        ...

    def close(self) -> None:
        """Clean up resources."""
        ...


# ── LLM Client Protocol ───────────────────────────────────────────────────

@runtime_checkable
class LLMClient(Protocol):
    """Protocol defining the interface for LLM/VLM clients.

    All LLM providers (Google, OpenAI-compatible, custom) must implement this.
    The ``step`` concept is handled at the factory level (get_llm_client),
    not here — individual clients just execute the call.
    """

    def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 3,
    ) -> str:
        """Generate a text response from a prompt."""
        ...

    def generate_with_vision(
        self,
        text_prompt: str,
        image_paths: List[str],
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
        response_format: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 3,
    ) -> str:
        """Generate a response using text + image inputs (VLM)."""
        ...

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 3,
    ) -> str:
        """OpenAI-style chat completion."""
        ...


# ── Image Client Protocol ─────────────────────────────────────────────────

@runtime_checkable
class ImageClient(Protocol):
    """Protocol defining the interface for image generation clients.

    All image providers (Google, OpenAI-compatible, custom) must implement this.
    """

    def generate_image(
        self,
        prompt: str,
        aspect_ratio: str = "16:9",
        image_size: str = "2K",
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 1,
        reference_images: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> bytes:
        """Generate an image from a text prompt, returning raw bytes."""
        ...
