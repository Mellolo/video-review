"""Repository routes — project scanning, run details, concat, media serving."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from starlette.responses import Response

from dashboard.deps import DashboardContext, get_dashboard_context
from dashboard.helpers.checkpoint import load_checkpoint, _load_storyboard_payload, _regen_requests_payload
from dashboard.helpers.media import scan_media
from dashboard.helpers.reconciliation import _collect_concat_sources
from dashboard.routes.core import _media_file_response
from dashboard.watchers import broadcast_snapshot_for_matching_run
from dashboard.workspace import resolve_run_path, resolve_user_path

router = APIRouter(tags=["repository"])



def _scan_all_projects(ctx: DashboardContext) -> list[dict]:
    """Scan the current user's output directory and return a summary of every project+run."""
    output_base = ctx.workspace.output_dir
    if not output_base.exists():
        return []

    projects = []
    for project_dir in output_base.iterdir():
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue

        sorted_runs = sorted(
            [rd for rd in project_dir.iterdir() if rd.is_dir() and not rd.name.startswith(".")],
            key=lambda p: p.name, reverse=True,
        )
        cp = load_checkpoint(sorted_runs[0]) if sorted_runs else None
        raw = _load_storyboard_payload(project_dir.name, sorted_runs[0] if sorted_runs else None, checkpoint=cp, workspace=ctx.workspace)
        sb_meta = None
        if raw:
            sb_meta = {
                "title": raw.get("title", project_dir.name),
                "style": raw.get("video_analysis", {}).get("style", ""),
                "theme": raw.get("video_analysis", {}).get("theme", ""),
                "total_scenes": len(raw.get("storyboard", [])),
                "characters": [c.get("name", "") for c in raw.get("characters", [])],
            }

        runs = []
        for run_dir in sorted(project_dir.iterdir(), key=lambda p: p.name, reverse=True):
            if not run_dir.is_dir() or run_dir.name.startswith("."):
                continue
            media = scan_media(run_dir)
            cp = load_checkpoint(run_dir)

            run_summary = {
                "run_id": run_dir.name,
                "date": _format_run_date(run_dir.name),
                "video_count": len(media["segments"]) + (1 if media["final"] else 0),
                "image_count": len(media["charsheets"]) + len(media["locsheets"]) + len(media["propsheets"]),
                "has_final": media["final"] is not None,
                "segments": media["segments"],
                "charsheets": media["charsheets"],
                "locsheets": media["locsheets"],
                "propsheets": media["propsheets"],
                "final": media["final"],
                "final_mtime": media.get("final_mtime"),
            }

            if cp:
                units = cp.get("script", {}).get("work_units", [])
                completed = sum(1 for u in units if u.get("is_completed") and not u.get("abandoned_no_video") and u.get("final_video_path"))
                total_attempts = sum(len(u.get("attempts", [])) for u in units)
                run_summary["units_total"] = len(units)
                run_summary["units_completed"] = completed
                run_summary["total_attempts"] = total_attempts
                run_summary["project_title"] = cp.get("script", {}).get("title", "")
                run_summary["progress"] = round((completed / len(units)) * 100) if units else 0
            else:
                run_summary["units_total"] = 0
                run_summary["units_completed"] = 0
                run_summary["total_attempts"] = 0
                run_summary["progress"] = 0

            if media["segments"] or media["final"] or media["charsheets"]:
                runs.append(run_summary)

        if runs:
            total_videos = sum(r["video_count"] for r in runs)
            total_images = sum(r["image_count"] for r in runs)
            projects.append({
                "project_name": project_dir.name,
                "storyboard_meta": sb_meta,
                "runs": runs,
                "total_runs": len(runs),
                "total_videos": total_videos,
                "total_images": total_images,
            })

    projects.sort(key=lambda p: p["runs"][0]["run_id"] if p["runs"] else "", reverse=True)
    return projects



def _format_run_date(run_id: str) -> str:
    try:
        dt = datetime.strptime(run_id, "%Y%m%d_%H%M%S")
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return run_id


@router.get("/api/repository")
async def api_repository(ctx: DashboardContext = Depends(get_dashboard_context)):
    return _scan_all_projects(ctx)


