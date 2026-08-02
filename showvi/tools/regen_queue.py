from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.io import load_versioned_payload, save_versioned_payload

REQUESTS_FILENAME = "regen_requests.json"



def requests_file(run_dir: Path) -> Path:
    return Path(run_dir) / REQUESTS_FILENAME



def _load_requests_with_version(run_dir: Path) -> tuple[List[Dict[str, Any]], int]:
    path = requests_file(run_dir)
    data, version = load_versioned_payload(path, payload_key="requests", default_payload=[])
    if isinstance(data, list):
        return data, version
    return [], version



def load_requests(run_dir: Path) -> List[Dict[str, Any]]:
    requests, _ = _load_requests_with_version(run_dir)
    return requests



def save_requests(run_dir: Path, requests: List[Dict[str, Any]]) -> Path:
    path = requests_file(run_dir)
    save_versioned_payload(path, payload_key="requests", payload=requests, default=[])
    return path



def enqueue_request(
    run_dir: Path,
    *,
    unit_id: int,
    source_prompt: str = "",
    manual_prompt: str = "",
    manual_image_ref_assets: Optional[Dict[str, Any]] = None,
    extra_attempts: int = 1,
    created_from_attempt_id: Optional[int] = None,
    placeholder_attempt_id: Optional[int] = None,
) -> Dict[str, Any]:
    requests, current_version = _load_requests_with_version(run_dir)
    now = time.time()
    request = {
        "request_id": uuid.uuid4().hex[:12],
        "unit_id": int(unit_id),
        "status": "draft",
        "extra_attempts": max(1, int(extra_attempts or 1)),
        "source_prompt": (source_prompt or "").strip(),
        "manual_prompt": (manual_prompt or "").strip(),
        "manual_image_ref_assets": dict(manual_image_ref_assets or {}),
        "created_from_attempt_id": int(created_from_attempt_id) if created_from_attempt_id is not None else None,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "consumed_at": None,
        "placeholder_attempt_id": int(placeholder_attempt_id) if placeholder_attempt_id is not None else None,
        "version": current_version + 1,
    }
    requests.append(request)
    save_requests(run_dir, requests)
    return request



def update_request(
    run_dir: Path,
    request_id: str,
    **changes: Any,
) -> Optional[Dict[str, Any]]:
    requests, _ = _load_requests_with_version(run_dir)
    for req in requests:
        if req.get("request_id") == request_id:
            req.update(changes)
            req["updated_at"] = time.time()
            req["version"] = int(req.get("version") or 0) + 1
            save_requests(run_dir, requests)
            return req
    return None



def get_request(run_dir: Path, request_id: str) -> Optional[Dict[str, Any]]:
    for req in load_requests(run_dir):
        if req.get("request_id") == request_id:
            return req
    return None



def list_unit_requests(run_dir: Path, unit_id: int) -> List[Dict[str, Any]]:
    return [req for req in load_requests(run_dir) if int(req.get("unit_id", -1)) == int(unit_id)]
