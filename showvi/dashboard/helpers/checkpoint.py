"""Checkpoint and run-related helper functions extracted from dashboard/server.py."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from dashboard.request_context import get_current_workspace
from dashboard.state import video_job_manager
from dashboard.workspace import WorkspaceContext, resolve_run_path
from tools.regen_queue import load_requests
from utils.io import load_json, save_embedded_versioned_json



def _workspace_or_current(workspace: Optional[WorkspaceContext] = None) -> WorkspaceContext:
    ctx = workspace or get_current_workspace()
    if not ctx:
        raise RuntimeError("Workspace context is required")
    return ctx



def find_latest_run(storyboard_name: str, *, workspace: Optional[WorkspaceContext] = None) -> Optional[Path]:
    ws = _workspace_or_current(workspace)
    base = ws.output_dir / storyboard_name
    if not base.exists():
        # Try with _storyboard suffix (output dirs use this naming)
        base = ws.output_dir / (storyboard_name + "_storyboard")
    if not base.exists():
        return None
    runs = sorted(
        [d for d in base.iterdir() if d.is_dir()],
        key=lambda p: p.name,
        reverse=True,
    )
    return runs[0] if runs else None



def load_storyboard(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)



def load_checkpoint(run_dir: Path) -> Optional[dict]:
    cp = Path(run_dir) / "checkpoint.json"
    data = load_json(cp, default=None)
    return data if isinstance(data, dict) else None



def save_checkpoint(run_dir: Path, checkpoint: dict) -> Path:
    cp = Path(run_dir) / "checkpoint.json"
    save_embedded_versioned_json(cp, checkpoint, default=str)
    return cp



def _find_run_storyboard_copy(
    project_name: str,
    run_dir: Optional[Path],
    checkpoint: Optional[dict] = None,
) -> Optional[Path]:
    """Prefer storyboard snapshots stored alongside a run in output/."""
    if not run_dir or not run_dir.exists():
        return None

    project_dir = run_dir.parent if run_dir.parent.exists() else None
    candidates: list[Path] = []

    if project_name:
        candidates.append(run_dir / f"{project_name}.json")
        if project_dir:
            candidates.append(project_dir / f"{project_name}.json")

    cp = checkpoint if checkpoint is not None else load_checkpoint(run_dir)
    if cp and cp.get("storyboard_path"):
        snapshot_name = Path(cp["storyboard_path"]).name
        candidates.append(run_dir / snapshot_name)
        if project_dir:
            candidates.append(project_dir / snapshot_name)

    try:
        candidates.extend(sorted(run_dir.glob("*_storyboard.json")))
        if project_dir:
            candidates.extend(sorted(project_dir.glob("*_storyboard.json")))
    except Exception:
        pass

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and candidate.is_file():
            return candidate
    return None



def _load_storyboard_payload(
    project_name: str,
    run_dir: Optional[Path] = None,
    *,
    preferred_path: Optional[str] = None,
    checkpoint: Optional[dict] = None,
    workspace: Optional[WorkspaceContext] = None,
) -> Optional[dict]:
    """Load storyboard JSON, preferring run-local snapshots for history views."""
    from dashboard.helpers.project import _resolve_storyboard

    ws = _workspace_or_current(workspace)
    candidate_paths: list[str] = []

    resolved_path = _resolve_storyboard(project_name, run_dir, checkpoint, workspace=ws)
    if resolved_path:
        candidate_paths.append(resolved_path)

    if preferred_path:
        candidate_paths.append(preferred_path)

    seen: set[str] = set()
    for sb_path in candidate_paths:
        if not sb_path or sb_path in seen:
            continue
        seen.add(sb_path)
        if Path(sb_path).exists():
            try:
                return load_storyboard(sb_path)
            except Exception:
                pass

    cp = checkpoint if checkpoint is not None else (load_checkpoint(run_dir) if run_dir and run_dir.exists() else None)
    if cp:
        return _storyboard_from_checkpoint(cp)
    return None



def _storyboard_from_checkpoint(checkpoint: Optional[dict]) -> Optional[dict]:
    """Build a minimal storyboard payload from checkpoint data when source JSON is unavailable."""
    if not checkpoint:
        return None

    script = checkpoint.get("script") or {}
    units = script.get("work_units") or []
    title = script.get("title", "")
    description = script.get("description", "")

    storyboard_items = []
    characters: dict[str, dict] = {}
    for unit in units:
        scene_numbers = unit.get("scene_numbers") or []
        scene_number = scene_numbers[0] if scene_numbers else unit.get("unit_id")
        storyboard_items.append({
            "scene_number": scene_number,
            "duration": unit.get("duration_seconds"),
            "narrative_summary": unit.get("narrative_summary") or unit.get("plot_description") or unit.get("visual_description") or "",
            "plot_description": unit.get("plot_description") or unit.get("narrative_summary") or "",
            "description": unit.get("visual_description") or unit.get("plot_description") or unit.get("narrative_summary") or "",
            "characters_in_scene": unit.get("characters_in_scene") or [],
            "camera_angle": unit.get("camera_angle"),
            "mood": unit.get("mood"),
            "lighting": unit.get("lighting"),
        })
        for name in unit.get("characters_in_scene") or []:
            if name and name not in characters:
                characters[name] = {"name": name, "description": ""}

    return {
        "title": title,
        "description": description,
        "narrative": description,
        "storyboard": storyboard_items,
        "characters": list(characters.values()),
        "locations": [],
        "props": [],
        "_from_checkpoint": True,
    }



def _find_unit_by_id(checkpoint: dict, unit_id: int) -> Optional[dict]:
    units = (checkpoint or {}).get("script", {}).get("work_units", []) or []
    for unit in units:
        if int(unit.get("unit_id", -1)) == int(unit_id):
            return unit
    return None



def _find_attempt_by_id(unit: dict, attempt_id: int) -> Optional[dict]:
    for attempt in (unit.get("attempts", []) or []):
        if int(attempt.get("attempt_id", -1)) == int(attempt_id):
            return attempt
    return None



def _find_video_job_for_run(run_dir: Path, *, user_id: Optional[int] = None) -> Optional[tuple[str, dict]]:
    target = str(run_dir)
    for jid, job in video_job_manager.all_items():
        if user_id is not None and int(job.get("owner_user_id") or -1) != int(user_id):
            continue
        if job.get("run_dir") == target:
            return jid, job
    return None



def _get_run_and_checkpoint(
    project_name: str,
    run_id: str,
    *,
    workspace: Optional[WorkspaceContext] = None,
) -> tuple[Optional[Path], Optional[dict], Optional["JSONResponse"]]:
    from fastapi.responses import JSONResponse

    ws = _workspace_or_current(workspace)
    run_path = resolve_run_path(ws, project_name, run_id, must_exist=True)
    if not run_path.exists() or not run_path.is_dir():
        return None, None, JSONResponse(status_code=404, content={"error": "Run not found"})
    cp = load_checkpoint(run_path)
    if not cp:
        return run_path, None, JSONResponse(status_code=404, content={"error": "Checkpoint not found"})
    return run_path, cp, None



def _normalize_storyboard_entity_descriptions(sb: dict) -> dict:
    """Ensure entities always carry a description field, even if blank."""
    if not isinstance(sb, dict):
        return sb
    for key in ("characters", "locations", "props"):
        items = sb.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                item.setdefault("description", "")
                if key == "characters":
                    item.setdefault("personality", "")
                    item.setdefault("voice_description", "")
                    item.setdefault("id", "")
    return sb



def _get_latest_active_regen_request(run_dir: Path, unit_id: int) -> Optional[dict]:
    requests = [
        req for req in load_requests(run_dir)
        if int(req.get("unit_id", -1)) == int(unit_id)
        and req.get("status") in {"draft", "queued"}
    ]
    if not requests:
        return None
    requests.sort(key=lambda req: float(req.get("updated_at") or req.get("created_at") or 0), reverse=True)
    return requests[0]



def _next_unit_attempt_id(run_dir: Path, unit: dict) -> int:
    attempt_ids = [int(attempt.get("attempt_id") or 0) for attempt in (unit.get("attempts") or [])]
    if attempt_ids:
        return max(attempt_ids) + 1
    return 1



def _unit_has_resolved_video(unit: dict, run_dir: Path) -> bool:
    if _resolve_existing_video_path(unit.get("final_video_path"), run_dir):
        return True
    final_attempt_id = unit.get("final_attempt_id")
    if final_attempt_id is not None:
        attempt = _find_attempt_by_id(unit, int(final_attempt_id))
        if attempt and _resolve_existing_video_path(attempt.get("output_path"), run_dir):
            return True
    for attempt in reversed(unit.get("attempts", []) or []):
        if _resolve_existing_video_path(attempt.get("output_path"), run_dir):
            return True
    return False



def _regen_requests_payload(run_dir: Optional[Path]) -> list[dict]:
    if not run_dir or not run_dir.exists():
        return []
    payload = []
    for req in load_requests(run_dir):
        payload.append({
            "request_id": req.get("request_id"),
            "unit_id": req.get("unit_id"),
            "status": req.get("status"),
            "source_prompt": req.get("source_prompt") or "",
            "manual_prompt": req.get("manual_prompt") or "",
            "manual_image_ref_assets": req.get("manual_image_ref_assets") or {},
            "extra_attempts": req.get("extra_attempts") or 1,
            "created_from_attempt_id": req.get("created_from_attempt_id"),
            "placeholder_attempt_id": req.get("placeholder_attempt_id"),
            "created_at": req.get("created_at"),
            "updated_at": req.get("updated_at"),
            "started_at": req.get("started_at"),
            "consumed_at": req.get("consumed_at"),
            "version": req.get("version", 0),
        })
    return payload



def _resolve_existing_video_path(raw_path: Optional[str], run_dir: Path) -> Optional[Path]:
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = run_dir / candidate
    try:
        candidate = candidate.resolve()
    except Exception:
        candidate = candidate.absolute()
    return candidate if candidate.exists() and candidate.is_file() else None



def _checkpoint_indicates_video_success(checkpoint: Optional[dict], run_dir: str | Path | None) -> bool:
    if not checkpoint:
        return False
    units = checkpoint.get("script", {}).get("work_units", []) or []
    if not units:
        return False
    base = Path(run_dir) if run_dir else None
    for unit in units:
        final_path = unit.get("final_video_path")
        chosen = _resolve_existing_video_path(final_path, base) if base else None
        if chosen:
            continue
        attempts = unit.get("attempts", []) or []
        found = False
        final_attempt_id = unit.get("final_attempt_id")
        if final_attempt_id is not None:
            attempt = _find_attempt_by_id(unit, int(final_attempt_id))
            chosen = _resolve_existing_video_path(attempt.get("output_path"), base) if (attempt and base) else None
            if chosen:
                found = True
        if not found:
            for attempt in reversed(attempts):
                chosen = _resolve_existing_video_path(attempt.get("output_path"), base) if base else None
                if chosen:
                    found = True
                    break
        if not found:
            return False
    return True



def _is_in_charsheet_phase(checkpoint: Optional[dict]) -> bool:
    """Return True if the job is still in the charsheet generation phase.

    The charsheet phase is identified by having work units but zero attempts
    across all of them — meaning video generation hasn't started yet.
    """
    if not checkpoint:
        return False
    units = checkpoint.get("script", {}).get("work_units", []) or []
    if not units:
        return False
    return all(len(unit.get("attempts", []) or []) == 0 for unit in units)


def _pid_is_alive(pid: Optional[int]) -> bool:
    try:
        if not pid:
            return False
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False



def _live_progress_for_job(job: dict) -> dict:
    return dict(job.get("progress") or {})



def _compute_video_job_progress(job: dict, run_dir: Path, checkpoint: Optional[dict]) -> dict:
    cp = checkpoint or {}
    units = cp.get("script", {}).get("work_units", []) or []
    total_units = len(units)
    completed_units = 0
    image_total = 0
    image_generated = 0
    attempts = 0

    # Build set of unit_ids that have pending (queued/draft) regen requests
    # so we don't count them as completed while regen is in progress.
    regen_pending_unit_ids: set[int] = set()
    if run_dir:
        try:
            for req in load_requests(run_dir):
                if req.get("status") in ("draft", "queued"):
                    regen_pending_unit_ids.add(int(req["unit_id"]))
        except Exception:
            pass

    for idx, unit in enumerate(units):
        unit_attempts = unit.get("attempts", []) or []
        attempts += len(unit_attempts)
        if unit_attempts:
            image_total += len(unit_attempts)
            image_generated += sum(1 for attempt in unit_attempts if attempt.get("image_path") or attempt.get("image_url"))

        # If this unit has a pending regen request, don't count it as completed
        unit_id = unit.get("id", idx)
        if unit_id in regen_pending_unit_ids:
            continue

        # If the unit is explicitly marked as not completed (e.g. regen in progress),
        # don't count it even if old video files exist on disk.
        if unit.get("is_completed") is False:
            continue

        chosen = _resolve_existing_video_path(unit.get("final_video_path"), run_dir)
        if not chosen:
            final_attempt_id = unit.get("final_attempt_id")
            if final_attempt_id is not None:
                selected_attempt = _find_attempt_by_id(unit, int(final_attempt_id))
                if selected_attempt:
                    chosen = _resolve_existing_video_path(selected_attempt.get("output_path"), run_dir)
        if not chosen:
            for attempt in reversed(unit_attempts):
                chosen = _resolve_existing_video_path(attempt.get("output_path"), run_dir)
                if chosen:
                    break
        if chosen:
            completed_units += 1

    percent = round((completed_units / total_units) * 100, 1) if total_units else 0
    image_percent = round((image_generated / image_total) * 100, 1) if image_total else 0

    stage = "starting"
    status = job.get("status")
    if status == "paused":
        stage = "paused"
    elif status == "completed":
        stage = "completed"
    elif completed_units and completed_units >= total_units:
        stage = "finalizing"
    elif image_generated:
        stage = "generating_video"
    elif attempts:
        stage = "generating_images"

    # ── Charsheet progress (when still in "starting" / charsheet phase) ──
    charsheet_info = {}
    if stage == "starting" and status == "running":
        generated_charsheets = cp.get("generated_charsheets", {})
        pending_charsheet_tasks = cp.get("pending_charsheet_tasks", {})

        # Count total entities that need charsheets
        storyboard_data = None
        # Try to read from the run-local storyboard
        run_sb_candidates = list(run_dir.glob("*_storyboard.json")) if run_dir else []
        for sb_file in run_sb_candidates:
            try:
                with open(sb_file, "r", encoding="utf-8") as f:
                    storyboard_data = json.load(f)
                break
            except Exception:
                pass

        if storyboard_data:
            all_entities = []
            for cat, prefix in [("characters", "char"), ("locations", "loc"), ("props", "prop")]:
                for entity in storyboard_data.get(cat, []):
                    name = entity.get("name", "")
                    key = f"{prefix}:{name}"
                    done = key in generated_charsheets and Path(generated_charsheets[key]).exists()
                    pending = key in pending_charsheet_tasks
                    all_entities.append({"name": name, "type": cat, "done": done, "pending": pending})

            charsheet_total = len(all_entities)
            charsheet_done = sum(1 for e in all_entities if e["done"])
            charsheet_pending = [e["name"] for e in all_entities if e["pending"] and not e["done"]]
            charsheet_waiting = [e["name"] for e in all_entities if not e["done"] and not e["pending"]]

            charsheet_info = {
                "charsheet_total": charsheet_total,
                "charsheet_done": charsheet_done,
                "charsheet_pending": charsheet_pending,
                "charsheet_waiting": charsheet_waiting,
            }
            if charsheet_total > 0:
                stage = "charsheet"

    return {
        "total": total_units,
        "completed": completed_units,
        "completed_checkpoint": completed_units,
        "attempts": attempts,
        "percent": percent,
        "stage": stage,
        "image_total": image_total,
        "image_generated": image_generated,
        "image_percent": image_percent,
        **charsheet_info,
    }



def _hydrate_checkpoint_critiques_from_log(checkpoint: Optional[dict], run_dir: Optional[Path]) -> Optional[dict]:
    return checkpoint
