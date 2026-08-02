"""
Shared Pydantic schemas and helpers for storyboard generation.

All generators (video, novel, future types) share the same output schema
so that the downstream parser / renderer is agnostic to the input source.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


# ── Schema helpers (Gemini structured-output compat) ──────────────────

_UNSUPPORTED_SCHEMA_KEYS = {"default", "examples", "$schema"}


def flatten_json_schema(schema: dict) -> dict:
    """Resolve ``$ref`` / ``$defs``, strip unsupported keys (``default``, etc.),
    and ensure every ``properties`` object has a complete ``required`` list —
    all needed for Gemini's structured-output API.

    Also collapses ``anyOf`` produced by ``Optional[T]`` (pydantic v2) into
    the non-null branch so that OpenAI-compatible strict mode doesn't choke.
    """
    defs = schema.pop("$defs", schema.pop("definitions", {}))

    def _resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                ref_path = node["$ref"]
                ref_name = ref_path.rsplit("/", 1)[-1]
                if ref_name in defs:
                    return _resolve(dict(defs[ref_name]))
                return node
            # Collapse anyOf: [SomeType, {"type": "null"}] → SomeType
            if "anyOf" in node:
                branches = node["anyOf"]
                non_null = [b for b in branches if b != {"type": "null"}]
                if len(non_null) == 1:
                    # Merge any sibling keys (e.g. description) into the resolved branch
                    merged = {k: v for k, v in node.items() if k != "anyOf"}
                    merged.update(_resolve(non_null[0]))
                    return merged
            resolved = {
                k: _resolve(v)
                for k, v in node.items()
                if k not in _UNSUPPORTED_SCHEMA_KEYS
            }
            if "properties" in resolved:
                resolved["required"] = list(resolved["properties"].keys())
            return resolved
        if isinstance(node, list):
            return [_resolve(item) for item in node]
        return node

    return _resolve(schema)


# ── Enums ─────────────────────────────────────────────────────────────

class StoryboardMode(str, Enum):
    """Generation mode for the video storyboard engine."""
    REPLICATE = "replicate"   # 视频复刻：忠实还原参考视频
    RECREATE = "recreate"     # 二创模式：保持主题，按用户需求创意改编


# ── Style presets (used by novel engine, available to all) ────────────

VIDEO_STYLE_PRESETS: Dict[str, str] = {
    "3d国漫": (
        "3D CG动画风格，中国国产3D动漫风格，画面色彩浓郁，"
        "特效华丽且富有动感"
    ),
    "真人": (
        "真人写实风格，电影级画面质感，真实的光影和材质，"
        "自然的人物比例与面部细节，写实的环境与服装纹理"
    ),
    "2d动漫": (
        "2D手绘动漫风格，精致的赛璐珞上色，"
        "动漫式的人物比例与面部特征，鲜明的线条勾勒"
    ),
    "水墨": (
        "中国水墨动画风格，留白意境，淡墨渲染，"
        "有东方美学画面质感"
    ),
}

AUTO_VIDEO_STYLE = ""
DEFAULT_VIDEO_STYLE = "3d国漫"

_STYLE_KEYWORD_PATTERNS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("真人", (
        "真人", "写实", "实拍", "电影感", "电影级", "live action", "cinematic",
    )),
    ("水墨", (
        "水墨", "国风水墨", "水彩水墨", "ink wash",
    )),
    ("2d动漫", (
        "2d", "2d动漫", "二维", "手绘", "二次元", "anime", "日漫", "赛璐珞",
    )),
    ("3d国漫", (
        "3d", "3d国漫", "国漫", "国产动漫", "cg动画", "三维", "3d cg",
    )),
)


def normalize_style_choice(raw: Optional[str]) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    lowered = re.sub(r"\s+", "", text.lower())
    aliases = {
        "auto": "",
        "自动": "",
        "自动解析": "",
        "自动判断": "",
        "不指定": "",
        "默认": "",
        "3d国漫": "3d国漫",
        "3d国漫风": "3d国漫",
        "国漫": "3d国漫",
        "3dcg": "3d国漫",
        "真人": "真人",
        "写实": "真人",
        "真人写实": "真人",
        "2d动漫": "2d动漫",
        "2d": "2d动漫",
        "二维": "2d动漫",
        "手绘": "2d动漫",
        "二次元": "2d动漫",
        "水墨": "水墨",
        "国风水墨": "水墨",
    }
    if lowered in aliases:
        return aliases[lowered]
    return text


def infer_style_from_text(text: str) -> str:
    source = (text or "").strip().lower()
    if not source:
        return ""
    for style_key, keywords in _STYLE_KEYWORD_PATTERNS:
        if any(keyword in source for keyword in keywords):
            return style_key
    return ""


def resolve_video_style(
    explicit_style: Optional[str] = None,
    text_candidates: Optional[List[str]] = None,
    fallback_style: str = DEFAULT_VIDEO_STYLE,
) -> tuple[str, str, bool]:
    normalized_explicit = normalize_style_choice(explicit_style)
    if normalized_explicit:
        return normalized_explicit, VIDEO_STYLE_PRESETS.get(normalized_explicit, normalized_explicit), True

    for candidate in text_candidates or []:
        inferred = infer_style_from_text(candidate)
        if inferred:
            return inferred, VIDEO_STYLE_PRESETS.get(inferred, inferred), False

    normalized_fallback = normalize_style_choice(fallback_style) or DEFAULT_VIDEO_STYLE
    return normalized_fallback, VIDEO_STYLE_PRESETS.get(normalized_fallback, normalized_fallback), False


# ═══════════════════════════════════════════════════════════════════════
#  Shared Pydantic models
# ═══════════════════════════════════════════════════════════════════════

class VideoAnalysis(BaseModel):
    style: str = Field(description="视频的整体风格描述")
    theme: str = Field(description="视频的主题")
    tone: str = Field(description="视频的基调（如：欢快、严肃、温馨等）")
    key_elements: List[str] = Field(description="视频的关键元素列表")


class StoryArcFields(BaseModel):
    hook: str = Field(
        description="开场 3-8 秒最抓人的钩子：异常画面、羞辱、危机、诱惑、谜团或反常信息，必须一句话说清观众为什么会想继续看"
    )
    core_conflict: str = Field(
        description="整条故事线最核心的对抗：主角想达成什么，谁或什么在阻止他/她"
    )
    stakes: str = Field(
        description="如果主角失败，将失去什么或付出什么代价，必须具体而非空泛"
    )
    turning_points: List[str] = Field(
        description="按时间顺序列出 2-5 个关键转折点：升级、揭晓、反转、压迫加码或局势逆转"
    )
    climax: str = Field(
        description="最终高潮/爆点：最强对抗、最狠反击、最震撼揭晓或最强情绪爆发"
    )
    payoff: str = Field(
        description="高潮后的兑现/回钩/余味：角色状态、关系变化、代价回收、讽刺回响或悬念尾钩"
    )
    emotional_curve: str = Field(
        description="整支短片的情绪曲线概括，如「压抑→羞辱→爆发→反杀→余悸」"
    )


class Character(BaseModel):
    name: str = Field(description="角色名称")
    description: str = Field(
        description=(
            "角色的【固定外观】描述，用于生成角色立绘，必须是静态、不变的视觉信息。"
            "只写：画面风格（开头注明）、年龄（如果未成年，不要写，写少年或者青年）、性别、发型颜色与样式、"
            "五官特征、肤色、身材体型、服装款式与颜色、配饰。"
            "【禁止】出现任何随时间/剧情变化的内容："
            "【禁止】出现情绪变化（如「眼神从希望变为绝望」）、"
            "【禁止】出现表情描写（如「面带微笑」）、动作姿态（如「握紧拳头」）、"
            "【禁止】出现剧情发展（如「后期变得...」「最终...」）、变身/觉醒前后对比。"
            "如果角色后续会换装、受伤、变身等，不要写在这里，只写最初出场的外观。"
            "这些动态内容属于分镜剧情，不要写在角色定义里。"
        )
    )
    personality: str = Field(description="角色的性格特点")
    voice_description: str = Field(
        description=(
            "角色的【固定声音特征】描述，用于语音合成，必须是稳定不变的音色属性。"
            "只写：性别、大致年龄段的声音、"
            "音色特点（如低沉浑厚、清脆明亮、沙哑沧桑、温柔甜美）、"
            "说话风格（如沉稳干练、活泼俏皮、冷酷寡言、豪爽粗犷）、"
            "语速倾向（快/中/慢）、是否有口头禅或标志性语气。"
            "【禁止】出现随剧情变化的情绪描写"
            "（如「起初平静后来愤怒」「声音逐渐颤抖」）。"
        ),
        default="",
    )


class Location(BaseModel):
    name: str = Field(description="场景名称（如：校园操场、废墟战场、云海仙境）")
    description: str = Field(
        description=(
            "场景的固定视觉描述：画面风格（开头注明）、地形地貌、建筑结构、环境氛围、色调、标志性物体等。"
            "只描述场景本身的固定特征，不要描述角色或剧情。"
            "如果场景后续会发生变化（如建筑损毁、火灾等），不要写在这里，只写最初的状态。"
            "后续场景变化会由系统自动检测并注册为独立的衍生场景定义。"
        )
    )


class Prop(BaseModel):
    name: str = Field(
        description="道具名称（如：轩辕剑、时光沙漏、传国玉玺）"
    )
    description: str = Field(
        description=(
            "道具【初始状态】的固定外观描述，用于在多个分镜中保持一致。"
            "包括：形状、大小、材质、颜色、发光/特效、标志性特征等。"
            "【禁止】写画面风格（如「3D CG动画风格」等）。"
            "只描述道具本身的固定视觉特征，不要描述使用方式或剧情。"
            "如果道具后续会损坏、变形等，不要写在这里，只写最初的状态。"
            "后续道具变化会由系统自动检测并注册为独立的衍生道具定义。"
            "注意：如果某个物体已经是场景(locations)描述的一部分"
            "（如广场中的石碑、大厅里的王座），则不要重复定义为道具。"
            "只有在横跨10秒以上不同剧情段落中反复出现的道具才需要定义。"
        )
    )


class DialogueLine(BaseModel):
    speaker: str = Field(
        description=(
            "说话/思考者：填角色名、'路人'或'旁白'。"
            "角色内心独白也填角色名（不要填旁白），"
            "只有纯第三人称叙述才填'旁白'"
        )
    )
    line_type: str = Field(
        description=(
            "台词类型，只能填以下四个值之一：dialogue（角色说出的话）、"
            "inner（角色内心独白，画外音）、narration（第三人称旁白叙述）、"
            "crowd（路人群众议论声）"
        )
    )
    text: str = Field(description="台词或旁白内容")
    emotion: str = Field(
        description="说话时的情绪（如：愤怒、温柔、惊讶、平静）",
        default="",
    )


# ── Novel engine: phase-specific schemas ──────────────────────────────

class NarrativeSegment(BaseModel):
    segment_id: int = Field(description="段落编号，从 1 开始")
    title: str = Field(description="段落标题，2-10 个中文字")
    start_hint: str = Field(
        description="本段起始的原文片段（15-30 字），必须是原文中连续存在的文字"
    )
    end_hint: str = Field(
        description="本段结束的原文片段（15-30 字），必须是原文中连续存在的文字"
    )
    summary: str = Field(description="一句话概要本段叙事内容")
    characters_involved: List[str] = Field(
        description="本段涉及的角色名称列表，必须与 characters 定义一致"
    )
    locations_involved: List[str] = Field(
        description="本段涉及的场景名称列表，必须与 locations 定义一致"
    )
    estimated_video_seconds: float = Field(
        description="建议本段对应的视频时长（秒），根据情节密度分配"
    )


class ChapterAnalysis(BaseModel):
    """Phase-1 output: global analysis of a novel chapter."""
    style: str = Field(description="推荐的视频画面风格（如：3D CG动画风格、真人写实风格等）")
    theme: str = Field(description="章节主题")
    tone: str = Field(description="章节基调（如：热血、悲壮、温馨、悬疑等）")
    key_elements: List[str] = Field(description="关键视觉元素列表")
    characters: List[Character] = Field(description="所有角色定义")
    locations: List[Location] = Field(description="所有场景定义")
    props: List[Prop] = Field(
        description="关键道具定义（只定义横跨10秒以上不同剧情段落中反复出现的重要道具，不含场景自带的固定物体）",
        default_factory=list,
    )
    segments: List[NarrativeSegment] = Field(description="叙事段落切分")


# ═══════════════════════════════════════════════════════════════════════
#  Direct-prompt segment mode schemas
#
#  Used in the pipeline:
#    narrative → LLM segmentation → parallel LLM → direct seedance prompts
# ═══════════════════════════════════════════════════════════════════════

class ScreenplaySchema(BaseModel):
    """Full screenplay: structured metadata + prose narrative text."""
    title: str = Field(
        description=(
            "为这个故事起一个简短有力的标题（2-10个中文字），"
            "能概括核心主题或最引人注目的元素"
        )
    )
    video_analysis: VideoAnalysis
    characters: List[Character]
    locations: List[Location]
    props: List[Prop] = Field(
        description="关键道具定义（只定义横跨10秒以上不同剧情段落中反复出现的重要道具，不含场景自带的固定物体）",
        default_factory=list,
    )
    hook: str = Field(
        description="开场钩子：必须在故事前 3-8 秒内抛出最抓人的异常、羞辱、危机、谜团、诱惑或目标"
    )
    core_conflict: str = Field(
        description="核心冲突：主角的目标、阻碍者/阻碍机制，以及双方的对抗关系"
    )
    stakes: str = Field(
        description="失败代价：如果主角失败，会失去什么、谁会受伤、世界会发生什么坏结果"
    )
    turning_points: List[str] = Field(
        description="按时间顺序列出 2-5 个关键转折点：局势升级、揭晓、反转、压迫加码或逆转节点"
    )
    climax: str = Field(
        description="最终高潮/爆点：最强对抗、最狠反击、最震撼揭晓或最强情绪爆发"
    )
    payoff: str = Field(
        description="高潮后的兑现/回钩/余味：主角最终状态、关系变化、代价回收或尾钩悬念"
    )
    emotional_curve: str = Field(
        description="全片情绪曲线概括，如「平静→受辱→压迫升级→爆发反杀→余悸」"
    )
    narrative: str = Field(
        description=(
            "连贯的故事叙述文本。用自然语言描述完整的故事情节，按自然段组织叙事；"
            '对话用引号标注并标明说话人（如：陈风怒喝：“你休想！”），'
            "内心独白用括号标注（如：陈风心想（这是最后的机会了）），"
            "比原素材更凝练，但保留所有核心情节和对话。"
            "必须围绕 hook、核心冲突、代价、转折、高潮与 payoff 组织事件，避免流水账。"
            "遇到时空/场景切换，或某一段以人物说话收束时，该段结尾要明确保留至少 1 秒的停顿、反应或环境余韵，避免戛然而止"
        )
    )



class ScreenplaySchemaWithDuration(ScreenplaySchema):
    """ScreenplaySchema extended with user-requested duration parsing.

    Used exclusively by PromptStoryboardEngine in quickchat (one-shot) mode
    where no explicit duration input exists — the LLM infers it from the text.
    """
    requested_duration_seconds: Optional[float] = Field(
        description=(
            "用户在创意描述中明确提到的目标视频时长（秒）。"
            "如果用户写了\"30秒\"、\"1分钟\"、\"2分钟\"等时长信息，请将其转换为秒数填入此字段。"
            "如果用户没有提到任何时长，填 null。"
        ),
        default=None,
    )

class ScreenplayNarrativeOutput(StoryArcFields):
    """Narrative-only structured output with explicit story arc fields."""
    narrative: str = Field(
        description=(
            "连贯的故事叙述文本，保留核心情节和对话。"
            "必须让 hook、冲突、代价、转折、高潮和 payoff 都在 narrative 中得到具体体现。"
            "按自然段组织叙事，避免流水账。"
        )
    )


class SegmentNarrative(BaseModel):
    """Per-segment narrative output — used by novel engine."""
    narrative: str = Field(
        description=(
            "本段的故事叙述文本，保留核心情节和对话，语言连贯凝练。"
            "必须有本段的目标、阻碍、升级或转折，并在段尾形成悬念、爆点或情绪落点。"
            "如果本段结尾即将发生时空/场景切换，或以人物说话收束，要在段尾明确保留至少 1 秒的停顿、反应或环境余韵"
        )
    )


# ═══════════════════════════════════════════════════════════════════════
#  Direct-prompt segment mode schemas
#
#    narrative → LLM segmentation → parallel LLM → direct seedance prompts
# ═══════════════════════════════════════════════════════════════════════

class NarrativeSegmentV2(BaseModel):
    """One segment produced by the narrative-splitting LLM call."""
    segment_id: int = Field(description="段落编号，从 1 开始")
    segment_goal: str = Field(
        description="本段主角最直接的目标或当下诉求，要具体可感知"
    )
    segment_conflict: str = Field(
        description="本段的直接阻碍、压迫或对抗对象，说明矛盾是如何显形的"
    )
    segment_turn: str = Field(
        description="本段最关键的升级、揭晓、反转或局势变化点"
    )
    segment_end_beat: str = Field(
        description="本段结尾的强节拍：悬念、反击、震惊、余韵、决心或高潮落点"
    )
    narrative: str = Field(
        description=(
            "该段落的独立叙事描述，包含完整的情节、动作和对话。"
            "应当是一段自成一体的小故事片段。"
            "必须围绕 segment_goal、segment_conflict、segment_turn 和 segment_end_beat 组织。"
            "如果涉及到定义的人物、场景、物品，一定要用定义的名字（如果名字带一些符号，也一定要保持一致）"
        )
    )
    duration_seconds: float = Field(
        description="建议视频时长（8-15 秒），根据情节密度、对抗强度和对话量分配；如无明显必要，优先落在 12-15 秒并尽量接近 15 秒"
    )
    characters_involved: List[str] = Field(
        description="该段涉及的角色名称列表，必须与 characters 定义一致"
    )
    locations_involved: List[str] = Field(
        description="该段涉及的场景名称列表，必须与 locations 定义一致"
    )
    props_involved: List[str] = Field(
        description="该段涉及的道具名称列表，必须与 props 定义一致",
        default_factory=list,
    )


class NarrativeSegmentation(BaseModel):
    """Output of the narrative-splitting LLM call."""
    segments: List[NarrativeSegmentV2]


class BatchFluentSeedanceScene(BaseModel):
    """One segment's result inside a batch fluent (连贯叙述) generation."""
    segment_id: int = Field(description="对应的段落编号，从 1 开始")
    seedance_prompt: str = Field(
        description=(
            "将本段 narrative 润色为一段连贯的视频生成 prompt。要求：\n"
            "1. 不使用「镜头1/镜头2」等结构化分镜格式，写成一段自然流畅的叙述\n"
            "2. 按时间顺序描述画面：景别、角色动作、表情、镜头运动、光照氛围\n"
            "3. 完整覆盖本段所有情节，不遗漏任何动作或事件\n"
            "4. 角色名严格匹配：必须使用角色定义中的合法名称，不得使用别名或代称\n"
            "5. 场景名严格匹配：必须使用场景定义中的合法名称\n"
            "6. 道具名严格匹配：必须使用道具定义中的合法名称\n"
            "7. 对话词量约束：1秒最多说3-6个词，根据段落时长严格控制台词字数\n"
            "8. 角色说话写明谁说了什么（需口型匹配）；内心独白/旁白注明画外音\n"
            "9. 禁止外貌描述；不加 @id/@图片 标记\n"
            "10. 以「全程无背景音乐」结尾"
        )
    )
    characters_in_scene: List[str] = Field(
        description="出现在该段落中的角色名称列表"
    )
    scene_location: str = Field(
        description="该段落的主要场景名称，必须与 locations 定义一致",
        default="",
    )
    props_in_scene: List[str] = Field(
        description="出现在该段落中的道具名称列表",
        default_factory=list,
    )
    transition_strategy: str = Field(
        description=(
            "一句话概括本段采用的衔接策略。"
            "必须写成当前段可独立理解的表述。"
            "不要出现「承接上一段」「上一镜头」「图片1」等依赖前序画面的措辞。"
        ),
        default="",
    )
    continuity_anchor: "ContinuityAnchor" = Field(
        description="本段的连续性锚点，用于段间衔接参考",
    )


