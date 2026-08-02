"""usage_tracker — single-user mode stub (no-op)."""

from __future__ import annotations

from contextlib import contextmanager


@contextmanager
def usage_context(user_id: int = 0, step: str = ""):
    yield


def record_api_call(*args, **kwargs) -> None:
    pass


def install_hooks() -> None:
    pass


def get_usage_user_id() -> int:
    return 1


def get_usage_step() -> str:
    return ""
