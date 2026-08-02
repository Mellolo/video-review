#!/usr/bin/env python3
"""
统一的分镜生成脚本 — 支持从视频或小说生成剧本 + 分镜。

═══════════════════════════════════════════════════════════════
  小说生成
═══════════════════════════════════════════════════════════════

    # 默认小说文件 (storyboards/小说/chapter1.txt)
    python generate_storyboard.py novel

    # 指定小说文件
    python3 generate_storyboard.py novel --chapter storyboards/小说/book2.txt

    # 完整参数
    python generate_storyboard.py novel \
        --chapter storyboards/小说/book2.txt \
        --style 3d国漫 \
        --style-hint "偏暗色调，夜景为主" \
        --duration 120 \
        --title "第二章"

    # 只生成剧本（不转分镜），方便先审查剧情
    python3 generate_storyboard.py novel --chapter storyboards/小说/book2.txt --screenplay-only

═══════════════════════════════════════════════════════════════
  视频生成
═══════════════════════════════════════════════════════════════

    # 视频复刻（默认）
    python generate_storyboard.py video --video path/to/video.mp4

    # 视频二创
    python3 generate_storyboard.py video --video example_video/example10.mp4 --mode recreate

    # 指定分镜数量和时长
    python generate_storyboard.py video \
        --video path/to/video.mp4 \
        --mode recreate \
        --num-scenes 20 \
        --duration 60

    # 只生成剧本
    python generate_storyboard.py video --video path/to/video.mp4 --screenplay-only

═══════════════════════════════════════════════════════════════
  从文字创意直接生成（交互式确认）
═══════════════════════════════════════════════════════════════

    # 直接输入创意描述 — 会先生成剧情梗概让你确认/修改
    python generate_storyboard.py prompt --idea "修仙少年在雷劫中觉醒剑意，一剑破天"

    # 时长/风格可以写在 idea 里，LLM 会自动提取
    python generate_storyboard.py prompt --idea "做一个30秒的真人短视频，赘婿翻身打脸"

    # 也可以用 CLI 参数显式指定（优先级高于 idea 文本）
    python generate_storyboard.py prompt \
        --idea "都市赘婿翻身打脸的故事" \
        --style 真人 --duration 90

    # 跳过交互确认，直接生成（脚本/自动化场景）
    python generate_storyboard.py prompt --idea "修仙少年觉醒" --yes

    # 从文件读取较长的创意描述
    python generate_storyboard.py prompt --idea-file my_idea.txt --style 3d国漫

═══════════════════════════════════════════════════════════════
  从已有剧本恢复（审查/编辑后继续生成分镜）
═══════════════════════════════════════════════════════════════

    python generate_storyboard.py resume --screenplay storyboards/xxx_screenplay.json
"""

import argparse
import json
import os
import sys

from pathlib import Path

from tools.storyboard_gen.schemas import AUTO_VIDEO_STYLE, DEFAULT_VIDEO_STYLE, normalize_style_choice


AVAILABLE_STYLES = [AUTO_VIDEO_STYLE, "3d国漫", "真人", "2d动漫", "水墨"]


def find_default_chapter() -> str:
    root = Path(__file__).resolve().parent
    novel_dir = root / "storyboards" / "小说"
    if novel_dir.exists():
        for txt in sorted(novel_dir.glob("*.txt")):
            return str(txt)
    return ""


def _resolve_style_image(raw: str) -> str:
    """Validate and return absolute path for the style reference image."""
    if not raw:
        return ""
    p = Path(raw).resolve()
    if not p.exists():
        print(f"Warning: 风格参考图片不存在: {raw}")
        return ""
    return str(p)


def _display_style_choice(style: str) -> str:
    normalized = normalize_style_choice(style)
    return normalized or "自动解析（未命中时回退 3d国漫）"


