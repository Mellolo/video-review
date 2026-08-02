"""Authentication helpers — single-user mode (no login required)."""

from __future__ import annotations

from typing import Optional

from fastapi import Request, WebSocket

from dashboard.user_stub import User, UserPreference, SINGLE_USER, get_single_user_preference


class AuthError(Exception):
    def __init__(self, detail: str = "", status_code: int = 401):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def get_or_create_preference(user: User) -> UserPreference:
    """Return a UserPreference built from current environment variables."""
    return get_single_user_preference()


def get_current_user(request: Request) -> User:
    """Always returns the single local user — no authentication required."""
    return SINGLE_USER


def get_optional_user(request: Request) -> Optional[User]:
    return SINGLE_USER


def get_current_user_ws(ws: WebSocket) -> User:
    return SINGLE_USER


def require_admin(user: User = None) -> User:
    return SINGLE_USER


def ensure_admin_user(username: str = "admin", password: str = "") -> User:
    return SINGLE_USER
