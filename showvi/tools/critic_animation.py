"""
动画审核工具 — 使用 DashScope qwen-vl-max 进行 7 维度视频质量评估。

输出结构化 JSON 报告，包含评分、问题时间戳和改进建议。
可作为 Showvi agent 工具使用，也可独立调用。
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from clients import get_llm_client
from prompts.critic_animation import build_animation_critique_prompt

_logger = logging.getLogger("video_agent.critic_animation")


# ── 审核结果 JSON Schema（用于验证和结构化输出）──

CRITIQUE_ANIMATION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "overall_score": {"type": "number", "minimum": 0, "maximum": 10},
        "motion_fluidity": {"type": "number", "minimum": 0, "maximum": 10},
        "character_consistency": {"type": "number", "minimum": 0, "maximum": 10},
        "scene_accuracy": {"type": "number", "minimum": 0, "maximum": 10},
        "visual_quality": {"type": "number", "minimum": 0, "maximum": 10},
        "pacing_timing": {"type": "number", "minimum": 0, "maximum": 10},
        "artistic_expression": {"type": "number", "minimum": 0, "maximum": 10},
        "model_clipping": {"type": "number", "minimum": 0, "maximum": 10},
        "critical_issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "string"},
                    "dimension": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
        },
        "minor_issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "string"},
                    "dimension": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
        },
        "strengths": {"type": "array", "items": {"type": "string"}},
        "recommendation": {"type": "string", "enum": ["ACCEPT", "REJECT", "RETRY"]},
        "feedback": {"type": "string"},
        "critical_timestamps": {"type": "array", "items": {"type": "string"}},
        "improvement_suggestions": {"type": "string"},
    },
    "required": [
        "overall_score",
        "motion_fluidity",
        "character_consistency",
        "scene_accuracy",
        "visual_quality",
        "pacing_timing",
        "artistic_expression",
        "model_clipping",
        "critical_issues",
        "recommendation",
        "feedback",
    ],
}


def critique_animation_video(
    video_path: str,
    scene_description: str,
    model: Optional[str] = None,
    timeout_seconds: int = 180,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """对动画视频进行质量审核。

    使用 DashScope qwen-vl-max 模型分析视频内容，按 7 大维度评分，
    标注关键问题时间戳，输出结构化 JSON 报告。

    Args:
        video_path: 视频文件路径
        scene_description: 场景描述文本（审核对照基准）
        model: 模型名称（默认从 .env 读取 LLM_MODEL_VIDEO_CRITIQUE）
        timeout_seconds: API 超时时间（秒）
        output_path: 审核结果 JSON 保存路径（可选）

    Returns:
        审核结果字典，包含评分、问题列表、建议等

    Raises:
        FileNotFoundError: 视频文件不存在
        ValueError: 审核结果格式无效
        Exception: API 调用失败
    """
    video_file = Path(video_path)
    if not video_file.exists():
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    file_size_mb = video_file.stat().st_size / (1024 * 1024)
    _logger.info(
        "动画审核开始: video=%s (%.1fMB), scene=%s...",
        video_path, file_size_mb, scene_description[:50],
    )
    print(f"[CRITIC] 审核视频: {video_path} ({file_size_mb:.1f}MB)")

    # 获取 DashScope 客户端（通过 custom:dashscope plugin）
    client = get_llm_client(step="video_critique")

    # 构建审核 prompt
    system_prompt = build_animation_critique_prompt(scene_description)

    user_message = (
        f"请审核以下动画视频。\n\n"
        f"场景描述:\n{scene_description}\n\n"
        f"请分析整个视频内容，按 7 大维度评分，标注关键问题时间戳。"
        f"请仅输出有效的 JSON 对象，不要包含其他文字。"
    )

    resolved_model = model or "qwen-vl-max"

    # 调用视频理解 API
    raw_text = client.generate_with_video(
        text_prompt=user_message,
        video_paths=[str(video_file)],
        system_instruction=system_prompt,
        temperature=0.3,
        response_format="json_object",
        model=resolved_model,
        timeout_seconds=timeout_seconds,
        max_retries=3,
    )

    _logger.debug("原始审核响应:\n%s", raw_text)

    # 解析 JSON 结果
    critique_data = _parse_critique_result(raw_text)

    # 保存结果（如果指定了输出路径）
    if output_path:
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(critique_data, f, ensure_ascii=False, indent=2)
        _logger.info("审核结果已保存: %s", output_path)

    # 打印摘要
    score = critique_data["overall_score"]
    rec = critique_data["recommendation"]
    print(f"[CRITIC] 审核 完成: {score}/10 ({rec})")

    if critique_data.get("critical_timestamps"):
        print(f"[CRITIC] 关键时间戳: {', '.join(critique_data['critical_timestamps'])}")

    return critique_data


def _parse_critique_result(raw_text: str) -> Dict[str, Any]:
    """解析审核结果 JSON，处理可能的格式问题。

    Args:
        raw_text: API 返回的原始文本

    Returns:
        解析后的审核结果字典

    Raises:
        ValueError: JSON 解析失败或结果格式无效
    """
    text = raw_text.strip()

    # 尝试提取 JSON（可能被 markdown 代码块包裹）
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.rindex("```")
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.rindex("```")
        text = text[start:end].strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        _logger.error("JSON 解析失败: %s\n原始文本:\n%s", e, raw_text[:500])
        raise ValueError(f"审核结果 JSON 解析失败: {e}") from e

    # 验证必填字段
    required_fields = [
        "overall_score", "motion_fluidity", "character_consistency",
        "scene_accuracy", "visual_quality", "pacing_timing",
        "artistic_expression", "model_clipping", "recommendation", "feedback",
    ]
    for field in required_fields:
        if field not in data:
            raise ValueError(f"审核结果缺少必填字段: {field}")

    # 验证评分范围
    score_fields = [
        "overall_score", "motion_fluidity", "character_consistency",
        "scene_accuracy", "visual_quality", "pacing_timing",
        "artistic_expression", "model_clipping",
    ]
    for field in score_fields:
        v = data.get(field, 0)
        if not isinstance(v, (int, float)) or v < 0 or v > 10:
            raise ValueError(f"无效的评分值 {field}={v}（应在 0-10 范围内）")

    # 验证 recommendation
    if data.get("recommendation") not in ("ACCEPT", "REJECT", "RETRY"):
        raise ValueError(f"无效的 recommendation: {data.get('recommendation')}")

    # 确保列表字段存在
    for key in ("critical_issues", "minor_issues", "strengths", "critical_timestamps"):
        if not isinstance(data.get(key), list):
            data[key] = []

    return data


def get_critical_timestamps(critique_data: Dict[str, Any]) -> List[str]:
    """从审核结果中提取关键时间戳列表。

    Args:
        critique_data: 审核结果字典

    Returns:
        时间戳字符串列表（格式: "MM:SS"）
    """
    timestamps: List[str] = []

    # 优先使用显式的时间戳列表
    if critique_data.get("critical_timestamps"):
        timestamps.extend(critique_data["critical_timestamps"])
    else:
        # 从 critical_issues 中提取
        for issue in critique_data.get("critical_issues", []):
            if isinstance(issue, dict) and issue.get("timestamp"):
                timestamps.append(issue["timestamp"])
            elif isinstance(issue, str) and ":" in issue:
                timestamps.append(issue)

    # 去重并保持顺序
    seen = set()
    unique = []
    for ts in timestamps:
        if ts not in seen:
            seen.add(ts)
            unique.append(ts)

    return unique


def timestamp_to_seconds(timestamp: str) -> float:
    """将时间戳字符串转换为秒数。

    支持格式: "MM:SS" 或 "HH:MM:SS"

    Args:
        timestamp: 时间戳字符串

    Returns:
        秒数
    """
    parts = timestamp.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    elif len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    else:
        try:
            return float(timestamp)
        except ValueError:
            return 0.0
