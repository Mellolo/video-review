"""Prompt builders for tools/storyboard_gen/base_engine.py.

函数调用阶段说明
================
1. build_segment_narrative_prompt
   → 阶段二 Step 1「段落切分」
   → 由 BaseStoryboardEngine._segment_narrative() 调用
   → 将 narrative 拆分为多个 8-15 秒的叙事段落

2. build_state_tracker_prompt
   → 阶段二 Step 3 第一轮「识别缺失场景 + 外观变化」
   → 由 BaseStoryboardEngine._track_narrative_states() 调用

3. build_state_validation_prompt
   → 阶段二 Step 3 第二轮「验证确认」
   → 由 BaseStoryboardEngine._track_narrative_states() 调用

3b. build_segment_grouping_prompt
   → 阶段二 Step 2「依赖分组」
   → 由 BaseStoryboardEngine._group_segments() 调用
   → 判断相邻 segment 的空间连续性依赖，输出分组信息

4. build_batch_fluent_prompt_system
   → 阶段二 Step 5「批量生成 seedance prompt」（连贯叙述模式，默认）
   → 由 BaseStoryboardEngine._generate_fluent_prompts_batch() 调用
   → 一次性为所有选定段落生成连贯叙述 prompt + continuity_anchor

5. build_fluent_continuity_director_prompt
   → 阶段二 Step 6「全局连续性重写」
   → 由 BaseStoryboardEngine._rewrite_fluent_prompts_for_continuity() 调用
   → 统筹所有段落的 seedance prompt，减少相邻段视觉跳变
"""

import json
from typing import List, Optional


def build_segment_narrative_prompt(
    segment_end_buffer_req: str,
    target_hint: str,
    char_names: List[str],
    loc_names: List[str],
    prop_names: List[str],
) -> str:
    """阶段二 Step 1 — 段落切分。

    调用方: BaseStoryboardEngine._segment_narrative()
    作用: 将 narrative 按叙事边界拆分为多个 8-15 秒段落，
          同时为每段分配 characters_involved / locations_involved 等。
    Response schema: NarrativeSegmentation (tools/storyboard_gen/schemas.py)
    """
    return (
        "你是专业的影视导演。请将给定的故事叙述按叙事边界拆分为多个段落。\n\n"
        "拆分规则：\n"
        "1. 每个段落默认对应一个 8-15 秒的视频片段，但应优先写成 12-15 秒、能接近 15 秒就尽量接近 15 秒\n"
        "2. 按叙事自然边界拆分：场景切换、故事切换、镜头切换等\n"
        "3. 连贯的动作序列放在同一个段落内，不要把同一波动作拆成多个偏短片段\n"
        "4. 对话密集的段落时长适当加长（对话 1 秒约 3-6 个词），优先通过补足反应、动作承接和余韵把段落写满\n"
        "5. 每个段落需要有独立的 narrative 描述，是完整的小故事片段\n"
        "6. 段落的 narrative 应保留原文中的对话和关键细节\n"
        "7. 每段都必须明确填写 segment_goal、segment_conflict、segment_turn、segment_end_beat，不允许空泛\n"
        "8. 至少保留一个承担高潮/爆点的段落；高潮段不能被平均拆碎\n"
        "9. 如果某段承担揭晓、反转、决战、断臂求生、身份反杀、世界规则改写等高压戏剧功能，应给更高时长优先级\n"
        f"10. {segment_end_buffer_req}\n"
        f"{target_hint}"
        "注意每段情节要足够充实，不要剧情推进过慢，也不要把强冲突平均摊平；为了提升一次生成的连贯性，优先减少过碎分段\n\n"
        f"可选角色：{char_names}\n"
        f"可选场景：{loc_names}\n"
        f"可选道具：{prop_names}\n"
    )


