"""Prompt builder functions for novel storyboard engine
(extracted from tools/storyboard_gen/novel_engine.py)."""

import json


def build_analyze_chapter_prompt(
    resolved_style: str,
    drama_requirements: str,
    fewshot_examples: str,
    target_duration: float,
) -> str:
    """Build system prompt for _analyze_chapter (Phase 1: Global Analysis)."""
    return (
        "你是专业的影视编剧和视觉导演。分析一篇小说章节，"
        "提取角色、场景信息，并将章节切分为适合分镜制作的叙事段落。\n\n"

        f"【画面风格（非常重要，必须严格遵守）】\n"
        f"{resolved_style}\n"
        f"角色 description 和场景 description 不需要重复标注风格，风格已在顶层 video_analysis.style 定义。\n\n"

        "【角色 characters】\n"
        "1. 只定义在横跨10秒以上不同剧情段落中出现的重要角色，"
        "一闪而过（总计不足10秒）的路人、配角不需要单独定义\n"
        "2. description 只写【固定外观】（年龄、性别、发型、服装、配饰等），"
        "用于生成角色立绘。禁止写情绪（如「眼神充满希望」）、"
        "表情、动作、剧情变化（如「后期变得...」）\n"
        "2b. ❌ 禁止在 description 中描述后期/变身后/受伤后/换装后等任何非初始状态的外观，"
        "所有角色只定义最初登场时的样子\n"
        "3. voice_description 只写【固定音色属性】（音色、语速、说话风格），"
        "用于语音合成。禁止写随剧情变化的情绪（如「起初平静后来愤怒」）\n"
        "4. ⚠️ 同一角色有明显不同外观形态（如变身前后、换装后）"
        "必须作为两个独立角色分别定义\n"
        "5. 不要使用知名影视 IP 角色（哈利波特、漫威英雄等），"
        "神话人物（孙悟空、白素贞等）不受此限\n\n"

        "【场景 locations】\n"
        "1. 只有被至少四个分镜共用的场景才需要定义\n"
        "2. description 只描述环境本身，不描述人物\n"
        "3. ✅ 场景只要初始场景，不要中间态的场景和结尾的场景"
        "（比如，地面被砸出大坑，这个属于后续场景）\n"
        "4. ❌ 不要把动态变化的道具和人物等定义在环境中\n\n"

        "【关键道具 props】\n"
        "1. 只定义在横跨10秒以上不同剧情段落中反复出现的重要道具"
        "（如：角色随身携带的神兵利器、信物、魔法物品等）\n"
        "2. description 只写道具的固定外观特征"
        "（形状、大小、材质、颜色、特效等），不要写画面风格\n"
        "3. 不写使用方式或剧情相关内容\n"
        "4. 只出现一两秒的一次性道具不需要定义\n"
        "5. 已属于场景(locations)描述的固定物体"
        "（如广场中的石碑、大殿里的王座）不要重复定义为道具\n\n"

        "【段落切分 segments】\n"
        "1. 切分原则：场景切换 / 时间跳跃 / 角色组合变化时切分\n"
        "2. 每段建议对应 10-30 秒视频，情节密集可长些\n"
        "3. start_hint 和 end_hint 必须是原文中实际存在的连续文字\n"
        "4. 所有段落的 estimated_video_seconds 之和应约等于目标总时长\n"
        "5. 段落之间不要有遗漏，整篇章节必须被完整覆盖\n"
        "6. 每个段落都必须有明确的段落目标、阻碍、转折和结尾强节拍，不能只按篇章平均切开\n"
        "7. 至少保留一个承担高潮、一个承担重大反转/揭晓、一个承担 payoff/尾钩的关键段落\n\n"

        "【短视频戏剧弧线要求】\n"
        f"{drama_requirements}\n"
        "【节奏参考示例】\n"
        f"{fewshot_examples}\n"
        f"目标总视频时长：约 {target_duration:.0f} 秒"
    )


def build_full_narrative_prompt(
    char_names: list,
    drama_requirements: str,
    fewshot_examples: str,
    story_arc_output_rules: str,
) -> str:
    """Build system prompt for _generate_full_narrative (Phase 2: single-pass)."""
    return (
        "你是专业的影视编剧。根据完整的小说章节原文，将其改写为凝练的**剧本叙述**。\n\n"

        "要求：\n"
        "1. 写成一段连贯的叙述文本，不要拆分成场景编号或结构化格式\n"
        "2. 比原文更精炼，去掉冗余描写，但保留所有核心情节和对话\n"
        "3. 对话用自然方式嵌入文本，标明说话人。例如：\n"
        '   - 角色说的话：陈风怒喝：\u201c你休想！\u201d\n'
        "   - 内心独白：陈风心想（这是最后的机会了）\n"
        '   - 路人/群众：周围人议论纷纷：\u201c这小子疯了吧？\u201d\n'
        "4. 保留情绪变化和氛围描写\n"
        "5. 忠实原文情节，不要添加原文没有的内容\n"
        "6. 叙述必须完整覆盖所有叙事段落，不能遗漏任何情节\n"
        "7. 按自然段组织叙事，遇到时空/场景切换，或某一段以人物说话收束时，该段结尾必须明确保留至少 1 秒的停顿、反应或环境余韵\n"
        "8. 不允许把章节改写成平均推进的流水账，必须主动提炼最强 hook、冲突升级、反转、高潮和 payoff\n"
        f"9. 角色名必须使用：{json.dumps(char_names, ensure_ascii=False)}\n\n"
        "【短视频戏剧弧线要求】\n"
        f"{drama_requirements}\n"
        "【节奏参考示例】\n"
        f"{fewshot_examples}\n"
        "【必须输出的戏剧字段】\n"
        f"{story_arc_output_rules}"
    )


def build_segment_narrative_prompt(
    char_names: list,
    drama_requirements: str,
    fewshot_examples: str,
) -> str:
    """Build system prompt for _generate_segment_narrative (Phase 2: per-segment)."""
    return (
        "你是专业的影视编剧。根据小说原文片段，将其改写为凝练的**剧本叙述**。\n\n"

        "要求：\n"
        "1. 写成连贯的叙述文本，不要拆分成场景编号或结构化格式\n"
        "2. 比原文更精炼，去掉冗余描写，但保留所有核心情节和对话\n"
        "3. 对话用自然方式嵌入文本，标明说话人。例如：\n"
        '   - 角色说的话：陈风怒喝：\u201c你休想！\u201d\n'
        "   - 内心独白：陈风心想（这是最后的机会了）\n"
        '   - 路人/群众：周围人议论纷纷：\u201c这小子疯了吧？\u201d\n'
        "4. 保留情绪变化和氛围描写\n"
        "5. 忠实原文情节，不要添加原文没有的内容\n"
        "6. 本段开头必须自然承接上一段结尾的动作、情绪、视角或场景状态，不能生硬重启\n"
        "7. 如果本段结尾即将发生时空/场景切换，或以人物说话收束，必须在段尾明确写出至少 1 秒的停顿、反应或环境余韵，再进入后续内容\n"
        "8. 本段结尾尽量保留下一个动作、情绪或场景变化的前奏，让后续段落容易继续衔接\n"
        "9. 本段不能写成平顺摘要，必须提炼出本段最强冲突、一次升级/揭晓/反转，以及清晰的段尾强节拍\n"
        f"10. 角色名必须使用：{json.dumps(char_names, ensure_ascii=False)}\n\n"
        "【短视频戏剧弧线要求】\n"
        f"{drama_requirements}\n"
        "【节奏参考示例】\n"
        f"{fewshot_examples}"
    )