def make_output_path(output_dir: str, name: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    return os.path.join(output_dir, f"{name}_storyboard.json")


def print_screenplay_summary(data: dict):
    va = data.get("video_analysis", {})
    chars = data.get("characters", [])
    locs = data.get("locations", [])
    narrative = data.get("narrative", "")

    print(f"\n{'═' * 60}")
    print(f"  剧本概要 (Screenplay)")
    print(f"{'═' * 60}")
    print(f"  主题 : {va.get('theme', 'N/A')}")
    print(f"  风格 : {va.get('style', 'N/A')}")
    print(f"  基调 : {va.get('tone', 'N/A')}")
    elements = va.get("key_elements", [])
    if elements:
        print(f"  元素 : {', '.join(elements)}")
    story_arc_fields = [
        ("开场钩子", data.get("hook", "")),
        ("核心冲突", data.get("core_conflict", "")),
        ("失败代价", data.get("stakes", "")),
        ("高潮爆点", data.get("climax", "")),
        ("结尾兑现", data.get("payoff", "")),
        ("情绪曲线", data.get("emotional_curve", "")),
    ]
    for label, value in story_arc_fields:
        if value:
            print(f"  {label} : {value}")
    turning_points = data.get("turning_points", []) or []
    if turning_points:
        print("  关键转折 :")
        for tp in turning_points:
            print(f"    - {tp}")

    print(f"\n  角色 ({len(chars)}):")
    for c in chars:
        print(f"    【{c['name']}】{c.get('description', '')[:60]}...")

    print(f"\n  场景 ({len(locs)}):")
    for loc in locs:
        print(f"    【{loc['name']}】{loc.get('description', '')[:50]}...")

    print(f"\n  故事叙述 ({len(narrative)} 字):")
    preview = narrative[:300]
    if len(narrative) > 300:
        preview += "..."
    print(f"    {preview}")

    print(f"{'═' * 60}")


def print_storyboard_summary(data: dict):
    va = data.get("video_analysis", {})
    chars = data.get("characters", [])
    locs = data.get("locations", [])
    scenes = data.get("storyboard", [])
    meta = data.get("_meta", {})

    print(f"\n{'═' * 60}")
    print(f"  分镜概要 (Storyboard)")
    print(f"{'═' * 60}")
    print(f"  主题 : {va.get('theme', 'N/A')}")
    print(f"  风格 : {va.get('style', 'N/A')}")
    print(f"  基调 : {va.get('tone', 'N/A')}")

    print(f"\n  角色 ({len(chars)}):")
    for c in chars:
        print(f"    【{c['name']}】{c.get('description', '')[:60]}...")

    print(f"\n  场景 ({len(locs)}):")
    for loc in locs:
        print(f"    【{loc['name']}】{loc.get('description', '')[:50]}...")

    print(f"\n  分镜 ({len(scenes)}):")
    for s in scenes:
        sn = s.get("scene_number", "?")
        d_raw = s.get("duration", "2秒")
        plot = s.get("plot_description", "")[:45]
        chars_in = ", ".join(s.get("characters_in_scene", []))
        dialogue = s.get("dialogue", "")
        has_dialogue = "💬" if dialogue.strip() else "  "

        print(f"    #{sn:>3}  [{d_raw:>4s}] {has_dialogue} {plot}...")
        if chars_in:
            print(f"          角色: {chars_in}")

        for dl in s.get("dialogue_lines", [])[:2]:
            speaker = dl.get("speaker", "?")
            lt = dl.get("line_type", "?")
            text = dl.get("text", "")[:35]
            emotion = dl.get("emotion", "")
            emo = f"({emotion})" if emotion else ""
            print(f"          {speaker}{emo}[{lt}]: {text}")
        extra = len(s.get("dialogue_lines", [])) - 2
        if extra > 0:
            print(f"          ... +{extra} more")

    total_dur = meta.get("estimated_duration_seconds", 0)
    print(f"\n  总分镜 : {meta.get('total_scenes', len(scenes))}")
    print(f"  总时长 : ~{total_dur:.1f}s")
    print(f"{'═' * 60}")


# ═══════════════════════════════════════════════════════════════
#  子命令: novel
# ═══════════════════════════════════════════════════════════════

def cmd_novel(args):
    chapter_path = args.chapter or find_default_chapter()
    if not chapter_path or not os.path.exists(chapter_path):
        print("Error: 未找到小说文件，请用 --chapter 指定")
        sys.exit(1)

    with open(chapter_path, "r", encoding="utf-8") as f:
        chapter_text = f.read()

    title = args.title or Path(chapter_path).stem
    output_path = make_output_path(args.output_dir, title)

    print("=" * 60)
    print("  小说 → 剧本 → 分镜")
    print("=" * 60)
    print(f"  来源   : {chapter_path} ({len(chapter_text)} 字)")
    print(f"  标题   : {title}")
    print(f"  风格   : {_display_style_choice(args.style)}")
    if args.style_hint:
        print(f"  风格提示: {args.style_hint}")
    if args.style_image:
        print(f"  风格参考: {args.style_image}")
    print(f"  时长   : {args.duration or 'auto'}")
    print(f"  模型   : {args.model}")
    print(f"  仅剧本 : {'是' if args.screenplay_only else '否'}")
    print(f"  输出   : {output_path}")
    print("=" * 60)

    style_img = _resolve_style_image(args.style_image)

    from tools.storyboard_gen import NovelStoryboardEngine

    engine = NovelStoryboardEngine(llm_model=args.model)

    if args.screenplay_only:
        screenplay = engine.generate_screenplay(
            chapter_text=chapter_text,
            output_path=output_path,
            video_style=args.style,
            style_hint=args.style_hint,
            target_duration=args.duration,
            title=title,
        )
        print_screenplay_summary(screenplay)
        sp_json, sp_txt = engine.screenplay_paths(output_path)
        print(f"\n剧本 JSON: {sp_json}")
        print(f"剧本 TXT:  {sp_txt}")
        print("\n可以审查 TXT 文件，修改 JSON 后用 resume 命令继续生成分镜。")
    else:
        storyboard = engine.generate(
            chapter_text=chapter_text,
            output_path=output_path,
            video_style=args.style,
            style_hint=args.style_hint,
            target_duration=args.duration,
            title=title,
            style_reference_image=style_img,
        )
        print_storyboard_summary(storyboard)
        print(f"\n分镜 JSON: {output_path}")

    print("=" * 60)


# ═══════════════════════════════════════════════════════════════
#  子命令: video
# ═══════════════════════════════════════════════════════════════

def cmd_video(args):
    video_path = args.video
    if not video_path or not os.path.exists(video_path):
        print(f"Error: 视频文件不存在: {video_path}")
        sys.exit(1)

    name = Path(video_path).stem
    output_path = make_output_path(args.output_dir, name)
    mode_label = "二创" if args.mode == "recreate" else "复刻"

    print("=" * 60)
    print("  视频 → 剧本 → 分镜")
    print("=" * 60)
    print(f"  视频   : {video_path}")
    print(f"  模式   : {mode_label} ({args.mode})")
    print(f"  风格   : {_display_style_choice(args.style)}")
    if args.num_scenes:
        print(f"  分镜数 : {args.num_scenes}")
    print(f"  时长   : {args.duration or 'auto'}")
    if args.style_image:
        print(f"  风格参考: {args.style_image}")
    print(f"  模型   : {args.model}")
    print(f"  仅剧本 : {'是' if args.screenplay_only else '否'}")
    print(f"  输出   : {output_path}")
    print("=" * 60)

    style_img = _resolve_style_image(args.style_image)

    from tools.storyboard_gen import VideoStoryboardEngine, StoryboardMode

    mode = StoryboardMode(args.mode)
    engine = VideoStoryboardEngine(llm_model=args.model)

    if args.screenplay_only:
        screenplay = engine.generate_screenplay(
            video_path=video_path,
            output_path=output_path,
            num_scenes=args.num_scenes,
            total_duration=args.duration,
            mode=mode,
            video_style=args.style,
            style_hint=args.style_hint,
        )
        print_screenplay_summary(screenplay)
        sp_json, sp_txt = engine.screenplay_paths(output_path)
        print(f"\n剧本 JSON: {sp_json}")
        print(f"剧本 TXT:  {sp_txt}")
        print("\n可以审查 TXT 文件，修改 JSON 后用 resume 命令继续生成分镜。")
    else:
        storyboard = engine.generate(
            video_path=video_path,
            output_path=output_path,
            num_scenes=args.num_scenes,
            total_duration=args.duration,
            mode=mode,
            style_reference_image=style_img,
            video_style=args.style,
            style_hint=args.style_hint,
        )
        print_storyboard_summary(storyboard)
        print(f"\n分镜 JSON: {output_path}")

    print("=" * 60)


# ═══════════════════════════════════════════════════════════════
#  子命令: prompt (从文字创意直接生成)
# ═══════════════════════════════════════════════════════════════

_OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "synopsis": {
            "type": "string",
            "description": (
                "200-400字的故事梗概。用流畅的叙述描写起承转合，"
                "让读者能快速理解整个故事走向。"
            ),
        },
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string", "description": "一句话角色定位"},
                },
                "required": ["name", "role"],
            },
            "description": "主要角色列表（2-6个）",
        },
        "tone": {
            "type": "string",
            "description": "故事基调，如：热血、悬疑、温馨、搞笑、黑暗",
        },
        "duration_seconds": {
            "type": ["number", "null"],
            "description": (
                "用户期望的视频时长（秒）。仅当用户明确提到时才填写，"
                "如「30秒」→30、「半分钟」→30、「两分钟」→120。"
                "未提及则填 null。"
            ),
        },
        "style": {
            "type": ["string", "null"],
            "description": (
                "画面风格。仅当用户明确提到时才填写，"
                "如「3d国漫」「真人」「2d动漫」「水墨」。未提及则填 null。"
            ),
        },
        "title": {
            "type": ["string", "null"],
            "description": "作品标题。仅当用户明确给出时才填写，未提及则填 null。",
        },
    },
    "required": ["synopsis", "characters", "tone",
                  "duration_seconds", "style", "title"],
}

