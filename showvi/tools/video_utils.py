"""Video utility functions shared across tools.

Provides:
- extract_smart_grid:  前序剧情提取 — 从视频抽 16 帧拼成 4×4 16 宫格
"""

import logging
from pathlib import Path

import cv2
import numpy as np

_log = logging.getLogger("video_agent.video_utils")




# ═══════════════════════════════════════════════════════════════════════
#  前序剧情提取：从视频抽 16 帧拼成 4×4 16 宫格
# ═══════════════════════════════════════════════════════════════════════


def _extract_uniform_frames(video_path: str, n: int = 16) -> list:
    """从视频均匀抽取 n 帧，返回 BGR ndarray 列表。"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total < n:
        raise RuntimeError(f"Video has only {total} frames, need at least {n}")

    indices = [int(total * (i + 0.5) / n) for i in range(n)]
    indices = [min(max(0, idx), total - 1) for idx in indices]

    frames = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError(f"Failed to read frame {idx} from {video_path}")
        frames.append(frame)

    cap.release()
    return frames


def _stitch_grid(frames: list, cols: int, rows: int):
    """将帧列表拼成 rows×cols 网格，返回 ndarray。"""
    assert len(frames) == cols * rows
    h, w = frames[0].shape[:2]
    resized = [cv2.resize(f, (w, h)) if f.shape[:2] != (h, w) else f for f in frames]
    grid_rows = []
    for r in range(rows):
        grid_rows.append(np.hstack(resized[r * cols : (r + 1) * cols]))
    return np.vstack(grid_rows)


def _annotate_frames(frames: list) -> list:
    """在每帧左上角标注编号 (1-based)。"""
    annotated = []
    for i, frame in enumerate(frames):
        f = frame.copy()
        label = str(i + 1)
        h, w = f.shape[:2]
        font_scale = max(0.8, min(h, w) / 400)
        thickness = max(1, int(font_scale * 2))
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
        cv2.rectangle(f, (0, 0), (tw + 16, th + 16), (0, 0, 0), -1)
        cv2.putText(f, label, (8, th + 8), cv2.FONT_HERSHEY_SIMPLEX,
                    font_scale, (255, 255, 255), thickness)
        annotated.append(f)
    return annotated


def extract_smart_grid(
    video_path: str,
    output_path: str,
) -> str:
    """前序剧情提取：从视频均匀抽取 16 帧，拼成 4×4 16 宫格。

    Args:
        video_path: 输入视频路径
        output_path: 输出 16 宫格 PNG 路径

    Returns:
        输出文件路径

    Raises:
        RuntimeError: 视频无法打开或帧数不足
    """
    video_name = Path(video_path).stem
    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    frames_16 = _extract_uniform_frames(video_path, n=16)

    grid_16 = _stitch_grid(frames_16, cols=4, rows=4)
    grid_16_path = str(out_dir / f"{video_name}_16grid.png")
    cv2.imwrite(grid_16_path, grid_16)
    _log.info("16-grid saved → %s", grid_16_path)

    return grid_16_path
