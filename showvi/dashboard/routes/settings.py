"""Settings and library routes — assets listing, voices, settings."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse
from starlette.responses import Response

from dashboard.deps import DashboardContext, get_dashboard_context
from dashboard.helpers.asset_utils import ASSET_IMAGE_EXTENSIONS, _scan_asset_library
from dashboard.state import get_image_concurrency, get_video_concurrency, set_image_concurrency, set_video_concurrency
from dashboard.workspace import VOICE_REF_DIR, resolve_asset_path

_logger = logging.getLogger("dashboard.settings")

router = APIRouter(tags=["settings"])


@router.get("/api/assets")
async def api_assets(ctx: DashboardContext = Depends(get_dashboard_context)):
    """Return the full asset library."""
    return _scan_asset_library(workspace=ctx.workspace)


@router.delete("/api/assets")
async def api_delete_asset(path: str, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Delete a user-uploaded asset from current user assets directory only."""
    if not path:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Missing asset path"})

    try:
        target = resolve_asset_path(ctx.workspace, path, must_exist=True)
        assets_root = ctx.workspace.assets_dir.resolve()
    except Exception:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Invalid asset path"})

    if not target.is_file():
        return JSONResponse(status_code=404, content={"ok": False, "error": "Asset file not found"})

    if assets_root not in target.parents:
        return JSONResponse(status_code=403, content={"ok": False, "error": "Only files under current user assets/ can be deleted"})

    if target.suffix.lower() not in ASSET_IMAGE_EXTENSIONS:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Only image assets can be deleted"})

    try:
        target.unlink()
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

    return {"ok": True, "path": str(target)}


@router.get("/api/voices")
async def api_voices():
    """List available voice references."""
    voices = []
    jimeng_dir = VOICE_REF_DIR / "jimeng_voices"
    if jimeng_dir.exists():
        for f in sorted(jimeng_dir.iterdir()):
            if f.suffix in (".mp3", ".wav", ".ogg"):
                voices.append({
                    "filename": f.name,
                    "name": f.stem,
                    "path": str(f),
                    "url": f"/voice/{f.name}",
                })
    return voices


@router.get("/voice/{filename}")
async def serve_voice(filename: str):
    """Serve a voice reference audio file."""
    p = VOICE_REF_DIR / "jimeng_voices" / filename
    if p.exists() and p.is_file():
        ct = "audio/mpeg" if p.suffix == ".mp3" else "audio/wav" if p.suffix == ".wav" else "audio/ogg"
        return FileResponse(str(p), media_type=ct)
    return Response(status_code=404)


@router.get("/api/settings")
async def api_settings(ctx: DashboardContext = Depends(get_dashboard_context)):
    """Return current settings — always read directly from .env file."""
    from dashboard.env_store import read_env
    env = read_env()

    def _get(key, default=""):
        # Always read from .env file — os.environ may have stale startup values
        return env.get(key, default)

    return {
        "image_concurrency": get_image_concurrency(),
        "video_concurrency": get_video_concurrency(),
        # Provider config
        "llm_provider":    _get("LLM_PROVIDER",    "openai_compatible"),
        "llm_base_url":    _get("LLM_BASE_URL",    ""),
        "llm_api_key":     _get("LLM_API_KEY",     ""),
        "llm_model":       _get("LLM_MODEL",       ""),
        "image_provider":  _get("IMAGE_PROVIDER",  "google"),
        "image_base_url":  _get("IMAGE_BASE_URL",  ""),
        "image_api_key":   _get("IMAGE_API_KEY",   ""),
        "image_model":     _get("IMAGE_MODEL",     ""),
        # Video generation
        "jimeng_session_id":  _get("SEEDDANCE_SESSION_ID", ""),
        "seeddance_backend":  _get("SEEDDANCE_BACKEND",    "jimeng"),
        "gemini_api_key":     _get("GEMINI_API_KEY",       ""),
    }