def build_batch_fluent_prompt_system(
    style: str,
) -> str:
    """阶段二 Step B（连贯叙述模式）— 一次性为所有段落生成连贯的 seedance prompt。

    调用方: BaseStoryboardEngine._generate_fluent_prompts_batch()
    作用: 将多个 segment 的 narrative 一次性送入 LLM，
          生成连贯自然的叙述性 prompt（不使用镜头1/镜头2结构），同时输出 continuity_anchor。
    Response schema: BatchFluentSeedanceOutput (tools/storyboard_gen/schemas.py)
    """
    return (
        "你是视频生成 Prompt 专家兼资深连续性导演。你会收到一组按时间顺序排列的叙事段落（segments），"
        "需要为每个段落生成一段连贯流畅的 seedance prompt，同时保证段间视觉连贯。\n\n"

        "⚠️ 核心要求——连贯叙述格式：\n"
        "- 不要使用「镜头1：Xs，场景：xxx」的结构化分镜格式\n"
        "- 将每段 narrative 润色为一段自然流畅的连贯叙述，像在描述一个完整的视频片段\n"
        "- 🚫 禁止描写比喻与拟人：比如使用“宛如”、“仿佛”、“像……一样”、“如同”。\n"
        "- 🚫 禁止描写抽象情感与心理活动，例如“深刻呈现出敬畏与迷茫”、“内心充满挣扎”。\n"
        "- ⚠️ 剧情要足够紧凑饱满，如果整体叙事内容过少，那么需要补足一些细节表现，比如加一些动作、表情、对话等，不要15s的内容只包含2-3个偏向静态的镜头描写~\n"
        "- 按时间顺序自然地串联景别切换、角色动作、表情反应、镜头运动\n"
        "- 用「镜头转为」「特写切换到」「画面拉远」等自然过渡词衔接不同画面\n"
        "- 每段 prompt 以「全程无背景音乐，无字幕。」开头，以「全程无背景音乐，无字幕。」结尾\n\n"

        "名称一致性（最高优先级）：\n"
        "1. ⚠️ 角色名严格匹配：必须使用角色定义中的合法名称，不得使用别名、代称、简称\n"
        "2. ⚠️ 场景名严格匹配：必须使用场景定义中的合法名称（包括带状态标记的变体如「xxx[受损]」）\n"
        "3. ⚠️ 道具名严格匹配：必须使用道具定义中的合法名称\n"
        "4. 同一角色可能有多个状态（如不同年龄），需要明确使用对应状态的角色名字\n\n"

        "剧情要求：\n"
        "5. ⚠️ **剧情完整性优先**：每段 narrative 中的所有关键事件、因果关系、角色动机和情绪变化必须完整体现，不得省略\n"
        "6. **戏剧节拍必须落地**：segment_goal、segment_conflict、segment_turn、segment_end_beat 必须清晰转成画面动作\n"
        "7. **因果链条**：包含「因为A所以B」的逻辑时，必须同时体现 A 和 B\n"
        "8. **情绪递进**：角色情绪变化弧线必须完整呈现\n"
        "9. **抽象剧情视觉化**：概括性描述转化为画外音独白/旁白/象征性视觉动作\n\n"

        "画面描述要求：\n"
        "10. 每个画面段落必须包含：景别（特写/中景/全景等）、主体动作、表情反应\n"
        "11. ⚠️ **人物空间关系**：必须明确人物在环境中的位置和相对关系：\n"
        "   - 单人：写明人物在场景中的具体位置（如站在大殿中央、坐在窗边）\n"
        "   - 多人：写明人物之间的相对位置（如面对面站立、A站在B身后）以及与环境的关系\n\n"

        "段间硬切过渡规则（每段视频独立生成，前后段必然硬切，需提前规划让硬切看起来自然）：\n"
        "12. ⚠️ 硬切差异原则：相邻两段的 ending_shot 与下一段 opening_shot 之间，"
        "必须在以下四个维度中至少有两项明显不同：①景别 ②画面主体 ③视角/拍摄角度 ④场景。"
        "绝对禁止「四同」硬切（景别、主体、视角、场景全部相同或相近 → 跳帧感）。"
        "示例：中景A角色→中景B角色 ✓（主体不同）；"
        "中景A角色正面→特写A角色手部 ✓（景别+主体局部都变了）；"
        "近景A角色持剑→全景A角色持剑 ✗（只有景别变了，主体/姿态/场景都一样→像画面抖了一下）；"
        "中景A角色→中景A角色 ✗（几乎没变化→跳帧）\n"
        "13. 场景切换时：后段开头推荐用全景或大远景做 establishing shot，天然形成差异\n"
        "14. 同场景连续时：优先通过切换画面主体或改变视角来制造差异\n"
        "15. 每段开头的前1-2秒建议用于建立新的视觉锚点（环境/人物/动作），不要直接进入对话或复杂动作\n"
        "16. 每段结尾不要在动作高峰处截断\n"
        "17. **最后一段**不需要留口，必须完整呈现剧情高潮与收束\n"
        "18. 时空切换不得硬切：时间跳跃要有过渡手法，场景跳跃要有 establishing shot\n\n"

        "技术约束：\n"
        "17. **台词与声音**：角色说话写明谁说了什么；内心独白/旁白注明画外音\n"
        "18. ⚠️ 对话词量约束：每段按时长计算，1秒最多说3-6个词\n"
        "19. **硬约束**：少用人物本身的外貌描述（已经定义为图片了，防止描述和图片冲突）；不加 @id/@图片 标记\n"
        "20. 严禁写出「承接上一段」「上一镜头」「前段结尾」等依赖前序画面的措辞\n"
        "21. ⚠️ 在剧情完整的前提下，优先把每段写满接近 15 秒\n\n"
        "21. ⚠️ 不要描述风格\n\n"

        "continuity_anchor 要求：\n"
        "为每段输出 continuity_anchor，包含 opening_shot、ending_shot、bridge_hint、"
        "plot_progression、emotional_arc、causal_link_to_next。最后一段的 causal_link_to_next 填「无」。\n\n"

        # f"画面风格：{style}\n"
    )



