"""
Creation engine — storyboard generation job orchestration.

All job state mutations go through creation_job_manager for thread safety.
Includes a creation_job_scheduler that enforces per-user and global concurrency.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi.responses import JSONResponse

from dashboard.persistence import _save_jobs
from dashboard.state import (
    STORYBOARDS_DIR,
    creation_job_manager,
    state,
    DEFAULT_MAX_RUNNING_CREATION_JOBS_GLOBAL,
    DEFAULT_MAX_RUNNING_CREATION_JOBS_PER_USER,
)
from dashboard.watchers import broadcast
from dashboard.workspace import get_workspace_by_user_id
from contextlib import contextmanager

from tools.storyboard_gen.base_engine import GenerationStoppedError
from tools.storyboard_gen.schemas import AUTO_VIDEO_STYLE, normalize_style_choice


@contextmanager
def _nullcontext():
    yield


# ── Log capture for streaming stdout to the frontend ─────────────────────

_tls = threading.local()
_original_stdout = sys.stdout


class _LogRouter(io.TextIOBase):
    """A sys.stdout replacement that checks thread-local storage for a queue.

    Installed once at module level. Each worker thread sets
    ``_tls.log_queue`` while running; if present, writes are duplicated
    to that queue. Otherwise output goes straight to the real stdout.
    This avoids the race of swapping sys.stdout per-thread.
    """

    def write(self, s):
        if s:
            _original_stdout.write(s)
            q = getattr(_tls, "log_queue", None)
            if q is not None:
                q.put_nowait(s)
        return len(s) if s else 0

    def flush(self):
        _original_stdout.flush()

    def fileno(self):
        return _original_stdout.fileno()

    @property
    def encoding(self):
        return getattr(_original_stdout, "encoding", "utf-8")


sys.stdout = _LogRouter()


class LogCapture:
    """Context manager: registers a queue on the current thread so print()
    output is duplicated into it.

    Usage (in a worker thread)::

        q = queue.Queue()
        with LogCapture(q):
            engine.run(...)     # print() inside → q AND real stdout
    """

    def __init__(self, log_queue: queue.Queue):
        self._q = log_queue

    def __enter__(self):
        _tls.log_queue = self._q
        return self

    def __exit__(self, *exc):
        _tls.log_queue = None


async def _drain_log_queue(job_id: str, log_queue: queue.Queue, stop_event: asyncio.Event):
    """Async task: drain queued log lines and broadcast them to the frontend."""
    buf = ""
    while not stop_event.is_set() or not log_queue.empty():
        try:
            while True:
                chunk = log_queue.get_nowait()
                buf += chunk
        except queue.Empty:
            pass

        if buf:
            lines = buf.split("\n")
            if buf.endswith("\n"):
                to_send = "\n".join(lines[:-1])
                buf = ""
            else:
                to_send = "\n".join(lines[:-1])
                buf = lines[-1]

            if to_send.strip():
                await broadcast_create_event(job_id, "log", {"text": to_send})

        if not stop_event.is_set():
            await asyncio.sleep(0.3)

    if buf.strip():
        await broadcast_create_event(job_id, "log", {"text": buf})

_CREATION_RUNNING_STATUSES = {"running", "pausing", "stopping"}

_MODE_SOURCE_LABEL = {
    "prompt": "prompt_storyboard_gen",
    "novel": "novel_storyboard_gen",
    "video": "video_storyboard_gen",
}


def _make_engine(mode: str, model: str, stop_checker=None, progress_callback=None):
    """统一的 engine 工厂，所有地方都从这里获取 engine 实例。"""
    from tools.storyboard_gen import (
        NovelStoryboardEngine,
        PromptStoryboardEngine,
        VideoStoryboardEngine,
    )
    cls_map = {
        "prompt": PromptStoryboardEngine,
        "novel": NovelStoryboardEngine,
        "video": VideoStoryboardEngine,
    }
    cls = cls_map.get(mode)
    if not cls:
        raise ValueError(f"Unknown mode: {mode}")
    return cls(llm_model=model, stop_checker=stop_checker, progress_callback=progress_callback)
# Registry of active asyncio tasks so the scheduler can track them.
_active_creation_tasks: dict[str, asyncio.Task] = {}


# ── Helpers ──────────────────────────────────────────────────────────────


def _job_owner_user_id(job: Optional[dict]) -> Optional[int]:
    if not job:
        return None
    owner = job.get("owner_user_id")
    return int(owner) if owner is not None else None


def _job_workspace(job: Optional[dict]):
    owner = _job_owner_user_id(job)
    if owner is None:
        return None
    try:
        return get_workspace_by_user_id(owner)
    except Exception:
        return None


async def broadcast_create_event(job_id: str, phase: str, data):
    """Push a creation progress event to the owning user's WebSocket clients."""
    job = creation_job_manager.get(job_id) or {}
    msg = {"type": "create_progress", "job_id": job_id, "phase": phase, "data": data}
    await broadcast(msg, user_id=_job_owner_user_id(job))