class BatchFluentSeedanceOutput(BaseModel):
    """Output of the batch fluent (连贯叙述) prompt-generation LLM call."""
    segments: List[BatchFluentSeedanceScene] = Field(
        description="按 segment_id 顺序排列的所有段落的连贯 seedance prompt 结果"
    )


class ContinuityAnchor(BaseModel):
    """Compact opening / ending anchor used to stitch adjacent prompts together."""
    opening_shot: str = Field(
        description=(
            "本段开头第一个画面的视觉锚点。格式：「景别，主体，首个动作/状态，环境」。"
            "景别必须从 大远景/全景/中景/近景/特写 中选一个。"
            "⚠️ 硬切过渡规则（每段视频独立生成，前后段必然硬切）：与上一段 ending_shot 相比，"
            "必须在以下维度中至少有两项明显不同：①景别 ②画面主体 ③视角/拍摄角度 ④场景。"
            "绝对禁止与上一段 ending_shot 景别、主体、视角、场景全部相同或相近（跳帧感）。"
            "第一段不受此限制。"
        )
    )
    ending_shot: str = Field(
        description=(
            "本段结尾最后一个画面的视觉锚点。格式：「景别，主体，停留动作/状态，环境」。"
            "景别必须从 大远景/全景/中景/近景/特写 中选一个。"
            "⚠️ 需为下一段 opening_shot 的硬切过渡留出空间："
            "避免结尾画面在景别、主体、视角上与下一段开头过于相似。"
            "结尾画面应收束到稳定状态，不要在动作高峰处截断。"
            "最后一段不受此限制。"
        )
    )
    bridge_hint: str = Field(
        description="一句话说明下一段最适合如何承接本段结尾，不要引用上一段图片或前一幕"
    )
    plot_progression: str = Field(
        description=(
            "本段的剧情推进概括：段初处境 → 段末变化。"
            "例如「太后被贬入冷宫（段初）→ 被炸鸡香味征服，放下身段大快朵颐（段末）」"
        ),
        default="",
    )
    emotional_arc: str = Field(
        description="本段的情绪弧线，如「愤怒→好奇→满足」",
        default="",
    )
    causal_link_to_next: str = Field(
        description="与下一段的因果关联，如「太后吃上瘾→命宫女去要秘方」；最后一段填「无」",
        default="",
    )


