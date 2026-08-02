"""
Video Concat tool — concatenate clips into a final video based on a timeline.

Handles heterogeneous resolutions by normalising all clips to a common
format before joining.  Supports optional crossfade transitions.
"""

import json
import os
import logging
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Literal, Optional

from .base import BaseTool, ToolResult, ExecutionContext, ToolCategory


# ── ffmpeg helpers ────────────────────────────────────────────────────

def _probe_video(path: str) -> dict:
    from utils.ffmpeg import get_first_video_stream
    return get_first_video_stream(path)


def _detect_resolution(video_paths: list[str]) -> tuple[int, int]:
    counter: Counter[tuple[int, int]] = Counter()
    for p in video_paths:
        info = _probe_video(p)
        if info:
            counter[(int(info["width"]), int(info["height"]))] += 1
    return counter.most_common(1)[0][0] if counter else (1280, 720)


def _measure_loudness(path: str) -> dict:
    """
    First-pass loudnorm analysis — measure integrated loudness, true peak, LRA.
    Returns the measured values needed for the second-pass linear normalization.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-i", path,
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    # loudnorm stats are printed to stderr as a JSON block
    stderr = result.stderr
    # Extract the JSON block from stderr (last { ... } block)
    json_start = stderr.rfind("{")
    json_end = stderr.rfind("}") + 1
    if json_start >= 0 and json_end > json_start:
        try:
            return json.loads(stderr[json_start:json_end])
        except json.JSONDecodeError:
            pass
    return {}


def _normalize_clip(
    src: str, dst: str, width: int, height: int, fps: int = 30,
    target_i: float = -16.0, target_tp: float = -1.5, target_lra: float = 11.0,
) -> bool:
    """
    Normalize video resolution/fps AND audio loudness (EBU R128 two-pass).

    Two-pass loudnorm ensures all clips land at the same integrated loudness
    using linear mode (no dynamic compression), preserving the original
    dynamics while aligning average levels across clips.
    """
    # ── Pass 1: measure loudness ──
    stats = _measure_loudness(src)
    measured_i = stats.get("input_i", "-24.0")
    measured_tp = stats.get("input_tp", "-2.0")
    measured_lra = stats.get("input_lra", "7.0")
    measured_thresh = stats.get("input_thresh", "-34.0")
    target_offset = stats.get("target_offset", "0.0")

    # ── Pass 2: apply linear normalization together with video scaling ──
    loudnorm_filter = (
        f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}"
        f":measured_I={measured_i}:measured_TP={measured_tp}"
        f":measured_LRA={measured_lra}:measured_thresh={measured_thresh}"
        f":offset={target_offset}:linear=true:print_format=summary"
    )

    cmd = [
        "ffmpeg", "-y", "-i", src,
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
        "-af", loudnorm_filter,
        "-r", str(fps),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart",
        dst,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def _concat_demuxer(file_list_path: str, output_path: str) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", file_list_path,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def _get_duration(path: str) -> float:
    from utils.ffmpeg import get_video_duration
    return get_video_duration(path)


def _concat_crossfade(
    normalized_paths: list[str], output_path: str, fade: float = 0.5
) -> bool:
    """
    Concatenate videos with crossfade transitions.
    
    IMPORTANT: Audio synchronization fix.
    The original implementation had audio/video desync because acrossfade
    doesn't support offset parameter. This version uses adelay + amix to
    ensure audio stays in sync with video xfade transitions.
    """
    n = len(normalized_paths)
    if n == 0:
        return False
    if n == 1:
        shutil.copy2(normalized_paths[0], output_path)
        return True

    inputs: list[str] = []
    for p in normalized_paths:
        inputs += ["-i", p]

    durations = [_get_duration(p) for p in normalized_paths]
    
    # Build video xfade chain
    video_parts: list[str] = []
    prev_v = "0:v"
    video_offset = durations[0] - fade
    
    for i in range(1, n):
        out_v = f"v{i}"
        video_parts.append(
            f"[{prev_v}][{i}:v]xfade=transition=fade:duration={fade}:offset={video_offset}[{out_v}]"
        )
        prev_v = out_v
        video_offset += durations[i] - fade
    
    # Build audio chain with proper delays to match video timing
    audio_parts: list[str] = []
    audio_streams: list[str] = []
    
    current_time = 0.0
    for i in range(n):
        delay_ms = int(current_time * 1000)
        
        if i == 0:
            # First clip: no delay, apply fade out at the end
            audio_parts.append(
                f"[{i}:a]afade=t=out:st={durations[i]-fade}:d={fade}[a{i}]"
            )
        elif i == n - 1:
            # Last clip: delay + fade in at the start
            audio_parts.append(
                f"[{i}:a]afade=t=in:st=0:d={fade},adelay={delay_ms}|{delay_ms}[a{i}]"
            )
        else:
            # Middle clips: delay + fade in/out
            audio_parts.append(
                f"[{i}:a]afade=t=in:st=0:d={fade},afade=t=out:st={durations[i]-fade}:d={fade},adelay={delay_ms}|{delay_ms}[a{i}]"
            )
        
        audio_streams.append(f"[a{i}]")
        current_time += durations[i] - fade
    
    # Mix all audio streams — normalize=0 because clips are already loudnorm'd
    audio_parts.append(f"{''.join(audio_streams)}amix=inputs={n}:duration=longest:normalize=0[aout]")
    
    filter_complex = ";".join(video_parts + audio_parts)
    
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{prev_v}]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FFmpeg crossfade error: {result.stderr}")
    return result.returncode == 0


# ── Public API ────────────────────────────────────────────────────────

def concat_videos_from_timeline(
    timeline_path: str,
    output_path: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    fps: int = 30,
    fade: float = 0.0,
) -> dict:
    """
    Read *timeline_path*, normalise all clips, and concatenate into one video.

    Returns a dict with keys: success, output_path, duration, size_mb, clip_count.
    """
    with open(timeline_path, encoding="utf-8") as f:
        data = json.load(f)

    entries = data["timeline"]
    video_paths = [e["clip_video_path"] for e in entries]

    missing = [p for p in video_paths if not Path(p).exists()]
    if missing:
        return {"success": False, "error": f"{len(missing)} clip(s) not found", "missing": missing[:5]}

    if output_path is None:
        output_path = str(Path(timeline_path).parent / "final_video.mp4")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if width and height:
        tw, th = width, height
    else:
        tw, th = _detect_resolution(video_paths)

    _log = logging.getLogger(__name__)

    tmp_dir = tempfile.mkdtemp(prefix="concat_timeline_")
    try:
        normalized: list[str] = []
        for i, src in enumerate(video_paths):
            dst = os.path.join(tmp_dir, f"{i:04d}.mp4")
            _log.info("  [%d/%d] Normalizing ...", i + 1, len(video_paths))
            # Loudness alignment is always enabled, including hard-cut mode.
            if _normalize_clip(src, dst, tw, th, fps):
                normalized.append(dst)
                _log.info("  [%d/%d] OK", i + 1, len(video_paths))
            else:
                _log.warning("  [%d/%d] FAILED (skipped)", i + 1, len(video_paths))

        if not normalized:
            return {"success": False, "error": "No clips normalised successfully"}

        if fade > 0:
            ok = _concat_crossfade(normalized, output_path, fade)
        else:
            filelist = os.path.join(tmp_dir, "filelist.txt")
            with open(filelist, "w") as fl:
                for p in normalized:
                    fl.write(f"file '{p}'\n")
            ok = _concat_demuxer(filelist, output_path)

        if not ok:
            return {"success": False, "error": "ffmpeg concat failed"}

        dur = _get_duration(output_path)
        size = os.path.getsize(output_path) / (1024 * 1024)
        return {
            "success": True,
            "output_path": output_path,
            "duration": round(dur, 2),
            "size_mb": round(size, 1),
            "clip_count": len(normalized),
            "resolution": f"{tw}x{th}",
            "audio_normalized": True,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── BaseTool wrapper ──────────────────────────────────────────────────

class VideoConcat(BaseTool):
    """Concatenate timeline clips into a single final video."""

    @property
    def name(self) -> str:
        return "video_concat"

    @property
    def description(self) -> str:
        return (
            "Concatenate all clips in a timeline JSON into one video. "
            "Auto-normalises resolution; supports optional crossfade."
        )

    @property
    def category(self) -> ToolCategory:
        return "post_processor"

    def execute(self, context: ExecutionContext, **params) -> ToolResult:
        """
        Params (passed via **params):
            timeline_path : str   — path to timeline.json (required)
            output_path   : str   — (optional) output video path
            width         : int   — (optional) target width
            height        : int   — (optional) target height
            fps           : int   — (optional, default 30)
            fade          : float — (optional) crossfade seconds, 0 = hard cut
        """
        timeline_path = params.get("timeline_path")
        if not timeline_path:
            return ToolResult(success=False, error="timeline_path is required")

        if not Path(timeline_path).exists():
            return ToolResult(success=False, error=f"Timeline not found: {timeline_path}")

        output = params.get("output_path")
        if not output:
            output = str(Path(timeline_path).parent / "final_video.mp4")

        result = concat_videos_from_timeline(
            timeline_path=timeline_path,
            output_path=output,
            width=params.get("width"),
            height=params.get("height"),
            fps=int(params.get("fps", 30)),
            fade=float(params.get("fade", 0.0)),
        )

        if result["success"]:
            return ToolResult(
                success=True,
                output_path=result["output_path"],
                metadata={
                    "duration": result["duration"],
                    "size_mb": result["size_mb"],
                    "clip_count": result["clip_count"],
                    "resolution": result["resolution"],
                },
            )
        return ToolResult(success=False, error=result.get("error", "Unknown error"))