def _is_creation_job_stop_requested(job_id: str) -> bool:
    job = creation_job_manager.get(job_id) or {}
    return bool(job.get("stop_requested"))


def _mark_creation_job_stopped(job_id: str, *, phase: str = "stopped", message: str = "用户已停止生成") -> str:
    """Mark a creation job as stopped or paused (if it was in pausing state).
    Returns the actual status the job landed in: 'paused' or 'stopped'."""
    job = creation_job_manager.get(job_id)
    if not job:
        return "stopped"
    # If the job was in "pausing" state, land it as "paused" (not "stopped")
    # so other jobs can be scheduled and the user can resume later.
    if job.get("status") == "pausing":
        creation_job_manager.update(
            job_id,
            status="paused",
            phase="paused",
            paused=True,
            stop_requested=False,
            error=None,
        )
        _save_jobs(user_id=_job_owner_user_id(job))
        return "paused"
    else:
        creation_job_manager.update(
            job_id,
            status="stopped",
            phase=phase,
            paused=False,
            stop_requested=False,
            error=message,
        )
        _save_jobs(user_id=_job_owner_user_id(job))
        return "stopped"


# ── File watcher ─────────────────────────────────────────────────────────


_WATCHER_SKIP_STATUSES = frozenset({"paused", "completed", "failed", "stopped", "interrupted"})


async def watch_creation_files(job_id: str, review_dir: Path, screenplay_dir: Path):
    """Watch for intermediate artifacts produced by the storyboard generation engine."""
    seen: set[str] = set()
    watcher_started_at = time.time()
    screenplay_event_sent = False
    while True:
        await asyncio.sleep(1)
        if _is_creation_job_stop_requested(job_id):
            return

        job = creation_job_manager.get(job_id) or {}

        if job.get("status") in _WATCHER_SKIP_STATUSES:
            return

        output_path = job.get("output_path") or ""
        expected_screenplay_name = ""
        if output_path:
            expected_screenplay_name = Path(output_path).name.replace("_storyboard.json", "_screenplay.json")

        if not screenplay_event_sent and expected_screenplay_name and screenplay_dir.exists():
            f = screenplay_dir / expected_screenplay_name
            if f.exists() and f.is_file():
                try:
                    if f.stat().st_mtime >= watcher_started_at and f.name not in seen:
                        seen.add(f.name)
                        # Atomically check-and-set phase to screenplay_done.
                        # If the engine thread already advanced beyond screenplay
                        # (e.g. to screenplay_review), the patch is a no-op,
                        # avoiding the TOCTOU race of a separate get→update.
                        _ADVANCED_PHASES = ("screenplay_review", "storyboard_review", "storyboard", "done")
                        skipped = [False]
                        def _set_screenplay_done(j):
                            if j.get("phase") in _ADVANCED_PHASES or j.get("status") in ("paused", "completed"):
                                skipped[0] = True
                                return
                            j["phase"] = "screenplay_done"
                        creation_job_manager.patch(job_id, _set_screenplay_done)
                        if skipped[0]:
                            screenplay_event_sent = True
                            continue
                        with open(f, "r", encoding="utf-8") as fh:
                            d = json.load(fh)
                        _save_jobs(user_id=_job_owner_user_id(job))
                        await broadcast_create_event(job_id, "screenplay_done", d)
                        screenplay_event_sent = True
                except Exception:
                    pass

        if review_dir.exists():
            for f in sorted(review_dir.iterdir()):
                if f.suffix == ".json" and f.name not in seen:
                    seen.add(f.name)
                    try:
                        with open(f, "r", encoding="utf-8") as fh:
                            d = json.load(fh)
                        phase_name = f.stem
                        creation_job_manager.update(job_id, phase=phase_name)
                        _save_jobs(user_id=_job_owner_user_id(job))
                        await broadcast_create_event(job_id, phase_name, d)
                    except Exception:
                        pass


