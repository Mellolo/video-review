"""Dashboard shared state and constants."""

from __future__ import annotations

import copy
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import WebSocket

from tools.storyboard_gen.schemas import AUTO_VIDEO_STYLE

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
STORYBOARDS_DIR = BASE_DIR / "storyboards"
UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"
ASSETS_DIR = BASE_DIR / "assets"

# Ensure project root is on sys.path
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

state = {
    "seeddance_backend": os.environ.get("SEEDDANCE_BACKEND", "jimeng"),
    "web_worker_mode": "single",
}


@dataclass
class MonitorSelection:
    storyboard_path: Optional[str] = None
    run_dir: Optional[str] = None
    storyboard_name: Optional[str] = None
    run_pinned: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "storyboard_path": self.storyboard_path,
            "run_dir": self.run_dir,
            "storyboard_name": self.storyboard_name,
            "run_pinned": self.run_pinned,
        }


monitor_states: dict[int, MonitorSelection] = {}


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = {}
        self._socket_users: dict[int, int] = {}
        self._lock = threading.RLock()

    def add(self, user_id: int, ws: WebSocket) -> None:
        with self._lock:
            self._connections.setdefault(int(user_id), set()).add(ws)
            self._socket_users[id(ws)] = int(user_id)

    def remove(self, ws: WebSocket) -> None:
        with self._lock:
            user_id = self._socket_users.pop(id(ws), None)
            if user_id is None:
                return
            sockets = self._connections.get(user_id)
            if not sockets:
                return
            sockets.discard(ws)
            if not sockets:
                self._connections.pop(user_id, None)

    def sockets_for_user(self, user_id: int) -> set[WebSocket]:
        with self._lock:
            return set(self._connections.get(int(user_id), set()))

    def active_user_ids(self) -> list[int]:
        with self._lock:
            return list(self._connections.keys())

    def all_connections(self) -> dict[int, set[WebSocket]]:
        with self._lock:
            return {uid: set(sockets) for uid, sockets in self._connections.items()}


connection_manager = ConnectionManager()

AVAILABLE_STYLES = [AUTO_VIDEO_STYLE, "3d国漫", "真人", "2d动漫", "水墨"]
RUNTIME_OVERRIDES_FILE_NAME = ".runtime_overrides.json"
DEFAULT_CREATION_WORKERS = max(1, int(os.environ.get("DASHBOARD_CREATION_MAX_WORKERS", "3")))
DEFAULT_MAX_RUNNING_CREATION_JOBS_PER_USER = max(1, int(os.environ.get("DASHBOARD_MAX_RUNNING_CREATION_JOBS_PER_USER", "1")))
DEFAULT_MAX_RUNNING_CREATION_JOBS_GLOBAL = max(1, int(os.environ.get("DASHBOARD_MAX_RUNNING_CREATION_JOBS_GLOBAL", "3")))
DEFAULT_MAX_RUNNING_VIDEO_JOBS_PER_USER = max(1, int(os.environ.get("DASHBOARD_MAX_RUNNING_VIDEO_JOBS_PER_USER", "1")))
DEFAULT_MAX_RUNNING_VIDEO_JOBS_GLOBAL = max(1, int(os.environ.get("DASHBOARD_MAX_RUNNING_VIDEO_JOBS_GLOBAL", "2")))
DEFAULT_VIDEO_MAX_PARALLEL = max(1, int(os.environ.get("DASHBOARD_DEFAULT_VIDEO_MAX_PARALLEL", "4")))

# ── Runtime-adjustable concurrency (can be changed via /api/settings) ──
_runtime_image_concurrency: int = max(1, int(os.environ.get("DASHBOARD_IMAGE_CONCURRENCY", "4")))
_runtime_video_concurrency: int = DEFAULT_VIDEO_MAX_PARALLEL


def get_image_concurrency() -> int:
    return _runtime_image_concurrency


def set_image_concurrency(value: int) -> None:
    global _runtime_image_concurrency
    _runtime_image_concurrency = max(1, int(value))


def get_video_concurrency() -> int:
    return _runtime_video_concurrency


def set_video_concurrency(value: int) -> None:
    global _runtime_video_concurrency, DEFAULT_VIDEO_MAX_PARALLEL
    _runtime_video_concurrency = max(1, int(value))
    DEFAULT_VIDEO_MAX_PARALLEL = _runtime_video_concurrency


class ManagedJobView:
    def __init__(self, manager: "JobManager", job_id: str) -> None:
        self._manager = manager
        self.job_id = job_id

    def exists(self) -> bool:
        return self._manager.contains(self.job_id)

    def get(self, key: str, default: Any = None) -> Any:
        with self._manager._lock:
            job = self._manager._jobs.get(self.job_id)
            if not job:
                return default
            return job.get(key, default)

    def set(self, key: str, value: Any) -> Any:
        with self._manager._lock:
            job = self._manager._jobs.get(self.job_id)
            if not job:
                raise KeyError(self.job_id)
            job[key] = value
            return value

    def update(self, **changes: Any) -> dict:
        with self._manager._lock:
            job = self._manager._jobs.get(self.job_id)
            if not job:
                raise KeyError(self.job_id)
            job.update(changes)
            return job

    def pop(self, key: str, default: Any = None) -> Any:
        with self._manager._lock:
            job = self._manager._jobs.get(self.job_id)
            if not job:
                return default
            return job.pop(key, default)

    def setdefault(self, key: str, default: Any) -> Any:
        with self._manager._lock:
            job = self._manager._jobs.get(self.job_id)
            if not job:
                raise KeyError(self.job_id)
            return job.setdefault(key, default)

    def snapshot(self) -> dict:
        with self._manager._lock:
            job = self._manager._jobs.get(self.job_id)
            return copy.deepcopy(job) if job else {}

    def mutate(self, updater) -> dict:
        with self._manager._lock:
            job = self._manager._jobs.get(self.job_id)
            if not job:
                raise KeyError(self.job_id)
            updater(job)
            return job

    def __getitem__(self, key: str) -> Any:
        with self._manager._lock:
            return self._manager._jobs[self.job_id][key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)

    def __contains__(self, key: str) -> bool:
        with self._manager._lock:
            job = self._manager._jobs.get(self.job_id)
            return bool(job and key in job)