@router.get("/api/run-detail/{project_name}/{run_id}")
async def api_run_detail(project_name: str, run_id: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Return full checkpoint + media for a specific run."""
    try:
        run_path = resolve_run_path(ctx.workspace, project_name, run_id, must_exist=True)
    except Exception:
        return Response(status_code=404)

    cp = load_checkpoint(run_path)
    media = scan_media(run_path)
    storyboard = _load_storyboard_payload(project_name, run_path, checkpoint=cp, workspace=ctx.workspace)

    return {
        "project_name": project_name,
        "run_id": run_id,
        "checkpoint": cp,
        "media": media,
        "storyboard": storyboard,
        "regen_requests": _regen_requests_payload(run_path),
    }


@router.post("/api/run/{project_name}/{run_id}/concat")
async def api_run_concat(project_name: str, run_id: str, body: Optional[dict] = Body(default=None), ctx: DashboardContext = Depends(get_dashboard_context)):
    """Concatenate all currently available unit videos into a final video for this run."""
    from tools.video_concat import concat_videos_from_timeline

    try:
        run_path = resolve_run_path(ctx.workspace, project_name, run_id, must_exist=True)
    except Exception:
        return JSONResponse(status_code=404, content={"error": "Run not found"})

    checkpoint = load_checkpoint(run_path)
    if not checkpoint:
        return JSONResponse(status_code=404, content={"error": "Checkpoint not found"})

    sources = _collect_concat_sources(run_path, checkpoint)
    if not sources:
        return JSONResponse(status_code=400, content={"error": "当前还没有可用于剪辑的视频片段"})

    timeline_data = {"timeline": [{"clip_video_path": path, "segment_index": idx} for idx, path in enumerate(sources)]}
    final_path = run_path / "final_video.mp4"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
        json.dump(timeline_data, tf, ensure_ascii=False, indent=2)
        timeline_tmp = tf.name

    concat_mode = str((body or {}).get("mode", "hard")).strip().lower()
    if concat_mode not in {"hard", "crossfade"}:
        return JSONResponse(status_code=400, content={"error": "不支持的剪辑模式"})
    raw_fade_seconds = (body or {}).get("fade_seconds", 0.5)
    try:
        fade_seconds = float(raw_fade_seconds)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"error": "交叉溶解时长必须是数字"})
    if fade_seconds < 0.0 or fade_seconds > 3.0:
        return JSONResponse(status_code=400, content={"error": "交叉溶解时长需在 0-3 秒之间"})
    fade = fade_seconds if concat_mode == "crossfade" else 0.0

    try:
        result = concat_videos_from_timeline(timeline_path=timeline_tmp, output_path=str(final_path), fade=fade)
    finally:
        try:
            os.unlink(timeline_tmp)
        except OSError:
            pass

    if not result.get("success"):
        return JSONResponse(status_code=500, content={"error": result.get("error", "剪辑失败")})

    await broadcast_snapshot_for_matching_run(project_name, str(run_path))

    return {
        "ok": True,
        "project_name": project_name,
        "run_id": run_id,
        "output_path": str(final_path),
        "clip_count": result.get("clip_count", len(sources)),
        "concat_mode": concat_mode,
        "fade_seconds": fade,
        "audio_normalized": result.get("audio_normalized", True),
        "message": f"Final video created from {len(sources)} clip(s)",
    }


@router.delete("/api/run/{project_name}/{run_id}/final")
async def api_delete_run_final(project_name: str, run_id: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Delete final_video.mp4 for a specific run."""
    try:
        run_path = resolve_run_path(ctx.workspace, project_name, run_id, must_exist=True)
    except Exception:
        return JSONResponse(status_code=404, content={"error": "Run not found"})

    final_path = run_path / "final_video.mp4"
    if not final_path.exists():
        return JSONResponse(status_code=404, content={"error": "Final video not found"})

    final_path.unlink()
    await broadcast_snapshot_for_matching_run(project_name, str(run_path))

    return {"ok": True, "project_name": project_name, "run_id": run_id, "message": "Final video deleted"}


@router.get("/repo-media/{project_name}/{run_id}/{filename}")
async def serve_repo_media(project_name: str, run_id: str, filename: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Serve media from any project/run directory inside the current user's workspace."""
    try:
        run_path = resolve_run_path(ctx.workspace, project_name, run_id, must_exist=True)
        p = resolve_user_path(ctx.workspace, run_path / filename, allowed_roots=[ctx.workspace.output_dir], must_exist=True)
    except Exception:
        return Response(status_code=404)
    if p.is_file():
        return _media_file_response(p)
    return Response(status_code=404)
