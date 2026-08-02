"""Unified FFmpeg/ffprobe utilities."""

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger("video_agent.ffmpeg")


def probe_video(path: Union[str, Path]) -> Optional[dict]:
    """Run ffprobe and return the full JSON output, or None on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception as e:
        logger.warning(f"ffprobe failed for {path}: {e}")
    return None


def get_video_duration(path: Union[str, Path]) -> float:
    """Get video duration in seconds. Returns 0.0 on failure."""
    info = probe_video(path)
    if info:
        # Try format.duration first
        try:
            return float(info["format"]["duration"])
        except (KeyError, ValueError, TypeError):
            pass
        # Try first video stream
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                try:
                    return float(stream["duration"])
                except (KeyError, ValueError, TypeError):
                    pass
    return 0.0


def get_video_duration_optional(path: Union[str, Path]) -> Optional[float]:
    """Get video duration in seconds, returning None on failure.

    Also falls back to OpenCV if ffprobe is unavailable.
    Used by dashboard/persistence where None signals 'unknown'.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None

    if shutil.which("ffprobe"):
        dur = get_video_duration(p)
        if dur > 0:
            return dur

    # cv2 fallback (best-effort)
    try:
        import cv2
        cap = cv2.VideoCapture(str(p))
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.release()
        if fps and fps > 0 and frame_count and frame_count > 0:
            value = float(frame_count) / float(fps)
            if value > 0:
                return value
    except Exception:
        pass

    return None


def get_first_video_stream(path: Union[str, Path]) -> dict:
    """Run ffprobe and return the first video stream dict, or {} on failure."""
    info = probe_video(path)
    if info:
        for s in info.get("streams", []):
            if s.get("codec_type") == "video":
                return s
    return {}
