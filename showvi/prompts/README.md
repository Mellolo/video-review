# Prompts 目录说明

本目录包含项目中所有 LLM System Prompt 的定义。按功能阶段分为三大类：**剧本生成**、**视频生成**、**后处理与辅助**。

---

## 一、剧本生成阶段（Storyboard Generation）

### `prompt_engine.py` — 创意简述 → 剧本

| 函数/常量 | 调用方 | 阶段 | 说明 |
|-----------|--------|------|------|
| `build_prompt_engine_system_prompt()` | `PromptStoryboardEngine` | Phase 1 | 用户给一句创意描述，LLM 一次性生成完整剧本（角色、场景、道具、叙事、故事弧线）。输出 schema: `ScreenplaySchema` |

### `novel_engine.py` — 小说章节 → 剧本

| 函数/常量 | 调用方 | 阶段 | 说明 |
|-----------|--------|------|------|
| `build_analyze_chapter_prompt()` | `NovelStoryboardEngine._analyze_chapter()` | Phase 1 Step 1 | 分析小说章节，提取角色/场景/道具，切分叙事段落。输出 schema: `ChapterAnalysis` |
| `build_full_narrative_prompt()` | `NovelStoryboardEngine._generate_full_narrative()` | Phase 1 Step 2（短章节） | 整章一次性改写为剧本叙述。输出 schema: `ScreenplayNarrativeOutput` |
| `build_segment_narrative_prompt()` | `NovelStoryboardEngine._generate_segment_narrative()` | Phase 1 Step 2（长章节） | 逐段改写小说为剧本叙述。输出 schema: `SegmentNarrative` |

### `storyboard_gen.py` — 剧本 → 分镜（Phase 2 共享管线）

所有引擎（Novel/Prompt/Video）在 Phase 2 共用这些 prompt。

| 函数/常量 | 调用方 | 阶段 | 说明 |
|-----------|--------|------|------|
| `build_segment_narrative_prompt()` | `BaseStoryboardEngine._segment_narrative()` | Phase 2 Step 1「段落切分」 | 将 narrative 按叙事边界拆分为 8-15 秒的视频段落。输出 schema: `NarrativeSegmentation` |
| `build_state_tracker_prompt()` | `BaseStoryboardEngine._track_narrative_states()` | Phase 2 Step 3 Round 1「状态追踪」 | 识别缺失场景 + 角色/场景/道具的重大外观变化。输出 schema: `NarrativeStateOutput` |
| `build_state_validation_prompt()` | `BaseStoryboardEngine._track_narrative_states()` | Phase 2 Step 3 Round 2「验证确认」 | 对 Round 1 结果逐条验证，过滤 LLM 幻觉。输出 schema: `StateValidationOutput` |
| `build_segment_grouping_prompt()` | `BaseStoryboardEngine._group_segments()` | Phase 2 Step 2「依赖分组」 | 判断相邻段落的空间连续性依赖，分组（组内串行，组间并行）。输出 schema: `SegmentGroupingOutput` |
| `build_batch_fluent_prompt_system()` | `BaseStoryboardEngine._generate_fluent_prompts_batch()` | Phase 2 Step 5「批量生成 seedance prompt」 | 为所有段落生成连贯叙述式 seedance prompt + continuity_anchor。输出 schema: `BatchFluentSeedanceOutput` |
| `build_fluent_continuity_director_prompt()` | `BaseStoryboardEngine._rewrite_fluent_prompts_for_continuity()` | Phase 2 Step 6「全局连续性重写」 | 统筹所有段落 prompt，减少相邻段视觉跳变。输出 schema: `ContinuityRewriteOutput` |

### `story_review.py` — 分镜审查与修复

| 函数/常量 | 调用方 | 阶段 | 说明 |
|-----------|--------|------|------|
| `_REVIEW_SYSTEM_PROMPT` | 分镜审查流程 | Phase 2 后「质量审查」 | 从 19 个维度审查分镜质量（场景跳变、剧情连贯、对白合理、戏剧强度等），输出问题列表 |
| `_FIX_SYSTEM_PROMPT` | 分镜修复流程 | Phase 2 后「自动修复」 | 根据审查问题列表修复分镜（修改/插入场景、补充角色/道具、清理外貌描述、统一光照等） |

---

## 二、视频生成阶段（Video Production）

### `critic.py` — 视频质量评估

| 函数/常量 | 调用方 | 阶段 | 说明 |
|-----------|--------|------|------|
| `CRITIC_SYSTEM_PROMPT` / `build_critique_prompt()` | 视频评估流程 | 视频生成后 | 从 4 个维度评估生成视频质量（内容准确度、角色忠实度、画面质量、艺术表现力），输出评分和 ACCEPT/RETRY/REJECT 决策 |

### `video_selector.py` — 视频素材选择

| 函数/常量 | 调用方 | 阶段 | 说明 |
|-----------|--------|------|------|
| `VIDEO_SELECTOR_SYSTEM` | 视频选择流程 | 视频生成后 | 从多段候选视频中选最佳素材，评估维度：主体一致性、音频正确性、画面质量、内容准确性 |

### `video_editor.py` — VLM 视频片段确认

| 函数/常量 | 调用方 | 阶段 | 说明 |
|-----------|--------|------|------|
| `build_vlm_confirm_prompt()` | 视频剪辑流程 | 视频后处理 | 根据分镜描述从候选视频片段中选择最匹配的一个 |