@router.post("/api/settings")
async def api_settings_update(body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Persist settings by writing directly to .env file."""
    from dashboard.env_store import write_env

    _VALID_LLM_PROVIDERS   = {"openai_compatible", "google"}
    _VALID_IMAGE_PROVIDERS = {"openai_compatible", "google"}

    updates: dict[str, str] = {}

    def _accept(key: str, body_key: str | None = None, valid_set: set | None = None,
                allow_custom_prefix: bool = False):
        bk = body_key or key.lower()
        if bk not in body:
            return
        val = str(body[bk]).strip()
        if not val:
            return
        if valid_set and val not in valid_set:
            if not (allow_custom_prefix and val.startswith("custom:")):
                return
        updates[key] = val

    _accept("LLM_PROVIDER",   "llm_provider",   _VALID_LLM_PROVIDERS,   allow_custom_prefix=True)
    _accept("LLM_BASE_URL",   "llm_base_url")
    _accept("LLM_API_KEY",    "llm_api_key")
    _accept("LLM_MODEL",      "llm_model")
    _accept("IMAGE_PROVIDER", "image_provider", _VALID_IMAGE_PROVIDERS, allow_custom_prefix=True)
    _accept("IMAGE_BASE_URL", "image_base_url")
    _accept("IMAGE_API_KEY",  "image_api_key")
    _accept("IMAGE_MODEL",    "image_model")
    _accept("SEEDDANCE_SESSION_ID", "jimeng_session_id")
    _accept("SEEDDANCE_BACKEND",    "seeddance_backend")
    _accept("GEMINI_API_KEY",       "gemini_api_key")

    # Concurrency is in-memory only (not persisted to .env)
    if "image_concurrency" in body:
        set_image_concurrency(int(body["image_concurrency"]))
    if "video_concurrency" in body:
        set_video_concurrency(int(body["video_concurrency"]))

    if updates:
        write_env(updates)  # writes .env and syncs os.environ

    return {"ok": True, **{k.lower(): v for k, v in updates.items()},
            "image_concurrency": get_image_concurrency(),
            "video_concurrency": get_video_concurrency()}


@router.post("/api/settings/test")
async def api_settings_test(body: dict, ctx: DashboardContext = Depends(get_dashboard_context)):
    """Test LLM and image provider connectivity using the supplied config.

    Accepts the same fields as POST /api/settings but does NOT persist anything.
    Returns per-provider test results so the frontend can show pass/fail details.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    llm_provider  = str(body.get("llm_provider", "")).strip() or os.environ.get("LLM_PROVIDER", "openai_compatible")
    llm_base_url  = str(body.get("llm_base_url", "")).strip()  or os.environ.get("LLM_BASE_URL", "")
    llm_api_key   = str(body.get("llm_api_key", "")).strip()   or os.environ.get("LLM_API_KEY", "")
    llm_model     = str(body.get("llm_model", "")).strip()     or os.environ.get("LLM_MODEL", "gemini-3-flash-preview")
    image_provider = str(body.get("image_provider", "")).strip() or os.environ.get("IMAGE_PROVIDER", "google")
    image_base_url = str(body.get("image_base_url", "")).strip() or os.environ.get("IMAGE_BASE_URL", "")
    image_api_key  = str(body.get("image_api_key", "")).strip()  or os.environ.get("IMAGE_API_KEY", "")
    image_model    = str(body.get("image_model", "")).strip()    or os.environ.get("IMAGE_MODEL", "")

    results = {}

    def _test_llm() -> dict:
        try:
            from clients import get_llm_client
            client = get_llm_client(
                provider=llm_provider,
                api_key=llm_api_key or None,
                base_url=llm_base_url or None,
                model=llm_model or None,
            )
            prompt_in = "Reply with exactly: ok"
            base_url_display = llm_base_url if llm_provider != "google" else "(google api_key mode, no base_url)"
            print(f"[connectivity-test][LLM] provider={llm_provider} model={llm_model} base_url={base_url_display or '(default)'} prompt={prompt_in!r}")
            reply = client.generate_text(
                prompt=prompt_in,
                system_instruction="You are a connectivity test. Reply with exactly: ok",
                temperature=0.0,
                max_retries=1,
                timeout_seconds=20,
            )
            if reply is None:
                print("[connectivity-test][LLM] ✘ returned None (empty response)")
                return {"ok": False, "error": "LLM returned None (empty response)"}
            print(f"[connectivity-test][LLM] ✓ reply={reply.strip()[:200]!r}")
            return {"ok": True, "reply": reply.strip()[:80]}
        except Exception as exc:
            print(f"[connectivity-test][LLM] ✘ error: {exc}")
            _logger.warning("[connectivity-test][LLM] error: %s", exc, exc_info=True)
            return {"ok": False, "error": str(exc)[:300]}

    def _test_image() -> dict:
        """Test image generation — must receive actual image bytes to pass."""
        try:
            from clients import normalize_image_provider
            normalized = normalize_image_provider(image_provider)
            prompt_in = "a single red dot on white background"
            print(f"[connectivity-test][image] provider={image_provider} normalized={normalized} model={image_model or '(default)'} prompt={prompt_in!r}")

            _COMMON = dict(
                prompt="A man shown in four views: close-up face portrait, front full body, side full body, and back full body",
                aspect_ratio="1:1",
                image_size="1K",
                max_retries=1,
                timeout_seconds=90,
            )

            def _ok_result(client_obj, img_bytes: bytes) -> dict:
                url = getattr(client_obj, "last_remote_url", "") or ""
                try:
                    test_png = Path(__file__).parent.parent.parent / "test.png"
                    test_png.write_bytes(img_bytes)
                    print(f"[connectivity-test][image] ✓ {len(img_bytes):,} bytes, saved to {test_png}, url={url or '(none)'}")
                except Exception as e:
                    print(f"[connectivity-test][image] ✓ {len(img_bytes):,} bytes, url={url or '(none)'} (save failed: {e})")
                return {"ok": True, "reply": f"生成成功（{len(img_bytes):,} bytes）", "image_url": url}

            if normalized == "openai_compatible":
                from clients.openai_image import OpenAIImageClient
                client = OpenAIImageClient(
                    api_key=image_api_key or None,
                    model=image_model or None,
                    base_url=image_base_url or None,
                )
                img_bytes = client.generate_image(**_COMMON)
                return _ok_result(client, img_bytes)

            if normalized == "google":
                from clients.llm_client import LLMClient
                print(f"[connectivity-test][image] google backend: api_key mode (base_url ignored)")
                client = LLMClient(
                    api_key=image_api_key or os.environ.get("GEMINI_API_KEY"),
                    model=image_model or "gemini-3-pro-image-preview",
                    backend="api_key",
                )
                img_bytes = client.generate_image(**_COMMON)
                return _ok_result(client, img_bytes)

            if normalized.startswith("custom:"):
                from clients import _load_custom_plugin
                plugin_name = normalized.split(":", 1)[1]
                client = _load_custom_plugin(plugin_name, "image",
                                             api_key=image_api_key or None,
                                             model=image_model or None)
                img_bytes = client.generate_image(**_COMMON)
                return _ok_result(client, img_bytes)

            return {"ok": False, "error": f"未知 provider: {normalized}"}
        except Exception as exc:
            print(f"[connectivity-test][image] ✘ error: {exc}")
            _logger.warning("[connectivity-test][image] error: %s", exc, exc_info=True)
            return {"ok": False, "error": str(exc)[:300]}

    from starlette.responses import StreamingResponse

    async def _event_stream():
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=2) as pool:
            llm_fut   = loop.run_in_executor(pool, _test_llm)
            image_fut = loop.run_in_executor(pool, _test_image)
            pending = {llm_fut, image_fut}
            labels = {id(llm_fut): "llm", id(image_fut): "image"}
            overall_timeout = 120

            while pending:
                done, pending = await asyncio.wait(
                    pending, timeout=overall_timeout, return_when=asyncio.FIRST_COMPLETED,
                )
                for fut in done:
                    label = labels[id(fut)]
                    try:
                        result = fut.result()
                    except Exception as exc:
                        result = {"ok": False, "error": str(exc)[:300]}
                    yield f"data: {json.dumps({label: result}, ensure_ascii=False)}\n\n"

                if not done and pending:
                    for fut in pending:
                        label = labels[id(fut)]
                        yield f"data: {json.dumps({label: {'ok': False, 'error': f'{label.upper()} test timed out ({overall_timeout}s)'}}, ensure_ascii=False)}\n\n"
                    break

        yield "data: [DONE]\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream")
