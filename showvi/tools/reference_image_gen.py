"""
参考图生成工具 — 基于原视频帧编辑模式。

工作流程:
  1. 从审核报告中提取关键时间戳
  2. 使用 ffmpeg 从视频中提取该时间戳的帧
  3. 调用 DashScope 图片编辑 API 对帧进行修改
  4. 保存参考图到输出目录

使用 DashScope 原生 API (httpx) 进行图片编辑，
支持 qwen-image-2.0-pro-2026-06-22 / qwen-image-max 等模型。
"""

import base64
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

_logger = logging.getLogger("video_agent.reference_image_gen")


# ── DashScope 图片编辑 API ─────────────────────────────────────────────

DASHSCOPE_IMAGE_API = "https://dashscope.aliyuncs.com/api/v1/services/aigc/image-generation/generation"
DEFAULT_IMAGE_MODEL = "qwen-image-2.0-pro-2026-06-22"


def _env(key: str, default: str = "") -> str:
    """Read config from .env file first, fall back to os.environ."""
    try:
        from dashboard.env_store import get_env_value
        return get_env_value(key, default)
    except Exception:
        return os.environ.get(key, default)


def _load_env_file() -> None:
    """从 .env 文件加载环境变量（如果尚未加载）。"""
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k and k not in os.environ:
                os.environ[k] = v


def generate_reference_image(
    critique_data: Dict[str, Any],
    video_path: str,
    scene_description: str,
    output_dir: str = "./output",
    model_image: str = DEFAULT_IMAGE_MODEL,
    api_key: Optional[str] = None,
) -> str:
    """基于审核结果和原视频帧生成参考图。

    1. 从审核结果中提取关键时间戳
    2. 用 ffmpeg 从视频提取该帧
    3. 调用 DashScope 图片编辑 API 对帧进行优化
    4. 保存参考图

    Args:
        critique_data: 审核结果 JSON（来自 critic_animation）
        video_path: 原视频文件路径
        scene_description: 场景描述
        output_dir: 输出目录
        model_image: 图片编辑模型名称
        api_key: DashScope API Key（默认从 .env 读取）

    Returns:
        生成的参考图文件路径

    Raises:
        FileNotFoundError: 视频文件不存在
        RuntimeError: 帧提取或图片编辑失败
    """
    _load_env_file()

    # 确保输出目录存在
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # 获取 API Key
    resolved_key = api_key or _env("LLM_API_KEY")
    if not resolved_key:
        raise ValueError("LLM_API_KEY 未设置，请在 .env 中配置")

    # 验证视频文件
    if not Path(video_path).exists():
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    # ── Step 1: 提取关键时间戳 ──
    critical_timestamps = _extract_critical_timestamps(critique_data)
    if not critical_timestamps:
        # 如果没有关键时间戳，使用视频中点
        duration = _get_video_duration(video_path)
        target_ts = duration / 2 if duration > 0 else 0
        print(f"[REF IMAGE] 无关键时间戳，使用视频中点: {target_ts:.1f}s")
    else:
        # 使用第一个关键时间戳
        target_ts = _timestamp_to_seconds(critical_timestamps[0])
        print(f"[REF IMAGE] 关键时间戳: {critical_timestamps[0]} ({target_ts:.1f}s)")

    # ── Step 2: 从视频提取帧 ──
    frame_path = _extract_video_frame(video_path, target_ts, output_dir)
    print(f"[REF IMAGE] 已提取帧: {frame_path}")

    # ── Step 3: 构建图片编辑 prompt ──
    edit_prompt = _build_edit_prompt(critique_data, scene_description)
    print(f"[REF IMAGE] 编辑 prompt: {edit_prompt[:100]}...")

    # ── Step 4: 调用 DashScope 图片编辑 API ──
    start_time = time.time()
    image_url = _call_dashscope_image_edit(
        frame_path=frame_path,
        prompt=edit_prompt,
        model=model_image,
        api_key=resolved_key,
    )

    # ── Step 5: 下载并保存参考图 ──
    output_path = _download_image(
        image_url, output_dir, prefix="reference_image"
    )

    elapsed = time.time() - start_time
    print(f"[REF IMAGE] Done in {elapsed:.1f}s → {output_path}")

    return output_path


