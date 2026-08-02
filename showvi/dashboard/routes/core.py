"""Core routes — index, snapshot, projects, WebSocket, media serving."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, RedirectResponse
from starlette.responses import Response

from dashboard.auth import get_current_user_ws, AuthError
from dashboard.user_stub import SINGLE_USER
from dashboard.deps import DashboardContext, get_dashboard_context
from dashboard.helpers.project import _list_all_projects, _switch_to_project
from dashboard.helpers.reconciliation import build_snapshot, build_snapshot_async
from dashboard.state import connection_manager, get_monitor_state
from dashboard.watchers import broadcast_snapshot_to_user
from dashboard.workspace import get_workspace_for_user, resolve_asset_path, resolve_user_path

router = APIRouter()

STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"


@router.get("/")
async def index():
    return FileResponse(STATIC_ROOT / "index.html")

@router.get("/api/snapshot")
async def api_snapshot(ctx: DashboardContext = Depends(get_dashboard_context)):
    return await build_snapshot_async(workspace=ctx.workspace, monitor=ctx.monitor)


@router.get("/api/projects")
async def api_projects(ctx: DashboardContext = Depends(get_dashboard_context)):
    return _list_all_projects(workspace=ctx.workspace)


@router.get("/api/switch-run/{run_id}")
async def switch_run(run_id: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    if ctx.monitor.storyboard_name:
        run_path = ctx.workspace.output_dir / ctx.monitor.storyboard_name / run_id
        if not run_path.exists():
            run_path = ctx.workspace.output_dir / (ctx.monitor.storyboard_name + "_storyboard") / run_id
        if run_path.exists():
            ctx.monitor.run_dir = str(run_path)
            ctx.monitor.run_pinned = True
            return {"ok": True, "data": await build_snapshot_async(workspace=ctx.workspace, monitor=ctx.monitor)}
    return {"ok": False}


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    try:
        user = get_current_user_ws(ws)
        workspace = get_workspace_for_user(user)
        monitor = get_monitor_state(user.id)
        await ws.accept()
        connection_manager.add(user.id, ws)
        await ws.send_json({"type": "full_update", "data": await build_snapshot_async(workspace=workspace, monitor=monitor)})
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "switch_run":
                run_id = msg.get("run_id")
                if run_id and monitor.storyboard_name:
                    rp = workspace.output_dir / monitor.storyboard_name / run_id
                    if not rp.exists():
                        rp = workspace.output_dir / (monitor.storyboard_name + "_storyboard") / run_id
                    if rp.exists():
                        monitor.run_dir = str(rp)
                        monitor.run_pinned = True
                        await ws.send_json({"type": "full_update", "data": await build_snapshot_async(workspace=workspace, monitor=monitor)})
            elif msg.get("type") == "switch_project":
                project_name = msg.get("project_name", "")
                run_id = msg.get("run_id")
                if project_name and _switch_to_project(user.id, project_name, run_id, workspace=workspace):
                    await ws.send_json({"type": "full_update", "data": await build_snapshot_async(workspace=workspace, monitor=monitor)})
            elif msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except AuthError:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
    except WebSocketDisconnect:
        connection_manager.remove(ws)
    finally:
        connection_manager.remove(ws)



def _media_file_response(p: Path) -> FileResponse:
    """Serve a run/media file; MP4s must not be cached aggressively (re-concat overwrites same path)."""
    suffix = p.suffix.lower()
    if suffix == ".png":
        ct = "image/png"
    elif suffix == ".mp4":
        ct = "video/mp4"
    elif suffix == ".mp3":
        ct = "audio/mpeg"
    elif suffix == ".wav":
        ct = "audio/wav"
    elif suffix == ".ogg":
        ct = "audio/ogg"
    else:
        ct = "application/octet-stream"
    headers = {"Cache-Control": "no-cache"} if suffix == ".mp4" else {}
    return FileResponse(str(p), media_type=ct, headers=headers)


@router.get("/media/{filename:path}")
async def serve_media(filename: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    if not ctx.monitor.run_dir:
        return Response(status_code=404)
    try:
        candidate = resolve_user_path(
            ctx.workspace,
            Path(ctx.monitor.run_dir) / filename,
            allowed_roots=[ctx.workspace.output_dir],
            must_exist=True,
        )
    except Exception:
        return Response(status_code=404)
    if candidate.is_file():
        return _media_file_response(candidate)
    return Response(status_code=404)


@router.get("/asset")
async def serve_asset(path: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    try:
        p = resolve_asset_path(ctx.workspace, path, must_exist=True)
    except Exception:
        return Response(status_code=404)
    return _media_file_response(p)
