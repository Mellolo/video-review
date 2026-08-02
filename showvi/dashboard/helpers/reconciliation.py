"""Reconciliation and snapshot helpers extracted from dashboard/server.py."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

from dashboard.persistence import _save_video_jobs
from dashboard.helpers.checkpoint import (
    load_checkpoint,
    _checkpoint_indicates_video_success,
    _pid_is_alive,
    _resolve_existing_video_path,
    _find_attempt_by_id,
    _load_storyboard_payload,
    _hydrate_checkpoint_critiques_from_log,
    _regen_requests_payload,
)
from dashboard.helpers.media import scan_media
from dashboard.helpers.project import _resolve_storyboard
from dashboard.request_context import get_current_workspace
from dashboard.state import video_job_manager, MonitorSelection
from dashboard.workspace import WorkspaceContext


# ── Short-lived cache for expensive disk scans ──────────────────────────
_CACHE_TTL = 3  # seconds — matches the watcher polling interval

_media_cache: dict[str, tuple[float, dict]] = {}   # run_dir_str -> (ts, result)
_runs_cache: dict[str, tuple[float, list]] = {}     # base_dir_str -> (ts, result)


def _cached_scan_media(run_dir: Path) -> dict:
    """scan_media with a short TTL cache keyed on run_dir path."""
    key = str(run_dir)
    now = time.monotonic()
    entry = _media_cache.get(key)
    if entry and (now - entry[0]) < _CACHE_TTL:
        return entry[1]
    result = scan_media(run_dir)
    _media_cache[key] = (now, result)
    return result


def _cached_list_runs(base: Path) -> list[str]:
    """List run directories with a short TTL cache."""
    key = str(base)
    now = time.monotonic()
    entry = _runs_cache.get(key)
    if entry and (now - entry[0]) < _CACHE_TTL:
        return entry[1]
    if not base.exists():
        return []
    result = sorted([d.name for d in base.iterdir() if d.is_dir()], reverse=True)
    _runs_cache[key] = (now, result)
    return result



def _workspace_or_current(workspace: Optional[WorkspaceContext] = None) -> WorkspaceContext:
    ctx = workspace or get_current_workspace()
    if not ctx:
        raise RuntimeError("Workspace context is required")
    return ctx



def _reconcile_stale_video_jobs(*, user_id: Optional[int] = None) -> bool:
    """Fix stale persisted video job statuses using pid liveness and on-disk outputs."""
    changed = False
    for _jid, j in video_job_manager.all_items():
        if user_id is not None and int(j.get("owner_user_id") or -1) != int(user_id):
            continue
        status = j.get("status")
        if status not in {"running", "paused", "crashed", "interrupted", "queued"}:
            continue

        if status == "queued":
            continue

        rd = j.get("run_dir")
        if not rd:
            continue

        cp = load_checkpoint(Path(rd))
        disk_ok = _checkpoint_indicates_video_success(cp, rd)
        if status == "crashed" and disk_ok:
            video_job_manager.update(
                _jid, status="completed", error=None,
                completion_note="子进程曾报告非零退出码，但 checkpoint 与视频文件已齐全，已更正为已完成。",
            )
            changed = True
            continue

        if status == "interrupted" and disk_ok:
            video_job_manager.update(
                _jid, status="completed", error=None,
                completion_note="任务曾被标记为中断，但输出已完整，已自动更正为已完成。",
            )
            changed = True
            continue

        if status in {"running", "paused"} and not _pid_is_alive(j.get("pid")):
            # paused with no pid is intentional (process was terminated for checkpoint-based pause)
            if status == "paused" and not j.get("pid"):
                continue
            if disk_ok:
                video_job_manager.update(
                    _jid, status="completed", error=None,
                    completion_note="检测到任务进程已退出，但输出已完整，已自动更正为已完成。",
                )
            else:
                video_job_manager.update(_jid, status="interrupted")
            from dashboard.jimeng_pool import jimeng_pool
            jimeng_pool.release(j)
            changed = True

    if changed:
        _save_video_jobs(user_id=user_id)
    return changed



def _collect_concat_sources(run_dir: Path, checkpoint: Optional[dict]) -> list[str]:
    if not run_dir or not run_dir.exists() or not checkpoint:
        return []

    units = checkpoint.get("script", {}).get("work_units", [])
    sources: list[str] = []

    for unit in units:
        chosen: Optional[Path] = None

        final_video = unit.get("final_video_path")
        chosen = _resolve_existing_video_path(final_video, run_dir)

        if not chosen:
            attempts = unit.get("attempts", []) or []
            final_attempt_id = unit.get("final_attempt_id")
            if final_attempt_id is not None:
                selected_attempt = _find_attempt_by_id(unit, int(final_attempt_id))
                if selected_attempt:
                    chosen = _resolve_existing_video_path(selected_attempt.get("output_path"), run_dir)

        if not chosen:
            attempts = unit.get("attempts", []) or []
            for attempt in reversed(attempts):
                attempt_path = attempt.get("output_path")
                chosen = _resolve_existing_video_path(attempt_path, run_dir)
                if chosen:
                    break

        if chosen:
            sources.append(str(chosen))

    return sources



def build_snapshot(
    *,
    workspace: Optional[WorkspaceContext] = None,
    monitor: Optional[MonitorSelection] = None,
) -> dict:
    """Build a complete state snapshot for the frontend."""
    ws = _workspace_or_current(workspace)
    if monitor is None:
        raise RuntimeError("Monitor selection is required")
    run_dir = Path(monitor.run_dir) if monitor.run_dir else None

    checkpoint = load_checkpoint(run_dir) if run_dir else None
    checkpoint = _hydrate_checkpoint_critiques_from_log(checkpoint, run_dir)
    storyboard = _load_storyboard_payload(
        monitor.storyboard_name or "",
        run_dir,
        preferred_path=monitor.storyboard_path,
        checkpoint=checkpoint,
        workspace=ws,
    )

    media = _cached_scan_media(run_dir) if run_dir else {}

    all_runs = []
    if monitor.storyboard_name:
        base = ws.output_dir / monitor.storyboard_name
        if not base.exists():
            base = ws.output_dir / (monitor.storyboard_name + "_storyboard")
        all_runs = _cached_list_runs(base)

    return {
        "storyboard": storyboard,
        "checkpoint": checkpoint,
        "media": media,
        "regen_requests": _regen_requests_payload(run_dir),
        "run_id": run_dir.name if run_dir else None,
        "all_runs": all_runs,
        "storyboard_name": monitor.storyboard_name,
        "storyboard_path": monitor.storyboard_path or (
            _resolve_storyboard(monitor.storyboard_name or "", run_dir, workspace=ws) if monitor.storyboard_name else None
        ),
    }


async def build_snapshot_async(
    *,
    workspace: Optional[WorkspaceContext] = None,
    monitor: Optional[MonitorSelection] = None,
) -> dict:
    """Non-blocking wrapper — runs build_snapshot in a thread so the event loop stays free."""
    return await asyncio.to_thread(build_snapshot, workspace=workspace, monitor=monitor)
