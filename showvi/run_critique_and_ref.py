#!/usr/bin/env python3
"""
端到端编排脚本 — 串联三个独立工具完成视频审核全流程。

三个工具:
  Phase 1: 视频审核      (tools/critic_animation.py)   → critique_result.json
  Phase 2: 参考图生成     (tools/reference_image_gen.py) → frames/ + references/
  Phase 3: 汇总报告       (tools/report_generator.py)    → report.md

每个工具也可独立调用:
  python tools/critic_animation.py --video input.mp4 --scene "..." --output result.json
  python tools/reference_image_gen.py --critique result.json --video input.mp4 --scene "..." --output session/
  python tools/report_generator.py --session session/

用法:
    # 完整流程
    python run_critique_and_ref.py --video input.mp4 --scene "场景描述" --output 视频目录/

    # 仅审核（跳过参考图和报告）
    python run_critique_and_ref.py --video input.mp4 --scene "..." --skip-ref --skip-report

    # 从已有审核结果继续（跳过审核，只生成参考图和报告）
    python run_critique_and_ref.py --video input.mp4 --scene "..." \\
        --session session_20260802_190000/ --skip-critique
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
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


def _create_session_dir(video_dir: str) -> str:
    """在视频所在目录下创建 session 目录。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_name = f"session_{timestamp}"
    session_dir = str(Path(video_dir) / session_name)
    Path(session_dir).mkdir(parents=True, exist_ok=True)
    return session_dir


def run_phase1_critique(
    video_path: str,
    scene_description: str,
    session_dir: str,
    model: str = None,
    timeout_seconds: int = 300,
) -> dict:
    """Phase 1: 视频审核。"""
    from tools.critic_animation import critique_animation_video

    resolved_model = model or os.environ.get("LLM_MODEL_VIDEO_CRITIQUE", "qwen-vl-max")
    critique_path = str(Path(session_dir) / "critique_result.json")

    print("=" * 60)
    print("Phase 1: 视频审核")
    print("=" * 60)
    print(f"模型: {resolved_model}")

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
    session_dir: str,
    model_image: str = None,
) -> list:
    """Phase 2: 参考图生成。"""
    from tools.reference_image_gen import generate_all_reference_images, _extract_critical_timestamps

    print("=" * 60)
    print("Phase 2: 参考图生成")
    print("=" * 60)

    timestamps = _extract_critical_timestamps(critique_data)
    if timestamps:
        print(f"关键时间戳: {', '.join(timestamps)}")

    start_time = time.time()
    try:
        ref_paths = generate_all_reference_images(
            critique_data=critique_data,
            video_path=video_path,
            scene_description=scene_description,
            output_dir=session_dir,
            model_image=model_image,
        )
        elapsed = time.time() - start_time
        print(f"\n参考图生成完成: {len(ref_paths)} 张，耗时 {elapsed:.1f}s")
        for p in ref_paths:
            print(f"  → {p}")
        return ref_paths
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n参考图生成失败 ({elapsed:.1f}s): {e}")
        return []


def run_phase3_report(
    session_dir: str,
    video_path: str,
    scene_description: str,
) -> str:
    """Phase 3: 汇总报告。"""
    from tools.report_generator import generate_report

    print("=" * 60)
    print("Phase 3: 汇总报告")
    print("=" * 60)

    try:
        report_path = generate_report(
            session_dir=session_dir,
            video_path=video_path,
            scene_description=scene_description,
        )
        print(f"报告已生成 → {report_path}")
        return report_path
    except Exception as e:
        print(f"报告生成失败: {e}")
        return ""


