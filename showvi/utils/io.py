"""Shared I/O utilities."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Union

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


@contextmanager
def file_lock(path: Union[str, Path], *, timeout_seconds: float = 10.0) -> Iterator[Path]:
    """Acquire an exclusive advisory lock for a target file path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.parent / f".{target.name}.lock"
    start = time.time()

    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        if fcntl is None:
            yield lock_path
            return

        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if timeout_seconds is not None and (time.time() - start) >= timeout_seconds:
                    raise TimeoutError(f"Timed out acquiring file lock: {lock_path}")
                time.sleep(0.05)

        try:
            yield lock_path
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def load_json(
    path: Union[str, Path],
    *,
    default: Any = None,
) -> Any:
    """Best-effort JSON read with fallback default."""
    target = Path(path)
    if not target.exists():
        return copy.deepcopy(default)
    try:
        with open(target, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return copy.deepcopy(default)


def atomic_save_json(
    path: Union[str, Path],
    data,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    default=None,
) -> None:
    """Atomically write JSON data to a file using write-to-temp + rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii, default=default)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_versioned_payload(
    path: Union[str, Path],
    *,
    payload_key: str,
    default_payload: Any,
) -> tuple[Any, int]:
    """Load either legacy bare payload or a versioned envelope payload."""
    raw = load_json(path, default=copy.deepcopy(default_payload))
    if isinstance(raw, dict) and payload_key in raw and isinstance(raw.get("version"), int):
        return raw.get(payload_key, copy.deepcopy(default_payload)), int(raw.get("version") or 0)

    if isinstance(default_payload, list) and isinstance(raw, list):
        return raw, 0
    if isinstance(default_payload, dict) and isinstance(raw, dict):
        return raw, 0
    return copy.deepcopy(default_payload), 0


def save_versioned_payload(
    path: Union[str, Path],
    *,
    payload_key: str,
    payload: Any,
    default=None,
) -> int:
    """Save payload in an envelope carrying a monotonically increasing version."""
    target = Path(path)
    with file_lock(target):
        _, current_version = load_versioned_payload(target, payload_key=payload_key, default_payload=default if default is not None else payload)
        next_version = current_version + 1
        atomic_save_json(
            target,
            {"version": next_version, payload_key: payload},
            default=default,
        )
        return next_version


def save_embedded_versioned_json(
    path: Union[str, Path],
    data: dict,
    *,
    meta_key: str = "_meta",
    default=None,
) -> int:
    """Save a dict JSON file with version metadata embedded under `meta_key`."""
    target = Path(path)
    with file_lock(target):
        existing = load_json(target, default={})
        current_version = 0
        if isinstance(existing, dict):
            meta = existing.get(meta_key)
            if isinstance(meta, dict):
                try:
                    current_version = int(meta.get("version") or 0)
                except (TypeError, ValueError):
                    current_version = 0
        next_version = current_version + 1
        payload = copy.deepcopy(data)
        meta = payload.get(meta_key)
        if not isinstance(meta, dict):
            meta = {}
        meta["version"] = next_version
        payload[meta_key] = meta
        atomic_save_json(target, payload, default=default)
        return next_version


def save_json(
    path: Union[str, Path],
    data,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> None:
    """Simple (non-atomic) JSON save. Use for debug/review artifacts where atomicity is not critical."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent)
