#!/usr/bin/env python3
"""
端到端编排脚本 — 串联四个阶段完成视频审核全流程。

四个阶段:
  Phase 0: 场景描述生成   (自动生成)                    → 场景描述.txt
  Phase 1: 视频审核      (tools/critic_animation.py)   → 审核结果.json
  Phase 2: 参考图生成     (tools/reference_image_gen.py) → 原帧/ + 参考图/
  Phase 3: 汇总报告       (tools/report_generator.py)    → 审核报告.md

每个工具也可独立调用:
  python tools/critic_animation.py --video input.mp4 --scene "..." --output result.json
  python tools/reference_image_gen.py --critique result.json --video input.mp4 --scene "..." --output session/
  python tools/report_generator.py --session session/

用法:
    # 完整流程（带场景描述）
    python run_critique_and_ref.py --video input.mp4 --scene "场景描述" --output 视频目录/

    # 完整流程（不提供场景描述，自动生成）
    python run_critique_and_ref.py --video input.mp4 --output 视频目录/

    # 仅审核（跳过参考图和报告）
    python run_critique_and_ref.py --video input.mp4 --scene "..." --skip-ref --skip-report

    # 从已有审核结果继续（跳过审核，只生成参考图和报告）
    python run_critique_and_ref.py --video input.mp4 --scene "..." \\
        --session 审核_20260802_190000/ --skip-critique
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
    session_name = f"审核_{timestamp}"
    session_dir = str(Path(video_dir) / session_name)
    Path(session_dir).mkdir(parents=True, exist_ok=True)
    return session_dir


def run_phase1_critique(
    video_path: str,
    scene_description: str,
    session_dir: str,
    model: str = None,
    timeout_seconds: int = 300,
    strictness: int = 3,
) -> dict:
    """Phase 1: 视频审核。"""
    from tools.critic_animation import critique_animation_video
    from prompts.critic_animation import STRICTNESS_LEVELS

    resolved_model = model or os.environ.get("LLM_MODEL_VIDEO_CRITIQUE", "qwen-vl-max")
    critique_path = str(Path(session_dir) / "审核结果.json")
    strictness_name = STRICTNESS_LEVELS.get(strictness, {}).get("name", "未知")

    print("=" * 60)
    print("Phase 1: 视频审核")
    print("=" * 60)
    print(f"模型: {resolved_model}")
    print(f"严格度: {strictness} ({strictness_name})")

    start_time = time.time()
    result = critique_animation_video(
        video_path=video_path,
        scene_description=scene_description,
        model=resolved_model,
        timeout_seconds=timeout_seconds,
        output_path=critique_path,
        strictness=strictness,
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


def run_phase0_scene_description(
    video_path: str,
    session_dir: str,
    model: str = None,
    timeout_seconds: int = 300,
) -> str:
    """Phase 0: 让模型看视频，自动生成场景描述。

    当用户未提供 --scene 时，先调用视频理解模型分析视频内容，
    生成包含角色、场景、动作、道具等关键要素的结构化场景描述，
    作为后续审核的对照基准。

    Args:
        video_path: 视频文件路径
        session_dir: session 目录
        model: 模型名称（默认同审核模型）
        timeout_seconds: API 超时时间（秒）

    Returns:
        生成的场景描述文本
    """
    from clients import get_llm_client

    resolved_model = model or os.environ.get("LLM_MODEL_VIDEO_CRITIQUE", "qwen-vl-max")

    print("=" * 60)
    print("Phase 0: 场景描述生成")
    print("=" * 60)
    print(f"模型: {resolved_model}")

    client = get_llm_client(step="video_critique")

    system_prompt = (
        "你是一位专业的动画/视频内容分析师。"
        "请仔细观察整个视频，提取并整理以下信息，输出一份详细的场景描述，"
        "用作后续质量审核的对照基准。\n\n"
        "请包含以下要素（按实际情况输出，不要脑补）：\n"
        "1. **整体形式**：视频类型（动画/3D预演/实拍）、画面布局（如分屏对照）、场景编号等\n"
        "2. **角色**：每个角色的外观、服装、发型、标志性特征\n"
        "3. **场景环境**：地点、时间、天气、氛围、关键环境元素\n"
        "4. **动作与镜头**：主要动作序列、镜头运动方式、关键剧情节点\n"
        "5. **关键道具**：场景中出现的道具及其用途\n"
        "6. **风格与情绪**：整体视觉风格、色调、叙事氛围\n\n"
        "请直接输出场景描述文本，不要输出 JSON，不要加多余标记。"
        "保持客观描述，不要加入质量评价。"
    )

    user_message = (
        "请仔细观察以下视频的全部内容，生成一份详细的场景描述，"
        "包含角色、场景、动作、道具、镜头语言、视觉风格等关键信息。"
    )

    start_time = time.time()
    scene_description = client.generate_with_video(
        text_prompt=user_message,
        video_paths=[str(video_path)],
        system_instruction=system_prompt,
        temperature=0.3,
        model=resolved_model,
        timeout_seconds=timeout_seconds,
        max_retries=3,
    )
    elapsed = time.time() - start_time

    # 清理输出
    scene_description = scene_description.strip()

    # 保存到 session 目录
    scene_file = Path(session_dir) / "场景描述.txt"
    scene_file.write_text(scene_description, encoding="utf-8")

    print(f"场景描述生成完成，耗时 {elapsed:.1f}s")
    print(f"场景描述 → {scene_file}")
    preview = scene_description[:120].replace("\n", " ")
    print(f"预览: {preview}...")
    print()

    return scene_description


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
        --session 审核_20260802_190000/ --skip-critique
        """,
    )
    parser.add_argument("--video", type=str, required=True, help="视频文件路径")
    parser.add_argument("--scene", type=str, required=False, default="", help="场景描述文本（可选；留空时模型自主看视频判断）")
    parser.add_argument("--output", type=str, default=None, help="视频所在目录（自动创建 session 子目录）")
    parser.add_argument("--session", type=str, default=None, help="指定已有 session 目录（用于续跑）")
    parser.add_argument("--model", type=str, default=None, help="视频审核模型")
    parser.add_argument("--model-image", type=str, default=None, help="参考图生成模型")
    parser.add_argument("--timeout", type=int, default=300, help="审核 API 超时时间（秒）")
    parser.add_argument("--strictness", type=int, default=3, choices=[1, 2, 3, 4],
                        help="严格度等级: 1=宽松, 2=普通, 3=严格(默认), 4=极严")
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

    # 确定场景描述
    scene_description = args.scene
    if scene_description and scene_description.strip():
        # 用户提供了场景描述，直接使用
        scene_file = Path(session_dir) / "场景描述.txt"
        scene_file.write_text(scene_description, encoding="utf-8")
    elif not args.skip_critique:
        # 用户未提供场景描述，自动通过 Phase 0 生成
        try:
            scene_description = run_phase0_scene_description(
                video_path=video_path,
                session_dir=session_dir,
                model=args.model,
                timeout_seconds=args.timeout,
            )
        except Exception as e:
            print(f"\n⚠️  场景描述自动生成失败: {e}")
            print("将以空场景描述继续后续流程。")
            scene_description = ""
            scene_file = Path(session_dir) / "场景描述.txt"
            scene_file.write_text(scene_description, encoding="utf-8")
    else:
        # skip-critique 且无场景描述，尝试从 session 读取
        scene_file = Path(session_dir) / "场景描述.txt"
        if scene_file.exists():
            scene_description = scene_file.read_text(encoding="utf-8").strip()
        else:
            scene_description = ""
            scene_file.write_text(scene_description, encoding="utf-8")

    scene_preview = scene_description[:80] + "..." if scene_description else "（空）"
    print(f"视频: {video_path}")
    print(f"Session: {session_dir}")
    print(f"场景: {scene_preview}")
    from prompts.critic_animation import STRICTNESS_LEVELS
    print(f"审核模型: {args.model or os.environ.get('LLM_MODEL_VIDEO_CRITIQUE', 'qwen-vl-max')}")
    print(f"严格度: {args.strictness} ({STRICTNESS_LEVELS[args.strictness]['name']})")
    print(f"图片模型: {resolved_model_image}")
    skip_p0 = "✓" if (scene_description and scene_description.strip()) or args.skip_critique else "0"
    print(f"Phases: {'✓' if not args.skip_critique else '–'} / {'✓' if not args.skip_ref else '–'} / {'✓' if not args.skip_report else '–'}")
    print()

    # ── Phase 1: 视频审核 ──
    critique_data = None
    if not args.skip_critique:
        try:
            critique_data = run_phase1_critique(
                video_path=video_path,
                scene_description=scene_description,
                session_dir=session_dir,
                model=args.model,
                timeout_seconds=args.timeout,
                strictness=args.strictness,
            )
        except Exception as e:
            print(f"\n❌ 视频审核失败: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        # 从 session 目录读取已有审核结果
        critique_path = Path(session_dir) / "审核结果.json"
        if not critique_path.exists():
            print(f"错误: --skip-critique 但 session 目录中无 审核结果.json")
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
            scene_description=scene_description,
            session_dir=session_dir,
            model_image=resolved_model_image,
        )

    # ── Phase 3: 汇总报告 ──
    if not args.skip_report and critique_data:
        run_phase3_report(
            session_dir=session_dir,
            video_path=video_path,
            scene_description=scene_description,
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
