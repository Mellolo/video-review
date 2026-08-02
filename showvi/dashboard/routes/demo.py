"""Demo videos API — returns the latest 8 projects that have a final video."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from dashboard.deps import DashboardContext, get_dashboard_context
from dashboard.helpers.checkpoint import load_checkpoint, _load_storyboard_payload

router = APIRouter(tags=["demo"])


@router.get("/api/demo-videos")
async def list_demo_videos(ctx: DashboardContext = Depends(get_dashboard_context)):
    """Scan user's output directory and return the latest 8 runs that have a final_video.mp4."""
    output_base = ctx.workspace.output_dir
    if not output_base or not output_base.exists():
        return []

    candidates = []

    for project_dir in output_base.iterdir():
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue

        for run_dir in project_dir.iterdir():
            if not run_dir.is_dir() or run_dir.name.startswith("."):
                continue

            final_path = run_dir / "final_video.mp4"
            if not final_path.exists():
                continue

            try:
                mtime = final_path.stat().st_mtime
            except OSError:
                mtime = 0

            candidates.append({
                "project_name": project_dir.name,
                "run_dir": run_dir,
                "run_id": run_dir.name,
                "mtime": mtime,
            })

    candidates.sort(key=lambda c: c["mtime"], reverse=True)
    latest = candidates[:8]

    results = []
    for item in latest:
        run_dir = item["run_dir"]
        project_name = item["project_name"]
        run_id = item["run_id"]

        title = project_name.replace("_storyboard", "").replace("_", " ")
        style = ""
        synopsis = ""

        cp = load_checkpoint(run_dir)
        if cp:
            script = cp.get("script", {})
            title = script.get("title") or title
            style = script.get("video_analysis", {}).get("style", "")
            synopsis = script.get("video_analysis", {}).get("theme", "")

        storyboard = _load_storyboard_payload(project_name, run_dir, checkpoint=cp, workspace=ctx.workspace)
        if storyboard:
            title = storyboard.get("title") or title
            va = storyboard.get("video_analysis", {})
            style = va.get("style", "") or style
            synopsis = va.get("theme", "") or synopsis

        video_url = f"/repo-media/{project_name}/{run_id}/final_video.mp4"

        results.append({
            "id": f"{project_name}__{run_id}",
            "title": title,
            "style": style,
            "synopsis": synopsis,
            "cover": "",
            "video": video_url,
        })

    return results