class JobManager:
    def __init__(self, *, default_terminal_statuses: tuple[str, ...] = ()) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._default_terminal_statuses = set(default_terminal_statuses)

    def all_items(self) -> list[tuple[str, dict]]:
        with self._lock:
            return [(job_id, self._jobs[job_id]) for job_id in list(self._jobs.keys())]

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {jid: copy.deepcopy(job) for jid, job in self._jobs.items()}

    def raw(self) -> dict[str, dict]:
        return self._jobs

    def list_ids(self) -> list[str]:
        with self._lock:
            return list(self._jobs.keys())

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            return self._jobs.get(job_id)

    def view(self, job_id: str) -> ManagedJobView:
        return ManagedJobView(self, job_id)

    def contains(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._jobs

    def set(self, job_id: str, job: dict) -> dict:
        with self._lock:
            self._jobs[job_id] = job
            return job

    def pop(self, job_id: str, default=None):
        with self._lock:
            return self._jobs.pop(job_id, default)

    def ensure(self, job_id: str, factory) -> dict:
        with self._lock:
            if job_id not in self._jobs:
                self._jobs[job_id] = factory()
            return self._jobs[job_id]

    def update(self, job_id: str, **changes: Any) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            job.update(changes)
            return job

    def patch(self, job_id: str, updater) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            updater(job)
            return job

    def setdefault(self, job_id: str, key: str, value: Any) -> Any:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            return job.setdefault(key, value)

    def values(self) -> list[dict]:
        with self._lock:
            return list(self._jobs.values())

    def items_for_user(self, user_id: int) -> list[tuple[str, dict]]:
        uid = int(user_id)
        with self._lock:
            return [(jid, job) for jid, job in self._jobs.items() if int(job.get("owner_user_id") or -1) == uid]

    def count_status_for_user(self, user_id: int, statuses: set[str]) -> int:
        uid = int(user_id)
        with self._lock:
            return sum(1 for job in self._jobs.values() if int(job.get("owner_user_id") or -1) == uid and job.get("status") in statuses)

    def count_status(self, statuses: set[str]) -> int:
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.get("status") in statuses)

    def transition(self, job_id: str, *, from_statuses: Optional[set[str]] = None, to_status: str, extra_updates: Optional[dict[str, Any]] = None) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if from_statuses is not None and job.get("status") not in from_statuses:
                return job
            if job.get("status") in self._default_terminal_statuses and to_status not in self._default_terminal_statuses:
                return job
            job["status"] = to_status
            if extra_updates:
                job.update(extra_updates)
            return job


class VideoProcessRegistry:
    def __init__(self) -> None:
        self._procs: dict[str, "subprocess.Popen"] = {}
        self._lock = threading.RLock()

    def __contains__(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._procs

    def __getitem__(self, job_id: str):
        with self._lock:
            return self._procs[job_id]

    def __setitem__(self, job_id: str, proc: "subprocess.Popen") -> None:
        with self._lock:
            self._procs[job_id] = proc

    def get(self, job_id: str):
        with self._lock:
            return self._procs.get(job_id)

    def set(self, job_id: str, proc: "subprocess.Popen") -> None:
        with self._lock:
            self._procs[job_id] = proc

    def pop(self, job_id: str, default=None):
        with self._lock:
            return self._procs.pop(job_id, default)

    def items(self):
        with self._lock:
            return list(self._procs.items())

    def snapshot(self) -> dict[str, "subprocess.Popen"]:
        with self._lock:
            return dict(self._procs)


creation_job_manager = JobManager(default_terminal_statuses=("completed", "failed", "stopped", "interrupted"))
video_job_manager = JobManager(default_terminal_statuses=("completed", "failed", "stopped", "crashed", "interrupted"))
video_process_registry = VideoProcessRegistry()

# Backwards-compatible aliases used across the current codebase.
creation_jobs = creation_job_manager.raw()
video_jobs = video_job_manager.raw()
_video_procs = video_process_registry


def get_default_backend() -> str:
    return state["seeddance_backend"]



def set_default_backend(value: str) -> str:
    if value == "jimeng":
        state["seeddance_backend"] = value
    return state["seeddance_backend"]



def get_web_worker_mode() -> str:
    return state.get("web_worker_mode", "single")



def set_web_worker_mode(value: str) -> str:
    state["web_worker_mode"] = value or "single"
    return state["web_worker_mode"]



def get_monitor_state(user_id: int) -> MonitorSelection:
    uid = int(user_id)
    if uid not in monitor_states:
        monitor_states[uid] = MonitorSelection()
    return monitor_states[uid]



def reset_monitor_state(user_id: int) -> None:
    monitor_states[int(user_id)] = MonitorSelection()



def set_monitor_state(user_id: int, **kwargs: Any) -> MonitorSelection:
    current = get_monitor_state(user_id)
    for key, value in kwargs.items():
        if hasattr(current, key):
            setattr(current, key, value)
    return current