class ContinuitySegmentRewrite(BaseModel):
    """Continuity-polished prompt for one generated segment."""
    segment_id: int = Field(description="对应的段落编号，从 1 开始")
    seedance_prompt: str = Field(
        description=(
            "在保留该段落核心剧情、角色、场景、道具和台词语义的前提下，"
            "基于本轮分析得到的连续性锚点和前后段落关系重写后的 seedance prompt。"
            "必须明确处理与上一段/下一段的衔接方式，减少视频跳变。"
        )
    )
    transition_strategy: str = Field(
        description=(
            "一句话概括本段采用的衔接策略。"
            "必须写成当前段可独立理解的表述，例如“以人物面部特写开场，再拉远到双人中景建立空间关系”。"
            "不要出现“承接上一段”“上一镜头”“图片1”等依赖前序画面的措辞。"
        )
    )
    continuity_anchor: ContinuityAnchor


class ContinuityRewriteOutput(BaseModel):
    """Output of the cross-segment continuity polishing pass."""
    segments: List[ContinuitySegmentRewrite]


class SingleSceneRewriteOutput(BaseModel):
    """Structured output for single-scene regenerate / chat refinement."""
    seedance_prompt: str = Field(description="重写后的单段 seedance prompt，保持「镜头N：Xs，场景：xxx，xxx」的结构化格式")
    transition_strategy: str = Field(description="该段相对前后段采用的衔接策略说明")
    continuity_anchor: ContinuityAnchor