def main():
    parser = argparse.ArgumentParser(
        description="视频审核 + 参考图生成 + 汇总报告（端到端编排）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 完整流程
    python run_critique_and_ref.py --video input.mp4 --scene "场景描述" --output 视频目录/

    # 仅审核
    python run_critique_and_ref.py --video input.mp4 --scene "..." --skip-ref --skip-report

    # 从已有 session 继续（跳过审核）
    python run_critique_and_ref.py --video input.mp4 --scene "..." \\
        --session session_20260802_190000/ --skip-critique
        """,
    )
    parser.add_argument("--video", type=str, required=True, help="视频文件路径")
    parser.add_argument("--scene", type=str, required=True, help="场景描述文本")
    parser.add_argument("--output", type=str, default=None, help="视频所在目录（自动创建 session 子目录）")
    parser.add_argument("--session", type=str, default=None, help="指定已有 session 目录（用于续跑）")
    parser.add_argument("--model", type=str, default=None, help="视频审核模型")
    parser.add_argument("--model-image", type=str, default=None, help="参考图生成模型")
    parser.add_argument("--timeout", type=int, default=300, help="审核 API 超时时间（秒）")
    parser.add_argument("--skip-critique", action="store_true", help="跳过 Phase 1 审核")
    parser.add_argument("--skip-ref", action="store_true", help="跳过 Phase 2 参考图生成")
    parser.add_argument("--skip-report", action="store_true", help="跳过 Phase 3 汇总报告")
    parser.add_argument("--env", type=str, default=".env", help=".env 文件路径")
    args = parser.parse_args()

    # ── 加载 .env ──
    _load_env_file(args.env)

    # 图片模型
    resolved_model_image = (
        args.model_image
        or os.environ.get("LLM_MODEL_IMAGE")
        or os.environ.get("IMAGE_MODEL")
        or "qwen-image-2.0-pro-2026-06-22"
    )

    # ── 验证视频文件 ──
    video_path = str(Path(args.video).expanduser().resolve())
    if not Path(video_path).exists():
        print(f"错误: 视频文件不存在: {video_path}")
        sys.exit(1)

    # ── 确定 session 目录 ──
    if args.session:
        session_dir = str(Path(args.session).expanduser().resolve())
        if not Path(session_dir).exists():
            print(f"错误: session 目录不存在: {session_dir}")
            sys.exit(1)
    else:
        video_dir = args.output or str(Path(video_path).parent)
        session_dir = _create_session_dir(video_dir)

    # 保存场景描述到 session 目录
    scene_file = Path(session_dir) / "scene_description.txt"
    scene_file.write_text(args.scene, encoding="utf-8")

    print(f"视频: {video_path}")
    print(f"Session: {session_dir}")
    print(f"场景: {args.scene[:80]}...")
    print(f"审核模型: {args.model or os.environ.get('LLM_MODEL_VIDEO_CRITIQUE', 'qwen-vl-max')}")
    print(f"图片模型: {resolved_model_image}")
    print(f"Phases: {'1' if not args.skip_critique else '–'} / {'2' if not args.skip_ref else '–'} / {'3' if not args.skip_report else '–'}")
    print()

    # ── Phase 1: 视频审核 ──
    critique_data = None
    if not args.skip_critique:
        try:
            critique_data = run_phase1_critique(
                video_path=video_path,
                scene_description=args.scene,
                session_dir=session_dir,
                model=args.model,
                timeout_seconds=args.timeout,
            )
        except Exception as e:
            print(f"\n❌ 视频审核失败: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        # 从 session 目录读取已有审核结果
        critique_path = Path(session_dir) / "critique_result.json"
        if not critique_path.exists():
            print(f"错误: --skip-critique 但 session 目录中无 critique_result.json")
            sys.exit(1)
        with open(critique_path, "r", encoding="utf-8") as f:
            critique_data = json.load(f)
        print(f"已加载审核结果: {critique_path}")
        print(f"评分: {critique_data.get('overall_score')}/10 ({critique_data.get('recommendation')})")
        print()

    # ── Phase 2: 参考图生成 ──
    if not args.skip_ref and critique_data:
        run_phase2_reference_image(
            critique_data=critique_data,
            video_path=video_path,
            scene_description=args.scene,
            session_dir=session_dir,
            model_image=resolved_model_image,
        )

    # ── Phase 3: 汇总报告 ──
    if not args.skip_report and critique_data:
        run_phase3_report(
            session_dir=session_dir,
            video_path=video_path,
            scene_description=args.scene,
        )

    # ── 汇总 ──
    print()
    print("=" * 60)
    print("流程完成")
    print("=" * 60)
    print(f"Session: {session_dir}")
    print(f"目录结构:")
    for item in sorted(Path(session_dir).rglob("*")):
        if item.is_file():
            rel = item.relative_to(session_dir)
            size = item.stat().st_size
            if size > 1024 * 1024:
                size_str = f"{size / (1024 * 1024):.1f}MB"
            elif size > 1024:
                size_str = f"{size / 1024:.0f}KB"
            else:
                size_str = f"{size}B"
            print(f"  {rel}  ({size_str})")


if __name__ == "__main__":
    main()
