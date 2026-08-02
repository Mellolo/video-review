"""Dashboard persistence helpers — save / load jobs and runtime overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

import dashboard.state as _state
from dashboard.user_stub import User, SINGLE_USER
from dashboard.request_context import get_current_user_info
from dashboard.state import get_default_backend, RUNTIME_OVERRIDES_FILE_NAME
from dashboard.workspace import WorkspaceContext, get_workspace_by_user_id, get_workspace_for_user
from utils.io import atomic_save_json, load_json, load_versioned_payload, save_versioned_payload



def _all_users() -> list[User]:
    return [SINGLE_USER]



def _all_user_ids() -> list[int]:
    return [1]



def _fallback_owner_user_id() -> int:
    return 1



def _workspace_for_user_id(user_id: int) -> WorkspaceContext:
    return get_workspace_by_user_id(int(user_id))



def _iter_workspaces() -> list[WorkspaceContext]:
    return [get_workspace_for_user(user) for user in _all_users()]



def _serialize_jobs(source: dict[str, dict]) -> dict[str, dict]:
    serializable: dict[str, dict] = {}
    for jid, job in source.items():
        entry = {k: v for k, v in job.items() if k != "storyboard"}
        serializable[jid] = entry
    return serializable



def _owner_id_from_job(job: dict) -> Optional[int]:
    owner = job.get("owner_user_id")
    if owner is not None:
        return int(owner)
    current = get_current_user_info()
    if current:
        return int(current.id)
    return _fallback_owner_user_id()



def _save_video_jobs(user_id: Optional[int] = None) -> None:
    owner_ids: set[int] = set()
    # Take a snapshot so iteration is safe from concurrent modifications.
    all_jobs_snap = _state.video_job_manager.snapshot()
    if user_id is not None:
        owner_ids.add(int(user_id))
    else:
        owner_ids.update(
            int(job.get("owner_user_id"))
            for job in all_jobs_snap.values()
            if job.get("owner_user_id") is not None
        )
        current = get_current_user_info()
        if current:
            owner_ids.add(int(current.id))
        if not owner_ids:
            owner_ids.update(_all_user_ids())

    for owner in owner_ids:
        workspace = _workspace_for_user_id(owner)
        workspace.video_job_logs_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            jid: job
            for jid, job in all_jobs_snap.items()
            if int(job.get("owner_user_id") or -1) == owner
        }
        workspace.video_jobs_file.parent.mkdir(parents=True, exist_ok=True)
        save_versioned_payload(
            workspace.video_jobs_file,
            payload_key="jobs",
            payload=payload,
            default={},
        )



def _load_video_jobs() -> None:
    from dashboard.helpers.checkpoint import load_checkpoint, _checkpoint_indicates_video_success
    from dashboard.helpers.reconciliation import _reconcile_stale_video_jobs

    _state.video_job_manager.clear()
    workspaces = _iter_workspaces()
    for workspace in workspaces:
        if not workspace.video_jobs_file.exists():
            continue
        try:
            loaded, _ = load_versioned_payload(workspace.video_jobs_file, payload_key="jobs", default_payload={})
            if not isinstance(loaded, dict):
                continue
            for jid, job in loaded.items():
                if not isinstance(job, dict):
                    continue
                job.setdefault("owner_user_id", workspace.user_id)
                _state.video_job_manager.set(jid, job)
        except Exception:
            continue

    changed = False
    for jid, job in _state.video_job_manager.all_items():
        if job.get("status") not in ("running", "paused"):
            continue

        rd = job.get("run_dir")
        cp = load_checkpoint(Path(rd)) if rd else None
        if _checkpoint_indicates_video_success(cp, rd):
            _state.video_job_manager.update(
                jid, status="completed", error=None,
                completion_note="Dashboard 重启后检测到任务已实际完成，已自动更正为已完成。",
            )
            changed = True
            continue

        pid = job.get("pid")
        if pid:
            try:
                import signal as _signal
                if hasattr(_signal, 'SIGCONT'):
                    os.kill(pid, _signal.SIGCONT)
                os.kill(pid, _signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
        _state.video_job_manager.update(jid, status="interrupted")
        changed = True

    if changed:
        _save_video_jobs()
    _reconcile_stale_video_jobs()



def _save_jobs(user_id: Optional[int] = None) -> None:
    owner_ids: set[int] = set()
    if user_id is not None:
        owner_ids.add(int(user_id))
    else:
        owner_ids.update(
            int(job.get("owner_user_id"))
            for job in _state.creation_job_manager.values()
            if job.get("owner_user_id") is not None
        )
        current = get_current_user_info()
        if current:
            owner_ids.add(int(current.id))
        if not owner_ids:
            owner_ids.update(_all_user_ids())

    serializable = _serialize_jobs(_state.creation_job_manager.snapshot())
    for owner in owner_ids:
        workspace = _workspace_for_user_id(owner)
        workspace.creation_jobs_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            jid: job
            for jid, job in serializable.items()
            if int(job.get("owner_user_id") or -1) == owner
        }
        save_versioned_payload(
            workspace.creation_jobs_file,
            payload_key="jobs",
            payload=payload,
            default={},
        )



def _extract_cookie_value(raw_cookie: str, key: str) -> str:
    if not raw_cookie:
        return ""
    for part in raw_cookie.split(";"):
        segment = part.strip()
        if not segment or "=" not in segment:
            continue
        name, value = segment.split("=", 1)
        if name.strip() == key:
            return unquote(value.strip())
    return ""



def _runtime_overrides_path(run_dir: Optional[str] | Path) -> Optional[Path]:
    if not run_dir:
        return None
    try:
        return Path(run_dir) / RUNTIME_OVERRIDES_FILE_NAME
    except Exception:
        return None



def _load_runtime_overrides(run_dir: Optional[str] | Path) -> dict:
    path = _runtime_overrides_path(run_dir)
    if not path or not path.exists():
        return {}
    data = load_json(path, default={})
    if isinstance(data, dict) and "overrides" in data and isinstance(data.get("overrides"), dict):
        return data.get("overrides", {})
    return data if isinstance(data, dict) else {}



def _save_runtime_overrides(run_dir: Optional[str] | Path, overrides: dict) -> bool:
    path = _runtime_overrides_path(run_dir)
    if not path:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        save_versioned_payload(path, payload_key="overrides", payload=overrides or {}, default={})
        return True
    except Exception:
        return False



def _build_runtime_overrides(
    *,
    backend: str,
    existing: Optional[dict] = None,
) -> dict:
    overrides = dict(existing or {})
    overrides.pop("proxy_mode", None)
    overrides.pop("jimeng_cookie", None)
    overrides.pop("seeddance_session_id", None)
    return overrides



def _find_runtime_overrides_by_storyboard(
    storyboard_path: Optional[str],
    *,
    user_id: Optional[int] = None,
) -> dict:
    if not storyboard_path:
        return {}
    target = Path(storyboard_path)
    if not target.is_absolute():
        target = Path.cwd() / target
    try:
        target = target.resolve()
    except Exception:
        pass

    effective_user_id = user_id if user_id is not None else (_owner_id_from_job({}) or None)

    for job in _state.creation_job_manager.values():
        owner = job.get("owner_user_id")
        if effective_user_id is not None and int(owner or -1) != int(effective_user_id):
            continue
        output_path = job.get("output_path")
        if not output_path:
            continue
        candidate = Path(output_path)
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        try:
            matches = candidate.resolve() == target
        except Exception:
            matches = str(candidate) == str(target)
        if not matches:
            continue
        overrides = _build_runtime_overrides(
            backend=job.get("seeddance_backend", get_default_backend()),
        )
        if overrides:
            return overrides
    return {}



def _probe_video_duration_seconds(video_path: str) -> Optional[float]:
    """Best-effort probe for local video duration in seconds."""
    if not video_path:
        return None
    from utils.ffmpeg import get_video_duration_optional
    return get_video_duration_optional(video_path)



def _load_jobs() -> None:
    """Restore creation_jobs from disk on startup."""
    _state.creation_job_manager.clear()
    for workspace in _iter_workspaces():
        if not workspace.creation_jobs_file.exists():
            continue
        try:
            loaded, _ = load_versioned_payload(workspace.creation_jobs_file, payload_key="jobs", default_payload={})
            if not isinstance(loaded, dict):
                continue
            for jid, job in loaded.items():
                if not isinstance(job, dict):
                    continue
                job.setdefault("owner_user_id", workspace.user_id)
                _state.creation_job_manager.set(jid, job)
        except Exception:
            continue

    for jid, job in _state.creation_job_manager.all_items():
        if job.get("status") in ("running", "stopping", "pausing"):
            _state.creation_job_manager.update(jid, status="interrupted", phase="interrupted", stop_requested=False)
    _save_jobs()
