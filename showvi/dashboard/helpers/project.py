"""Project switching and listing helpers extracted from dashboard/server.py."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from dashboard.helpers.checkpoint import (
    find_latest_run,
    load_checkpoint,
    _find_run_storyboard_copy,
    _load_storyboard_payload,
)
from dashboard.request_context import get_current_workspace
from dashboard.state import get_monitor_state
from dashboard.workspace import WorkspaceContext



def _workspace_or_current(workspace: Optional[WorkspaceContext] = None) -> WorkspaceContext:
    ctx = workspace or get_current_workspace()
    if not ctx:
        raise RuntimeError("Workspace context is required")
    return ctx



def _list_all_projects(*, workspace: Optional[WorkspaceContext] = None) -> list[dict]:
    """Return a lightweight list of all projects with storyboard metadata."""
    ws = _workspace_or_current(workspace)
    output_base = ws.output_dir
    if not output_base.exists():
        return []
    projects = []
    for d in sorted(output_base.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        runs = sorted([r.name for r in d.iterdir() if r.is_dir() and not r.name.startswith(".")], reverse=True)
        if not runs:
            continue
        latest_run = d / runs[0]
        cp = load_checkpoint(latest_run)
        raw = _load_storyboard_payload(d.name, latest_run, checkpoint=cp, workspace=ws)
        title = d.name.replace("_storyboard", "")
        theme = ""
        if cp:
            title = cp.get("script", {}).get("title", "") or title
        if raw:
            title = raw.get("title", title)
            theme = raw.get("video_analysis", {}).get("theme", "")
        projects.append({
            "project_name": d.name,
            "title": title,
            "theme": theme,
            "run_count": len(runs),
        })
    return projects



def _resolve_storyboard(
    project_name: str,
    run_dir: Optional[Path] = None,
    checkpoint: Optional[dict] = None,
    *,
    workspace: Optional[WorkspaceContext] = None,
) -> Optional[str]:
    """Try multiple strategies to find the storyboard JSON for a project."""
    ws = _workspace_or_current(workspace)
    run_copy = _find_run_storyboard_copy(project_name, run_dir, checkpoint)
    if run_copy:
        return str(run_copy)

    sb_file = ws.storyboards_dir / f"{project_name}.json"
    if sb_file.exists():
        return str(sb_file)

    cp = checkpoint if checkpoint is not None else (load_checkpoint(run_dir) if run_dir and run_dir.exists() else None)
    if cp and cp.get("storyboard_path"):
        p = Path(cp["storyboard_path"])
        if p.exists():
            try:
                p.resolve().relative_to(ws.root_dir.resolve())
                return str(p)
            except Exception:
                pass

    for f in ws.storyboards_dir.iterdir():
        if f.suffix == ".json" and f.stem in project_name:
            return str(f)
    return None



def _switch_to_project(
    user_id: int,
    project_name: str,
    run_id: str | None = None,
    *,
    workspace: Optional[WorkspaceContext] = None,
) -> bool:
    """Switch the monitor to a different project (and optionally a specific run)."""
    ws = _workspace_or_current(workspace)
    monitor = get_monitor_state(user_id)
    monitor.storyboard_name = project_name

    # output directories may use "{name}_storyboard" naming
    _proj_dir = ws.output_dir / project_name
    if not _proj_dir.exists():
        _proj_dir = ws.output_dir / (project_name + "_storyboard")
    _effective_proj_name = _proj_dir.name if _proj_dir.exists() else project_name

    if run_id:
        rp = _proj_dir / run_id
        if rp.exists():
            monitor.run_dir = str(rp)
            monitor.run_pinned = True
        else:
            return False
    else:
        latest = find_latest_run(project_name, workspace=ws)
        if not latest:
            latest = find_latest_run(project_name + "_storyboard", workspace=ws)
        monitor.run_dir = str(latest) if latest else None
        monitor.run_pinned = False

    run_path = Path(monitor.run_dir) if monitor.run_dir else None
    monitor.storyboard_path = _resolve_storyboard(project_name, run_path, workspace=ws)
    return True