# ── 内部工具方法 ──────────────────────────────────────────────────────


def _extract_critical_timestamps(critique_data: Dict[str, Any]) -> List[str]:
    """从审核结果中提取关键时间戳。"""
    timestamps: List[str] = []

    # 优先使用显式的时间戳列表
    if critique_data.get("critical_timestamps"):
        timestamps.extend(critique_data["critical_timestamps"])
    else:
        # 从 critical_issues 中提取
        for issue in critique_data.get("critical_issues", []):
            if isinstance(issue, dict) and issue.get("timestamp"):
                timestamps.append(issue["timestamp"])

    # 去重
    seen = set()
    unique = []
    for ts in timestamps:
        if ts and ts not in seen:
            seen.add(ts)
            unique.append(ts)

    return unique


def _timestamp_to_seconds(timestamp: str) -> float:
    """将时间戳字符串转换为秒数。支持 MM:SS 或 HH:MM:SS。"""
    parts = timestamp.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    try:
        return float(timestamp)
    except ValueError:
        return 0.0


def _get_video_duration(video_path: str) -> float:
    """获取视频时长（秒）。"""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0", video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _extract_video_frame(
    video_path: str,
    timestamp: float,
    output_dir: str,
) -> str:
    """使用 ffmpeg 从视频中提取指定时间戳的帧。

    Args:
        video_path: 视频文件路径
        timestamp: 时间戳（秒）
        output_dir: 输出目录

    Returns:
        提取的帧图片路径
    """
    frame_path = str(Path(output_dir) / f"frame_{timestamp:.0f}s.png")

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", "2",
        frame_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 帧提取失败: {result.stderr[-500:]}"
        )

    if not Path(frame_path).exists():
        raise RuntimeError(f"帧提取完成但文件不存在: {frame_path}")

    return frame_path


def _build_edit_prompt(
    critique_data: Dict[str, Any],
    scene_description: str,
) -> str:
    """基于审核结果构建图片编辑 prompt。

    将审核中发现的问题转化为具体的图片修改指令，
    结合场景描述生成优化的参考图。
    """
    parts = [f"场景描述: {scene_description}\n"]

    # 提取需要改进的问题
    issues = []
    for issue in critique_data.get("critical_issues", []):
        if isinstance(issue, dict):
            issues.append(f"- {issue.get('dimension', '')}: {issue.get('description', '')}")
        elif isinstance(issue, str):
            issues.append(f"- {issue}")

    if issues:
        parts.append("需要改进的问题:")
        parts.extend(issues)

    # 添加改进建议
    suggestions = critique_data.get("improvement_suggestions", "")
    if suggestions:
        parts.append(f"\n改进建议: {suggestions}")

    # 添加审核反馈
    feedback = critique_data.get("feedback", "")
    if feedback:
        parts.append(f"\n审核反馈: {feedback}")

    # 构建最终的图片编辑指令
    parts.append(
        "\n请基于此帧生成优化的参考图，修正上述问题，"
        "提升画面质量和角色一致性，保持场景描述中的关键元素。"
    )

    return "\n".join(parts)


