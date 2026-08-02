"""Creation-mode routes — storyboard generation job management."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from dashboard.creation_engine import (
    _is_creation_job_stop_requested,
    _job_owner_user_id,
    _mark_creation_job_stopped,
    broadcast_create_event,
    run_creation_job,
)
from dashboard.deps import DashboardContext, get_dashboard_context
from dashboard.job_access import get_creation_job_for_user, list_creation_jobs_for_user
from dashboard.persistence import _save_jobs
from dashboard.state import creation_job_manager, state
from dashboard.workspace import (
    resolve_storyboard_path,
    resolve_upload_path,
    resolve_user_path,
)
from clients import _env
from tools.storyboard_gen.schemas import AUTO_VIDEO_STYLE, normalize_style_choice

router = APIRouter(tags=["creation"])


def _cancel_llm_for_job(job_id: str) -> None:
    """Cancel any in-flight LLM API calls associated with this creation job."""
    try:
        from clients.llm_client import cancel_llm_scope
        cancel_llm_scope(job_id)
    except Exception:
        pass  # best-effort; the stop_checker will catch it on next iteration



def _json_http_error(exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


_ARTIFACT_PATTERNS = [
    re.compile(r"^(USER|ASSISTANT|AI|SYSTEM|编辑|用户)\s*[:：].*$", re.MULTILINE),
    re.compile(r"^You are a screenplay editor.*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^(Current Narrative|User Feedback|Previous conversation)\s*[:：]?\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^【(原始叙事正文|用户修改意见|之前的修改记录)】\s*$", re.MULTILINE),
    re.compile(r"^Please modify the narrative.*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^请根据修改意见.*只输出.*叙事正文。\s*$", re.MULTILINE),
    re.compile(r"^\[用户\][:：].*$", re.MULTILINE),
    re.compile(r"^\[编辑\][:：].*$", re.MULTILINE),
]


def _strip_narrative_artifacts(text: str) -> str:
    """Remove leaked prompt/chat artifacts from LLM-refined narrative."""
    text = (text or "").strip()
    for pat in _ARTIFACT_PATTERNS:
        text = pat.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()



def _backend_for_request(ctx: DashboardContext, body: dict) -> str:
    return (body.get("seeddance_backend") or ctx.preference.seeddance_backend or state["seeddance_backend"]).strip() or state["seeddance_backend"]



def _user_backend_session(ctx: DashboardContext, backend: str) -> str:
    return ""



def _creation_job_payload(job_id: str, job: dict) -> dict:
    return {
        "job_id": job_id,
        "status": job["status"],
        "phase": job["phase"],
        "mode": job["mode"],
        "title": job.get("title", ""),
        "output_path": job.get("output_path"),
        "screenplay_data": job.get("screenplay_data"),
        "storyboard_data": job.get("storyboard_data"),
        "error": job.get("error"),
        "stop_requested": job.get("stop_requested", False),
        "video_job_id": job.get("video_job_id"),
        "one_click": job.get("one_click", False),
        "seeddance_model": job.get("seeddance_model", "seedance-2.0"),
    }


# ── Creation Mode API ─────────────────────────────────────────────────────

@router.post("/api/create/start")
async def api_create_start(body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Start a storyboard generation job."""
    mode = body.get("mode", "prompt")
    title = body.get("title", "").strip()
    backend = _backend_for_request(ctx, body)
    user_session_id = _user_backend_session(ctx, backend)

    # Auto-stop old paused/queued jobs for the same user to prevent stale state on page load
    for old_jid, old_job in list_creation_jobs_for_user(ctx.user.id):
        if old_job.get("status") in ("paused", "queued"):
            creation_job_manager.update(old_jid, status="stopped", phase="stopped")

    job_id = str(uuid.uuid4())[:8]
    creation_job_manager.set(job_id, {
        "owner_user_id": ctx.user.id,
        "status": "queued",
        "mode": mode,
        "phase": "queued",
        "progress": [],
        "output_path": None,
        "review_dir": None,
        "error": None,
        "title": title,
        "paused": False,
        "screenplay_data": None,
        "storyboard_data": None,
        "source_context": "",
        "model": body.get("model") or _env("LLM_MODEL", "gemini-3-flash-preview"),
        "one_click": bool(body.get("one_click", False)),
        "video_mode": body.get("video_mode", "replicate"),
        "recreate_direction": body.get("recreate_direction", ""),
        "auto_start_video": bool(body.get("auto_start_video", False)),
        "generation_mode": body.get("generation_mode", "parallel"),
        "seeddance_backend": backend,
        "seeddance_model": body.get("seeddance_model", "seedance-2.0"),
        "duration": body.get("duration"),
        "video_job_id": None,
        "stop_requested": False,
        "queued_at": datetime.now().isoformat(),
    })

    params = {
        "mode": mode,
        "title": title,
        "style": normalize_style_choice(body.get("style", AUTO_VIDEO_STYLE)),
        "style_hint": body.get("style_hint", ""),
        "duration": body.get("duration"),
        "quickchat": bool(body.get("quickchat", False)),
        "model": body.get("model") or _env("LLM_MODEL", "gemini-3-flash-preview"),
        "num_scenes": body.get("num_scenes"),
        "one_click": bool(body.get("one_click", False)),
    }

    if mode == "prompt":
        params["idea"] = body.get("idea", "")
        if not params["idea"].strip():
            creation_job_manager.pop(job_id, None)
            return JSONResponse(status_code=400, content={"error": "Missing idea for prompt mode"})
        creation_job_manager.update(job_id, source_context=params["idea"])
    elif mode == "novel":
        params["chapter_text"] = body.get("chapter_text", "")
        creation_job_manager.update(job_id, source_context=params["chapter_text"])
    elif mode == "video":
        raw_video_path = body.get("video_path", "")
        try:
            params["video_path"] = str(resolve_upload_path(ctx.workspace, raw_video_path, must_exist=True))
        except Exception:
            creation_job_manager.pop(job_id, None)
            return JSONResponse(status_code=400, content={"error": "Invalid video_path"})
        params["video_mode"] = body.get("video_mode", "replicate")
        params["recreate_direction"] = body.get("recreate_direction", "")
        creation_job_manager.update(job_id, source_context=params["video_path"])
    else:
        creation_job_manager.pop(job_id, None)
        return JSONResponse(status_code=400, content={"error": f"Unsupported mode: {mode}"})

    # Store params on the job so the scheduler can launch it later.
    creation_job_manager.update(job_id, _params=params)
    _save_jobs(user_id=ctx.user.id)
    return {"job_id": job_id, "status": "queued"}