_OUTLINE_SYSTEM = """你是专业的影视策划。用户会给你一段创意描述，请快速梳理成一个简洁的故事梗概。

要求：
1. synopsis: 200-400字，用流畅的叙述讲清起承转合，让人快速理解故事
2. characters: 提炼2-6个主要角色，每人一句话定位
3. tone: 判断故事基调
4. 同时从用户描述中提取时长/风格/标题等参数（仅用户明确提到的才填，否则 null）

注意：
- 不要过度展开，梗概要精炼
- 角色名避免使用知名 IP 角色名（如斗破苍穹、火影忍者等版权角色）
- 如果用户描述模糊，合理补充使故事完整"""

_REVISE_SYSTEM = """你是专业的影视策划。用户之前给了一个创意描述，你生成了一版故事梗概，
但用户对当前版本不满意，给出了修改意见。请根据修改意见调整故事梗概。

要求：
1. 认真理解用户的修改意见，针对性地调整
2. 保持未被要求修改的部分不变
3. 输出格式与之前一致"""


def _generate_story_outline(idea: str) -> dict | None:
    """Call LLM to produce a story outline from the user's creative brief."""
    from clients import get_llm_client

    try:
        client = get_llm_client(step="screenplay_gen")
        raw = client.generate_text(
            prompt=f"请根据以下创意描述，梳理一个故事梗概：\n\n{idea}",
            system_instruction=_OUTLINE_SYSTEM,
            response_schema=_OUTLINE_SCHEMA,
            temperature=0.7,
            model="gemini-2.0-flash",
        )
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        print(f"[PromptGen] 剧情梳理失败: {e}")
        return None