# ═══════════════════════════════════════════════════════════════════════
#  Screenplay metadata sync — re-extract characters/locations/props
#  from an edited narrative to keep definitions consistent.
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
#  Step 3 — Narrative state tracking & transition gap detection
# ═══════════════════════════════════════════════════════════════════════

class MissingLocation(BaseModel):
    """narrative 中隐含但未在 locations 定义中注册的场景"""
    name: str = Field(description="场景名称，简洁明确")
    description: str = Field(
        description="场景外观描述，用于生图。包含空间布局、光线、关键物件等视觉信息"
    )
    mentioned_in_segments: List[int] = Field(
        description="该场景在哪些 segment_id 中出现"
    )


class StateChange(BaseModel):
    """角色/场景/道具的重大外观变化（受伤、换装、建筑损毁等剧情驱动的变化）"""
    original_name: str = Field(
        description="原始实体名，必须与 characters/locations/props 定义中的 name 精确一致"
    )
    entity_type: str = Field(
        description="实体类型：character / location / prop"
    )
    state_label: str = Field(
        description="简短状态描述，如「衬衫沾满油污」「屏幕出现裂纹」"
    )
    change_description: str = Field(
        description="相对于原始外观的变化描述，用于生图 prompt（描述 delta，不重复原始外观）"
    )
    first_segment_id: int = Field(
        description="从哪个 segment_id 开始生效"
    )
    last_segment_id: int = Field(
        description="到哪个 segment_id 结束（含），-1 表示持续到最后一段"
    )
    requires_sheet: bool = Field(
        description=(
            "是否需要生成衍生实体设定图。True = 变化后的外观成为后续核心情节的视觉锚点"
            "（如变身为全新形态、场景关键物体不可逆变化且后续情节围绕此展开）；"
            "False = 变化可用文字在 prompt 中精确描述（如流血、衣物破损、碎玻璃等），"
            "不需要单独生成设定图"
        ),
        default=True,
    )


