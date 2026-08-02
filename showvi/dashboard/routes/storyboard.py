"""Storyboard routes — list, duplicate, rename, delete, sync, save, load."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse

from dashboard.deps import DashboardContext, get_dashboard_context
from dashboard.helpers.asset_utils import _normalize_asset_category, _save_uploaded_asset_file
from dashboard.helpers.checkpoint import _normalize_storyboard_entity_descriptions
from dashboard.helpers.project import _resolve_storyboard
from dashboard.job_access import list_creation_jobs_for_user, list_video_jobs_for_user
from dashboard.persistence import _save_jobs, _save_video_jobs
from dashboard.state import creation_job_manager, video_job_manager
from dashboard.watchers import broadcast, broadcast_snapshot_to_user
from dashboard.workspace import resolve_asset_path, resolve_storyboard_path
from tools.scene_editor import sync_storyboard_entities

router = APIRouter(tags=["storyboard"])



def _stored_path(base_dir: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(base_dir.resolve()))
    except Exception:
        return str(path)



def _same_local_path(base_dir: Path, raw_path: Optional[str], target: Path) -> bool:
    if not raw_path:
        return False
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    try:
        return candidate.resolve() == target.resolve()
    except Exception:
        return str(candidate) == str(target)


@router.get("/api/storyboards")
async def api_storyboards(ctx: DashboardContext = Depends(get_dashboard_context)):
    """List all storyboard JSON files with basic metadata."""
    results = []
    if not ctx.workspace.storyboards_dir.exists():
        return results

    json_files = []
    for f in ctx.workspace.storyboards_dir.rglob("*.json"):
        if f.name.endswith("_screenplay.json") or (f.name.startswith("0") and len(f.name) > 1 and f.name[1].isdigit() and "final" not in f.name):
            continue
        json_files.append(f)
    json_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    for f in json_files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if "narrative" not in raw:
                continue

            desc = raw.get("description", "")
            if not desc:
                sb_list = raw.get("storyboard", [])
                if sb_list:
                    desc = sb_list[0].get("plot_description", sb_list[0].get("description", ""))
            results.append(
                {
                    "path": str(f),
                    "filename": f.name,
                    "title": raw.get("title", f.stem),
                    "description": desc[:200] if desc else "",
                    "style": raw.get("video_analysis", {}).get("style", ""),
                    "theme": raw.get("video_analysis", {}).get("theme", ""),
                    "scenes": len(raw.get("storyboard", [])),
                    "characters": len(raw.get("characters", [])),
                    "locations": len(raw.get("locations", [])),
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "mtime": f.stat().st_mtime,
                }
            )
        except Exception:
            pass
    return results


@router.post("/api/storyboard/duplicate")
async def api_storyboard_duplicate(body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Duplicate a storyboard (and its screenplay) with a new title."""
    src_path = body.get("path", "")
    if not src_path:
        return JSONResponse(status_code=400, content={"error": "Missing path"})

    try:
        src_abs = resolve_storyboard_path(ctx.workspace, src_path, must_exist=True)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid storyboard path"})

    try:
        with open(src_abs, "r", encoding="utf-8") as f:
            sb_data = json.load(f)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Failed to read storyboard: {e}"})

    orig_title = sb_data.get("title", src_abs.stem.replace("_storyboard", ""))
    copy_num = 1
    while True:
        new_title = f"{orig_title}_副本{copy_num}" if copy_num > 1 else f"{orig_title}_副本"
        new_sb_path = ctx.workspace.storyboards_dir / f"{new_title}_storyboard.json"
        if not new_sb_path.exists():
            break
        copy_num += 1

    sb_data["title"] = new_title
    ctx.workspace.storyboards_dir.mkdir(parents=True, exist_ok=True)
    with open(new_sb_path, "w", encoding="utf-8") as f:
        json.dump(sb_data, f, ensure_ascii=False, indent=2)

    src_stem = src_abs.stem
    base_title = src_stem[:-len("_storyboard")] if src_stem.endswith("_storyboard") else src_stem
    screenplay_src = src_abs.parent / f"{base_title}_screenplay.json"
    if screenplay_src.exists():
        try:
            with open(screenplay_src, "r", encoding="utf-8") as f:
                sp_data = json.load(f)
            sp_data["title"] = new_title
            screenplay_dst = ctx.workspace.storyboards_dir / f"{new_title}_screenplay.json"
            with open(screenplay_dst, "w", encoding="utf-8") as f:
                json.dump(sp_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    return {"ok": True, "new_path": str(new_sb_path), "new_title": new_title}


@router.post("/api/storyboard/rename")
async def api_storyboard_rename(body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Rename a storyboard's title (updates JSON content + sibling screenplay content)."""
    project_name = body.get("project_name", "")
    new_title = body.get("new_title", "").strip()
    if not project_name or not new_title:
        return JSONResponse(status_code=400, content={"error": "Missing project_name or new_title"})

    sb_path_str = _resolve_storyboard(project_name, workspace=ctx.workspace)
    if not sb_path_str:
        return JSONResponse(status_code=404, content={"error": "Storyboard not found"})

    try:
        sb_json_path = resolve_storyboard_path(ctx.workspace, sb_path_str, must_exist=True)
    except Exception:
        return JSONResponse(status_code=404, content={"error": "Storyboard not found"})

    try:
        with open(sb_json_path, "r", encoding="utf-8") as f:
            sb_data = json.load(f)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Failed to read storyboard: {e}"})

    old_title = sb_data.get("title", "")
    sb_data["title"] = new_title
    if "script" in sb_data and isinstance(sb_data["script"], dict):
        sb_data["script"]["title"] = new_title

    with open(sb_json_path, "w", encoding="utf-8") as f:
        json.dump(sb_data, f, ensure_ascii=False, indent=2)

    parent = sb_json_path.parent
    for sp_file in parent.glob("*_screenplay.json"):
        try:
            with open(sp_file, "r", encoding="utf-8") as f:
                sp_data = json.load(f)
            sp_data["title"] = new_title
            with open(sp_file, "w", encoding="utf-8") as f:
                json.dump(sp_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    for jid, job in list_creation_jobs_for_user(ctx.user.id):
        if job.get("title") == old_title:
            creation_job_manager.update(jid, title=new_title)
    _save_jobs(user_id=ctx.user.id)

    for jid, vj in list_video_jobs_for_user(ctx.user.id):
        if vj.get("title") == old_title or vj.get("storyboard_name") == project_name:
            video_job_manager.update(jid, title=new_title)
    _save_video_jobs(user_id=ctx.user.id)

    await broadcast({"type": "title_renamed", "project_name": project_name, "new_title": new_title}, user_id=ctx.user.id)
    return {"ok": True, "old_title": old_title, "new_title": new_title}


@router.delete("/api/storyboard")
async def api_storyboard_delete(path: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Delete a storyboard and its related local artifacts."""
    if not path:
        return JSONResponse(status_code=400, content={"error": "Missing storyboard path"})

    try:
        sb_abs = resolve_storyboard_path(ctx.workspace, path, must_exist=True)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid storyboard path"})

    project_name = sb_abs.stem
    base_title = project_name[:-len("_storyboard")] if project_name.endswith("_storyboard") else project_name

    matched_creation_jobs = []
    for jid, job in list_creation_jobs_for_user(ctx.user.id):
        if _same_local_path(ctx.workspace.base_dir, job.get("output_path"), sb_abs):
            matched_creation_jobs.append(jid)

    matched_video_jobs = []
    for jid, job in list_video_jobs_for_user(ctx.user.id):
        same_storyboard = _same_local_path(ctx.workspace.base_dir, job.get("storyboard_path"), sb_abs) or job.get("storyboard_name") == project_name
        if not same_storyboard:
            continue
        if job.get("status") in ("running", "paused"):
            return JSONResponse(status_code=400, content={"error": "该分镜仍有关联的视频任务在运行，请先停止后再删除"})
        matched_video_jobs.append(jid)

    deletion_targets = [sb_abs]
    screenplay_json = sb_abs.parent / f"{base_title}_screenplay.json"
    screenplay_txt = sb_abs.parent / f"{base_title}_screenplay.txt"
    for candidate in (screenplay_json, screenplay_txt):
        if candidate.exists():
            deletion_targets.append(candidate)

    deleted_paths = []
    for target in deletion_targets:
        if not target.exists():
            continue
        target.unlink()
        deleted_paths.append(str(target))

    # Stop and remove all matched creation jobs
    from dashboard.routes.creation import _cancel_llm_for_job
    for jid in matched_creation_jobs:
        job = creation_job_manager.get(jid)
        if not job:
            continue
        if job.get("status") in ("running", "pausing", "stopping", "queued"):
            creation_job_manager.update(jid, stop_requested=True, paused=False)
            _cancel_llm_for_job(jid)
        creation_job_manager.pop(jid, None)
    if matched_creation_jobs:
        _save_jobs(user_id=ctx.user.id)

    if ctx.monitor.storyboard_name == project_name or _same_local_path(ctx.workspace.base_dir, ctx.monitor.storyboard_path, sb_abs):
        run_path = Path(ctx.monitor.run_dir) if ctx.monitor.run_dir else None
        ctx.monitor.storyboard_name = project_name
        ctx.monitor.storyboard_path = _resolve_storyboard(project_name, run_path, workspace=ctx.workspace)
        ctx.monitor.run_pinned = bool(ctx.monitor.run_dir)

    await broadcast_snapshot_to_user(ctx.user.id, workspace=ctx.workspace)
    return {
        "ok": True,
        "path": str(sb_abs),
        "project_name": project_name,
        "deleted_paths": deleted_paths,
        "removed_creation_jobs": len(matched_creation_jobs),
        "removed_video_jobs": len(matched_video_jobs),
    }


@router.post("/api/storyboard/sync-entities")
async def api_storyboard_sync_entities(body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Apply entity card mutations and sync scene references."""
    sb_path = body.get("storyboard_path", "")
    sb_data = body.get("storyboard")
    edits = body.get("edits") or {}
    if not sb_path or not sb_data:
        return JSONResponse(status_code=400, content={"error": "Missing storyboard_path or storyboard data"})

    try:
        sb_abs = resolve_storyboard_path(ctx.workspace, sb_path, must_exist=False)
        result = sync_storyboard_entities(sb_data, {"edits": edits})
        synced_storyboard = result["storyboard"]
        sb_abs.parent.mkdir(parents=True, exist_ok=True)
        with open(sb_abs, "w", encoding="utf-8") as f:
            json.dump(synced_storyboard, f, ensure_ascii=False, indent=2)
        return {
            "ok": True,
            "path": str(sb_abs),
            "storyboard": synced_storyboard,
            "sync_summary": result.get("sync_summary", {}),
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/api/storyboard/add-entity")
async def api_storyboard_add_entity(
    file: Optional[UploadFile] = File(None),
    storyboard_path: str = Form(...),
    category: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    asset_path: str = Form(""),
    ctx: DashboardContext = Depends(get_dashboard_context),
):
    """Add a new character/location/prop to the storyboard JSON."""
    normalized_category = _normalize_asset_category(category)
    if not normalized_category:
        return JSONResponse(status_code=400, content={"ok": False, "error": "不支持的素材分类"})

    clean_name = (name or "").strip()
    if not clean_name:
        return JSONResponse(status_code=400, content={"ok": False, "error": "名称不能为空"})

    try:
        sb_abs = resolve_storyboard_path(ctx.workspace, storyboard_path, must_exist=True)
    except Exception:
        return JSONResponse(status_code=404, content={"ok": False, "error": "Storyboard not found"})

    with open(sb_abs, "r", encoding="utf-8") as f:
        sb = json.load(f)

    list_key = normalized_category
    entities = sb.setdefault(list_key, [])
    if any(e.get("name") == clean_name for e in entities):
        return JSONResponse(status_code=409, content={"ok": False, "error": f"'{clean_name}' 已存在"})

    image_rel = ""
    if file and file.filename:
        try:
            dest = await _save_uploaded_asset_file(file, category=normalized_category, name=clean_name, workspace=ctx.workspace)
            image_rel = _stored_path(ctx.workspace.base_dir, dest)
        except ValueError as e:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})
    elif asset_path.strip():
        try:
            ap = resolve_asset_path(ctx.workspace, asset_path.strip(), must_exist=True)
        except Exception:
            return JSONResponse(status_code=404, content={"ok": False, "error": "素材文件不存在"})
        image_rel = _stored_path(ctx.workspace.base_dir, ap)

    entity: dict = {"name": clean_name, "description": description.strip() if description else ""}
    if normalized_category == "characters":
        entity.setdefault("personality", "")
        entity.setdefault("voice_description", "")
        entity.setdefault("id", "")
    if image_rel:
        entity["image_path"] = image_rel

    entities.append(entity)
    _normalize_storyboard_entity_descriptions(sb)
    with open(sb_abs, "w", encoding="utf-8") as f:
        json.dump(sb, f, ensure_ascii=False, indent=2)

    return {"ok": True, "entity": entity, "storyboard": sb}


@router.post("/api/storyboard/save")
async def api_storyboard_save(body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Save an edited storyboard back to disk."""
    sb_path = body.get("storyboard_path", "")
    sb_data = body.get("storyboard")
    if not sb_path or not sb_data:
        return JSONResponse(status_code=400, content={"error": "Missing storyboard_path or storyboard data"})

    try:
        sb_abs = resolve_storyboard_path(ctx.workspace, sb_path, must_exist=False)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid storyboard path"})

    sb_abs.parent.mkdir(parents=True, exist_ok=True)
    _normalize_storyboard_entity_descriptions(sb_data)
    with open(sb_abs, "w", encoding="utf-8") as f:
        json.dump(sb_data, f, ensure_ascii=False, indent=2)

    return {"ok": True, "path": str(sb_abs)}


@router.get("/api/storyboard/load")
async def api_storyboard_load(path: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Load a storyboard JSON file."""
    try:
        sb_abs = resolve_storyboard_path(ctx.workspace, path, must_exist=True)
    except Exception:
        return JSONResponse(status_code=404, content={"error": "File not found"})
    with open(sb_abs, "r", encoding="utf-8") as f:
        return json.load(f)


@router.get("/api/storyboard/load-with-screenplay")
async def api_storyboard_load_with_screenplay(path: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Load a storyboard JSON and its associated screenplay JSON (if exists)."""
    try:
        sb_abs = resolve_storyboard_path(ctx.workspace, path, must_exist=True)
    except Exception:
        return JSONResponse(status_code=404, content={"error": "Storyboard file not found"})

    with open(sb_abs, "r", encoding="utf-8") as f:
        storyboard = json.load(f)

    stem = sb_abs.stem.replace("_storyboard", "")
    screenplay_path = sb_abs.parent / f"{stem}_screenplay.json"
    screenplay = None
    if screenplay_path.exists():
        try:
            with open(screenplay_path, "r", encoding="utf-8") as f:
                screenplay = json.load(f)
        except Exception:
            pass

    return {
        "storyboard": storyboard,
        "screenplay": screenplay,
        "storyboard_path": str(sb_abs),
        "screenplay_path": str(screenplay_path) if screenplay else None,
    }
