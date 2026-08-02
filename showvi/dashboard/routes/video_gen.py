"""Video generation routes — start, stop, pause, resume, delete, list jobs."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from dashboard.deps import DashboardContext, get_dashboard_context
from dashboard.helpers.checkpoint import (
    _find_attempt_by_id,
    _find_unit_by_id,
    _find_video_job_for_run,
    _get_latest_active_regen_request,
    _get_run_and_checkpoint,
    _live_progress_for_job,
    _next_unit_attempt_id,
    _resolve_existing_video_path,
    load_checkpoint,
    save_checkpoint,
)
from dashboard.helpers.project import _switch_to_project
from dashboard.helpers.reconciliation import _reconcile_stale_video_jobs
from dashboard.job_access import get_video_job_for_user, list_video_jobs_for_user
from dashboard.persistence import (
    _find_runtime_overrides_by_storyboard,
    _load_runtime_overrides,
    _save_runtime_overrides,
    _save_video_jobs,
)
from dashboard.state import (
    BASE_DIR,
    DEFAULT_MAX_RUNNING_VIDEO_JOBS_GLOBAL,
    DEFAULT_MAX_RUNNING_VIDEO_JOBS_PER_USER,
    get_video_concurrency,
    state,
    video_job_manager,
    video_process_registry,
)
from dashboard.watchers import _broadcast_video_jobs, broadcast_snapshot_for_matching_run, _monitor_process
from dashboard.workspace import get_workspace_by_user_id, resolve_run_path, resolve_storyboard_path
from tools.regen_queue import enqueue_request, get_request, update_request

router = APIRouter(tags=["video_gen"])

_RUNNING_SLOT_STATUSES = {"running"}
_RESUMABLE_STATUSES = {"stopped", "crashed", "interrupted", "failed", "paused"}


def _resolve_image_ref_asset_paths(assets: dict, workspace) -> dict:
    """把 manual_image_ref_assets 里 /repo-media/{project}/{run_id}/{file} 格式的 URL 路径
    转换成真实磁盘路径，其余路径保持不变。"""
    if not assets:
        return assets
    resolved = {}
    for key, asset in assets.items():
        if not isinstance(asset, dict):
            resolved[key] = asset
            continue
        path = str(asset.get("path") or "").strip()
        # /repo-media/{project_name}/{run_id}/{filename}
        if path.startswith("/repo-media/"):
            parts = path.split("/")  # ['', 'repo-media', project, run_id, filename]
            if len(parts) == 5:
                project_name, run_id, filename = parts[2], parts[3], parts[4]
                real_path = workspace.output_dir / project_name / run_id / filename
                path = str(real_path)
        resolved[key] = {**asset, "path": path}
    return resolved



def _json_http_error(exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})



def _job_owner_id(job: dict | None, fallback_user_id: int | None = None) -> int | None:
    if job and job.get("owner_user_id") is not None:
        return int(job["owner_user_id"])
    return int(fallback_user_id) if fallback_user_id is not None else None



def _default_max_parallel_for_backend(backend: str) -> int:
    return get_video_concurrency()



def _video_job_payload(job_id: str, job: dict) -> dict:
    run_dir = job.get("run_dir")
    return {
        "job_id": job_id,
        "storyboard_path": job.get("storyboard_path", ""),
        "storyboard_name": job.get("storyboard_name", ""),
        "title": job.get("title", job.get("storyboard_name", "")),
        "status": job["status"],
        "started_at": job.get("started_at", ""),
        "queued_at": job.get("queued_at", ""),
        "max_parallel": job.get("max_parallel", 3),
        "backend": job.get("backend", ""),
        "generation_mode": job.get("generation_mode", "parallel"),
        "progress": _live_progress_for_job(job),
        "error": job.get("error"),
        "completion_note": job.get("completion_note"),
        "run_dir": run_dir,
        "run_id": Path(run_dir).name if run_dir else None,
        "pid": job.get("pid"),
        "queue_position": job.get("queue_position"),
    }



def _resolve_enqueue_request(body: dict) -> tuple[dict, object | None]:
    checkpoint_path = body.get("checkpoint_path", "")
    resume_run_dir = body.get("run_dir", "")
    owner_user_id = body.get("owner_user_id")
    owner_user_id = int(owner_user_id) if owner_user_id is not None else None

    workspace = None
    if owner_user_id is not None:
        try:
            workspace = get_workspace_by_user_id(owner_user_id)
        except Exception:
            workspace = None

    sb_path = body.get("storyboard_path", "")
    sb_abs = None
    if sb_path:
        try:
            if workspace is not None:
                sb_abs = resolve_storyboard_path(workspace, sb_path, must_exist=True)
            else:
                candidate = Path(sb_path)
                if not candidate.is_absolute():
                    candidate = BASE_DIR / candidate
                sb_abs = candidate.resolve()
                if not sb_abs.exists():
                    raise FileNotFoundError(sb_path)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=404, detail=f"Storyboard not found: {sb_path}")

    if resume_run_dir:
        if workspace is not None:
            try:
                run_path = resolve_run_path(workspace, Path(resume_run_dir).parent.name, Path(resume_run_dir).name, must_exist=True)
            except Exception:
                candidate = Path(resume_run_dir)
                try:
                    if candidate.exists() and workspace.output_dir.resolve() in candidate.resolve().parents:
                        run_path = candidate.resolve()
                    else:
                        raise HTTPException(status_code=404, detail="Run not found")
                except HTTPException:
                    raise
                except Exception:
                    raise HTTPException(status_code=404, detail="Run not found")
        else:
            run_path = Path(resume_run_dir)
        resume_run_dir = str(run_path)
    else:
        run_path = None

    if not checkpoint_path and sb_abs is None:
        raise HTTPException(status_code=400, detail="Missing storyboard_path or checkpoint_path")

    backend = (body.get("seeddance_backend") or state["seeddance_backend"]).strip() or state["seeddance_backend"]
    generation_mode = body.get("generation_mode", "parallel")
    # body 里显式传了 seeddance_model 才算用户主动选择；否则留空，后面从 overrides 继承
    body_seeddance_model = (body.get("seeddance_model") or "").strip()
    _VIP_MODELS = {"seedance-2.0-vip", "seedance-2.0-fast-vip"}
    max_parallel = max(1, int(body.get("max_parallel") or _default_max_parallel_for_backend(backend)))

    title = sb_abs.stem if sb_abs else (Path(resume_run_dir).parent.name if resume_run_dir else "resume")
    if sb_abs:
        try:
            with open(sb_abs, "r", encoding="utf-8") as f:
                sb_data = json.load(f)
            title = sb_data.get("title", title)
        except Exception:
            pass

    storyboard_name = sb_abs.stem if sb_abs else (Path(resume_run_dir).parent.name if resume_run_dir else "")
    if storyboard_name.endswith("_storyboard"):
        storyboard_name = storyboard_name[:-len("_storyboard")]

    runtime_overrides = {}
    if resume_run_dir:
        runtime_overrides = _load_runtime_overrides(resume_run_dir)
    elif sb_abs:
        runtime_overrides = _find_runtime_overrides_by_storyboard(str(sb_abs), user_id=owner_user_id)

    # 清除旧 overrides 中残留的 proxy 配置
    runtime_overrides.pop("proxy_mode", None)
    runtime_overrides.pop("seeddance_session_id", None)

    # 用户显式选择了模型则覆盖；resume 时若未传则保留旧 overrides 里的值，最终 fallback 到 seedance-2.0
    seeddance_model = body_seeddance_model or runtime_overrides.get("seeddance_model") or "seedance-2.0"
    runtime_overrides["seeddance_model"] = seeddance_model

    # 非 VIP 模型最终确认后再次强制并行数为 1（覆盖 resume 场景下从 overrides 读到的模型）
    if seeddance_model not in _VIP_MODELS:
        max_parallel = 1

    return {
        "owner_user_id": owner_user_id,
        "workspace": workspace,
        "storyboard_path": str(sb_abs) if sb_abs else (body.get("storyboard_path") or ""),
        "storyboard_name": storyboard_name,
        "title": title,
        "resume_run_dir": resume_run_dir or None,
        "checkpoint_path": checkpoint_path,
        "backend": backend,
        "generation_mode": generation_mode,
        "max_parallel": max_parallel,
        "runtime_overrides": runtime_overrides if runtime_overrides else {},
        "request_body": dict(body or {}),
    }, sb_abs



def _build_command(job: dict, workspace) -> list[str]:
    cmd = [sys.executable, str(BASE_DIR / "main.py")]
    checkpoint_path = job.get("checkpoint_path") or ""
    run_dir = job.get("run_dir") or ""
    generation_mode = job.get("generation_mode", "parallel")
    max_parallel = int(job.get("max_parallel") or _default_max_parallel_for_backend(job.get("backend", "jimeng")))
    storyboard_path = job.get("storyboard_path") or ""

    if checkpoint_path:
        cmd.extend(["--resume", str(checkpoint_path)])
        if generation_mode != "sequential":
            cmd.extend(["--parallel", "--max-parallel", str(max_parallel)])
        else:
            cmd.append("--transition-bridge")
    elif generation_mode == "sequential":
        cmd.extend(["--storyboard", storyboard_path, "--output", str((workspace.output_dir if workspace else (BASE_DIR / "output"))), "--transition-bridge"])
    else:
        cmd.extend(["--storyboard", storyboard_path, "--output", str((workspace.output_dir if workspace else (BASE_DIR / "output"))), "--parallel", "--max-parallel", str(max_parallel)])

    if run_dir and checkpoint_path and not Path(checkpoint_path).exists():
        cp = Path(run_dir) / "checkpoint.json"
        if cp.exists():
            checkpoint_path = str(cp)
    return cmd



def _running_jobs_global_count() -> int:
    return video_job_manager.count_status(_RUNNING_SLOT_STATUSES)



def _running_jobs_for_user_count(user_id: int) -> int:
    return video_job_manager.count_status_for_user(int(user_id), _RUNNING_SLOT_STATUSES)



def _queued_jobs_for_user_count(user_id: int) -> int:
    return video_job_manager.count_status_for_user(int(user_id), {"queued"})



def _refresh_queue_positions() -> None:
    queued = [
        (jid, job)
        for jid, job in video_job_manager.all_items()
        if job.get("status") == "queued"
    ]
    queued.sort(key=lambda item: (item[1].get("queued_at") or "", item[0]))
    for idx, (jid, _job) in enumerate(queued, start=1):
        video_job_manager.update(jid, queue_position=idx)


async def _enqueue_video_generation(body: dict) -> dict | JSONResponse:
    try:
        prepared, _ = _resolve_enqueue_request(body)
    except HTTPException as exc:
        return _json_http_error(exc)

    owner_user_id = prepared["owner_user_id"]
    resume_run_dir = prepared["resume_run_dir"]
    existing_job_match = None
    if resume_run_dir:
        existing_job_match = _find_video_job_for_run(Path(resume_run_dir), user_id=owner_user_id)
    job_id = existing_job_match[0] if existing_job_match else str(uuid.uuid4())[:8]

    current = video_job_manager.get(job_id)
    if current and current.get("status") in {"queued", "running", "paused"}:
        return {
            "ok": True,
            "queued": current.get("status") == "queued",
            "job_id": job_id,
            "status": current.get("status"),
            "message": "Job already active",
        }

    now_iso = datetime.now().isoformat()
    progress = {
        "total": 0,
        "completed": 0,
        "attempts": 0,
        "percent": 0,
        "stage": "queued",
        "image_total": 0,
        "image_generated": 0,
        "image_percent": 0,
    }

    if current and current.get("progress"):
        progress.update(current.get("progress") or {})
        progress["stage"] = "queued"

    video_job_manager.set(job_id, {
        "owner_user_id": owner_user_id,
        "status": "queued",
        "pid": None,
        "storyboard_path": prepared["storyboard_path"],
        "storyboard_name": prepared["storyboard_name"],
        "title": prepared["title"],
        "started_at": current.get("started_at") if current else "",
        "queued_at": now_iso,
        "max_parallel": prepared["max_parallel"],
        "backend": prepared["backend"],
        "generation_mode": prepared["generation_mode"],
        "_runtime_overrides": prepared["runtime_overrides"],
        "progress": progress,
        "error": None,
        "run_dir": prepared["resume_run_dir"],
        "checkpoint_path": prepared["checkpoint_path"],
        "request_body": prepared["request_body"],
        "completion_note": None,
        "queue_position": None,
    })
    _refresh_queue_positions()
    _save_video_jobs(user_id=owner_user_id)
    await _broadcast_video_jobs(user_ids={owner_user_id} if owner_user_id is not None else None)
    return {
        "ok": True,
        "queued": True,
        "job_id": job_id,
        "status": "queued",
        "message": "Video generation queued",
    }


async def _launch_video_job(job_id: str) -> bool:
    job = video_job_manager.get(job_id)
    if not job or job.get("status") != "queued":
        return False

    owner_user_id = job.get("owner_user_id")
    workspace = None
    if owner_user_id is not None:
        try:
            workspace = get_workspace_by_user_id(int(owner_user_id))
        except Exception:
            workspace = None

    try:
        cmd = _build_command(job, workspace)
        env = os.environ.copy()
        env["SEEDDANCE_BACKEND"] = job.get("backend", state["seeddance_backend"])
        if owner_user_id is not None:
            env["VIDEO_AGENT_OWNER_USER_ID"] = str(owner_user_id)

        env.setdefault("VIDEO_AGENT_DASHBOARD_PORT", os.environ.get("VIDEO_AGENT_DASHBOARD_PORT", "8501"))

        runtime_overrides = dict(job.get("_runtime_overrides") or {})
        if runtime_overrides.get("seeddance_session_id"):
            env["SEEDDANCE_SESSION_ID"] = runtime_overrides["seeddance_session_id"]
        elif os.environ.get("SEEDDANCE_SESSION_ID"):
            env["SEEDDANCE_SESSION_ID"] = os.environ["SEEDDANCE_SESSION_ID"]
        env.pop("VIDEO_AGENT_PROXY_MODE", None)
        # 传递用户选择的 seeddance 模型（新任务无 run_dir，无法通过文件传递）
        if runtime_overrides.get("seeddance_model"):
            env["SEEDDANCE_MODEL"] = runtime_overrides["seeddance_model"]
        else:
            env.pop("SEEDDANCE_MODEL", None)

        logs_dir = workspace.video_job_logs_dir if workspace else (BASE_DIR / ".dashboard" / "video_job_logs")
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = (logs_dir / f"{job_id}.log").resolve()

        if job.get("run_dir") and runtime_overrides:
            _save_runtime_overrides(job.get("run_dir"), runtime_overrides)

        # Immediately mark as running so the scheduler won't re-launch on next tick.
        video_job_manager.update(job_id, status="running")

        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )

        def _pump_stdout_to_log() -> None:
            try:
                with open(log_path, "a", encoding="utf-8", errors="replace", buffering=1) as log_f:
                    if proc.stdout:
                        shutil.copyfileobj(proc.stdout, log_f)
            except Exception:
                pass
            finally:
                try:
                    if proc.stdout:
                        proc.stdout.close()
                except Exception:
                    pass

        threading.Thread(target=_pump_stdout_to_log, daemon=True).start()
        video_process_registry[job_id] = proc

        video_job_manager.update(
            job_id,
            status="running",
            pid=proc.pid,
            started_at=datetime.now().isoformat(),
            log_path=str(log_path),
            queue_position=None,
            error=None,
            completion_note=None,
            progress={
                "total": 0,
                "completed": 0,
                "attempts": 0,
                "percent": 0,
                "stage": "starting",
                "image_total": 0,
                "image_generated": 0,
                "image_percent": 0,
            },
        )

        asyncio.create_task(_monitor_process(job_id, proc))
        if owner_user_id is not None:
            run_dir = job.get("run_dir")
            # Only switch_to_project when we have a concrete run_dir.
            # Without it, find_latest_run would pick up a *previous* run
            # and the monitor would display stale data (looks like a resume).
            if run_dir:
                _switch_to_project(
                    int(owner_user_id),
                    job.get("storyboard_name", ""),
                    Path(run_dir).name,
                    workspace=workspace,
                )
        _refresh_queue_positions()
        _save_video_jobs(user_id=int(owner_user_id) if owner_user_id is not None else None)
        await _broadcast_video_jobs(user_ids={int(owner_user_id)} if owner_user_id is not None else None)
        return True
    except Exception as e:
        video_job_manager.update(job_id, status="failed", error=str(e), pid=None)
        from dashboard.jimeng_pool import jimeng_pool
        jimeng_pool.release(job)
        _refresh_queue_positions()
        _save_video_jobs(user_id=int(owner_user_id) if owner_user_id is not None else None)
        await _broadcast_video_jobs(user_ids={int(owner_user_id)} if owner_user_id is not None else None)
        return False


async def video_job_scheduler() -> None:
    """Start queued video jobs under per-user and global concurrency limits."""
    while True:
        try:
            await asyncio.sleep(1)
            _reconcile_stale_video_jobs()
            queued = [
                (jid, job)
                for jid, job in video_job_manager.all_items()
                if job.get("status") == "queued"
            ]
            queued.sort(key=lambda item: (item[1].get("queued_at") or "", item[0]))
            _refresh_queue_positions()

            running_global = _running_jobs_global_count()
            if running_global >= DEFAULT_MAX_RUNNING_VIDEO_JOBS_GLOBAL:
                continue

            for jid, job in queued:
                owner_user_id = job.get("owner_user_id")
                if owner_user_id is None:
                    continue
                if running_global >= DEFAULT_MAX_RUNNING_VIDEO_JOBS_GLOBAL:
                    break
                if _running_jobs_for_user_count(int(owner_user_id)) >= DEFAULT_MAX_RUNNING_VIDEO_JOBS_PER_USER:
                    continue
                from dashboard.jimeng_pool import jimeng_pool
                if not jimeng_pool.try_assign(job):
                    continue
                launched = await _launch_video_job(jid)
                if launched:
                    running_global += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            import traceback
            traceback.print_exc()


async def _start_video_generation(body: dict):
    """Queue a video generation job; scheduler launches it when capacity is available."""
    return await _enqueue_video_generation(body)


@router.post("/api/generate/start")
async def api_generate_start(body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    body = dict(body or {})
    backend = body.get("seeddance_backend") or ctx.preference.seeddance_backend or state["seeddance_backend"]
    body["owner_user_id"] = ctx.user.id
    body.setdefault("seeddance_backend", backend)

    return await _start_video_generation(body)


@router.post("/api/run/{project_name}/{run_id}/unit/{unit_id}/regenerate")
async def api_regenerate_unit(project_name: str, run_id: str, unit_id: int, body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    run_path, cp, err = _get_run_and_checkpoint(project_name, run_id, workspace=ctx.workspace)
    if err:
        return err

    unit = _find_unit_by_id(cp, unit_id)
    if not unit:
        return JSONResponse(status_code=404, content={"error": "Unit not found"})

    extra_attempts = max(1, int(body.get("extra_attempts") or 1))
    manual_prompt = (body.get("manual_prompt") or "").strip()
    source_prompt = (body.get("source_prompt") or "").strip()
    manual_image_ref_assets = body.get("manual_image_ref_assets") or {}
    if not isinstance(manual_image_ref_assets, dict):
        manual_image_ref_assets = {}
    manual_image_ref_assets = _resolve_image_ref_asset_paths(manual_image_ref_assets, ctx.workspace)
    created_from_attempt_id = body.get("created_from_attempt_id")

    existing = _get_latest_active_regen_request(run_path, unit_id)
    active_job = _find_video_job_for_run(run_path, user_id=ctx.user.id)
    job_is_active = bool(active_job and active_job[1].get("status") in ("queued", "running", "paused"))

    if existing and existing.get("status") == "queued":
        req = update_request(
            run_path,
            existing["request_id"],
            status="queued" if job_is_active else "draft",
            manual_prompt=manual_prompt,
            source_prompt=source_prompt,
            manual_image_ref_assets=manual_image_ref_assets,
            extra_attempts=extra_attempts,
            created_from_attempt_id=int(created_from_attempt_id) if created_from_attempt_id is not None else existing.get("created_from_attempt_id"),
            placeholder_attempt_id=existing.get("placeholder_attempt_id") or _next_unit_attempt_id(run_path, unit),
        )
    elif existing and existing.get("status") == "draft":
        req = update_request(
            run_path,
            existing["request_id"],
            manual_prompt=manual_prompt,
            source_prompt=source_prompt,
            manual_image_ref_assets=manual_image_ref_assets,
            extra_attempts=extra_attempts,
            created_from_attempt_id=int(created_from_attempt_id) if created_from_attempt_id is not None else existing.get("created_from_attempt_id"),
            placeholder_attempt_id=existing.get("placeholder_attempt_id") or _next_unit_attempt_id(run_path, unit),
        )
    else:
        req = enqueue_request(
            run_path,
            unit_id=unit_id,
            source_prompt=source_prompt,
            manual_prompt=manual_prompt,
            manual_image_ref_assets=manual_image_ref_assets,
            extra_attempts=extra_attempts,
            created_from_attempt_id=int(created_from_attempt_id) if created_from_attempt_id is not None else None,
            placeholder_attempt_id=_next_unit_attempt_id(run_path, unit),
        )

    await broadcast_snapshot_for_matching_run(project_name, str(run_path))
    return {"ok": True, "mode": "draft", "unit_id": unit_id, "request": req}


@router.post("/api/run/{project_name}/{run_id}/unit/{unit_id}/regenerate/start")
async def api_start_regenerate_unit(project_name: str, run_id: str, unit_id: int, body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    run_path, cp, err = _get_run_and_checkpoint(project_name, run_id, workspace=ctx.workspace)
    if err:
        return err

    unit = _find_unit_by_id(cp, unit_id)
    if not unit:
        return JSONResponse(status_code=404, content={"error": "Unit not found"})

    request_id = (body.get("request_id") or "").strip()
    req = get_request(run_path, request_id) if request_id else _get_latest_active_regen_request(run_path, unit_id)
    if not req:
        return JSONResponse(status_code=404, content={"error": "Regenerate request not found"})
    if req.get("status") not in ("draft", "queued"):
        return JSONResponse(status_code=400, content={"error": f"Regenerate request status invalid: {req.get('status')}"})

    req = update_request(
        run_path,
        req["request_id"],
        status="queued",
        started_at=time.time(),
        manual_prompt=(body.get("manual_prompt") or req.get("manual_prompt") or "").strip(),
        source_prompt=(body.get("source_prompt") or req.get("source_prompt") or "").strip(),
        manual_image_ref_assets=_resolve_image_ref_asset_paths(
            body.get("manual_image_ref_assets") if isinstance(body.get("manual_image_ref_assets"), dict) else req.get("manual_image_ref_assets") or {},
            ctx.workspace,
        ),
        extra_attempts=max(1, int(body.get("extra_attempts") or req.get("extra_attempts") or 1)),
    )

    active_job = _find_video_job_for_run(run_path, user_id=ctx.user.id)
    start_result = None
    if not active_job or active_job[1].get("status") not in ("queued", "running", "paused"):
        backend = body.get("seeddance_backend", ctx.preference.seeddance_backend or state["seeddance_backend"])
        start_result = await _start_video_generation(
            {
                "checkpoint_path": str(run_path / "checkpoint.json"),
                "run_dir": str(run_path),
                "storyboard_path": cp.get("storyboard_path") or "",
                "seeddance_backend": backend,
                "generation_mode": body.get("generation_mode", "parallel"),
                "max_parallel": body.get("max_parallel", _default_max_parallel_for_backend(backend)),
                "owner_user_id": ctx.user.id,
            }
        )
        if isinstance(start_result, JSONResponse):
            return start_result

    await broadcast_snapshot_for_matching_run(project_name, str(run_path))
    await _broadcast_video_jobs(user_ids={ctx.user.id})
    return {"ok": True, "mode": "queued", "unit_id": unit_id, "request": req, "job": start_result}


@router.post("/api/run/{project_name}/{run_id}/unit/{unit_id}/final-attempt")
async def api_set_unit_final_attempt(project_name: str, run_id: str, unit_id: int, body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    try:
        run_path = resolve_run_path(ctx.workspace, project_name, run_id, must_exist=True)
    except HTTPException as exc:
        return _json_http_error(exc)

    cp = load_checkpoint(run_path)
    if not cp:
        return JSONResponse(status_code=404, content={"error": "Checkpoint not found"})

    unit = _find_unit_by_id(cp, unit_id)
    if not unit:
        return JSONResponse(status_code=404, content={"error": "Unit not found"})

    attempt_id = int(body.get("attempt_id") or 0)
    attempt = _find_attempt_by_id(unit, attempt_id)
    if not attempt:
        return JSONResponse(status_code=404, content={"error": "Attempt not found"})

    output_path = attempt.get("output_path")
    resolved = _resolve_existing_video_path(output_path, run_path)
    if not output_path or not resolved:
        return JSONResponse(status_code=400, content={"error": "Selected attempt has no usable video"})

    unit["final_video_path"] = str(resolved)
    unit["final_attempt_id"] = attempt_id
    unit["final_attempt_locked"] = True
    save_checkpoint(run_path, cp)

    await broadcast_snapshot_for_matching_run(project_name, str(run_path))
    return {"ok": True, "unit_id": unit_id, "attempt_id": attempt_id, "final_video_path": str(resolved)}


@router.post("/api/generate/stop/{job_id}")
async def api_generate_stop(job_id: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Stop a queued/running video generation job."""
    try:
        j = get_video_job_for_user(job_id, ctx.user.id)
    except HTTPException as exc:
        return _json_http_error(exc)

    status = j["status"]
    if status not in ("queued", "running", "paused"):
        return JSONResponse(status_code=400, content={"error": f"Job cannot be stopped (status: {status})"})

    if status == "queued":
        video_job_manager.update(job_id, status="stopped", queue_position=None)
        from dashboard.jimeng_pool import jimeng_pool
        jimeng_pool.release(j)
        _save_video_jobs(user_id=ctx.user.id)
        await _broadcast_video_jobs(user_ids={ctx.user.id})
        return {"ok": True, "message": f"Job {job_id} stopped"}

    pid = j.get("pid")
    if not pid:
        video_job_manager.update(job_id, status="stopped", queue_position=None)
        from dashboard.jimeng_pool import jimeng_pool
        jimeng_pool.release(j)
        _save_video_jobs(user_id=ctx.user.id)
        await _broadcast_video_jobs(user_ids={ctx.user.id})
        return {"ok": True, "message": "Job marked as stopped (no PID)"}

    # ── Set status to "stopped" BEFORE killing, to prevent the monitor
    #    watcher from racing and marking the job as "crashed". ──
    video_job_manager.transition(
        job_id,
        from_statuses={"running", "paused", "queued"},
        to_status="stopped",
        extra_updates={"pid": None, "queue_position": None},
    )
    from dashboard.jimeng_pool import jimeng_pool
    jimeng_pool.release(j)
    _save_video_jobs(user_id=ctx.user.id)

    try:
        os.kill(pid, signal.SIGTERM)
        await asyncio.sleep(3)
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        pass
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Failed to stop process: {e}"})

    video_process_registry.pop(job_id, None)
    await _broadcast_video_jobs(user_ids={ctx.user.id})
    return {"ok": True, "message": f"Job {job_id} stopped"}