class NarrativeStateOutput(BaseModel):
    """第一轮 LLM 输出：识别缺失场景和外观变化"""
    missing_locations: List[MissingLocation] = Field(
        description="narrative 中出现但未在 locations 定义中注册的场景列表",
        default_factory=list,
    )
    state_changes: List[StateChange] = Field(
        description="角色/场景/道具的重大外观变化列表（仅剧情驱动的重大变化）",
        default_factory=list,
    )


class StateAssignment(BaseModel):
    """Pass 2 对一条状态变化的判定"""
    index: int = Field(description="对应 Pass 1 输出列表中的索引（从 0 开始）")
    confirmed: bool = Field(description="是否确认该条变化有效，false 则整条丢弃")


class LocationValidationItem(BaseModel):
    """Pass 2 对一条缺失场景的判定"""
    index: int = Field(description="对应 Pass 1 输出列表中的索引（从 0 开始）")
    confirmed: bool = Field(description="是否确认该场景缺失有效，false 则整条丢弃")
    reason: str = Field(default="", description="判定理由")


class StateAssignmentOutput(BaseModel):
    """Pass 2 输出：逐条确认 Pass 1 的识别结果"""
    location_validations: List[LocationValidationItem] = Field(
        description="对 missing_locations 的逐条确认",
        default_factory=list,
    )
    change_assignments: List[StateAssignment] = Field(
        description="对 state_changes 的逐条确认",
        default_factory=list,
    )


