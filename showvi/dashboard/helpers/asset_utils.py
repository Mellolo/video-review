"""Asset utility helpers extracted from dashboard/server.py."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from dashboard.request_context import get_current_workspace
from dashboard.workspace import WorkspaceContext

ASSET_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
ASSET_CATEGORY_PREFIXES = {
    "characters": "charsheet_",
    "locations": "locsheet_",
    "props": "propsheet_",
}
ASSET_CATEGORY_ALIASES = {
    "character": "characters",
    "characters": "characters",
    "location": "locations",
    "locations": "locations",
    "prop": "props",
    "props": "props",
}



def _workspace_or_current(workspace: Optional[WorkspaceContext] = None) -> WorkspaceContext:
    ctx = workspace or get_current_workspace()
    if not ctx:
        raise RuntimeError("Workspace context is required")
    return ctx



def _normalize_asset_category(category: str) -> Optional[str]:
    return ASSET_CATEGORY_ALIASES.get((category or "").strip().lower())



def _sanitize_asset_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", (name or "").strip())
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._ ")
    return cleaned or f"asset_{int(time.time())}"



def _guess_uploaded_image_suffix(file: UploadFile) -> str:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix in ASSET_IMAGE_EXTENSIONS:
        return suffix
    content_type = (file.content_type or "").lower()
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
    }.get(content_type, ".png")



def _get_unique_asset_path(category: str, name: str, suffix: str, *, workspace: Optional[WorkspaceContext] = None) -> Path:
    ws = _workspace_or_current(workspace)
    prefix = ASSET_CATEGORY_PREFIXES[category]
    candidate = ws.assets_dir / f"{prefix}{_sanitize_asset_name(name)}{suffix}"
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    counter = 2
    while True:
        alt = ws.assets_dir / f"{stem}_{counter}{suffix}"
        if not alt.exists():
            return alt
        counter += 1



async def _save_uploaded_asset_file(file: UploadFile, *, category: str, name: str, workspace: Optional[WorkspaceContext] = None) -> Path:
    ws = _workspace_or_current(workspace)
    normalized_category = _normalize_asset_category(category)
    if not normalized_category:
        raise ValueError("不支持的素材分类")

    suffix = _guess_uploaded_image_suffix(file)
    content_type = (file.content_type or "").lower()
    if not content_type.startswith("image/") and suffix not in ASSET_IMAGE_EXTENSIONS:
        raise ValueError("只支持 PNG、JPG、JPEG、WEBP 图片")

    content = await file.read()
    if not content:
        raise ValueError("上传的图片不能为空")

    ws.assets_dir.mkdir(parents=True, exist_ok=True)
    dest = _get_unique_asset_path(normalized_category, name, suffix, workspace=ws)
    with open(dest, "wb") as f:
        f.write(content)
    return dest



def _categorize_assets_dir_file(file_path: Path) -> tuple[str, str]:
    name_lower = file_path.name.lower()
    label = file_path.stem
    if name_lower.startswith("charsheet_"):
        return "characters", label[len("charsheet_"):]
    if name_lower.startswith("locsheet_"):
        return "locations", label[len("locsheet_"):]
    if name_lower.startswith("propsheet_"):
        return "props", label[len("propsheet_"):]
    return "characters", label



def _build_assets_dir_entry(file_path: Path) -> dict:
    category, display_name = _categorize_assets_dir_file(file_path)
    return {
        "filename": file_path.name,
        "path": str(file_path),
        "project": "assets",
        "run": "",
        "url": f"/asset?path={str(file_path)}",
        "name": display_name,
        "size": file_path.stat().st_size,
        "mtime": file_path.stat().st_mtime,
        "category": category,
    }



def _scan_asset_library(*, workspace: Optional[WorkspaceContext] = None) -> dict:
    """Scan all output directories to collect generated images into a library."""
    ws = _workspace_or_current(workspace)
    library = {"characters": [], "locations": [], "props": []}
    seen_hashes = set()
    output_base = ws.output_dir

    if output_base.exists():
        for project_dir in sorted(output_base.iterdir()):
            if not project_dir.is_dir() or project_dir.name.startswith("."):
                continue
            for run_dir in sorted(project_dir.iterdir()):
                if not run_dir.is_dir() or run_dir.name.startswith("."):
                    continue
                for f in sorted(run_dir.iterdir()):
                    if not f.is_file() or f.suffix != ".png":
                        continue
                    name_lower = f.name.lower()
                    if "_masked" in name_lower or "_v2" in name_lower:
                        continue
                    dedup_key = f"{f.stem}_{f.stat().st_size}"
                    if dedup_key in seen_hashes:
                        continue
                    seen_hashes.add(dedup_key)

                    mtime = f.stat().st_mtime
                    entry = {
                        "filename": f.name,
                        "path": str(f),
                        "project": project_dir.name,
                        "run": run_dir.name,
                        "url": f"/repo-media/{project_dir.name}/{run_dir.name}/{f.name}",
                        "size": f.stat().st_size,
                        "mtime": mtime,
                    }
                    label = f.stem
                    if name_lower.startswith("charsheet_"):
                        entry["name"] = label.replace("charsheet_", "")
                        library["characters"].append(entry)
                    elif name_lower.startswith("locsheet_"):
                        entry["name"] = label.replace("locsheet_", "")
                        library["locations"].append(entry)
                    elif name_lower.startswith("propsheet_"):
                        entry["name"] = label.replace("propsheet_", "")
                        library["props"].append(entry)

    if ws.assets_dir.exists():
        for f in sorted(ws.assets_dir.iterdir(), key=lambda x: x.stat().st_mtime):
            if f.is_file() and f.suffix.lower() in ASSET_IMAGE_EXTENSIONS:
                entry = _build_assets_dir_entry(f)
                library[entry["category"]].append(entry)

    for cat in library:
        library[cat].sort(key=lambda x: x.get("mtime", 0), reverse=True)

    return library