def _revise_story_outline(idea: str, prev_outline: dict, feedback: str) -> dict | None:
    """Revise an existing outline based on user feedback."""
    from clients import get_llm_client

    prev_text = json.dumps(prev_outline, ensure_ascii=False, indent=2)
    prompt = (
        f"原始创意：\n{idea}\n\n"
        f"当前梗概：\n{prev_text}\n\n"
        f"用户修改意见：\n{feedback}\n\n"
        f"请根据修改意见生成新的故事梗概。"
    )

    try:
        client = get_llm_client(step="screenplay_gen")
        raw = client.generate_text(
            prompt=prompt,
            system_instruction=_REVISE_SYSTEM,
            response_schema=_OUTLINE_SCHEMA,
            temperature=0.7,
            model="gemini-2.0-flash",
        )
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        print(f"[PromptGen] 梗概修订失败: {e}")
        return None


def _print_outline(outline: dict):
    """Pretty-print a story outline for user review."""
    print(f"\n{'═' * 60}")
    print(f"  剧情梗概")
    print(f"{'═' * 60}")

    if outline.get("title"):
        print(f"\n  标题: {outline['title']}")
    if outline.get("tone"):
        print(f"  基调: {outline['tone']}")
    if outline.get("style"):
        print(f"  风格: {outline['style']}")
    if outline.get("duration_seconds"):
        print(f"  时长: {outline['duration_seconds']:.0f}s")

    chars = outline.get("characters", [])
    if chars:
        print(f"\n  角色 ({len(chars)}):")
        for c in chars:
            print(f"    【{c['name']}】{c.get('role', '')}")

    synopsis = outline.get("synopsis", "")
    print(f"\n  故事:")
    for line in synopsis.split("\n"):
        print(f"    {line}")

    print(f"\n{'═' * 60}")