def _call_dashscope_image_edit(
    frame_path: str,
    prompt: str,
    model: str,
    api_key: str,
) -> str:
    """调用 DashScope 原生图片编辑 API。

    使用 httpx 直接调用 DashScope 的图片生成/编辑 API，
    传入参考帧和编辑 prompt，返回编辑后的图片 URL。

    Args:
        frame_path: 参考帧图片路径
        prompt: 编辑 prompt
        model: 图片模型名称
        api_key: DashScope API Key

    Returns:
        编辑后的图片 URL

    Raises:
        RuntimeError: API 调用失败
    """
    # 读取帧图片并转为 base64
    with open(frame_path, "rb") as f:
        frame_bytes = f.read()
    frame_b64 = base64.b64encode(frame_bytes).decode("utf-8")
    ref_image = f"data:image/png;base64,{frame_b64}"

    # 构建请求体
    payload = {
        "model": model,
        "input": {
            "prompt": prompt,
            "ref_image": ref_image,
        },
        "parameters": {
            "size": "1024*1024",
            "n": 1,
        },
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    _logger.info("调用 DashScope 图片编辑 API (model=%s)", model)

    # 调用 API（带重试）
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=180.0) as client:
                response = client.post(
                    DASHSCOPE_IMAGE_API,
                    json=payload,
                    headers=headers,
                )

            if response.status_code == 200:
                result = response.json()
                image_url = _extract_image_url(result)
                if image_url:
                    _logger.info("图片编辑成功，URL: %s", image_url[:80])
                    return image_url
                else:
                    raise RuntimeError(
                        f"API 返回成功但未找到图片 URL: {json.dumps(result, ensure_ascii=False)[:500]}"
                    )
            else:
                error_msg = f"HTTP {response.status_code}: {response.text[:500]}"
                if attempt < max_retries:
                    _logger.warning(
                        "图片编辑 API 调用失败 (attempt %d/%d): %s",
                        attempt, max_retries, error_msg,
                    )
                    time.sleep(5 * attempt)
                else:
                    raise RuntimeError(f"DashScope 图片编辑 API 调用失败: {error_msg}")

        except httpx.TimeoutException:
            if attempt < max_retries:
                _logger.warning(
                    "图片编辑 API 超时 (attempt %d/%d)", attempt, max_retries
                )
                time.sleep(5 * attempt)
            else:
                raise RuntimeError("DashScope 图片编辑 API 超时（3 次重试后仍失败）")
        except Exception as e:
            if attempt < max_retries:
                _logger.warning(
                    "图片编辑 API 异常 (attempt %d/%d): %s",
                    attempt, max_retries, e,
                )
                time.sleep(5 * attempt)
            else:
                raise

    raise RuntimeError("DashScope 图片编辑 API 调用失败（不应到达此处）")


def _extract_image_url(response: Dict[str, Any]) -> Optional[str]:
    """从 DashScope API 响应中提取图片 URL。"""
    try:
        output = response.get("output", {})
        choices = output.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("image"):
                        return item["image"]
                    if isinstance(item, dict) and item.get("image_url"):
                        return item["image_url"]
            elif isinstance(content, str):
                # 有些 API 返回的 content 是字符串 URL
                if content.startswith("http"):
                    return content
        # 尝试其他可能的字段
        result = output.get("result", "")
        if isinstance(result, str) and result.startswith("http"):
            return result
        results = output.get("results", [])
        if results and isinstance(results[0], dict):
            url = results[0].get("url", "")
            if url:
                return url
    except Exception as e:
        _logger.error("解析图片 URL 失败: %s", e)
    return None


def _download_image(url: str, output_dir: str, prefix: str = "reference_image") -> str:
    """下载图片并保存到输出目录。

    Args:
        url: 图片 URL
        output_dir: 输出目录
        prefix: 文件名前缀

    Returns:
        保存的图片文件路径
    """
    timestamp = int(time.time())
    output_path = str(Path(output_dir) / f"{prefix}_{timestamp}.png")

    with httpx.Client(timeout=120.0) as client:
        response = client.get(url)
        response.raise_for_status()

    with open(output_path, "wb") as f:
        f.write(response.content)

    file_size_kb = Path(output_path).stat().st_size / 1024
    _logger.info("参考图已保存: %s (%.1fKB)", output_path, file_size_kb)

    return output_path
