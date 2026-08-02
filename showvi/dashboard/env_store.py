"""
env_store.py — Read/write .env file as the single source of truth for all
provider and API key configuration.

Rules:
- The .env file lives at PROJECT_ROOT/.env (next to server.py / manage.py).
- Reading: parse the file line-by-line, return a dict of key→value.
- Writing: update or insert individual keys while preserving all comments,
  blank lines, and the order of existing keys.
- After writing, also update os.environ so the running process sees the
  new values immediately (no restart required for most settings).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Optional

# Resolve project root: two levels up from this file (dashboard/env_store.py)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = _PROJECT_ROOT / ".env"

# Keys managed by the settings page (in display order)
MANAGED_KEYS = [
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "IMAGE_PROVIDER",
    "IMAGE_BASE_URL",
    "IMAGE_API_KEY",
    "IMAGE_MODEL",
    "SEEDDANCE_SESSION_ID",
    "SEEDDANCE_BACKEND",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
]


def read_env() -> Dict[str, str]:
    """Parse .env and return a dict of all key=value pairs (no comments)."""
    result: Dict[str, str] = {}
    if not ENV_FILE.exists():
        return result
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            # Strip surrounding quotes
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            result[key] = val
    return result


def write_env(updates: Dict[str, str]) -> None:
    """Update or insert keys in .env, preserving comments and blank lines.

    - Existing key lines are updated in-place.
    - New keys are appended at the end.
    - Empty-string values write `KEY=` (clears the value but keeps the key).
    - os.environ is updated immediately after writing.
    """
    if not updates:
        return

    lines: list[str] = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()

    updated_keys: set[str] = set()

    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    # Append keys that weren't already in the file
    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}")

    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    # Sync to os.environ immediately
    for key, val in updates.items():
        os.environ[key] = val


def get_env_value(key: str, default: str = "") -> str:
    """Get a single value from .env file, falling back to os.environ then default.

    .env file is the source of truth for user-configured values.
    os.environ is only used as a last resort (e.g. values set via shell export).
    """
    val = read_env().get(key)
    if val is not None:
        return val
    return os.environ.get(key, default)