def _confirm_outline_loop(idea: str) -> dict | None:
    """Interactive loop: generate outline → user confirms or gives feedback → revise.

    Returns the approved outline dict, or None if the user cancels.
    """
    print("\n[PromptGen] 正在梳理剧情...")
    outline = _generate_story_outline(idea)
    if not outline:
        return None

    while True:
        _print_outline(outline)
        print("\n  [y] 确认，开始生成    [n] 取消    [其他] 输入修改意见")
        try:
            user_input = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return None

        if not user_input:
            continue

        if user_input.lower() in ("y", "yes", "确认", "ok", "好", "可以", "通过"):
            print("\n[PromptGen] 剧情已确认!")
            return outline

        if user_input.lower() in ("n", "no", "取消", "算了", "quit", "exit"):
            print("\n已取消")
            return None

        print(f"\n[PromptGen] 收到修改意见，正在调整...")
        revised = _revise_story_outline(idea, outline, user_input)
        if revised:
            outline = revised
        else:
            print("[PromptGen] 修订失败，保留当前版本")


def cmd_prompt(args):
    idea = args.idea or ""
    if args.idea_file:
        if not os.path.exists(args.idea_file):
            print(f"Error: 文件不存在: {args.idea_file}")
            sys.exit(1)
        with open(args.idea_file, "r", encoding="utf-8") as f:
            idea = f.read().strip()

    if not idea:
        print("Error: 请用 --idea 或 --idea-file 提供创意描述")
        sys.exit(1)

    # ── Interactive outline confirmation ──────────────────────
    outline = None
    if not args.yes:
        outline = _confirm_outline_loop(idea)
        if outline is None:
            sys.exit(0)

        if args.duration is None and outline.get("duration_seconds"):
            args.duration = outline["duration_seconds"]
        if not normalize_style_choice(args.style) and outline.get("style"):
            args.style = outline["style"]
        if not args.title and outline.get("title"):
            args.title = outline["title"]
    else:
        # --yes mode: still extract params from a quick outline, skip interaction
        outline = _generate_story_outline(idea)
        if outline:
            _print_outline(outline)
            if args.duration is None and outline.get("duration_seconds"):
                args.duration = outline["duration_seconds"]
            if not normalize_style_choice(args.style) and outline.get("style"):
                args.style = outline["style"]
            if not args.title and outline.get("title"):
                args.title = outline["title"]

    # Build enriched prompt: original idea + approved synopsis
    enriched_idea = idea
    if outline and outline.get("synopsis"):
        char_lines = "\n".join(
            f"- {c['name']}: {c.get('role', '')}"
            for c in outline.get("characters", [])
        )
        enriched_idea = (
            f"【用户创意】\n{idea}\n\n"
            f"【确认的故事梗概】\n{outline['synopsis']}\n\n"
            f"【角色设定】\n{char_lines}\n\n"
            f"【基调】{outline.get('tone', '')}"
        )

    title = args.title or "prompt"
    output_path = make_output_path(args.output_dir, title)

    print("\n" + "=" * 60)
    print("  创意描述 → 剧本 → 分镜")
    print("=" * 60)
    print(f"  创意   : {idea[:80]}{'...' if len(idea) > 80 else ''}")
    print(f"  风格   : {_display_style_choice(args.style)}")
    if args.style_hint:
        print(f"  风格提示: {args.style_hint}")
    if args.style_image:
        print(f"  风格参考: {args.style_image}")
    print(f"  时长   : {args.duration or 60}s")
    if args.num_scenes:
        print(f"  场景数 : {args.num_scenes}")
    print(f"  模型   : {args.model}")
    print(f"  仅剧本 : {'是' if args.screenplay_only else '否'}")
    print(f"  输出   : {output_path}")
    print("=" * 60)

    style_img = _resolve_style_image(args.style_image)

    from tools.storyboard_gen import PromptStoryboardEngine

    engine = PromptStoryboardEngine(llm_model=args.model)

    if args.screenplay_only:
        screenplay = engine.generate_screenplay(
            prompt_text=enriched_idea,
            output_path=output_path,
            video_style=args.style,
            style_hint=args.style_hint,
            target_duration=args.duration,
            title=args.title or "",
            num_scenes=args.num_scenes,
        )
        print_screenplay_summary(screenplay)
        sp_json, sp_txt = engine.screenplay_paths(output_path)
        print(f"\n剧本 JSON: {sp_json}")
        print(f"剧本 TXT:  {sp_txt}")
        print("\n可以审查 TXT 文件，修改 JSON 后用 resume 命令继续生成分镜。")
    else:
        storyboard = engine.generate(
            prompt_text=enriched_idea,
            output_path=output_path,
            video_style=args.style,
            style_hint=args.style_hint,
            target_duration=args.duration,
            title=args.title or "",
            num_scenes=args.num_scenes,
            style_reference_image=style_img,
        )
        print_storyboard_summary(storyboard)
        actual_title = storyboard.get("title", "")
        if actual_title:
            actual_path = os.path.join(args.output_dir, f"{actual_title}_storyboard.json")
            if os.path.exists(actual_path):
                output_path = actual_path
        print(f"\n分镜 JSON: {output_path}")

    print("=" * 60)


