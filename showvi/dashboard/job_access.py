"""Helpers for filtering and validating user-owned jobs."""

from __future__ import annotations

from fastapi import HTTPException, status

from dashboard.state import creation_job_manager, video_job_manager



def _matches_owner(job: dict, user_id: int) -> bool:
    return int(job.get("owner_user_id") or -1) == int(user_id)



def list_creation_jobs_for_user(user_id: int) -> list[tuple[str, dict]]:
    return creation_job_manager.items_for_user(int(user_id))



def list_video_jobs_for_user(user_id: int) -> list[tuple[str, dict]]:
    return video_job_manager.items_for_user(int(user_id))



def get_creation_job_for_user(job_id: str, user_id: int) -> dict:
    job = creation_job_manager.get(job_id)
    if not job or not _matches_owner(job, user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job



def get_video_job_for_user(job_id: str, user_id: int) -> dict:
    job = video_job_manager.get(job_id)
    if not job or not _matches_owner(job, user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job
