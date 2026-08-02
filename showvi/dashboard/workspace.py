"""User-scoped workspace and path-resolution helpers."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, status

from dashboard.user_stub import User, SINGLE_USER
from dashboard.state import BASE_DIR, STATIC_DIR

LEGACY_STORYBOARDS_DIR = BASE_DIR / "storyboards"
LEGACY_OUTPUT_DIR = BASE_DIR / "output"
LEGACY_ASSETS_DIR = BASE_DIR / "assets"
LEGACY_DASHBOARD_DIR = BASE_DIR / ".dashboard"
LEGACY_UPLOADS_DIR = BASE_DIR / "dashboard" / "uploads"
VOICE_REF_DIR = BASE_DIR / "voice_references"
DATA_ROOT = BASE_DIR / "data" / "users"


@dataclass(frozen=True)
class WorkspaceContext:
    user_id: int
    username: str
    role: str
    root_dir: Path
    storyboards_dir: Path
    output_dir: Path
    uploads_dir: Path
    assets_dir: Path
    dashboard_dir: Path
    static_dir: Path
    base_dir: Path
    voice_ref_dir: Path
    use_legacy_paths: bool = False

    def ensure_dirs(self) -> None:
        for path in (self.root_dir, self.storyboards_dir, self.output_dir, self.uploads_dir, self.assets_dir, self.dashboard_dir):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def creation_jobs_file(self) -> Path:
        return self.dashboard_dir / "creation_jobs.json"

    @property
    def video_jobs_file(self) -> Path:
        return self.dashboard_dir / "video_jobs.json"

    @property
    def video_job_logs_dir(self) -> Path:
        return self.dashboard_dir / "video_job_logs"



def _admin_uses_legacy_root() -> bool:
    return os.environ.get("DASHBOARD_ADMIN_USE_LEGACY_ROOT", "1") == "1"



def get_workspace_for_user(user: User) -> WorkspaceContext:
    use_legacy = user.role == "admin" and _admin_uses_legacy_root()
    if use_legacy:
        ctx = WorkspaceContext(
            user_id=user.id,
            username=user.username,
            role=user.role,
            root_dir=BASE_DIR,
            storyboards_dir=LEGACY_STORYBOARDS_DIR,
            output_dir=LEGACY_OUTPUT_DIR,
            uploads_dir=LEGACY_UPLOADS_DIR,
            assets_dir=LEGACY_ASSETS_DIR,
            dashboard_dir=LEGACY_DASHBOARD_DIR,
            static_dir=STATIC_DIR,
            base_dir=BASE_DIR,
            voice_ref_dir=VOICE_REF_DIR,
            use_legacy_paths=True,
        )
    else:
        root_dir = DATA_ROOT / str(user.id)
        ctx = WorkspaceContext(
            user_id=user.id,
            username=user.username,
            role=user.role,
            root_dir=root_dir,
            storyboards_dir=root_dir / "storyboards",
            output_dir=root_dir / "output",
            uploads_dir=root_dir / "uploads",
            assets_dir=root_dir / "assets",
            dashboard_dir=root_dir / ".dashboard",
            static_dir=STATIC_DIR,
            base_dir=BASE_DIR,
            voice_ref_dir=VOICE_REF_DIR,
            use_legacy_paths=False,
        )
    ctx.ensure_dirs()
    ctx.video_job_logs_dir.mkdir(parents=True, exist_ok=True)
    return ctx



def get_workspace_by_user_id(user_id: int) -> WorkspaceContext:
    return get_workspace_for_user(SINGLE_USER)



def resolve_user_path(workspace: WorkspaceContext, raw_path: str | Path, *, allowed_roots: Iterable[Path], must_exist: bool = False) -> Path:
    if not raw_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing path")
    path = Path(raw_path)
    if not path.is_absolute():
        path = workspace.base_dir / path
    try:
        path = path.resolve()
    except Exception:
        path = path.absolute()
    normalized_roots = []
    for root in allowed_roots:
        try:
            normalized_roots.append(Path(root).resolve())
        except Exception:
            normalized_roots.append(Path(root).absolute())
    if not any(root == path or root in path.parents for root in normalized_roots):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Path is outside current user workspace")
    if must_exist and not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return path



def resolve_storyboard_path(workspace: WorkspaceContext, raw_path: str | Path, *, must_exist: bool = False) -> Path:
    return resolve_user_path(
        workspace,
        raw_path,
        allowed_roots=[workspace.storyboards_dir, workspace.output_dir],
        must_exist=must_exist,
    )



def resolve_asset_path(workspace: WorkspaceContext, raw_path: str | Path, *, must_exist: bool = False) -> Path:
    return resolve_user_path(
        workspace,
        raw_path,
        allowed_roots=[workspace.assets_dir, workspace.output_dir, workspace.storyboards_dir],
        must_exist=must_exist,
    )



def resolve_upload_path(workspace: WorkspaceContext, raw_path: str | Path, *, must_exist: bool = False) -> Path:
    return resolve_user_path(workspace, raw_path, allowed_roots=[workspace.uploads_dir], must_exist=must_exist)



def resolve_run_path(workspace: WorkspaceContext, project_name: str, run_id: str, *, must_exist: bool = True) -> Path:
    run_path = workspace.output_dir / project_name / run_id
    if not run_path.exists():
        run_path = workspace.output_dir / (project_name + "_storyboard") / run_id
    if must_exist and (not run_path.exists() or not run_path.is_dir()):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run_path



def migrate_legacy_data_to_workspace(user: User, *, move: bool = False) -> dict[str, str]:
    workspace = get_workspace_for_user(user)
    if workspace.use_legacy_paths:
        return {}

    operations = {
        str(LEGACY_STORYBOARDS_DIR): str(workspace.storyboards_dir),
        str(LEGACY_OUTPUT_DIR): str(workspace.output_dir),
        str(LEGACY_ASSETS_DIR): str(workspace.assets_dir),
        str(LEGACY_UPLOADS_DIR): str(workspace.uploads_dir),
        str(LEGACY_DASHBOARD_DIR): str(workspace.dashboard_dir),
    }
    for src_raw, dst_raw in operations.items():
        src = Path(src_raw)
        dst = Path(dst_raw)
        dst_has_content = dst.exists() and any(dst.iterdir())
        if not src.exists() or dst_has_content:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if move:
            shutil.move(str(src), str(dst))
        else:
            shutil.copytree(src, dst, dirs_exist_ok=True)
    return operations
