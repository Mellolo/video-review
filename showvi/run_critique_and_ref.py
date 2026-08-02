#!/usr/bin/env python3
"""
端到端审核 + 参考图生成编排脚本。

两阶段流程:
  Phase 1: 视频审核 — 使用 DashScope qwen-vl-max 进行 6 维度评分
  Phase 2: 参考图生成 — 基于原视频帧 + 审核反馈，调用 DashScope 图片编辑 API

用法:
    python run_critique_and_ref.py \
        --video ~/Downloads/normal_video.mp4 \
        --scene "场景描述" \
        --output ~/Downloads

    # 指定图片模型
    python run_critique_and_ref.py \
        --video input.mp4 \
        --scene "场景描述" \
        --output ./output \
        --model-image qwen-image-max
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path


def _load_env_file(env_path: str = ".env") -> None:
    """从 .env 文件加载环境变量。"""
    path = Path(env_path)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k and k not in os.environ:
                os.environ[k] = v


def run_phase1_critique(
    video_path: str,
    scene_description: str,
    output_dir: str,
    model: str = None,
    timeout_seconds: int = 180,
) -> dict:
    """Phase 1: 视频审核。

    Args:
        video_path: 视频文件路径
        scene_description: 场景描述
        output_dir: 输出目录
        model: 审核模型名称（默认从 .env 读取）
        timeout_seconds: 超时时间

    Returns:
        审核结果字典
    """
    from tools.critic_animation import critique_animation_video

    resolved_model = model or os.environ.get("LLM_MODEL_VIDEO_CRITIQUE", "qwen-vl-max")
    critique_path = str(Path(output_dir) / "critique_result.json")

    print("=" * 60)
    print("Phase 1: 视频审核")
    print("=" * 60)

    start_time = time.time()
    result = critique_animation_video(
        video_path=video_path,
        scene_description=scene_description,
        model=resolved_model,
        timeout_seconds=timeout_seconds,
        output_path=critique_path,
    )
    elapsed = time.time() - start_time

    score = result["overall_score"]
    recommendation = result["recommendation"]
    print(f"审核完成: {score}/10 ({recommendation})，耗时 {elapsed:.1f}s")
    print(f"审核报告 → {critique_path}")
    print()

    return result


def run_phase2_reference_image(
    critique_data: dict,
    video_path: str,
    scene_description: str,
    output_dir: str,
    model_image: str = "qwen-image-2.0-pro-2026-06-22",
) -> str:
    """Phase 2: 参考图生成（基于原视频帧编辑）。

    Args:
        critique_data: 审核结果
        video_path: 原视频路径
        scene_description: 场景描述
        output_dir: 输出目录
        model_image: 图片编辑模型名称

    Returns:
        生成的参考图路径
    """
    from tools.reference_image_gen import generate_reference_image, _extract_critical_timestamps

    print("=" * 60)
    print("Phase 2: 参考图生成（基于原视频帧编辑）")
    print("=" * 60)

    # 显示关键时间戳
    timestamps = _extract_critical_timestamps(critique_data)
    if timestamps:
        print(f"[REF IMAGE] Critical timestamp: {timestamps[0]}")

    start_time = time.time()
    try:
        ref_path = generate_reference_image(
            critique_data=critique_data,
            video_path=video_path,
            scene_description=scene_description,
            output_dir=output_dir,
            model_image=model_image,
        )
        elapsed = time.time() - start_time
        print(f"[REF IMAGE] Done in {elapsed:.1f}s → {ref_path}")
        return ref_path
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"[REF IMAGE] 失败 ({elapsed:.1f}s): {e}")
        return ""


def main():
    parser = argparse.ArgumentParser(
        description="Showvi 视频审核 + 参考图生成（端到端）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--video", type=str, required=True,
        help="视频文件路径",
    )
    parser.add_argument(
        "--scene", type=str, required=True,
        help="场景描述（审核对照基准）",
    )
    parser.add_argument(
        "--output", type=str, default="./output",
        help="输出目录（默认: ./output）",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="视频审核模型（默认从 .env 读取 LLM_MODEL_VIDEO_CRITIQUE）",
    )
    parser.add_argument(
        "--model-image", type=str,
        default="qwen-image-2.0-pro-2026-06-22",
        help="参考图生成模型（默认: qwen-image-2.0-pro-2026-06-22）",
    )
    parser.add_argument(
        "--timeout", type=int, default=180,
        help="API 超时时间（秒，默认: 180）",
    )
    parser.add_argument(
        "--skip-ref", action="store_true",
        help="跳过参考图生成，仅运行审核",
    )
    parser.add_argument(
        "--env", type=str, default=".env",
        help=".env 文件路径（默认: .env）",
    )
    args = parser.parse_args()

    # ── 加载 .env ──
    _load_env_file(args.env)

    # ── 验证视频文件 ──
    video_path = str(Path(args.video).expanduser().resolve())
    if not Path(video_path).exists():
        print(f"错误: 视频文件不存在: {video_path}")
        sys.exit(1)

    # ── 确保输出目录存在 ──
    output_dir = str(Path(args.output).expanduser().resolve())
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    print(f"视频: {video_path}")
    print(f"场景: {args.scene}")
    print(f"输出: {output_dir}")
    print(f"审核模型: {args.model or os.environ.get('LLM_MODEL_VIDEO_CRITIQUE', 'qwen-vl-max')}")
    print(f"图片模型: {args.model_image}")
    print()

    # ── Phase 1: 视频审核 ──
    try:
        critique_data = run_phase1_critique(
            video_path=video_path,
            scene_description=args.scene,
            output_dir=output_dir,
            model=args.model,
            timeout_seconds=args.timeout,
        )
    except Exception as e:
        print(f"\n❌ 视频审核失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # ── Phase 2: 参考图生成 ──
    ref_path = ""
    if not args.skip_ref:
        try:
            ref_path = run_phase2_reference_image(
                critique_data=critique_data,
                video_path=video_path,
                scene_description=args.scene,
                output_dir=output_dir,
                model_image=args.model_image,
            )
        except Exception as e:
            print(f"\n⚠ 参考图生成失败: {e}")
            import traceback
            traceback.print_exc()

    # ── 汇总 ──
    print()
    print("=" * 60)
    print("流程完成")
    print("=" * 60)
    print(f"审核评分: {critique_data['overall_score']}/10 ({critique_data['recommendation']})")
    print(f"审核报告: {Path(output_dir) / 'critique_result.json'}")
    if ref_path:
        print(f"参考图: {ref_path}")
    else:
        print("参考图: 未生成（跳过或失败）")


if __name__ == "__main__":
    main()