def build_fluent_continuity_director_prompt(style: str) -> str:
    """阶段二 Step C（连贯叙述版）— 全局连续性重写。

    调用方: BaseStoryboardEngine._rewrite_fluent_prompts_for_continuity()
    作用: 统筹所有段落的连贯叙述 seedance prompt，减少相邻段视觉跳变。
    Response schema: ContinuityRewriteOutput (tools/storyboard_gen/schemas.py)
    """
    return (
        "你是资深的视频连续性导演与 Prompt 总编。\n"
        "你会拿到一个已经按顺序生成好的段落级 seedance prompt 列表。每段 prompt 是一段连贯的自然语言叙述。\n"
        "你的任务不是推翻每段剧情，而是先为每段自行提炼 continuity_anchor"
        "（opening_shot、ending_shot、bridge_hint、plot_progression、emotional_arc、causal_link_to_next），"
        "再把所有段落作为一个连续长视频来统筹，逐段重写 prompt，在保证剧情完整性的前提下减少相邻段落之间的视觉跳变（主要重写开和结尾）。\n\n"

        "⚠️ 最高优先级——剧情保护：\n"
        "- 每段 narrative_summary 中的所有事件、对话、因果关系必须完整保留，不得删掉或压缩\n"
        "- 参考 plot_progression 和 causal_link_to_next 确保段间因果链不断裂\n"
        "- emotional_arc 必须有对应视觉表现，不能为了转场平滑而跳过情绪转折\n"
        "- 概括性/总结性剧情不得省略，必须转化为画外音、旁白或象征性视觉动作\n"
        "- 优先级排序：剧情完整性 > 视觉连贯性 > 镜头语言美感\n\n"

        "连续性改写规则：\n"
        "1. 参考上一段 ending_shot 与当前段 opening_shot 设计开场，但最终文字必须让当前段独立成立\n"
        "2. ⚠️ 硬切过渡检查（每段视频独立生成，前后段必然硬切）：逐对检查相邻段的 ending_shot 和 opening_shot，"
        "必须在以下四个维度中至少有两项明显不同：①景别 ②画面主体 ③视角/拍摄角度 ④场景。"
        "如果差异不足，必须重写其中一段的开头或结尾。"
        "重点检查：同主体+相似姿态/构图的情况（即使景别不同也会跳帧），必须调整为不同主体或明显不同的视角\n"
        "3. 场景切换时：后段开头必须用全景或大远景做 establishing shot\n"
        "4. 同场景连续时：后段开头必须切换到不同主体或明显不同的视角\n"
        "5. 非结尾段落可为后续留下 ending anchor，但不要在文案中写「下一段如何承接」；"
        "最后一段不需要留口，必须完整呈现高潮与收束\n"
        "6. 时空切换不得硬切：时间跳跃要有过渡手法（文字卡/环境暗示/季节符号），场景跳跃要有 establishing shot\n"
        "7. 每段开头的前1-2秒建议用于建立新的视觉锚点（环境/人物/动作），不要直接进入对话或复杂动作\n"
        "8. 每段结尾不要在动作高峰处截断\n"
        "9. 严禁写出「承接上一段」「上一镜头」「前段结尾」等依赖前序画面的措辞\n"
        "10. 保留原 prompt 的所有硬约束（角色名严格匹配、禁止外貌描述、台词口型/画外音标注、「全程无背景音乐，无字幕」、不加 @id/@图片 标记），"
        "保持连贯自然语言叙述格式，不要改成「镜头N：Xs，场景：xxx」的结构化格式\n"
        "11. 按原 segment_id 顺序返回，每段返回重写后的 seedance_prompt、transition_strategy 和 continuity_anchor\n\n"

        "⚠️ seedance_prompt 输出质量硬约束：\n"
        "- 每段重写后的 seedance_prompt 必须包含完整的画面描述正文\n"
        "- 重写后的 seedance_prompt 长度不得低于原始 prompt 长度的 80%，不得大幅缩减\n"
        "- seedance_prompt 中不要包含「风格：xxx」前缀，只输出画面描述正文\n\n"

        f"整体画面风格：{style}\n"
    )


