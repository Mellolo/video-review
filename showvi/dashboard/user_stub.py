"""Single-user stub — replaces SQLAlchemy User/UserPreference models.

In single-user mode there is exactly one user: id=1, username="admin", role="admin".
All provider configuration is read from environment variables / .env file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    id: int = 1
    username: str = "admin"
    role: str = "admin"
    is_active: bool = True


@dataclass(frozen=True)
class UserPreference:
    user_id: int = 1
    seeddance_backend: str = "jimeng"
    jimeng_session_id: str = ""
    xiaoyunque_session_id: str = ""
    llm_backend: str = "openai_compatible"
    llm_provider: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    image_provider: str = ""
    image_base_url: str = ""
    image_api_key: str = ""
    image_model: str = ""


SINGLE_USER = User()


def get_single_user_preference() -> UserPreference:
    """Build a UserPreference from current environment variables."""
    return UserPreference(
        user_id=1,
        seeddance_backend=os.environ.get("SEEDDANCE_BACKEND", "jimeng"),
        jimeng_session_id=os.environ.get("SEEDDANCE_SESSION_ID", ""),
        llm_backend=os.environ.get("LLM_PROVIDER", os.environ.get("GEMINI_BACKEND", "openai_compatible")),
        llm_provider=os.environ.get("LLM_PROVIDER", ""),
        llm_base_url=os.environ.get("LLM_BASE_URL", ""),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        llm_model=os.environ.get("LLM_MODEL", ""),
        image_provider=os.environ.get("IMAGE_PROVIDER", ""),
        image_base_url=os.environ.get("IMAGE_BASE_URL", ""),
        image_api_key=os.environ.get("IMAGE_API_KEY", ""),
        image_model=os.environ.get("IMAGE_MODEL", ""),
    )