# ── Sync engine runner ───────────────────────────────────────────────────


def _run_engine_sync(params: dict, job_id: str, log_queue: Optional[queue.Queue] = None, _loop=None):
    """Run the storyboard generation engine synchronously (called in a thread).

    IMPORTANT: This runs in a worker thread via asyncio.to_thread.
    All job state mutations MUST go through creation_job_manager (which holds a lock).
    Never hold a direct reference to the job dict.
    """
    from clients.llm_client import llm_cancel_scope
    from dashboard.usage_tracker import usage_context

    workspace_owner = creation_job_manager.get(job_id) or {}
    owner_uid = _job_owner_user_id(workspace_owner) or 0
    workspace = _job_workspace(workspace_owner)
    storyboards_dir = workspace.storyboards_dir if workspace else STORYBOARDS_DIR

    def _update_job(**kw):
        """Thread-safe job update helper."""
        creation_job_manager.update(job_id, **kw)

    def _get_job_field(key, default=None):
        """Thread-safe job field read helper."""
        j = creation_job_manager.get(job_id)
        return j.get(key, default) if j else default

    def ensure_not_stopped():
        return _is_creation_job_stop_requested(job_id)

    def _progress_callback(phase: str, data: dict):
        if _loop and not _loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                broadcast_create_event(job_id, phase, data), _loop
            )

    # Wrap the entire engine run in an LLM cancel scope so that
    # cancel_llm_scope(job_id) from the pause/stop route can
    # immediately abort in-flight LLM HTTP calls.
    with llm_cancel_scope(job_id, checker=ensure_not_stopped), \
         usage_context(user_id=owner_uid, step="creation"):

        def sync_generated_title(screenplay: dict):
            generated_title = (screenplay or {}).get("title", "").strip()
            requested_title = (title or "").strip()
            if not generated_title or generated_title == "untitled":
                return
            if requested_title and requested_title != "untitled":
                return

            old_stem = requested_title or "untitled"
            _update_job(
                title=generated_title,
                output_path=os.path.join(output_dir, f"{generated_title}_storyboard.json"),
                review_dir=str(storyboards_dir / generated_title),
            )

            old_sp_json = os.path.join(output_dir, f"{old_stem}_screenplay.json")
            old_sp_txt = os.path.join(output_dir, f"{old_stem}_screenplay.txt")
            new_sp_json = os.path.join(output_dir, f"{generated_title}_screenplay.json")
            new_sp_txt = os.path.join(output_dir, f"{generated_title}_screenplay.txt")
            for old_f, new_f in [(old_sp_json, new_sp_json), (old_sp_txt, new_sp_txt)]:
                if os.path.exists(old_f) and old_f != new_f:
                    os.rename(old_f, new_f)
            print(f"[Dashboard] 使用 LLM 生成的标题: {generated_title}")

        mode = params["mode"]
        title = params.get("title", "")
        style = normalize_style_choice(params.get("style", AUTO_VIDEO_STYLE))
        style_hint = params.get("style_hint", "")
        duration = params.get("duration")
        quickchat = bool(params.get("quickchat", False))
        one_click = bool(params.get("one_click", False))
        output_dir = str(storyboards_dir)
        output_path = os.path.join(output_dir, f"{title or 'untitled'}_storyboard.json")
        _update_job(output_path=output_path)

        review_dir = storyboards_dir / (title or "untitled")
        _update_job(review_dir=str(review_dir), screenplay_dir=str(storyboards_dir))

        from clients import _env
        engine = _make_engine(mode, params.get("model") or _env("LLM_MODEL", "gemini-3-flash-preview"), ensure_not_stopped, _progress_callback)
        _update_job(phase="screenplay")

        # ── Screenplay generation (no log streaming — screenplay is fast) ──
        if mode == "prompt":
            idea = params.get("idea", "")
            screenplay = engine.generate_screenplay(
                prompt_text=idea,
                output_path=output_path,
                video_style=style,
                style_hint=style_hint,
                target_duration=None if quickchat else (duration or 60),
                title=title,
                num_scenes=params.get("num_scenes"),
                save=True,
            )
        elif mode == "novel":
            chapter_text = params.get("chapter_text", "")
            screenplay = engine.generate_screenplay(
                chapter_text=chapter_text,
                output_path=output_path,
                video_style=style,
                style_hint=style_hint,
                target_duration=duration,
                title=title,
                save=True,
            )
        elif mode == "video":
            from tools.storyboard_gen import StoryboardMode
            video_path = params.get("video_path", "")
            video_mode = StoryboardMode(params.get("video_mode", "replicate"))
            recreate_direction = params.get("recreate_direction", "")
            screenplay = engine.generate_screenplay(
                video_path=video_path,
                output_path=output_path,
                num_scenes=params.get("num_scenes"),
                total_duration=duration,
                mode=video_mode,
                recreate_direction=recreate_direction,
                video_style=style,
                style_hint=style_hint,
                save=True,
            )
        else:
            raise ValueError(f"Unknown mode: {mode}")

        _update_job(screenplay_data=screenplay)
        sync_generated_title(screenplay)

        if not one_click:
            _update_job(status="paused", paused=True, phase="screenplay_review")
            _save_jobs(user_id=_job_owner_user_id(creation_job_manager.get(job_id)))
            return {"paused": True, "screenplay": screenplay}

        # ── Storyboard generation (with log streaming — this is slow) ──
        current_output = _get_job_field("output_path", output_path)
        current_title = _get_job_field("title", title)
        with (LogCapture(log_queue) if log_queue else _nullcontext()):
            if mode == "prompt":
                result = engine.generate(
                    prompt_text=idea,
                    output_path=current_output,
                    video_style=style,
                    style_hint=style_hint,
                    target_duration=None if quickchat else (duration or 60),
                    title=current_title,
                    num_scenes=params.get("num_scenes"),
                    screenplay_data=screenplay,
                )
            elif mode == "novel":
                result = engine.generate(
                    chapter_text=chapter_text,
                    output_path=current_output,
                    video_style=style,
                    style_hint=style_hint,
                    target_duration=duration,
                    title=current_title,
                    screenplay_data=screenplay,
                )
            else:  # video
                result = engine.generate(
                    video_path=video_path,
                    output_path=current_output,
                    num_scenes=params.get("num_scenes"),
                    total_duration=duration,
                    mode=video_mode,
                    screenplay_data=screenplay,
                    recreate_direction=recreate_direction,
                    video_style=style,
                    style_hint=style_hint,
                )

        if isinstance(result, dict):
            actual_title = result.get("title", "")
            if actual_title and actual_title != title:
                new_path = os.path.join(output_dir, f"{actual_title}_storyboard.json")
                if os.path.exists(new_path):
                    _update_job(title=actual_title, output_path=new_path, review_dir=str(storyboards_dir / actual_title))

        return result


