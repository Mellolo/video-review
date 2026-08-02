"""Scene editing routes — regenerate prompt, refine with chat."""

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from dashboard.deps import DashboardContext, get_dashboard_context
from dashboard.workspace import resolve_storyboard_path

router = APIRouter(tags=["scene"])



def _json_http_error(exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})



def _resolve_storyboard_payload(body: dict, ctx: DashboardContext) -> dict:
    storyboard = body.get("storyboard")
    storyboard_path = (body.get("storyboard_path") or "").strip()
    if storyboard:
        return storyboard
    if not storyboard_path:
        raise HTTPException(status_code=400, detail="Missing storyboard or storyboard_path")
    sb_abs = resolve_storyboard_path(ctx.workspace, storyboard_path, must_exist=True)
    with open(sb_abs, "r", encoding="utf-8") as f:
        return json.load(f)


@router.post("/api/scene/regenerate-prompt")
async def api_scene_regenerate_prompt(body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Re-generate seedance_prompt for a single scene from its narrative."""
    from tools.scene_editor import regenerate_seedance_prompt

    scene_index = body.get("scene_index")
    narrative = body.get("narrative_summary", "")

    try:
        storyboard = _resolve_storyboard_payload(body, ctx)
    except HTTPException as exc:
        return _json_http_error(exc)

    if scene_index is None:
        return JSONResponse(status_code=400, content={"error": "Missing scene_index"})

    scenes = storyboard.get("storyboard", [])
    if scene_index < 0 or scene_index >= len(scenes):
        return JSONResponse(status_code=400, content={"error": f"Invalid scene_index: {scene_index}"})

    scene = scenes[scene_index]
    if narrative:
        scene["narrative_summary"] = narrative

    try:
        def _regen_sync():
            from dashboard.usage_tracker import usage_context
            with usage_context(user_id=ctx.user.id, step="scene_edit"):
                return regenerate_seedance_prompt(
                    narrative_summary=narrative or scene.get("narrative_summary", ""),
                    scene=scene,
                    storyboard_context=storyboard,
                )
        result = await asyncio.get_event_loop().run_in_executor(
            None, _regen_sync,
        )
        return {
            "ok": True,
            "seedance_prompt": result["seedance_prompt"],
            "transition_strategy": result.get("transition_strategy", ""),
            "continuity_anchor": result.get("continuity_anchor", {}),
            "scene_index": scene_index,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/api/scene/refine-with-chat")
async def api_scene_refine_with_chat(body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Refine a scene's seedance_prompt via user chat feedback. narrative_summary is read-only."""
    from tools.scene_editor import refine_scene_with_chat

    scene_index = body.get("scene_index")
    feedback = body.get("user_feedback", "")
    field = "seedance"
    chat_history = body.get("chat_history", [])

    try:
        storyboard = _resolve_storyboard_payload(body, ctx)
    except HTTPException as exc:
        return _json_http_error(exc)

    if scene_index is None or not feedback:
        return JSONResponse(status_code=400, content={
            "error": "Missing scene_index or user_feedback"
        })

    scenes = storyboard.get("storyboard", [])
    if scene_index < 0 or scene_index >= len(scenes):
        return JSONResponse(status_code=400, content={"error": f"Invalid scene_index: {scene_index}"})

    scene = scenes[scene_index]

    try:
        def _refine_sync():
            from dashboard.usage_tracker import usage_context
            with usage_context(user_id=ctx.user.id, step="scene_edit"):
                return refine_scene_with_chat(
                    user_feedback=feedback,
                    scene=scene,
                    storyboard_context=storyboard,
                    field=field,
                    chat_history=chat_history,
                )
        result = await asyncio.get_event_loop().run_in_executor(
            None, _refine_sync,
        )
        return {
            "ok": True,
            "narrative_summary": result["narrative_summary"],
            "seedance_prompt": result["seedance_prompt"],
            "transition_strategy": result.get("transition_strategy", ""),
            "continuity_anchor": result.get("continuity_anchor", {}),
            "scene_index": scene_index,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