# ═══════════════════════════════════════════════════════════════
#  子命令: resume (从已有剧本继续生成分镜)
# ═══════════════════════════════════════════════════════════════

def cmd_resume(args):
    sp_path = args.screenplay
    if not os.path.exists(sp_path):
        print(f"Error: 剧本文件不存在: {sp_path}")
        sys.exit(1)

    with open(sp_path, "r", encoding="utf-8") as f:
        screenplay_data = json.load(f)

    source_context = ""
    if args.source:
        if not os.path.exists(args.source):
            print(f"Error: 原始素材文件不存在: {args.source}")
            sys.exit(1)
        with open(args.source, "r", encoding="utf-8") as f:
            source_context = f.read()

    narrative = screenplay_data.get("narrative", "")
    chars = screenplay_data.get("characters", [])
    print("=" * 60)
    print("  剧本 + 原始素材 → 分镜（恢复）")
    print("=" * 60)
    print(f"  剧本文件   : {sp_path}")
    print(f"  叙述字数   : {len(narrative)}")
    print(f"  角色数     : {len(chars)}")
    if source_context:
        print(f"  原始素材   : {args.source} ({len(source_context)} 字)")
    else:
        print(f"  原始素材   : (无，可用 --source 指定)")
    print(f"  模型       : {args.model}")

    output_path = args.output
    if not output_path:
        p = Path(sp_path)
        stem = p.stem.replace("_screenplay", "")
        output_path = str(p.parent / f"{stem}_storyboard.json")
    print(f"  输出       : {output_path}")
    print("=" * 60)

    from tools.storyboard_gen import VideoStoryboardEngine

    engine = VideoStoryboardEngine(llm_model=args.model)
    storyboard = engine.screenplay_to_storyboard(
        screenplay_data=screenplay_data,
        output_path=output_path,
        source_context=source_context,
        source_label="resume_from_screenplay",
    )

    print_storyboard_summary(storyboard)
    print(f"\n分镜 JSON: {output_path}")
    print("=" * 60)