# ── Async job runner ─────────────────────────────────────────────────────


async def run_creation_job(job_id: str, params: dict):
    """Run a creation job in a background thread with file watching."""
    job_snap = creation_job_manager.get(job_id)
    if not job_snap:
        return
    workspace = _job_workspace(job_snap)
    storyboards_dir = workspace.storyboards_dir if workspace else STORYBOARDS_DIR
    owner_user_id = _job_owner_user_id(job_snap)

    creation_job_manager.update(job_id, phase="starting", status="running")
    _save_jobs(user_id=owner_user_id)
    await broadcast_create_event(job_id, "starting", {})
    # Immediately also broadcast 'screenplay' so the frontend always sees it
    # even if it missed the 'starting' event (e.g. due to a brief WS reconnect).
    creation_job_manager.update(job_id, phase="screenplay")
    await broadcast_create_event(job_id, "screenplay", {})

    title = params.get("title", "") or "untitled"
    review_dir = storyboards_dir / title
    screenplay_dir = storyboards_dir

    if review_dir.exists():
        import shutil

        backup_name = f"{title}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        backup_dir = storyboards_dir / backup_name
        shutil.move(str(review_dir), str(backup_dir))

    watch_task = asyncio.create_task(watch_creation_files(job_id, review_dir, screenplay_dir))

    one_click = bool(params.get("one_click", False))
    if one_click:
        log_queue: Optional[queue.Queue] = queue.Queue()
        log_stop: Optional[asyncio.Event] = asyncio.Event()
        log_task: Optional[asyncio.Task] = asyncio.create_task(
            _drain_log_queue(job_id, log_queue, log_stop)
        )
    else:
        log_queue = None
        log_stop = None
        log_task = None

    try:
        _loop = asyncio.get_running_loop()
        result = await asyncio.to_thread(_run_engine_sync, params, job_id, log_queue, _loop)

        # Stop log drain BEFORE broadcasting any terminal event so late
        # log messages don't overwrite the UI the event renders.
        if log_stop:
            log_stop.set()
        if log_task:
            await log_task
            log_task = None

        if _is_creation_job_stop_requested(job_id):
            actual_status = _mark_creation_job_stopped(job_id)
            if actual_status == "paused":
                await broadcast_create_event(job_id, "paused", {"message": "任务已暂停，可随时继续"})
            else:
                await broadcast_create_event(job_id, "stopped", {"message": "用户已停止生成"})
            return

        if isinstance(result, dict) and result.get("paused"):
            current_title = (creation_job_manager.get(job_id) or {}).get("title", "")
            await broadcast_create_event(
                job_id,
                "screenplay_review",
                {"screenplay": result.get("screenplay"), "title": current_title},
            )
            return

        creation_job_manager.update(job_id, status="completed", phase="done")
        _save_jobs(user_id=owner_user_id)
        job_now = creation_job_manager.get(job_id) or {}
        output_path = job_now.get("output_path", "")
        final_data = {}
        if output_path and Path(output_path).exists():
            with open(output_path, "r", encoding="utf-8") as f:
                final_data = json.load(f)

        video_job_info = None
        if job_now.get("auto_start_video") and output_path:
            from dashboard.routes.video_gen import _start_video_generation

            start_result = await _start_video_generation(
                {
                    "storyboard_path": output_path,
                    "seeddance_backend": job_now.get("seeddance_backend", state["seeddance_backend"]),
                    "generation_mode": job_now.get("generation_mode", "parallel"),
                    "owner_user_id": owner_user_id,
                }
            )
            if isinstance(start_result, JSONResponse):
                try:
                    err = json.loads(start_result.body.decode("utf-8"))
                except Exception:
                    err = {"error": "Auto start video failed"}
                raise RuntimeError(err.get("error", "Auto start video failed"))
            video_job_info = start_result
            creation_job_manager.update(job_id, video_job_id=start_result.get("job_id"))
            _save_jobs(user_id=owner_user_id)

        await broadcast_create_event(
            job_id,
            "done",
            {
                "output_path": output_path,
                "storyboard": final_data,
                "auto_started_video": bool(video_job_info),
                "video_job": video_job_info,
            },
        )
    except GenerationStoppedError:
        actual_status = _mark_creation_job_stopped(job_id)
        if actual_status == "paused":
            await broadcast_create_event(job_id, "paused", {"message": "任务已暂停，可随时继续"})
        else:
            await broadcast_create_event(job_id, "stopped", {"message": "用户已停止生成"})
    except Exception as e:
        creation_job_manager.update(
            job_id, status="failed", phase="error", error=str(e), stop_requested=False,
        )
        _save_jobs(user_id=owner_user_id)
        await broadcast_create_event(job_id, "error", {"error": str(e)})
    finally:
        if log_stop and not log_stop.is_set():
            log_stop.set()
        if log_task and not log_task.done():
            await log_task
        watch_task.cancel()
        _active_creation_tasks.pop(job_id, None)