### `transition_bridge.py` — 场景过渡

| 函数/常量 | 调用方 | 阶段 | 说明 |
|-----------|--------|------|------|
| `PREV_PLOT_SUMMARY_SYSTEM` | 过渡桥接工具 | 视频拼接 | 总结前序场景剧情为一段话（≤100字） |
| `TRANSITION_PROMPT_SYSTEM` | 过渡桥接工具 | 视频拼接 | 根据上一帧画面 + 前序剧情 + 后续 prompt，设计丝滑过渡描述（≤50字，必须是完成态/静态描写） |

---

## 三、后处理与辅助（Post-processing & Utilities）

### `rewriter.py` — Prompt 重写与场景简化

| 函数/常量 | 调用方 | 阶段 | 说明 |
|-----------|--------|------|------|
| `PROMPT_REWRITER_SYSTEM` | Prompt 重写工具 | 视频生成失败时 | 优化 Sora/Seedance prompt 修复质量问题（面部畸变、运动问题、风格不一致等），最小改动原则 |
| `SCENE_REWRITER_SYSTEM` | 场景编辑工具 | 视频生成失败时 | 将复杂场景 simplify/replace/split 为 AI 可生成的版本 |

### `scene_editor.py` — 分镜编辑

| 函数/常量 | 调用方 | 阶段 | 说明 |
|-----------|--------|------|------|
| `build_seedance_system_prompt()` | Dashboard 分镜编辑 | 用户手动编辑 | 为单个分镜生成 seedance prompt（镜头切换、角色动作、台词处理） |
| `build_refine_scene_system_prompt()` | Dashboard 分镜编辑 | 用户手动编辑 | 根据用户修改意见重写单个分镜的 prompt，兼顾前后段连续性 |

### `image_gen.py` — 图片生成相关

| 函数/常量 | 调用方 | 阶段 | 说明 |
|-----------|--------|------|------|
| `_IMAGE_SAFETY_REWRITE_SYSTEM` | 图片生成安全过滤 | 设定图生成 | 改写被安全过滤器拦截的 prompt（暴力软化、年龄提升、去除敏感词） |
| `_PROMPT_FAILURE_ANALYSIS_SYSTEM` | 图片生成失败分析 | 设定图生成 | 诊断 prompt 被拦截的原因（IP 侵权、暴力、敏感内容等），逐条修复 |
| `CHARSHEET_TEMPLATE` | 角色设定图生成 | 设定图生成 | 生成角色 2×2 四视图设定图（特写/正面/侧面/背面） |
| `LOCATION_SHEET_TEMPLATE` | 场景设定图生成 | 设定图生成 | 生成场景 2×2 四视图设定图（正面/背面/侧面/俯视） |
| `PROP_SHEET_TEMPLATE` | 道具设定图生成 | 设定图生成 | 生成道具 2×2 四视图设定图 |
| `DERIVED_CHARSHEET_TEMPLATE` | 派生角色设定图 | 设定图生成 | 基于原始角色参考图，生成外观变化后的新设定图（如受伤、变身） |
| `DERIVED_LOCATION_SHEET_TEMPLATE` | 派生场景设定图 | 设定图生成 | 基于原始场景参考图，生成变化后的新环境设定图（如损毁、火灾） |
| `DERIVED_PROP_SHEET_TEMPLATE` | 派生道具设定图 | 设定图生成 | 基于原始道具参考图，生成变化后的新外观设定图 |

### `style_checker.py` — 风格一致性检查

| 函数/常量 | 调用方 | 阶段 | 说明 |
|-----------|--------|------|------|
| `_VERIFY_SYSTEM` | 风格一致性检查器 | 设定图生成后 | 审核生成图片是否匹配 prompt（艺术风格 > 布局构图 > 主体内容 > 色调光影 > 细节完整度） |

### `narrative_refine.py` — 叙事微调

| 函数/常量 | 调用方 | 阶段 | 说明 |
|-----------|--------|------|------|
| `NARRATIVE_REFINE_SYSTEM` / `NARRATIVE_REFINE_TEMPLATE` | 叙事编辑 | 用户手动编辑 | 根据用户反馈修改叙事文本 |

### `__init__.py`

空文件，各消费方直接从子模块导入（如 `from prompts.critic import ...`）。

---

## 整体流程对照

```
用户输入
  │
  ├─ 创意简述 → prompt_engine.py (Phase 1)
  ├─ 小说章节 → novel_engine.py (Phase 1)
  └─ 参考视频 → video_engine (prompt 在引擎内部)
        │
        ▼
   Screenplay JSON
        │
        ▼
   storyboard_gen.py (Phase 2: Step A → A.5 → A.6 → B → C)
        │
        ▼
   story_review.py (审查 + 修复)
        │
        ▼
   Storyboard JSON
        │
        ▼
   ┌─ image_gen.py (设定图生成)
   │    └─ style_checker.py (风格校验)
   │
   ├─ director.py (视频生成决策)
   │    ├─ critic.py (质量评估)
   │    ├─ rewriter.py (失败重写)
   │    └─ scene_editor.py (分镜编辑)
   │
   ├─ video_selector.py (素材选择)
   ├─ video_editor.py (片段确认)
   └─ transition_bridge.py (过渡衔接)
        │
        ▼
   最终视频
```