# ═══════════════════════════════════════════════════════════════════════
#  Step 3 — Narrative state tracking & transition gap detection
# ═══════════════════════════════════════════════════════════════════════

def build_state_tracker_prompt(
    char_defs: str,
    loc_defs: str,
    prop_defs: str,
    style: str,
) -> str:
    """阶段二 Step 3 第一轮 — 识别缺失场景 + 外观变化。

    调用方: BaseStoryboardEngine._track_narrative_states()
    作用: 扫描所有 segment narrative，识别：
          1. narrative 中隐含但未在 locations 定义中注册的场景
          2. 角色/场景/道具的剧情驱动的重大外观变化
    Response schema: NarrativeStateOutput (tools/storyboard_gen/schemas.py)
    """
    return (
        "你是专业的影视连续性监督。你会收到一组按顺序排列的叙事段落（segments），"
        "以及当前已定义的角色、场景和道具列表。\n\n"

        "你需要完成两项检查：\n\n"

        "## 1. 缺失场景检查（missing_locations）\n"
        "逐段阅读 narrative，找出所有在叙事中**明确出现或隐含**但未在 locations 定义中注册的场景。\n"
        "例如：narrative 提到「走进教学楼走廊」「来到实验室门口」，但 locations 里没有「教学楼走廊」「实验室门口」。\n"
        "对每个缺失场景，输出：\n"
        "- name：场景名称\n"
        f"- description：场景外观描述（用于生图，需符合整体画面风格：{style}）\n"
        "- mentioned_in_segments：在哪些 segment_id 中出现\n\n"

        "规则：\n"
        "- ⚠️ **只报告在至少两个相邻 segment 中都出现的场景**。如果一个场景只在单个 segment 中出现，不要报告\n"
        "- 只报告**具体的物理空间**，不报告抽象概念（如「回忆」「梦境」）\n"
        "- 如果 narrative 中的场景可以归入已有 location 定义（只是措辞不同），不要报告\n\n"

        "## 2. 外观变化追踪（state_changes）\n"
        "逐段阅读 narrative，找出角色、场景或道具发生的**剧情驱动的重大外观变化**。\n"
        "只追踪以下类型的变化：\n"
        "- 角色：受伤（流血、断肢）、换装、变身、衰老/年轻化\n"
        "- 场景：建筑损毁、火灾、洪水、装修改造、季节变化导致的外观变化\n"
        "- 道具：损坏、变形、消失、被改造\n\n"

        "⚠️ **不要追踪**：表情变化、姿态变化、情绪变化、光线变化、镜头角度变化\n\n"
        "⚠️ **不要追踪细微的变化，或者对后续剧情基本上不会注意到的变化**：比如脸上\n\n"

        "对每个变化，输出：\n"
        "- original_name：原始实体名（必须与定义中的 name 完全一致，一字不差）\n"
        "- entity_type：character / location / prop\n"
        "- state_label：简短状态标签（如「衬衫沾满油污」「左臂受伤」）\n"
        "- change_description：相对于原始外观的变化描述（只描述变化的部分，不重复原始外观）\n"
        "- first_segment_id：从哪个 segment 开始生效\n"
        "- last_segment_id：到哪个 segment 结束（含），-1 表示持续到最后\n"
        "- requires_sheet：是否需要生成新的设定图（true/false），判断标准见下方\n\n"
        "⚠️ **同一实体的多次变化不得有 segment 范围重叠**：\n"
        "如果同一实体发生了多次变化，每次变化的 [first_segment_id, last_segment_id] 区间必须互不重叠。\n"
        "具体做法：前一次变化的 last_segment_id 必须严格小于下一次变化的 first_segment_id。\n"
        "例如：变化A生效于 seg 1，变化B生效于 seg 2，则变化A的 last_segment_id 应为 1（而非 -1），变化B从 seg 2 开始。\n\n"

        "### requires_sheet 判断标准\n"
        "- **true**（需要生成设定图）：变化后的外观成为后续核心情节的视觉锚点，"
        "例如角色变身为全新形态且后续多个场景以此形态出现、场景中的关键物体发生不可逆变化且后续情节围绕此展开（如石碑碎裂、建筑坍塌后废墟成为新的主要场景）\n"
        "- **false**（仅文本描述）：变化可以用文字在视频生成 prompt 中精确描述，"
        "例如嘴角流血、头发散落、衣物破损、碎玻璃、灰尘覆盖等局部细节变化，"
        "或者变化虽然视觉上明显但不是后续情节的核心视觉元素\n"
        "- 简单判断原则：**如果不生成新设定图，仅靠在 prompt 中写「嘴角有血迹」「街道玻璃碎裂」等文字描述就能让视频生成模型正确呈现，则 requires_sheet = false**\n\n"

        "规则：\n"
        "- original_name 必须与已有定义中的 name 字段**完全一致**，包括括号、标点\n"
        "- ⚠️ **只追踪后续还会出现的实体变化**：如果某个角色/场景/道具在发生变化后的 segment 之后不再出现，不要报告\n"
        "- ⚠️ **不要报告已有独立定义覆盖的变化**：如果变化后的形态已经作为独立实体定义在角色/场景/道具列表中"
        "（例如「林曦」变身后的形态已有独立角色「剑仙林曦」），则不要报告该变化，因为已有定义会直接使用\n"
        "- 如果没有任何重大变化，state_changes 返回空数组\n"
        "- 如果没有缺失场景，missing_locations 返回空数组\n"
        "- 同一实体可以有多次变化（不同 segment），每次单独一条\n\n"

        "## 输出格式\n"
        "⚠️ 你必须严格按照 response_schema 输出纯 JSON，不要输出 markdown、表格或任何解释文字。\n\n"

        "## 已有定义\n\n"
        f"### 角色定义\n{char_defs}\n\n"
        f"### 场景定义\n{loc_defs}\n\n"
        f"### 道具定义\n{prop_defs}\n"
    )


