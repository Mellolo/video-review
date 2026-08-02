"""Media scanning helpers extracted from dashboard/server.py."""

import re
from pathlib import Path


def _segment_sort_key(name: str):
    match = re.search(r"segment_(\d+)", name.lower())
    return (int(match.group(1)) if match else 10**9, name)



def scan_media(run_dir: Path) -> dict:
    """Scan the run directory for all generated media files."""
    media = {
        "charsheets": [],
        "locsheets": [],
        "propsheets": [],
        "segments": [],
        "final": None,
        "final_mtime": None,
    }
    if not run_dir or not run_dir.exists():
        return media
    for f in sorted(run_dir.iterdir()):
        name = f.name.lower()
        if name.startswith("charsheet_") and name.endswith(".png") and "_masked" not in name:
            media["charsheets"].append(f.name)
        elif name.startswith("locsheet_") and name.endswith(".png") and "_v2" not in name:
            media["locsheets"].append(f.name)
        elif name.startswith("propsheet_") and name.endswith(".png"):
            media["propsheets"].append(f.name)
        elif name.startswith("segment_") and name.endswith(".mp4"):
            media["segments"].append(f.name)
        elif name == "final_video.mp4":
            media["final"] = f.name
            try:
                media["final_mtime"] = int(f.stat().st_mtime)
            except OSError:
                media["final_mtime"] = None
    media["segments"].sort(key=_segment_sort_key)
    return media