# ═══════════════════════════════════════════════════════════════════════
#  Step 2 — Segment dependency grouping
# ═══════════════════════════════════════════════════════════════════════

class SegmentDependencyGroup(BaseModel):
    """一组需要串行生成视频的相邻 segment"""
    group_id: Optional[int] = Field(default=None, description="组编号，从 1 开始")
    segment_ids: List[int] = Field(
        description="组内 segment_id 列表，按顺序排列。组内视频串行生成，前一段的末尾画面作为后一段的参考"
    )
    reason: str = Field(
        description="为什么这些段需要串行（如「共享同一场景且人物站位需要延续」）"
    )


class SegmentGroupingOutput(BaseModel):
    """Step 2 LLM 输出：segment 依赖分组"""
    groups: List[SegmentDependencyGroup] = Field(
        description="所有 segment 的分组列表。每个 segment 必须恰好出现在一个 group 中。独立段落单独成组。"
    )


class ScreenplayMetadataSync(BaseModel):
    """Output of the metadata-sync LLM call: updated characters, locations, props."""
    characters: List[Character] = Field(
        description="根据最新叙述文本重新提取的完整角色列表，保留原有角色的描述风格"
    )
    locations: List[Location] = Field(
        description="根据最新叙述文本重新提取的完整场景列表，保留原有场景的描述风格"
    )
    props: List[Prop] = Field(
        description="根据最新叙述文本重新提取的完整道具列表（只保留横跨10秒以上不同段落反复出现的重要道具）",
        default_factory=list,
    )