def build_state_validation_prompt(
    char_defs: str,
    loc_defs: str,
    prop_defs: str,
) -> str:
    """阶段二 Step 3 第二轮 — 确认过滤。

    调用方: BaseStoryboardEngine._track_narrative_states()
    作用: 对第一轮识别结果逐条确认，过滤 LLM 幻觉。
    Response schema: StateAssignmentOutput (tools/storyboard_gen/schemas.py)
    """
    return (
        "你是影视连续性审核员。你会收到：\n"
        "1. 一组叙事段落（segments）\n"
        "2. 第一轮分析得到的「缺失场景」和「外观变化」列表\n"
        "3. 已有的角色/场景/道具定义\n\n"

        "请逐条审核，只判断每条识别结果是否有效（confirmed = true/false）。\n\n"

        "## 审核标准\n\n"

        "### 缺失场景（location_validations）\n"
        "对 missing_locations 中的每一条，检查：\n"
        "- 该场景是否确实在 narrative 中被明确提及或强烈暗示？\n"
        "- 该场景是否确实不在已有 locations 定义中？（注意措辞不同但指同一地点的情况）\n"
        "- 该场景是否需要独立画面（而非一闪而过的背景提及）？\n"
        "如果以上任一条不满足，confirmed = false\n\n"

        "### 外观变化（change_assignments）\n"
        "对 state_changes 中的每一条，检查：\n"
        "- original_name 是否与已有定义中的 name 完全一致？\n"
        "- 该变化是否确实在 narrative 中有明确依据（不是推测）？\n"
        "- 该变化是否属于重大外观变化（受伤/换装/损毁等），而非表情/姿态/情绪？\n"
        "如果以上任一条不满足，confirmed = false\n\n"

        "⚠️ 只输出 confirmed，不要尝试修正任何字段。\n\n"

        "## 输出格式\n"
        "⚠️ 你必须严格按照 response_schema 输出纯 JSON，不要输出 markdown、表格或任何解释文字。\n\n"

        "## 已有定义\n\n"
        f"### 角色定义\n{char_defs}\n\n"
        f"### 场景定义\n{loc_defs}\n\n"
        f"### 道具定义\n{prop_defs}\n"
    )


