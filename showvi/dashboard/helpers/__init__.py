"""Dashboard helper functions — re-exports for convenience."""

from dashboard.helpers.checkpoint import (
    find_latest_run,
    load_storyboard,
    load_checkpoint,
    save_checkpoint,
    _find_run_storyboard_copy,
    _load_storyboard_payload,
    _storyboard_from_checkpoint,
    _find_unit_by_id,
    _find_attempt_by_id,
    _find_video_job_for_run,
    _get_run_and_checkpoint,
    _normalize_storyboard_entity_descriptions,
    _get_latest_active_regen_request,
    _next_unit_attempt_id,
    _regen_requests_payload,
    _compute_video_job_progress,
    _live_progress_for_job,
    _hydrate_checkpoint_critiques_from_log,
    _resolve_existing_video_path,
    _unit_has_resolved_video,
    _checkpoint_indicates_video_success,
    _pid_is_alive,
)

from dashboard.helpers.project import (
    _list_all_projects,
    _resolve_storyboard,
    _switch_to_project,
)

from dashboard.helpers.media import (
    _segment_sort_key,
    scan_media,
)

from dashboard.helpers.asset_utils import (
    ASSET_IMAGE_EXTENSIONS,
    ASSET_CATEGORY_PREFIXES,
    ASSET_CATEGORY_ALIASES,
    _normalize_asset_category,
    _sanitize_asset_name,
    _guess_uploaded_image_suffix,
    _get_unique_asset_path,
    _save_uploaded_asset_file,
    _categorize_assets_dir_file,
    _build_assets_dir_entry,
    _scan_asset_library,
)

from dashboard.helpers.reconciliation import (
    _reconcile_stale_video_jobs,
    _collect_concat_sources,
    build_snapshot,
)

__all__ = [
    # checkpoint
    "find_latest_run",
    "load_storyboard",
    "load_checkpoint",
    "save_checkpoint",
    "_find_run_storyboard_copy",
    "_load_storyboard_payload",
    "_storyboard_from_checkpoint",
    "_find_unit_by_id",
    "_find_attempt_by_id",
    "_find_video_job_for_run",
    "_get_run_and_checkpoint",
    "_normalize_storyboard_entity_descriptions",
    "_get_latest_active_regen_request",
    "_next_unit_attempt_id",
    "_regen_requests_payload",
    "_compute_video_job_progress",
    "_live_progress_for_job",
    "_hydrate_checkpoint_critiques_from_log",
    "_resolve_existing_video_path",
    "_unit_has_resolved_video",
    "_checkpoint_indicates_video_success",
    "_pid_is_alive",
    # project
    "_list_all_projects",
    "_resolve_storyboard",
    "_switch_to_project",
    # media
    "_segment_sort_key",
    "scan_media",
    # asset_utils
    "ASSET_IMAGE_EXTENSIONS",
    "ASSET_CATEGORY_PREFIXES",
    "ASSET_CATEGORY_ALIASES",
    "_normalize_asset_category",
    "_sanitize_asset_name",
    "_guess_uploaded_image_suffix",
    "_get_unique_asset_path",
    "_save_uploaded_asset_file",
    "_categorize_assets_dir_file",
    "_build_assets_dir_entry",
    "_scan_asset_library",
    # reconciliation
    "_reconcile_stale_video_jobs",
    "_collect_concat_sources",
    "build_snapshot",
]