@router.post("/api/generate/pause/{job_id}")
async def api_generate_pause(job_id: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Pause a running job: terminate the process (checkpoint is saved by the process), release slot."""
    try:
        j = get_video_job_for_user(job_id, ctx.user.id)
    except HTTPException as exc:
        return _json_http_error(exc)

    if j["status"] not in ("running", "queued"):
        return JSONResponse(status_code=400, content={"error": f"Job is not running (status: {j['status']})"})

    if j["status"] == "queued":
        # Not yet started — just mark as paused directly
        video_job_manager.update(job_id, status="paused", queue_position=None)
        from dashboard.jimeng_pool import jimeng_pool
        jimeng_pool.release(j)
        _save_video_jobs(user_id=ctx.user.id)
        await _broadcast_video_jobs(user_ids={ctx.user.id})
        return {"ok": True, "message": f"Job {job_id} paused"}

    pid = j.get("pid")
    run_dir = j.get("run_dir") or ""

    # Resolve checkpoint path so resume can pick it up
    checkpoint_path = j.get("checkpoint_path") or ""
    if run_dir and not checkpoint_path:
        cp = Path(run_dir) / "checkpoint.json"
        if cp.exists():
            checkpoint_path = str(cp)

    # Mark as paused BEFORE killing to prevent monitor from marking it crashed
    video_job_manager.update(job_id, status="paused", pid=None, queue_position=None,
                             checkpoint_path=checkpoint_path or j.get("checkpoint_path"))
    from dashboard.jimeng_pool import jimeng_pool
    jimeng_pool.release(j)
    _save_video_jobs(user_id=ctx.user.id)

    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            await asyncio.sleep(2)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass
    video_process_registry.pop(job_id, None)

    await _broadcast_video_jobs(user_ids={ctx.user.id})
    return {"ok": True, "message": f"Job {job_id} paused"}


@router.post("/api/generate/unpause/{job_id}")
async def api_generate_unpause(job_id: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Unpause a paused job by re-queuing it with checkpoint resume."""
    try:
        j = get_video_job_for_user(job_id, ctx.user.id)
    except HTTPException as exc:
        return _json_http_error(exc)

    if j["status"] != "paused":
        return JSONResponse(status_code=400, content={"error": f"Job is not paused (status: {j['status']})"})

    # Re-queue with checkpoint so the scheduler picks it up and resumes from where it left off
    run_dir = j.get("run_dir") or ""
    checkpoint_path = j.get("checkpoint_path") or ""
    if run_dir and not checkpoint_path:
        cp = Path(run_dir) / "checkpoint.json"
        if cp.exists():
            checkpoint_path = str(cp)

    video_job_manager.update(
        job_id,
        status="queued",
        pid=None,
        checkpoint_path=checkpoint_path,
        queued_at=__import__("datetime").datetime.now().isoformat(),
    )
    _refresh_queue_positions()
    _save_video_jobs(user_id=ctx.user.id)
    await _broadcast_video_jobs(user_ids={ctx.user.id})
    return {"ok": True, "message": f"Job {job_id} re-queued for resume"}


@router.post("/api/generate/resume/{job_id}")
async def api_generate_resume(job_id: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Resume a stopped/crashed/interrupted video generation job by re-queueing it."""
    try:
        j = get_video_job_for_user(job_id, ctx.user.id)
    except HTTPException as exc:
        return _json_http_error(exc)

    if j["status"] not in _RESUMABLE_STATUSES:
        return JSONResponse(status_code=400, content={"error": f"Cannot resume job with status: {j['status']}"})

    run_dir = j.get("run_dir")
    if not run_dir:
        return JSONResponse(status_code=400, content={"error": "Job run_dir missing"})

    run_path = Path(run_dir)
    checkpoint_path = run_path / "checkpoint.json"
    if not checkpoint_path.exists():
        return JSONResponse(status_code=404, content={"error": "Checkpoint not found"})

    backend = j.get("backend", ctx.preference.seeddance_backend or state["seeddance_backend"])
    # 从 runtime_overrides 文件读取上次使用的 model，避免 resume 时 fallback 到默认值
    saved_overrides = _load_runtime_overrides(str(run_path))
    saved_model = saved_overrides.get("seeddance_model") or j.get("_runtime_overrides", {}).get("seeddance_model") or ""
    start_result = await _start_video_generation(
        {
            "checkpoint_path": str(checkpoint_path),
            "run_dir": str(run_path),
            "storyboard_path": j.get("storyboard_path", ""),
            "seeddance_backend": backend,
            "generation_mode": j.get("generation_mode", "parallel"),
            "max_parallel": j.get("max_parallel", _default_max_parallel_for_backend(backend)),
            "owner_user_id": ctx.user.id,
            "seeddance_model": saved_model,
        }
    )
    if isinstance(start_result, JSONResponse):
        return start_result

    return {"ok": True, "job_id": start_result.get("job_id", job_id), "status": start_result.get("status", "queued"), "message": f"Job {job_id} queued for resume"}


@router.delete("/api/generate/delete/{job_id}")
async def api_generate_delete(job_id: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Delete a video generation job: stop/kill it if active, then remove from list."""
    try:
        j = get_video_job_for_user(job_id, ctx.user.id)
    except HTTPException as exc:
        return _json_http_error(exc)

    status = j.get("status")

    # If active, stop it first (kill process)
    if status in ("running", "queued", "paused"):
        pid = j.get("pid")
        video_job_manager.update(job_id, status="stopped", pid=None, queue_position=None)
        from dashboard.jimeng_pool import jimeng_pool
        jimeng_pool.release(j)
        _save_video_jobs(user_id=ctx.user.id)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                await asyncio.sleep(2)
                try:
                    os.kill(pid, 0)
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass
        video_process_registry.pop(job_id, None)

    video_process_registry.pop(job_id, None)
    video_job_manager.pop(job_id, None)
    _refresh_queue_positions()
    _save_video_jobs(user_id=ctx.user.id)

    if ctx.monitor.storyboard_name == j.get("storyboard_name"):
        ctx.monitor.storyboard_name = ""
        ctx.monitor.storyboard_path = ""
        ctx.monitor.run_dir = ""
        ctx.monitor.run_pinned = False

    await _broadcast_video_jobs(user_ids={ctx.user.id})
    return {"ok": True, "job_id": job_id, "message": f"Job {job_id} deleted"}


@router.get("/api/generate/jobs")
async def api_generate_jobs(ctx: DashboardContext = Depends(get_dashboard_context)):
    """List all video generation jobs for the current user."""
    changed = _reconcile_stale_video_jobs(user_id=ctx.user.id)
    jobs_list = [_video_job_payload(jid, j) for jid, j in list_video_jobs_for_user(ctx.user.id)]
    if changed:
        _save_video_jobs(user_id=ctx.user.id)
    return jobs_list
