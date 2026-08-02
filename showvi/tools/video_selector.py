"""
Video selector & concatenator.

Phase 1 — Selection:
  After generation, use Gemini VLM to pick the best video for each segment
  that has multiple successful attempts.  Criteria: subject consistency with
  reference image, audio/dialogue correctness, visual quality, storyboard
  coherence.

Phase 2 — Concatenation:
  Stitch the selected videos in segment order with smooth audio transitions.
  Each clip's audio loudness is normalised so adjacent clips have matching
  levels, and a short audio crossfade is applied at each junction.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google.genai import types

from clients import get_llm_client
from prompts.video_selector import VIDEO_SELECTOR_SYSTEM

_logger = logging.getLogger("video_agent.video_selector")

# ── Gemini response schema ───────────────────────────────────────────

_SELECTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "selected_index": {
            "type": "integer",
            "description": "0-based index of the best video among the candidates",
        },
        "reasoning": {
            "type": "string",
            "description": "Brief explanation of why this video was chosen",
        },
        "per_video_scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "subject_consistency": {
                        "type": "number", "minimum": 0, "maximum": 10,
                        "description": "How well the characters/subjects match the reference image",
                    },
                    "audio_correctness": {
                        "type": "number", "minimum": 0, "maximum": 10,
                        "description": "Whether dialogue/audio matches the script",
                    },
                    "visual_quality": {
                        "type": "number", "minimum": 0, "maximum": 10,
                        "description": "Overall visual quality, cinematography, lighting",
                    },
                    "content_accuracy": {
                        "type": "number", "minimum": 0, "maximum": 10,
                        "description": "How well the video matches the storyboard description",
                    },
                    "overall": {
                        "type": "number", "minimum": 0, "maximum": 10,
                    },
                },
                "required": [
                    "index", "subject_consistency", "audio_correctness",
                    "visual_quality", "content_accuracy", "overall",
                ],
            },
        },
    },
    "required": ["selected_index", "reasoning", "per_video_scores"],
}


def _build_system_prompt() -> str:
    return VIDEO_SELECTOR_SYSTEM


def _build_user_prompt(script_text: str, n_videos: int, has_ref_image: bool) -> str:
    parts = [f"## 分镜剧本\n\n{script_text}\n"]

    if has_ref_image:
        parts.append("## 参考图片\n已附在消息中（第一张图片）。\n")

    parts.append(
        f"## 候选视频\n"
        f"共 {n_videos} 段候选视频，已按顺序附在消息中"
        f"（视频 0 ~ 视频 {n_videos - 1}）。\n\n"
        f"请评估每段视频并选出最佳的一段。"
    )
    return "\n".join(parts)


def select_best_video(
    video_paths: List[str],
    script_text: str,
    reference_image_path: Optional[str] = None,
    model: str = "gemini-3-pro-preview",
) -> Dict[str, Any]:
    """
    Send multiple candidate videos (+ optional reference image) to Gemini
    and ask it to pick the best one.

    Returns:
        {
            "selected_index": int,
            "selected_path": str,
            "reasoning": str,
            "per_video_scores": [...],
        }
    """
    if len(video_paths) < 2:
        raise ValueError("Need at least 2 candidate videos for selection")

    client = get_llm_client(step="video_select")

    leading_parts: list[types.Part] = []
    has_ref = False
    if reference_image_path and Path(reference_image_path).exists():
        img_bytes = Path(reference_image_path).read_bytes()
        suffix = Path(reference_image_path).suffix.lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "webp": "image/webp"}.get(suffix.lstrip("."), "image/png")
        leading_parts.append(types.Part(inline_data=types.Blob(data=img_bytes, mime_type=mime)))
        has_ref = True

    user_prompt = _build_user_prompt(script_text, len(video_paths), has_ref)

    _logger.info(
        "Selecting best video from %d candidates (ref_image=%s, model=%s)",
        len(video_paths), bool(has_ref), model,
    )

    data = json.loads(client.generate_with_video(
        text_prompt=user_prompt,
        video_paths=video_paths,
        system_instruction=_build_system_prompt(),
        temperature=0.2,
        response_schema=_SELECTION_SCHEMA,
        model=model,
        timeout_seconds=client.VIDEO_TIMEOUT_SECONDS,
        max_retries=3,
        auto_adjust_fps=True,
        use_low_resolution=True,
        leading_parts=leading_parts,
    ))
    idx = data["selected_index"]
    if idx < 0 or idx >= len(video_paths):
        _logger.warning("Gemini returned invalid index %d, falling back to 0", idx)
        idx = 0
        data["selected_index"] = 0

    data["selected_path"] = video_paths[idx]
    return data


def select_best_videos_for_project(
    work_units,
    output_dir: str,
    storyboard=None,
    model: str = "gemini-3-pro-preview",
) -> Dict[int, Dict[str, Any]]:
    """
    Scan all work units, find those with multiple successful video attempts,
    and use Gemini to pick the best video for each.

    Args:
        work_units: list of WorkUnit
        output_dir: project output directory
        storyboard: Storyboard object (for scene context)
        model: Gemini model name

    Returns:
        {unit_id: selection_result} for units where selection was performed
    """
    results: Dict[int, Dict[str, Any]] = {}

    for unit in work_units:
        candidate_paths = _collect_candidate_videos(unit, output_dir)

        if len(candidate_paths) < 2:
            _logger.info(
                "Unit %d: %d candidate(s), skipping selection",
                unit.unit_id, len(candidate_paths),
            )
            continue

        script_text = _build_script_context(unit, storyboard)

        print(f"\n[SELECTOR] Unit {unit.unit_id}: {len(candidate_paths)} candidate videos")
        for i, p in enumerate(candidate_paths):
            print(f"  [{i}] {Path(p).name}")

        try:
            result = select_best_video(
                video_paths=candidate_paths,
                script_text=script_text,
                reference_image_path=unit.reference_image_path,
                model=model,
            )
            results[unit.unit_id] = result

            selected = result["selected_path"]
            print(f"[SELECTOR] ✓ Unit {unit.unit_id}: selected [{result['selected_index']}] "
                  f"{Path(selected).name}")
            print(f"  Reason: {result['reasoning']}")

            if result.get("per_video_scores"):
                for vs in result["per_video_scores"]:
                    print(f"  Video {vs['index']}: "
                          f"主体={vs['subject_consistency']:.0f} "
                          f"音频={vs['audio_correctness']:.0f} "
                          f"画质={vs['visual_quality']:.0f} "
                          f"内容={vs['content_accuracy']:.0f} "
                          f"综合={vs['overall']:.0f}")

            unit.final_video_path = selected

        except Exception as exc:
            _logger.error("Selection failed for unit %d: %s", unit.unit_id, exc, exc_info=True)
            print(f"[SELECTOR] ✗ Unit {unit.unit_id}: selection failed ({exc}), keeping current choice")

    return results


# ── Helpers ───────────────────────────────────────────────────────────

def _collect_candidate_videos(unit, output_dir: str) -> List[str]:
    """Collect all existing video files for a unit from attempts + filesystem."""
    seen = set()
    paths = []

    for attempt in unit.attempts:
        p = attempt.output_path
        if p and Path(p).exists() and p not in seen:
            seen.add(p)
            paths.append(p)

    pattern = f"segment_{unit.unit_id}_attempt_*.mp4"
    for f in sorted(Path(output_dir).glob(pattern)):
        fp = str(f)
        if fp not in seen:
            seen.add(fp)
            paths.append(fp)

    return paths


def _build_script_context(unit, storyboard=None) -> str:
    """Build the script/storyboard description for a unit."""
    parts = []

    if unit.group_name:
        parts.append(f"**段落名称**: {unit.group_name}")
    if unit.narrative_summary:
        parts.append(f"**剧情概要**: {unit.narrative_summary}")

    if storyboard and unit.scene_numbers:
        scene_map = {s.scene_number: s for s in storyboard.scenes}
        for sn in sorted(unit.scene_numbers):
            s = scene_map.get(sn)
            if not s:
                continue
            scene_text = (
                f"### 场景 {s.scene_number}\n"
                f"- 剧情: {s.plot_description}\n"
                f"- 画面: {s.visual_description}\n"
                f"- 角色: {', '.join(s.characters_in_scene)}\n"
                f"- 场景: {s.scene_location}\n"
                f"- 机位: {s.camera_angle}\n"
                f"- 氛围: {s.mood}\n"
                f"- 光线: {s.lighting}\n"
                f"- 时长: {s.duration}"
            )
            if s.dialogue_lines:
                dl_parts = []
                for dl in s.dialogue_lines:
                    speaker = dl.get("speaker", "")
                    text = dl.get("text", "")
                    emotion = dl.get("emotion", "")
                    emo = f"({emotion})" if emotion else ""
                    dl_parts.append(f"  [{speaker}{emo}] {text}")
                scene_text += "\n- 对白:\n" + "\n".join(dl_parts)
            elif s.dialogue:
                scene_text += f"\n- 对白: {s.dialogue}"
            parts.append(scene_text)
    else:
        parts.append(f"**生成提示词**: {unit.prompt}")

    return "\n\n".join(parts)


# ══════════════════════════════════════════════════════════════════════
#  Phase 2 — Audio-aware video concatenation
# ══════════════════════════════════════════════════════════════════════

def _measure_mean_volume(video_path: str) -> float:
    """Return the mean volume (dB) of a video's audio track via ffmpeg."""
    cmd = [
        "ffmpeg", "-i", video_path,
        "-af", "volumedetect",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    stderr = result.stderr
    match = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", stderr)
    if match:
        return float(match.group(1))
    _logger.warning("Could not detect mean_volume for %s, defaulting to -20 dB", video_path)
    return -20.0


def _get_duration(video_path: str) -> float:
    """Return video duration in seconds."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0", video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def concatenate_videos(
    video_paths: List[str],
    output_path: str,
    audio_crossfade_ms: int = 500,
    video_codec: str = "libx264",
    audio_codec: str = "aac",
    crf: int = 18,
) -> str:
    """
    Concatenate videos in order with audio loudness normalisation and crossfade.

    Steps:
      1. Measure each clip's mean audio volume (dB).
      2. Use the first clip as the reference; compute per-clip gain adjustments.
      3. Build an ffmpeg filter_complex that:
         a. Scales each clip's video to a common resolution / fps.
         b. Applies volume gain to each clip's audio.
         c. Concatenates with a short audio crossfade between adjacent clips.
      4. Output the final file.

    Args:
        video_paths: ordered list of video file paths
        output_path: destination mp4
        audio_crossfade_ms: crossfade duration at junctions (milliseconds)
        video_codec / audio_codec / crf: encoding settings

    Returns:
        output_path
    """
    if not video_paths:
        raise ValueError("No video paths provided")
    if len(video_paths) == 1:
        import shutil
        shutil.copy2(video_paths[0], output_path)
        print(f"[CONCAT] Single video, copied to {output_path}")
        return output_path

    n = len(video_paths)

    # ── 1. Measure volumes ────────────────────────────────────────────
    volumes: List[float] = []
    durations: List[float] = []
    for vp in video_paths:
        vol = _measure_mean_volume(vp)
        dur = _get_duration(vp)
        volumes.append(vol)
        durations.append(dur)
        print(f"[CONCAT] {Path(vp).name}: mean_volume={vol:.1f} dB, duration={dur:.2f}s")

    ref_volume = volumes[0]
    gains = [0.0] + [ref_volume - v for v in volumes[1:]]
    for i, g in enumerate(gains):
        if abs(g) > 0.1:
            print(f"[CONCAT] {Path(video_paths[i]).name}: gain adjustment = {g:+.1f} dB")

    # ── 2. Probe first clip for resolution / fps ─────────────────────
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate",
        "-of", "json", video_paths[0],
    ]
    probe = subprocess.run(probe_cmd, capture_output=True, text=True)
    probe_data = json.loads(probe.stdout)
    stream = probe_data["streams"][0]
    width, height = stream["width"], stream["height"]
    fps_str = stream["r_frame_rate"]

    # ── 3. Build ffmpeg filter_complex ────────────────────────────────
    inputs = []
    for vp in video_paths:
        inputs.extend(["-i", vp])

    cf_sec = audio_crossfade_ms / 1000.0

    # Video: scale + fps + setsar → concat
    # Audio: volume adjust → acrossfade chain
    v_filters = []
    a_filters = []

    for i in range(n):
        v_filters.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={fps_str},setsar=1[v{i}]"
        )
        gain_filter = f"volume={gains[i]}dB" if abs(gains[i]) > 0.1 else "anull"
        a_filters.append(f"[{i}:a]{gain_filter}[a{i}]")

    # Video concat (simple)
    v_inputs = "".join(f"[v{i}]" for i in range(n))
    v_filters.append(f"{v_inputs}concat=n={n}:v=1:a=0[vout]")

    # Audio: chain acrossfade between adjacent clips
    if n == 2:
        a_filters.append(
            f"[a0][a1]acrossfade=d={cf_sec}:c1=tri:c2=tri[aout]"
        )
    else:
        # Chain: (a0 x a1) → tmp0, (tmp0 x a2) → tmp1, ... → aout
        prev = "a0"
        for i in range(1, n):
            out_label = "aout" if i == n - 1 else f"atmp{i}"
            a_filters.append(
                f"[{prev}][a{i}]acrossfade=d={cf_sec}:c1=tri:c2=tri[{out_label}]"
            )
            prev = out_label

    filter_complex = ";\n".join(v_filters + a_filters)

    # ── 4. Run ffmpeg ─────────────────────────────────────────────────
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", video_codec, "-crf", str(crf),
        "-c:a", audio_codec, "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]

    _logger.info("Concat command: %s", " ".join(cmd))
    print(f"[CONCAT] Concatenating {n} videos → {output_path}")

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        _logger.error("ffmpeg failed:\n%s", proc.stderr[-2000:])
        raise RuntimeError(f"ffmpeg concat failed (rc={proc.returncode}): {proc.stderr[-500:]}")

    out_dur = _get_duration(output_path)
    out_size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    print(f"[CONCAT] ✓ Done: {out_dur:.2f}s, {out_size_mb:.1f} MB")
    return output_path


def select_and_concat(
    work_units,
    output_dir: str,
    storyboard=None,
    model: str = "gemini-3-pro-preview",
    output_filename: str = "final_video.mp4",
    audio_crossfade_ms: int = 500,
) -> Dict[str, Any]:
    """
    End-to-end post-generation pipeline:
      1. For each unit with multiple candidates, use Gemini to pick the best.
      2. Concatenate the selected videos in unit order with audio normalisation.

    Returns:
        {
            "selection_results": {unit_id: ...},
            "final_video_path": str,
            "segment_videos": [path, ...],
        }
    """
    # ── Selection ─────────────────────────────────────────────────────
    all_units = sorted(work_units, key=lambda u: u.unit_id)

    multi_units = [
        u for u in all_units
        if u.is_completed and len(_collect_candidate_videos(u, output_dir)) >= 2
    ]

    selection_results: Dict[int, Dict[str, Any]] = {}
    if multi_units:
        print(f"\n{'=' * 70}")
        print(f"VIDEO SELECTOR — Picking best video for {len(multi_units)} unit(s)")
        print(f"{'=' * 70}")
        selection_results = select_best_videos_for_project(
            work_units=multi_units,
            output_dir=output_dir,
            storyboard=storyboard,
            model=model,
        )
    else:
        print("[SELECTOR] No units with multiple candidates, skipping selection")

    # ── Gather final videos in order ──────────────────────────────────
    segment_videos: List[str] = []
    for unit in all_units:
        vp = unit.final_video_path
        if vp and Path(vp).exists():
            segment_videos.append(vp)
        else:
            print(f"[CONCAT] ⚠ Unit {unit.unit_id}: no video available, skipping")

    if not segment_videos:
        raise RuntimeError("No segment videos available for concatenation")

    # ── Concatenate ───────────────────────────────────────────────────
    # Use the new concat algorithm (tools/video_concat.py) which fixes the
    # audio/video desync caused by chained acrossfade in the old implementation.
    # We write a temporary timeline JSON and call concat_videos_from_timeline.
    import json as _json
    import tempfile as _tempfile
    from tools.video_concat import concat_videos_from_timeline

    final_path = str(Path(output_dir) / output_filename)

    timeline_data = {
        "timeline": [
            {"clip_video_path": vp, "segment_index": i}
            for i, vp in enumerate(segment_videos)
        ]
    }
    with _tempfile.NamedTemporaryFile(
        mode="w", suffix="_timeline.json", delete=False, encoding="utf-8"
    ) as tf:
        _json.dump(timeline_data, tf, ensure_ascii=False)
        timeline_tmp = tf.name

    try:
        result = concat_videos_from_timeline(
            timeline_path=timeline_tmp,
            output_path=final_path,
            fade=audio_crossfade_ms / 1000.0,
        )
        if not result.get("success"):
            raise RuntimeError(f"concat_videos_from_timeline failed: {result.get('error')}")
    finally:
        import os as _os
        _os.unlink(timeline_tmp)

    return {
        "selection_results": selection_results,
        "final_video_path": final_path,
        "segment_videos": segment_videos,
    }
