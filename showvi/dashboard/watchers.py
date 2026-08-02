"""
File & video-job watchers — background polling loops and broadcast helpers.

Extracted from dashboard/server.py to reduce file size.
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import Optional

from dashboard.persistence import _save_video_jobs, _save_runtime_overrides
from dashboard.helpers.checkpoint import (
    find_latest_run,
    load_checkpoint,
    _compute_video_job_progress,
    _live_progress_for_job,
    _checkpoint_indicates_video_success,
)
from dashboard.helpers.reconciliation import _reconcile_stale_video_jobs, build_snapshot_async
from dashboard.state import (
    connection_manager,
    video_job_manager,
    video_process_registry,
    RUNTIME_OVERRIDES_FILE_NAME,
    get_monitor_state,
)
from dashboard.workspace import WorkspaceContext, get_workspace_by_user_id


def _scan_run_dir_mtimes(run_dir: Path) -> dict[str, float]:
    """Synchronous helper — scan file mtimes in a run directory."""
    result = {}
    if not run_dir.exists():
        return result
    for f in run_dir.iterdir():
        if f.is_file():
            result[f.name] = f.stat().st_mtime
    return result


async def file_watcher():
    """Poll user-selected run directories for changes and push updates via WebSocket."""
    last_mtime: dict[int, dict[str, float]] = {}
    while True:
        try:
            await asyncio.sleep(2)
            for user_id in connection_manager.active_user_ids():
                workspace = get_workspace_by_user_id(user_id)
                monitor = get_monitor_state(user_id)
                cache = last_mtime.setdefault(user_id, {})
                changed = False

                if not monitor.run_dir:
                    if monitor.storyboard_name:
                        latest = await asyncio.to_thread(find_latest_run, monitor.storyboard_name, workspace=workspace)
                        if latest:
                            monitor.run_dir = str(latest)
                            changed = True
                    if changed:
                        await broadcast_snapshot_to_user(user_id, workspace=workspace)
                    continue

                run_dir = Path(monitor.run_dir)

                # Offload directory scan to a thread so the event loop stays free.
                current_files = await asyncio.to_thread(_scan_run_dir_mtimes, run_dir)
                if not current_files and not cache:
                    continue

                for fname, mt in current_files.items():
                    if fname not in cache or cache[fname] < mt:
                        changed = True
                        break

                if monitor.storyboard_name and not monitor.run_pinned:
                    latest = await asyncio.to_thread(find_latest_run, monitor.storyboard_name, workspace=workspace)
                    if latest and str(latest) != monitor.run_dir:
                        monitor.run_dir = str(latest)
                        changed = True

                if changed:
                    last_mtime[user_id] = current_files
                    await broadcast_snapshot_to_user(user_id, workspace=workspace)
        except asyncio.CancelledError:
            raise
        except Exception:
            import traceback
            traceback.print_exc()


_WS_SEND_TIMEOUT = 5  # seconds — drop slow connections instead of blocking the loop


async def send_to_user(user_id: int, message: dict) -> None:
    dead = []
    for ws in connection_manager.sockets_for_user(user_id):
        try:
            await asyncio.wait_for(ws.send_json(message), timeout=_WS_SEND_TIMEOUT)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connection_manager.remove(ws)


async def broadcast(message: dict, *, user_id: Optional[int] = None) -> None:
    if user_id is not None:
        await send_to_user(user_id, message)
        return
    # Fan out concurrently so one slow client doesn't block others.
    await asyncio.gather(
        *(send_to_user(uid, message) for uid in connection_manager.active_user_ids()),
        return_exceptions=True,
    )


async def broadcast_snapshot_to_user(user_id: int, *, workspace: Optional[WorkspaceContext] = None) -> None:
    ws_ctx = workspace or get_workspace_by_user_id(user_id)
    monitor = get_monitor_state(user_id)
    data = await build_snapshot_async(workspace=ws_ctx, monitor=monitor)
    await send_to_user(user_id, {"type": "full_update", "data": data})


async def broadcast_snapshot_for_matching_run(project_name: str, run_dir: str) -> None:
    for user_id in connection_manager.active_user_ids():
        monitor = get_monitor_state(user_id)
        if monitor.storyboard_name == project_name and monitor.run_dir == run_dir:
            await broadcast_snapshot_to_user(user_id)


async def _video_jobs_watcher():
    """Periodically scan checkpoints for queued/running video_jobs, broadcast updates."""
    while True:
        try:
            await asyncio.sleep(3)
            changed_users: set[int] = set()
            changed = _reconcile_stale_video_jobs()

            tracked_ids = [
                jid
                for jid, j in video_job_manager.all_items()
                if j.get("status") in {"queued", "running"}
            ]
            if not tracked_ids:
                if changed:
                    await _broadcast_video_jobs()
                continue

            for jid in tracked_ids:
                j = video_job_manager.get(jid)
                if not j:
                    continue
                owner_user_id = int(j.get("owner_user_id") or -1)
                if owner_user_id < 0:
                    continue
                workspace = get_workspace_by_user_id(owner_user_id)
                sb_name = j.get("storyboard_name", "")
                proj_dir_name = sb_name + "_storyboard" if sb_name else ""
                proj_dir = workspace.output_dir / proj_dir_name if proj_dir_name else workspace.output_dir
                # Also check without _storyboard suffix (legacy naming)
                if not proj_dir.exists() and sb_name:
                    proj_dir = workspace.output_dir / sb_name
                if not proj_dir.exists() and j.get("status") == "running":
                    continue

                run_dir_str = j.get("run_dir")
                if j.get("status") == "running" and not run_dir_str:
                    runs = sorted(
                        [d for d in proj_dir.iterdir() if d.is_dir()],
                        key=lambda p: p.name,
                        reverse=True,
                    ) if proj_dir.exists() else []
                    if runs:
                        # Only adopt a run directory that was created AFTER this
                        # job was queued.  Otherwise we'd pick up a stale run
                        # from a previous generation and the monitor would show
                        # old data (looks like a resume).
                        candidate_run = runs[0]
                        queued_at = j.get("queued_at", "")
                        # Run dirs are named like 20260404_213112 (timestamp).
                        # Compare lexicographically with the queued_at ISO string
                        # converted to the same format.
                        run_ts = candidate_run.name  # e.g. "20260404_213112"
                        job_ts = queued_at[:19].replace("-", "").replace("T", "_").replace(":", "") if queued_at else ""
                        if not job_ts or run_ts >= job_ts:
                            run_dir_str = str(candidate_run)
                            video_job_manager.update(jid, run_dir=run_dir_str)
                            changed = True
                            changed_users.add(owner_user_id)
                            stored_overrides = j.get("_runtime_overrides")
                            if stored_overrides:
                                overrides_path = Path(run_dir_str) / RUNTIME_OVERRIDES_FILE_NAME
                                if not overrides_path.exists():
                                    try:
                                        _save_runtime_overrides(run_dir_str, stored_overrides)
                                    except Exception:
                                        pass

                if j.get("status") == "queued":
                    changed_users.add(owner_user_id)
                    continue

                if not run_dir_str:
                    continue

                run_dir = Path(run_dir_str)
                cp = load_checkpoint(run_dir)
                new_progress = _compute_video_job_progress(j, run_dir, cp)
                if new_progress != j.get("progress"):
                    video_job_manager.update(jid, progress=new_progress)
                    changed = True
                    changed_users.add(owner_user_id)

            if changed:
                _save_video_jobs()
                await _broadcast_video_jobs(user_ids=changed_users or None)
        except asyncio.CancelledError:
            raise
        except Exception:
            import traceback
            traceback.print_exc()


async def _broadcast_video_jobs(*, user_ids: Optional[set[int]] = None):
    """Send current video_jobs state to connected clients."""
    target_user_ids = user_ids or set(connection_manager.active_user_ids())
    changed = _reconcile_stale_video_jobs()
    if changed:
        _save_video_jobs()
    for user_id in target_user_ids:
        jobs_list = []
        for jid, j in video_job_manager.items_for_user(user_id):
            run_dir = j.get("run_dir")
            jobs_list.append({
                "job_id": jid,
                "storyboard_path": j.get("storyboard_path", ""),
                "storyboard_name": j.get("storyboard_name", ""),
                "title": j.get("title", j.get("storyboard_name", "")),
                "status": j["status"],
                "started_at": j.get("started_at", ""),
                "max_parallel": j.get("max_parallel", 3),
                "backend": j.get("backend", ""),
                "generation_mode": j.get("generation_mode", "parallel"),
                "progress": _live_progress_for_job(j),
                "error": j.get("error"),
                "completion_note": j.get("completion_note"),
                "run_dir": run_dir,
                "run_id": Path(run_dir).name if run_dir else None,
                "pid": j.get("pid"),
                "queue_position": j.get("queue_position"),
            })
        await send_to_user(user_id, {"type": "video_jobs_update", "jobs": jobs_list})



def _tail_video_job_log(job: dict, max_chars: int = 4000) -> str:
    lp = job.get("log_path")
    if not lp:
        return ""
    try:
        p = Path(lp)
        if not p.is_file():
            return ""
        data = p.read_text(encoding="utf-8", errors="replace")
        return data[-max_chars:] if len(data) > max_chars else data
    except OSError:
        return ""


async def _monitor_process(job_id: str, proc):
    """Monitor a subprocess until it exits; update video_jobs accordingly."""
    try:
        while True:
            await asyncio.sleep(2)
            retcode = proc.poll()
            if retcode is not None:
                # If the process was killed by SIGTERM (-15), the stop handler
                # (_stop_running_video_job_for_run) may still be in its
                # asyncio.sleep(3) before it transitions the status to "stopped".
                # Give it a moment to finish so we don't race and set "crashed".
                if retcode == -signal.SIGTERM:
                    await asyncio.sleep(4)

                j = video_job_manager.get(job_id)
                if not j:
                    video_process_registry.pop(job_id, None)
                    return

                disk_ok = False
                rd = j.get("run_dir")
                if rd:
                    cp = load_checkpoint(Path(rd))
                    disk_ok = _checkpoint_indicates_video_success(cp, rd)

                current_status = j.get("status")
                if current_status in ("stopped", "paused"):
                    # "stopped" / "paused": the stop/pause handler already set the
                    # correct terminal status — don't overwrite it with "crashed".
                    pass
                elif retcode == 0:
                    updates = {"status": "completed", "error": None}
                    if rd and not disk_ok:
                        updates["completion_note"] = (
                            "进程已退出，但有单元未生成成片文件；进度与分镜已按实际出片统计，请补跑缺失单元。"
                        )
                    video_job_manager.update(job_id, **updates)
                elif disk_ok:
                    video_job_manager.update(
                        job_id, status="completed", error=None,
                        completion_note=(
                            f"子进程退出码为 {retcode}（常见于向管道刷 stdout 时解释器关闭异常；"
                            "已改将日志写入文件）。已根据 checkpoint 与视频文件判定为成功完成。"
                        ),
                    )
                else:
                    tail = _tail_video_job_log(j)
                    if not tail:
                        try:
                            tail = proc.stdout.read()[-2000:] if proc.stdout else ""
                        except Exception:
                            tail = ""
                    video_job_manager.update(
                        job_id, status="crashed",
                        error=f"Process exited with code {retcode}\n{tail}",
                    )

                video_job_manager.update(job_id, pid=None, progress=_live_progress_for_job(video_job_manager.get(job_id) or j))
                from dashboard.jimeng_pool import jimeng_pool
                jimeng_pool.release(j)
                owner_uid = int(j.get("owner_user_id") or -1) if j.get("owner_user_id") is not None else None
                _save_video_jobs(user_id=owner_uid)
                video_process_registry.pop(job_id, None)
                await _broadcast_video_jobs(user_ids={owner_uid} if owner_uid is not None else None)
                # Also push a full monitor snapshot so the stage transitions
                # from "finalizing" to "completed" without requiring a page refresh.
                if owner_uid is not None and owner_uid >= 0:
                    try:
                        await broadcast_snapshot_to_user(owner_uid)
                    except Exception:
                        pass
                return
    except asyncio.CancelledError:
        pass