# ── Continue generation (screenplay → storyboard) ────────────────────────


async def continue_generation(job_id: str, params: dict):
    """Continue storyboard generation from an existing screenplay.

    Moved here from routes/creation.py so all engine orchestration lives in one place.
    """
    job = creation_job_manager.get(job_id)
    if not job:
        return
    owner_user_id = _job_owner_user_id(job)

    creation_job_manager.update(job_id, status="running", phase="storyboard")

    workspace = get_workspace_by_user_id(owner_user_id) if owner_user_id is not None else None
    storyboards_dir = workspace.storyboards_dir if workspace else STORYBOARDS_DIR

    log_queue = queue.Queue()
    log_stop = asyncio.Event()
    log_task = asyncio.create_task(_drain_log_queue(job_id, log_queue, log_stop))

    try:
        review_dir = Path(job.get("review_dir", ""))
        if review_dir.exists():
            import shutil
            backup_name = f"{review_dir.name}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.move(str(review_dir), str(review_dir.parent / backup_name))

        mode = params.get("mode", "prompt")
        from clients import _env
        model = params.get("model") or _env("LLM_MODEL", "gemini-3-flash-preview")
        source_context = params.get("source_context", "")
        source_label = _MODE_SOURCE_LABEL.get(mode, "prompt_storyboard_gen")

        _cont_loop = asyncio.get_running_loop()
        engine = _make_engine(mode, model, lambda: _is_creation_job_stop_requested(job_id))

        def _progress_callback_continue(phase: str, data: dict):
            if not _cont_loop.is_closed():
                asyncio.run_coroutine_threadsafe(
                    broadcast_create_event(job_id, phase, data), _cont_loop
                )

        engine.progress_callback = _progress_callback_continue

        screenplay = params["screenplay"]
        output_path = params["output_path"]
        one_click = bool(params.get("one_click", False))
        duration = params.get("duration")
        if duration:
            try:
                screenplay.setdefault("_meta", {})["target_duration_seconds"] = round(float(duration), 1)
            except (TypeError, ValueError):
                pass

        def _sync_metadata():
            from dashboard.usage_tracker import usage_context
            with LogCapture(log_queue), \
                 usage_context(user_id=owner_user_id or 0, step="metadata_sync"):
                engine.sync_screenplay_metadata(screenplay)
        await asyncio.to_thread(_sync_metadata)
        creation_job_manager.update(job_id, screenplay_data=screenplay)
        _save_jobs(user_id=owner_user_id)

        def _run_continue_sync():
            from dashboard.usage_tracker import usage_context
            with LogCapture(log_queue), \
                 usage_context(user_id=owner_user_id or 0, step="storyboard_gen"):
                return engine.screenplay_to_storyboard(
                    screenplay_data=screenplay,
                    output_path=output_path,
                    source_context=source_context,
                    source_label=source_label,
                )
        result = await asyncio.to_thread(_run_continue_sync)

        # Stop log drain BEFORE broadcasting terminal events so late log
        # messages don't recreate the log viewer and overwrite the new UI.
        log_stop.set()
        await log_task
        log_task = None

        if _is_creation_job_stop_requested(job_id):
            actual_status = _mark_creation_job_stopped(job_id)
            if actual_status == "paused":
                await broadcast_create_event(job_id, "paused", {"message": "任务已暂停，可随时继续"})
            else:
                await broadcast_create_event(job_id, "stopped", {"message": "用户已停止生成"})
            return

        actual_output_path = output_path
        actual_title = result.get("title", "") if isinstance(result, dict) else ""
        if actual_title and actual_title != "untitled" and storyboards_dir:
            maybe_new = storyboards_dir / f"{actual_title}_storyboard.json"
            if maybe_new.exists():
                actual_output_path = str(maybe_new)
                creation_job_manager.update(job_id, title=actual_title, output_path=actual_output_path)

        if one_click:
            creation_job_manager.update(
                job_id,
                storyboard_data=result, paused=False, status="completed",
                phase="done", output_path=actual_output_path,
            )
            _save_jobs(user_id=owner_user_id)

            video_job_info = None
            job_now = creation_job_manager.get(job_id) or {}
            if job_now.get("auto_start_video"):
                from dashboard.routes.video_gen import _start_video_generation
                import json as _json
                start_result = await _start_video_generation(
                    {
                        "storyboard_path": actual_output_path,
                        "seeddance_backend": job_now.get("seeddance_backend", state["seeddance_backend"]),
                        "seeddance_model": job_now.get("seeddance_model", "seedance-2.0"),
                        "generation_mode": job_now.get("generation_mode", "parallel"),
                        "owner_user_id": owner_user_id,
                    }
                )
                if isinstance(start_result, JSONResponse):
                    try:
                        err = _json.loads(start_result.body.decode("utf-8"))
                    except Exception:
                        err = {"error": "Auto start video failed"}
                    raise RuntimeError(err.get("error", "Auto start video failed"))
                video_job_info = start_result
                creation_job_manager.update(job_id, video_job_id=start_result.get("job_id"))
                _save_jobs(user_id=owner_user_id)

            await broadcast_create_event(
                job_id, "done",
                {
                    "output_path": actual_output_path,
                    "storyboard": result,
                    "auto_started_video": bool(video_job_info),
                    "video_job": video_job_info,
                    "waiting_for_video_start": False,
                },
            )
            return

        creation_job_manager.update(
            job_id,
            status="paused", phase="storyboard_review",
            storyboard_data=result, paused=True, output_path=actual_output_path,
        )
        _save_jobs(user_id=owner_user_id)
        await broadcast_create_event(
            job_id, "storyboard_review",
            {"storyboard": result, "output_path": actual_output_path},
        )

    except GenerationStoppedError:
        actual_status = _mark_creation_job_stopped(job_id)
        if actual_status == "paused":
            await broadcast_create_event(job_id, "paused", {"message": "任务已暂停，可随时继续"})
        else:
            await broadcast_create_event(job_id, "stopped", {"message": "用户已停止生成"})
    except Exception as e:
        creation_job_manager.update(
            job_id, status="failed", phase="error", error=str(e), stop_requested=False,
        )
        _save_jobs(user_id=owner_user_id)
        await broadcast_create_event(job_id, "error", {"error": str(e)})
    finally:
        if not log_stop.is_set():
            log_stop.set()
        if log_task and not log_task.done():
            await log_task


