"""
Video Director Agent — Live Dashboard Server (single-user mode)

Usage:
    python -m dashboard.server
    python -m dashboard.server --port 9000
"""

from __future__ import annotations

import argparse
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from dashboard.auth import AuthError
from dashboard.persistence import _load_jobs, _load_video_jobs
from dashboard.routes import all_routers
from dashboard.state import STATIC_DIR, set_web_worker_mode
from dashboard.watchers import file_watcher, _video_jobs_watcher
from dashboard.routes.video_gen import video_job_scheduler
from dashboard.creation_engine import creation_job_scheduler


def _enforce_single_worker_runtime() -> None:
    worker_hint = os.environ.get("UVICORN_WORKERS") or os.environ.get("WEB_CONCURRENCY") or "1"
    try:
        workers = int(worker_hint)
    except ValueError:
        workers = 1
    if workers > 1:
        raise RuntimeError(
            "Dashboard 当前仍依赖进程内状态与 WebSocket 连接管理，暂不支持多个 Web workers。"
            " 请先使用单 worker 部署。"
        )
    set_web_worker_mode("single")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _enforce_single_worker_runtime()

    # Load .env file — this is the single source of truth for all API keys
    # and provider configuration. os.environ takes precedence if already set
    # (e.g. from shell export), so use override=False.
    from dashboard.env_store import ENV_FILE
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=ENV_FILE, override=False)
    except ImportError:
        # python-dotenv not installed — parse manually
        from dashboard.env_store import read_env
        import os as _os
        for k, v in read_env().items():
            _os.environ.setdefault(k, v)

    _load_jobs()
    _load_video_jobs()
    task = asyncio.create_task(file_watcher())
    vj_task = asyncio.create_task(_video_jobs_watcher())
    scheduler_task = asyncio.create_task(video_job_scheduler())
    creation_scheduler_task = asyncio.create_task(creation_job_scheduler())
    yield
    task.cancel()
    vj_task.cancel()
    scheduler_task.cancel()
    creation_scheduler_task.cancel()


app = FastAPI(title="Video Director Dashboard", lifespan=lifespan)


@app.middleware("http")
async def handle_auth_errors(request: Request, call_next):
    try:
        response = await call_next(request)
    except AuthError as exc:
        response = JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
    return response


for r in all_routers:
    app.include_router(r)


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


app.mount("/static", NoCacheStaticFiles(directory=str(STATIC_DIR)), name="static")


def main():
    parser = argparse.ArgumentParser(description="Video Director Dashboard")
    parser.add_argument("--storyboard", default=None, help="Path to storyboard JSON")
    parser.add_argument("--output", default="./output", help="Output base directory")
    parser.add_argument("--run", default=None, help="Specific run ID")
    parser.add_argument("--port", type=int, default=8501, help="Port to serve on")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--workers", type=int, default=1, help="Web workers (must be 1)")
    args = parser.parse_args()

    if args.workers != 1:
        raise SystemExit("当前版本仅支持单个 dashboard worker，请使用 --workers 1。")

    print("\n  Video Director Dashboard (single-user mode)")
    print(f"  Dashboard  : http://localhost:{args.port}\n")

    os.environ["VIDEO_AGENT_DASHBOARD_PORT"] = str(args.port)

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", workers=1)


if __name__ == "__main__":
    main()