# ═══════════════════════════════════════════════════════════════
#  CLI 入口
# ═══════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="统一分镜生成 — 支持小说/视频来源，支持复刻/二创模式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="生成来源")

    # ── novel ──────────────────────────────────────────────────
    p_novel = sub.add_parser("novel", help="从小说章节生成")
    p_novel.add_argument("--chapter", type=str, default=None,
                         help="小说 .txt 文件路径")
    p_novel.add_argument("--style", type=str, default=AUTO_VIDEO_STYLE,
                         help=f"画面风格（留空=自动解析，未命中则回退 {DEFAULT_VIDEO_STYLE}）: {', '.join(s or '自动解析' for s in AVAILABLE_STYLES)}")
    p_novel.add_argument("--style-hint", type=str, default="",
                         help="额外风格描述")
    p_novel.add_argument("--duration", type=float, default=None,
                         help="目标视频时长(秒)")
    p_novel.add_argument("--title", type=str, default="",
                         help="章节标题")
    p_novel.add_argument("--model", type=str, default="gemini-3-flash-preview")
    p_novel.add_argument("--output-dir", type=str, default="./storyboards")
    p_novel.add_argument("--style-image", type=str, default="",
                         help="风格参考图片路径（生图时参考此风格）")
    p_novel.add_argument("--screenplay-only", action="store_true",
                         help="只生成剧本，不转分镜（方便先审查）")

    # ── video ──────────────────────────────────────────────────
    p_video = sub.add_parser("video", help="从视频生成")
    p_video.add_argument("--video", type=str, required=True,
                         help="视频文件路径")
    p_video.add_argument("--mode", type=str, default="replicate",
                         choices=["replicate", "recreate"],
                         help="replicate=复刻, recreate=二创 (默认: replicate)")
    p_video.add_argument("--style", type=str, default=AUTO_VIDEO_STYLE,
                         help=f"画面风格（留空=自动解析，未命中则回退 {DEFAULT_VIDEO_STYLE}）: {', '.join(s or '自动解析' for s in AVAILABLE_STYLES)}")
    p_video.add_argument("--style-hint", type=str, default="",
                         help="额外风格描述")
    p_video.add_argument("--num-scenes", type=int, default=None,
                         help="目标分镜数量")
    p_video.add_argument("--duration", type=float, default=None,
                         help="目标视频时长(秒)")
    p_video.add_argument("--model", type=str, default="gemini-3-flash-preview")
    p_video.add_argument("--output-dir", type=str, default="./storyboards")
    p_video.add_argument("--style-image", type=str, default="",
                         help="风格参考图片路径（生图时参考此风格）")
    p_video.add_argument("--screenplay-only", action="store_true",
                         help="只生成剧本，不转分镜（方便先审查）")

    # ── prompt ─────────────────────────────────────────────────
    p_prompt = sub.add_parser("prompt", help="从文字创意描述直接生成")
    p_prompt.add_argument("--idea", type=str, default=None,
                          help="创意描述文本（短句即可）")
    p_prompt.add_argument("--idea-file", type=str, default=None,
                          help="从文件读取创意描述（用于较长的描述）")
    p_prompt.add_argument("--style", type=str, default=AUTO_VIDEO_STYLE,
                          help=f"画面风格（留空=自动解析，未命中则回退 {DEFAULT_VIDEO_STYLE}）: {', '.join(s or '自动解析' for s in AVAILABLE_STYLES)}")
    p_prompt.add_argument("--style-hint", type=str, default="",
                          help="额外风格描述")
    p_prompt.add_argument("--duration", type=float, default=None,
                          help="目标视频时长(秒)，默认60")
    p_prompt.add_argument("--num-scenes", type=int, default=None,
                          help="目标场景数量")
    p_prompt.add_argument("--title", type=str, default="",
                          help="作品标题")
    p_prompt.add_argument("--model", type=str, default="gemini-3-flash-preview")
    p_prompt.add_argument("--output-dir", type=str, default="./storyboards")
    p_prompt.add_argument("--style-image", type=str, default="",
                          help="风格参考图片路径（生图时参考此风格）")
    p_prompt.add_argument("--screenplay-only", action="store_true",
                          help="只生成剧本，不转分镜")
    p_prompt.add_argument("--yes", "-y", action="store_true",
                          help="跳过剧情确认，直接生成（非交互模式）")

    # ── resume ─────────────────────────────────────────────────
    p_resume = sub.add_parser("resume", help="从已有剧本继续生成分镜")
    p_resume.add_argument("--screenplay", type=str, required=True,
                          help="剧本 JSON 文件路径 (xxx_screenplay.json)")
    p_resume.add_argument("--source", type=str, default=None,
                          help="原始素材文件路径（小说 .txt / 创意描述），可选")
    p_resume.add_argument("--output", type=str, default=None,
                          help="分镜输出路径 (默认自动推导)")
    p_resume.add_argument("--model", type=str, default="gemini-3-flash-preview")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        print("\n示例:")
        print('  python generate_storyboard.py prompt --idea "修仙少年雷劫中觉醒剑意"')
        print("  python generate_storyboard.py novel --chapter storyboards/小说/book2.txt")
        print("  python generate_storyboard.py video --video my_video.mp4 --mode recreate")
        print("  python generate_storyboard.py resume --screenplay storyboards/xxx_screenplay.json")
        sys.exit(0)

    dispatch = {
        "prompt": cmd_prompt,
        "novel": cmd_novel,
        "video": cmd_video,
        "resume": cmd_resume,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
