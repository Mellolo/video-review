"""
汇总报告工具 — 根据审核结果和参考图生成结构化 Markdown 报告。

独立工具，可单独调用，也可作为编排流程的第三阶段。

用法:
    # 独立调用
    python tools/report_generator.py --session 审核_20260802_190000/

    # 在编排脚本中调用
    from tools.report_generator import generate_report
    generate_report(session_dir="审核_20260802_190000/", video_path="...", scene_description="...")
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ── 维度中文映射 ──────────────────────────────────────────────────────

DIMENSION_MAP: Dict[str, str] = {
    "motion_fluidity": "动作流畅度",
    "character_consistency": "角色一致性",
    "scene_accuracy": "场景还原度",
    "visual_quality": "画面质量",
    "pacing_timing": "节奏与时间控制",
    "artistic_expression": "艺术表现力",
    "model_clipping": "模型穿模",
}

# 评分维度在报告中的展示顺序
DIMENSION_ORDER: List[str] = [
    "motion_fluidity",
    "character_consistency",
    "scene_accuracy",
    "visual_quality",
    "pacing_timing",
    "artistic_expression",
    "model_clipping",
]

# 推荐决策的中文标签
RECOMMENDATION_LABEL: Dict[str, str] = {
    "ACCEPT": "通过 ✅",
    "RETRY": "需重试 ⚠️",
    "REJECT": "不通过 ❌",
}


def generate_report(
    session_dir: str,
    video_path: Optional[str] = None,
    scene_description: Optional[str] = None,
) -> str:
    """根据 session 目录中的审核产物生成结构化 Markdown 报告。

    读取 session 目录下的:
      - 审核结果.json  (必须)
      - 场景描述.txt (可选，如未传入 scene_description)
      - 原帧/               (可选)
      - 参考图/           (可选)

    生成 审核报告.md 保存到 session 目录下。

    Args:
        session_dir: session 目录路径
        video_path: 原视频路径（用于报告头部信息，可选）
        scene_description: 场景描述（可选，如未传入则从文件读取）

    Returns:
        生成的报告文件路径
    """
    session = Path(session_dir)

    # 读取审核结果
    critique_path = session / "审核结果.json"
    if not critique_path.exists():
        raise FileNotFoundError(f"审核结果不存在: {critique_path}")
    with open(critique_path, "r", encoding="utf-8") as f:
        critique_data = json.load(f)

    # 读取场景描述
    if not scene_description:
        scene_file = session / "场景描述.txt"
        if scene_file.exists():
            scene_description = scene_file.read_text(encoding="utf-8").strip()
        else:
            scene_description = "（未提供）"

    # 查找帧和参考图
    frames_dir = session / "原帧"
    refs_dir = session / "参考图"

    # 构建 timestamp → 帧路径 / 参考图路径 的映射
    frame_map = _build_image_map(frames_dir, "原帧_")
    ref_map = _build_image_map(refs_dir, "参考图_")

    # 生成报告
    report = _build_report(
        critique_data=critique_data,
        scene_description=scene_description,
        video_path=video_path,
        frame_map=frame_map,
        ref_map=ref_map,
    )

    # 保存报告
    report_path = session / "审核报告.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"[REPORT] 报告已生成: {report_path}")

    return str(report_path)


def _build_report(
    critique_data: Dict[str, Any],
    scene_description: str,
    video_path: Optional[str],
    frame_map: Dict[str, str],
    ref_map: Dict[str, str],
) -> str:
    """构建 Markdown 报告内容。"""
    lines: List[str] = []

    # ── 头部 ──
    overall = critique_data.get("overall_score", 0)
    rec = critique_data.get("recommendation", "UNKNOWN")
    rec_label = RECOMMENDATION_LABEL.get(rec, rec)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines.append(f"# 视频审核报告")
    lines.append("")
    lines.append(f"> **总分 {overall}/10** &nbsp;&nbsp; **结论: {rec_label}** &nbsp;&nbsp; 生成时间: {now}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── 基本信息 ──
    lines.append("## 基本信息")
    lines.append("")
    lines.append("| 项目 | 内容 |")
    lines.append("|------|------|")
    video_name = Path(video_path).name if video_path else "（未指定）"
    lines.append(f"| 视频文件 | {video_name} |")
    lines.append(f"| 审核模型 | qwen-vl-max |")
    lines.append(f"| 图片模型 | {os.environ.get('LLM_MODEL_IMAGE', 'qwen-image-2.0-pro-2026-06-22')} |")
    lines.append(f"| 审核时间 | {now} |")
    lines.append("")

    # ── 场景描述 ──
    lines.append("## 场景描述")
    lines.append("")
    lines.append(f"```\n{scene_description}\n```")
    lines.append("")

    # ── 评分表 ──
    lines.append("## 审核评分")
    lines.append("")
    lines.append("| 维度 | 评分 | 等级 |")
    lines.append("|------|:----:|:----:|")

    for dim_key in DIMENSION_ORDER:
        score = critique_data.get(dim_key)
        if score is None:
            continue
        dim_name = DIMENSION_MAP.get(dim_key, dim_key)
        grade = _score_to_grade(score)
        lines.append(f"| {dim_name} | {score} | {grade} |")

    lines.append(f"| **总分** | **{overall}** | **{_score_to_grade(overall)}** |")
    lines.append(f"| **结论** | — | **{rec_label}** |")
    lines.append("")

    # ── 关键问题 ──
    critical_issues = critique_data.get("critical_issues", [])
    if critical_issues:
        lines.append("## 关键问题")
        lines.append("")

        for i, issue in enumerate(critical_issues, 1):
            if isinstance(issue, dict):
                ts = issue.get("timestamp", "")
                dim = issue.get("dimension", "")
                desc = issue.get("description", "")
            elif isinstance(issue, str):
                ts, dim, desc = "", "", issue
            else:
                continue

            dim_name = DIMENSION_MAP.get(dim, dim)
            lines.append(f"### 问题 {i} — [{ts}] {dim_name}")
            lines.append("")
            lines.append(f"**描述:** {desc}")
            lines.append("")

            # 对比图
            ts_key = _normalize_ts(ts)
            frame_rel = frame_map.get(ts_key)
            ref_rel = ref_map.get(ts_key)

            if frame_rel or ref_rel:
                lines.append("<table>")
                lines.append("<tr>")
                if frame_rel:
                    lines.append(f'<td align="center"><b>原帧</b><br><img src="{frame_rel}" width="400"></td>')
                if ref_rel:
                    lines.append(f'<td align="center"><b>参考图</b><br><img src="{ref_rel}" width="400"></td>')
                lines.append("</tr>")
                lines.append("</table>")
                lines.append("")

        lines.append("")

    # ── 次要问题 ──
    minor_issues = critique_data.get("minor_issues", [])
    if minor_issues:
        lines.append("## 次要问题")
        lines.append("")
        lines.append("| 时间戳 | 维度 | 描述 |")
        lines.append("|--------|------|------|")
        for issue in minor_issues:
            if isinstance(issue, dict):
                ts = issue.get("timestamp", "")
                dim = issue.get("dimension", "")
                desc = issue.get("description", "")
                dim_name = DIMENSION_MAP.get(dim, dim)
                lines.append(f"| {ts} | {dim_name} | {desc} |")
        lines.append("")

    # ── 优点 ──
    strengths = critique_data.get("strengths", [])
    if strengths:
        lines.append("## 优点")
        lines.append("")
        for s in strengths:
            lines.append(f"- {s}")
        lines.append("")

    # ── 改进建议 ──
    suggestions = critique_data.get("improvement_suggestions", "")
    if suggestions:
        lines.append("## 改进建议")
        lines.append("")
        lines.append(suggestions)
        lines.append("")

    # ── 审核反馈 ──
    feedback = critique_data.get("feedback", "")
    if feedback:
        lines.append("## 审核反馈")
        lines.append("")
        lines.append(f"> {feedback}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*本报告由视频审核系统自动生成*")

    return "\n".join(lines)


def _score_to_grade(score: float) -> str:
    """将分数转为等级标签。"""
    if score >= 9:
        return "优秀 ⭐"
    elif score >= 7:
        return "良好 ✓"
    elif score >= 5:
        return "一般"
    elif score >= 3:
        return "较差"
    else:
        return "不可用"


def _build_image_map(directory: Path, prefix: str) -> Dict[str, str]:
    """扫描目录中的图片文件，构建 timestamp_key → relative_path 的映射。

    文件名格式: frame_00s12.png / reference_00s12.png
    提取时间戳 key: 00:12
    """
    result: Dict[str, str] = {}
    if not directory.exists():
        return result

    for f in directory.iterdir():
        if not f.is_file():
            continue
        if not f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            continue

        # 从文件名提取时间戳
        # 格式: frame_12s.png, frame_00s12.png, reference_00s12_1785666509.png
        name = f.stem
        # 提取数字部分
        match = re.search(r"(\d+)s(\d+)", name)
        if match:
            minutes, seconds = match.group(1), match.group(2)
            ts_key = f"{int(minutes):02d}:{int(seconds):02d}"
        else:
            # 尝试纯数字: frame_12.png
            match2 = re.search(r"(\d+)", name)
            if match2:
                total_sec = int(match2.group(1))
                ts_key = f"{total_sec // 60:02d}:{total_sec % 60:02d}"
            else:
                continue

        # 计算相对路径（相对于 session 目录）
        rel_path = f"{directory.name}/{f.name}"
        result[ts_key] = rel_path

    return result


def _normalize_ts(ts: str) -> str:
    """将时间戳归一化为 "MM:SS" 格式的 key。"""
    ts = ts.strip()
    # "00:12" → "00:12"
    if ":" in ts:
        parts = ts.split(":")
        if len(parts) == 2:
            return f"{int(parts[0]):02d}:{int(float(parts[1])):02d}"
        elif len(parts) == 3:
            total = int(parts[0]) * 60 + int(parts[1])
            return f"{total // 60:02d}:{total % 60:02d}"
    # "12" → "00:12"
    try:
        total = int(float(ts))
        return f"{total // 60:02d}:{total % 60:02d}"
    except ValueError:
        return ts


# ── CLI 入口 ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="汇总报告工具 — 根据审核结果和参考图生成结构化 Markdown 报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 独立调用（session 目录需包含 critique_result.json）
    python tools/report_generator.py --session 审核_20260802_190000/

    # 指定视频路径和场景描述
    python tools/report_generator.py --session 审核_20260802_190000/ \\
        --video path/to/video.mp4 \\
        --scene "场景描述文本"
        """,
    )
    parser.add_argument(
        "--session", type=str, required=True,
        help="session 目录路径（需包含 critique_result.json）",
    )
    parser.add_argument(
        "--video", type=str, default=None,
        help="原视频路径（用于报告头部信息，可选）",
    )
    parser.add_argument(
        "--scene", type=str, default=None,
        help="场景描述文本（可选，默认从 session 目录的 scene_description.txt 读取）",
    )
    args = parser.parse_args()

    session_dir = str(Path(args.session).expanduser().resolve())
    if not Path(session_dir).exists():
        print(f"错误: session 目录不存在: {session_dir}")
        sys.exit(1)

    try:
        report_path = generate_report(
            session_dir=session_dir,
            video_path=args.video,
            scene_description=args.scene,
        )
        print(f"\n✅ 报告已生成: {report_path}")
    except Exception as e:
        print(f"\n❌ 报告生成失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