# ── Scheduler ────────────────────────────────────────────────────────────


def _running_creation_global_count() -> int:
    return creation_job_manager.count_status(_CREATION_RUNNING_STATUSES)


def _running_creation_for_user_count(user_id: int) -> int:
    return creation_job_manager.count_status_for_user(int(user_id), _CREATION_RUNNING_STATUSES)


async def _launch_creation_job(job_id: str) -> bool:
    """Transition a queued creation job to running and start its async task."""
    job = creation_job_manager.get(job_id)
    if not job or job.get("status") != "queued":
        return False
    params = job.get("_params")
    if not params:
        creation_job_manager.update(job_id, status="failed", error="Missing _params")
        _save_jobs(user_id=_job_owner_user_id(job))
        return False

    # Immediately mark as running so the scheduler won't re-launch on next tick.
    creation_job_manager.update(job_id, status="running", phase="starting")

    # If params contain a screenplay, this is a "continue" job — use continue_generation.
    # Otherwise it's a fresh creation job — use run_creation_job.
    if params.get("screenplay") and params.get("output_path"):
        task = asyncio.create_task(continue_generation(job_id, params))
    else:
        task = asyncio.create_task(run_creation_job(job_id, params))
    _active_creation_tasks[job_id] = task
    return True


_INTERMEDIATE_STATE_TIMEOUT = 120  # seconds — max time a job can stay in pausing/stopping