@router.post("/api/create/continue/{job_id}")
async def api_create_continue(job_id: str, body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Continue a paused creation job with user-edited screenplay."""
    try:
        job = get_creation_job_for_user(job_id, ctx.user.id)
    except HTTPException as exc:
        return _json_http_error(exc)

    allowed_statuses = ("paused", "completed", "failed", "stopped", "interrupted")
    if not job.get("paused") and job.get("status") not in allowed_statuses:
        return JSONResponse(status_code=400, content={"error": "Job is not paused"})

    edited_screenplay = body.get("screenplay")
    if edited_screenplay:
        creation_job_manager.update(job_id, screenplay_data=edited_screenplay)

    # Re-read job after update to build params
    job = creation_job_manager.get(job_id) or job
    params = {
        "mode": job["mode"],
        "title": job["title"],
        "screenplay": job.get("screenplay_data"),
        "output_path": job.get("output_path"),
        "source_context": job.get("source_context", ""),
        "model": job.get("model") or _env("LLM_MODEL", "gemini-3-flash-preview"),
        "one_click": job.get("one_click", False),
        "auto_start_video": job.get("auto_start_video", False),
        "generation_mode": job.get("generation_mode", "parallel"),
        "seeddance_backend": job.get("seeddance_backend", ctx.preference.seeddance_backend or state["seeddance_backend"]),
        "duration": job.get("duration"),
    }
    creation_job_manager.update(
        job_id, status="queued", stop_requested=False, paused=False,
        phase="storyboard", queued_at=datetime.now().isoformat(), _params=params,
    )
    _save_jobs(user_id=ctx.user.id)
    # Scheduler will pick this up and call _continue_generation or run_creation_job.
    return {"ok": True, "status": "resumed"}


@router.post("/api/create/pause/{job_id}")
async def api_create_pause(job_id: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Pause a running creation job by re-queuing it, allowing other jobs to be scheduled. Can be resumed later."""
    try:
        job = get_creation_job_for_user(job_id, ctx.user.id)
    except HTTPException as exc:
        return _json_http_error(exc)

    if job.get("status") in ("completed", "failed", "stopped", "interrupted", "paused"):
        return JSONResponse(status_code=400, content={"error": "Job is not running"})

    if job.get("status") == "queued":
        # Already queued — mark as paused directly (not yet running)
        creation_job_manager.update(job_id, status="paused", paused=True, stop_requested=False)
        _save_jobs(user_id=ctx.user.id)
        await broadcast_create_event(job_id, "paused", {"message": "任务已暂停"})
        return {"ok": True, "status": "paused"}

    # Running — signal the engine to stop, then _mark_creation_job_stopped will land it as "paused"
    creation_job_manager.update(job_id, stop_requested=True, paused=False, stop_requested_at=time.time(), status="pausing")
    _save_jobs(user_id=ctx.user.id)
    # Immediately cancel any in-flight Gemini calls for this job.
    _cancel_llm_for_job(job_id)
    await broadcast_create_event(job_id, "pausing", {"message": "正在暂停，请稍候..."})
    return {"ok": True, "status": "pausing"}


@router.post("/api/create/stop/{job_id}")
async def api_create_stop(job_id: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Internal: stop a running job (used by delete). Kept for backward compatibility."""
    try:
        job = get_creation_job_for_user(job_id, ctx.user.id)
    except HTTPException as exc:
        return _json_http_error(exc)

    if job.get("status") in ("completed", "failed", "stopped", "interrupted"):
        return {"ok": True, "status": job.get("status")}

    creation_job_manager.update(job_id, stop_requested=True, paused=False, stop_requested_at=time.time())
    if job.get("status") in ("paused", "queued"):
        _mark_creation_job_stopped(job_id)
        return {"ok": True, "status": "stopped"}

    creation_job_manager.update(job_id, status="stopping")
    _save_jobs(user_id=ctx.user.id)
    _cancel_llm_for_job(job_id)
    return {"ok": True, "status": "stopping"}


@router.post("/api/create/delete/{job_id}")
async def api_create_delete(job_id: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Delete a creation job: stop it if running, then remove from job list."""
    try:
        job = get_creation_job_for_user(job_id, ctx.user.id)
    except HTTPException as exc:
        return _json_http_error(exc)

    # Stop if still active
    active_statuses = ("running", "pausing", "stopping", "queued", "paused")
    if job.get("status") in active_statuses:
        creation_job_manager.update(job_id, stop_requested=True, paused=False, stop_requested_at=time.time())
        if job.get("status") in ("paused", "queued"):
            _mark_creation_job_stopped(job_id)
        else:
            creation_job_manager.update(job_id, status="stopping")
            _cancel_llm_for_job(job_id)
            # Wait briefly for engine to acknowledge stop
            import asyncio as _asyncio
            for _ in range(10):
                await _asyncio.sleep(0.3)
                j = creation_job_manager.get(job_id) or {}
                if j.get("status") in ("stopped", "failed", "completed"):
                    break

    # Remove from manager
    creation_job_manager.pop(job_id, None)
    _save_jobs(user_id=ctx.user.id)
    await broadcast_create_event(job_id, "deleted", {"message": "任务已删除"})
    return {"ok": True, "status": "deleted"}


# ── Resume / Continue-Storyboard / Refine / Status / Jobs / Regenerate ───

@router.post("/api/create/resume-video-polling")
async def api_resume_video_polling(body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    """重启后恢复视频轮询：对 checkpoint 里 IN_PROGRESS 的 attempt 继续等待，不重新提交。"""
    run_dir = body.get("run_dir", "")
    if not run_dir:
        return JSONResponse(status_code=400, content={"error": "Missing run_dir"})

    try:
        run_path = resolve_user_path(ctx.workspace, run_dir, allowed_roots=[ctx.workspace.output_dir], must_exist=True)
    except HTTPException as exc:
        return _json_http_error(exc)

    def _do_resume():
        from agent import VideoDirectorAgent
        from config import AppConfig
        from models import ProjectState

        try:
            ps = ProjectState.load_checkpoint(str(run_path))
        except Exception as e:
            return {"error": f"Failed to load checkpoint: {e}"}

        import logging
        import threading

        agent = VideoDirectorAgent.__new__(VideoDirectorAgent)
        agent.project_state = ps
        agent._checkpoint_lock = threading.Lock()
        agent._log = logging.getLogger("video_agent.resume")
        agent.pipeline_executor = None
        agent.config = AppConfig.from_env()
        resumed = agent.resume_pending_attempts()
        return {"ok": True, "resumed": resumed}

    result = await asyncio.get_event_loop().run_in_executor(None, _do_resume)
    return result


@router.post("/api/create/continue-storyboard/{job_id}")
async def api_create_continue_storyboard(job_id: str, body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Continue after storyboard review — user has finished editing scenes."""
    try:
        job = get_creation_job_for_user(job_id, ctx.user.id)
    except HTTPException as exc:
        return _json_http_error(exc)

    if job.get("phase") not in ("storyboard_review", "done"):
        return JSONResponse(status_code=400, content={"error": "Job is not in storyboard_review or done phase"})

    edited_storyboard = body.get("storyboard")
    if edited_storyboard:
        creation_job_manager.update(job_id, storyboard_data=edited_storyboard)

    # Re-read after update
    job = creation_job_manager.get(job_id) or job
    storyboard = job.get("storyboard_data")
    output_path = job.get("output_path", "")

    if output_path:
        try:
            sb_abs = resolve_storyboard_path(ctx.workspace, output_path, must_exist=False)
            sb_abs.parent.mkdir(parents=True, exist_ok=True)
            with open(sb_abs, "w", encoding="utf-8") as f:
                json.dump(storyboard, f, ensure_ascii=False, indent=2)
            output_path = str(sb_abs)
        except HTTPException as exc:
            return _json_http_error(exc)

    creation_job_manager.update(
        job_id, paused=False, status="completed", phase="done", output_path=output_path,
    )
    _save_jobs(user_id=ctx.user.id)

    video_job_info = None
    if job.get("auto_start_video"):
        from dashboard.routes.video_gen import _start_video_generation

        start_result = await _start_video_generation(
            {
                "storyboard_path": output_path,
                "seeddance_backend": job.get("seeddance_backend", ctx.preference.seeddance_backend or state["seeddance_backend"]),
                "seeddance_model": job.get("seeddance_model", "seedance-2.0"),
                "generation_mode": job.get("generation_mode", "parallel"),
                "owner_user_id": ctx.user.id,
            }
        )
        if isinstance(start_result, JSONResponse):
            try:
                err = json.loads(start_result.body.decode("utf-8"))
            except Exception:
                err = {"error": "Auto start video failed"}
            return JSONResponse(status_code=500, content=err)
        video_job_info = start_result
        creation_job_manager.update(job_id, video_job_id=start_result.get("job_id"))
        _save_jobs(user_id=ctx.user.id)

    # Non-one-click mode without auto_start_video: mark as waiting for manual video start
    waiting_for_video = not bool(video_job_info) and not job.get("auto_start_video")

    await broadcast_create_event(
        job_id,
        "done",
        {
            "output_path": output_path,
            "storyboard": storyboard,
            "auto_started_video": bool(video_job_info),
            "video_job": video_job_info,
            "waiting_for_video_start": waiting_for_video,
        },
    )
    return {"ok": True, "status": "completed", "video_job": video_job_info}


@router.post("/api/create/start-video/{job_id}")
async def api_create_start_video(job_id: str, body: dict = None, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Manually start video generation after storyboard creation (non-one-click mode)."""
    body = body or {}
    try:
        job = get_creation_job_for_user(job_id, ctx.user.id)
    except HTTPException as exc:
        return _json_http_error(exc)

    if job.get("phase") != "done":
        return JSONResponse(status_code=400, content={"error": "Job is not in done phase"})

    output_path = job.get("output_path", "")
    if not output_path:
        return JSONResponse(status_code=400, content={"error": "No storyboard output path"})

    from dashboard.routes.video_gen import _start_video_generation

    start_result = await _start_video_generation(
        {
            "storyboard_path": output_path,
            "seeddance_backend": body.get("seeddance_backend") or job.get("seeddance_backend", ctx.preference.seeddance_backend or state["seeddance_backend"]),
            "seeddance_model": body.get("seeddance_model") or job.get("seeddance_model", "seedance-2.0"),
            "generation_mode": body.get("generation_mode") or job.get("generation_mode", "parallel"),
            "owner_user_id": ctx.user.id,
        }
    )
    if isinstance(start_result, JSONResponse):
        try:
            err = json.loads(start_result.body.decode("utf-8"))
        except Exception:
            err = {"error": "Start video failed"}
        return JSONResponse(status_code=500, content=err)

    creation_job_manager.update(job_id, video_job_id=start_result.get("job_id"))
    _save_jobs(user_id=ctx.user.id)

    return {"ok": True, "video_job": start_result}


@router.post("/api/create/refine-narrative")
async def api_refine_narrative(body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Refine screenplay narrative based on user feedback via LLM."""
    job_id = body.get("job_id")
    screenplay = body.get("screenplay")
    user_feedback = body.get("user_feedback", "")
    chat_history = body.get("chat_history", [])

    if not screenplay or not user_feedback:
        return JSONResponse(status_code=400, content={"error": "Missing screenplay or feedback"})

    job = None
    if job_id:
        try:
            job = get_creation_job_for_user(job_id, ctx.user.id)
        except HTTPException as exc:
            return _json_http_error(exc)

    job_model = (job.get("model") if job else None) or _env("LLM_MODEL", "gemini-3-flash-preview")

    current_narrative = screenplay.get("narrative", "")
    from prompts.narrative_refine import NARRATIVE_REFINE_SYSTEM, NARRATIVE_REFINE_TEMPLATE

    prompt = NARRATIVE_REFINE_TEMPLATE.format(current_narrative=current_narrative, user_feedback=user_feedback)
    if chat_history:
        history_text = "\n".join([
            f"{'[用户]' if msg.get('role') == 'user' else '[编辑]'}: {msg.get('content', '')}"
            for msg in chat_history[-4:]
        ])
        prompt = f"【之前的修改记录】\n{history_text}\n\n{prompt}"

    retry_delays = [2, 5, 10, 10, 10]
    last_error = None

    for attempt in range(5):
        try:
            from clients import get_llm_client

            client = get_llm_client(step="screenplay_gen")
            def _refine_sync():
                from dashboard.usage_tracker import usage_context
                with usage_context(user_id=ctx.user.id, step="critic"):
                    return client.generate_text(
                        prompt=prompt,
                        system_instruction=NARRATIVE_REFINE_SYSTEM,
                        model=job_model,
                        temperature=0.4,
                    )
            refined_narrative = await asyncio.to_thread(_refine_sync)
            refined_narrative = _strip_narrative_artifacts(refined_narrative)
            screenplay["narrative"] = refined_narrative

            try:
                from dashboard.creation_engine import _make_engine
                _sync_engine = _make_engine("prompt", job_model)

                def _do_sync():
                    from dashboard.usage_tracker import usage_context
                    with usage_context(user_id=ctx.user.id, step="metadata_sync"):
                        _sync_engine.sync_screenplay_metadata(screenplay)
                await asyncio.to_thread(_do_sync)
            except Exception as sync_err:
                print(f"[refine_narrative] metadata sync failed (non-fatal): {sync_err}")

            if job_id and creation_job_manager.contains(job_id):
                creation_job_manager.update(job_id, screenplay_data=screenplay)
                _save_jobs(user_id=ctx.user.id)

            return {
                "ok": True,
                "screenplay": screenplay,
                "response": "Updated based on your feedback." + (f" (attempt {attempt + 1})" if attempt > 0 else ""),
            }
        except Exception as e:
            last_error = str(e)
            if attempt < 4:
                await asyncio.sleep(retry_delays[attempt])
            continue

    return JSONResponse(status_code=500, content={"error": f"Failed after 5 attempts: {last_error}"})


@router.get("/api/create/status/{job_id}")
async def api_create_status(job_id: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Poll creation job status."""
    try:
        job = get_creation_job_for_user(job_id, ctx.user.id)
    except HTTPException as exc:
        return _json_http_error(exc)
    return {
        "job_id": job_id,
        "status": job["status"],
        "phase": job["phase"],
        "output_path": job.get("output_path"),
        "error": job.get("error"),
        "stop_requested": job.get("stop_requested", False),
    }


@router.get("/api/create/jobs")
async def api_create_jobs(ctx: DashboardContext = Depends(get_dashboard_context)):
    """List all creation jobs for the current user."""
    return [_creation_job_payload(jid, job) for jid, job in list_creation_jobs_for_user(ctx.user.id)]


@router.get("/api/create/job/{job_id}")
async def api_create_job_detail(job_id: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Get full detail of a single creation job."""
    try:
        job = get_creation_job_for_user(job_id, ctx.user.id)
    except HTTPException as exc:
        return _json_http_error(exc)
    return _creation_job_payload(job_id, job)


@router.post("/api/create/regenerate-screenplay")
async def api_create_regenerate_screenplay(body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Regenerate screenplay from an existing storyboard path."""
    storyboard_path = body.get("storyboard_path", "")
    if not storyboard_path:
        return JSONResponse(status_code=400, content={"error": "Missing storyboard_path"})

    try:
        sb_abs = resolve_storyboard_path(ctx.workspace, storyboard_path, must_exist=True)
    except HTTPException as exc:
        return _json_http_error(exc)

    with open(sb_abs, "r", encoding="utf-8") as f:
        existing_sb = json.load(f)

    title = existing_sb.get("title", "untitled")
    stem = sb_abs.stem.replace("_storyboard", "")
    sp_path = sb_abs.parent / f"{stem}_screenplay.json"
    source_context = ""
    if sp_path.exists():
        try:
            with open(sp_path, "r", encoding="utf-8") as f:
                sp_data = json.load(f)
            source_context = sp_data.get("narrative", "")
        except Exception:
            pass

    if body.get("narrative"):
        source_context = body["narrative"]

    model = body.get("model") or _env("LLM_MODEL", "gemini-3-flash-preview")
    mode = body.get("mode", "prompt")
    duration = body.get("duration")
    backend = _backend_for_request(ctx, body)
    user_session_id = _user_backend_session(ctx, backend)

    if mode == "video" and not body.get("video_path"):
        return JSONResponse(status_code=400, content={"error": "video mode requires video_path"})

    job_id = str(uuid.uuid4())[:8]
    creation_job_manager.set(job_id, {
        "owner_user_id": ctx.user.id,
        "status": "queued",
        "mode": mode,
        "phase": "screenplay",
        "progress": [],
        "output_path": str(sb_abs),
        "review_dir": str(sb_abs.parent / title),
        "error": None,
        "title": title,
        "paused": False,
        "screenplay_data": None,
        "screenplay_dir": str(sb_abs.parent),
        "source_context": source_context,
        "model": model,
        "one_click": False,
        "video_mode": body.get("video_mode", "replicate"),
        "auto_start_video": False,
        "generation_mode": body.get("generation_mode", "parallel"),
        "seeddance_backend": backend,
        "duration": duration,
        "video_job_id": None,
        "stop_requested": False,
        "queued_at": datetime.now().isoformat(),
    })
    _save_jobs(user_id=ctx.user.id)

    params = {
        "mode": mode,
        "title": title,
        "model": model,
        "one_click": False,
        "auto_start_video": False,
        "generation_mode": body.get("generation_mode", "parallel"),
        "seeddance_backend": backend,
        "duration": duration,
    }
    if mode == "novel":
        params["chapter_text"] = source_context
    elif mode == "video":
        try:
            params["video_path"] = str(resolve_upload_path(ctx.workspace, body.get("video_path", ""), must_exist=True))
        except Exception:
            creation_job_manager.pop(job_id, None)
            _save_jobs(user_id=ctx.user.id)
            return JSONResponse(status_code=400, content={"error": "Invalid video_path"})
        params["video_mode"] = body.get("video_mode", "replicate")
        params["recreate_direction"] = body.get("recreate_direction", "")
    else:
        params["idea"] = source_context

    creation_job_manager.update(job_id, _params=params)
    _save_jobs(user_id=ctx.user.id)
    return {"ok": True, "job_id": job_id}


@router.post("/api/create/regenerate-storyboard")
async def api_create_regenerate_storyboard(body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Regenerate storyboard from existing/edited screenplay data."""
    storyboard_path = body.get("storyboard_path", "")
    screenplay = body.get("screenplay")
    if not storyboard_path:
        return JSONResponse(status_code=400, content={"error": "Missing storyboard_path"})
    if not screenplay:
        return JSONResponse(status_code=400, content={"error": "Missing screenplay data"})

    try:
        sb_abs = resolve_storyboard_path(ctx.workspace, storyboard_path, must_exist=False)
    except HTTPException as exc:
        return _json_http_error(exc)

    title = screenplay.get("title", "untitled")
    model = body.get("model") or _env("LLM_MODEL", "gemini-3-flash-preview")
    mode = body.get("mode", "prompt")
    duration = body.get("duration")
    backend = _backend_for_request(ctx, body)
    user_session_id = _user_backend_session(ctx, backend)

    job_id = str(uuid.uuid4())[:8]
    creation_job_manager.set(job_id, {
        "owner_user_id": ctx.user.id,
        "status": "queued",
        "mode": mode,
        "phase": "storyboard",
        "progress": [],
        "output_path": str(sb_abs),
        "review_dir": str(sb_abs.parent / title),
        "error": None,
        "title": title,
        "paused": False,
        "screenplay_data": screenplay,
        "screenplay_dir": str(sb_abs.parent),
        "source_context": screenplay.get("narrative", ""),
        "model": model,
        "one_click": False,
        "video_mode": body.get("video_mode", "replicate"),
        "auto_start_video": False,
        "generation_mode": body.get("generation_mode", "parallel"),
        "seeddance_backend": backend,
        "duration": duration,
        "video_job_id": None,
        "stop_requested": False,
        "queued_at": datetime.now().isoformat(),
    })
    _save_jobs(user_id=ctx.user.id)

    params = {
        "mode": mode,
        "title": title,
        "screenplay": screenplay,
        "output_path": str(sb_abs),
        "source_context": screenplay.get("narrative", ""),
        "model": model,
        "one_click": False,
        "auto_start_video": False,
        "generation_mode": body.get("generation_mode", "parallel"),
        "seeddance_backend": backend,
        "duration": duration,
    }
    creation_job_manager.update(job_id, _params=params)
    _save_jobs(user_id=ctx.user.id)
    # Scheduler will pick this up — params contain screenplay + output_path so it routes to _continue_generation.
    return {"ok": True, "job_id": job_id}