# ═══════════════════════════════════════════════════════════════════════
#  Step 2 — Segment dependency grouping
# ═══════════════════════════════════════════════════════════════════════

def build_segment_grouping_prompt() -> str:
    """阶段二 Step 2 — 判断相邻 segment 之间的空间连续性依赖。

    调用方: BaseStoryboardEngine._group_segments()
    作用: 将 segments 分组，组内串行生成视频（前一段末尾画面作为后一段参考），组间并行。
    Response schema: SegmentGroupingOutput (tools/storyboard_gen/schemas.py)
    """
    return (
        "你是专业的影视分镜调度员。你会收到一组按顺序排列的叙事段落（segments），"
        "每段包含 narrative、characters_involved、locations_involved。\n\n"

        "你的任务是判断哪些**相邻**段落之间存在**空间连续性依赖**，需要串行生成视频。\n\n"

        "## 判断标准\n\n"

        "两个相邻段落应该归入同一组（串行），当且仅当：\n"
        "1. 它们共享**同一个场景**（locations_involved 有交集）\n"
        "2. 它们共享**同一个角色**（characters_involved 有交集）\n"
        "3. 后一段的开头需要**延续前一段结尾的空间关系**"
        "（人物站位、姿态、物品位置、镜头视角等）\n\n"

        "以上三个条件必须**同时满足**。\n\n"

        "两个相邻段落应该拆开（可并行），当：\n"
        "- 场景切换（不同 location）\n"
        "- 时空跳跃（时间线不连续）\n"
        "- 虽然同场景同角色，但后一段是全新的镜头构图，不需要延续前一段的空间关系\n\n"

        "⚠️ **默认倾向拆开**：如果不确定是否需要延续空间关系，就拆开。"
        "串行会增加生成等待时间，只在确实需要时才合并。\n\n"

        "## 输出要求\n\n"
        "- 每个 segment 必须恰好出现在一个 group 中\n"
        "- 独立段落（不依赖前后段）单独成组\n"
        "- 组内 segment_ids 必须是连续的相邻段落，按顺序排列\n"
        "- reason 简要说明为什么这些段需要串行\n\n"
        "⚠️ **输出格式硬约束**：你必须严格按照 response_schema 输出纯 JSON 对象，"
        "不要输出 markdown、不要用 **加粗**、不要用 - 列表、不要加任何解释文字。直接输出 {...} JSON。"
    )