def _reconcile_intermediate_creation_states() -> None:
    """Force-converge jobs stuck in pausing/stopping past the timeout."""
    now = time.time()
    for jid, job in creation_job_manager.all_items():
        st = job.get("status")
        if st not in ("pausing", "stopping"):
            continue
        requested_at = job.get("stop_requested_at") or 0
        if requested_at and (now - requested_at) < _INTERMEDIATE_STATE_TIMEOUT:
            # Still within grace period — check if the task already finished.
            task = _active_creation_tasks.get(jid)
            if task and not task.done():
                continue
        # Task is done or timeout exceeded — converge the state.
        owner = job.get("owner_user_id")
        if st == "pausing":
            creation_job_manager.update(jid, status="paused", paused=True, stop_requested=False, phase="interrupted")
        else:
            creation_job_manager.update(jid, status="stopped", stop_requested=False, phase="stopped")
        _active_creation_tasks.pop(jid, None)
        _save_jobs(user_id=owner)


async def creation_job_scheduler() -> None:
    """Background loop: start queued creation jobs under concurrency limits."""
    while True:
        try:
            await asyncio.sleep(1)

            # Converge any jobs stuck in pausing/stopping.
            _reconcile_intermediate_creation_states()

            queued = [
                (jid, job)
                for jid, job in creation_job_manager.all_items()
                if job.get("status") == "queued"
            ]
            queued.sort(key=lambda item: (item[1].get("queued_at") or "", item[0]))

            running_global = _running_creation_global_count()
            if running_global >= DEFAULT_MAX_RUNNING_CREATION_JOBS_GLOBAL:
                continue

            for jid, job in queued:
                owner_user_id = job.get("owner_user_id")
                if owner_user_id is None:
                    continue
                if running_global >= DEFAULT_MAX_RUNNING_CREATION_JOBS_GLOBAL:
                    break
                if _running_creation_for_user_count(int(owner_user_id)) >= DEFAULT_MAX_RUNNING_CREATION_JOBS_PER_USER:
                    continue
                launched = await _launch_creation_job(jid)
                if launched:
                    running_global += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            import traceback
            traceback.print_exc()
