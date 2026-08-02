# Showvi + DashScope 视频审核系统

基于 [Showvi](https://github.com/sjtuplayer/showvi) 框架 + 阿里云百炼 DashScope 的视频质量审核与参考图生成系统。

---

## 快速开始

```bash
cd showvi
source .venv/bin/activate

# 完整流程：审核 + 参考图 + 汇总报告（一条命令）
python run_critique_and_ref.py \
    --video 视频审核/测试视频1/your_video.mp4 \
    --scene "场景描述文本" \
    --output 视频审核/测试视频1

# 仅审核（跳过参考图和报告）
python run_critique_and_ref.py \
    --video your_video.mp4 \
    --scene "场景描述" \
    --output 视频审核/测试视频1 \
    --skip-ref --skip-report

# 从已有 session 续跑（跳过审核，只生成参考图和报告）
python run_critique_and_ref.py \
    --video your_video.mp4 \
    --scene "场景描述" \
    --session 视频审核/测试视频1/session_20260802_184651 \
    --skip-critique
```

**前提**：`showvi/.env` 中的 `LLM_API_KEY` 已配置。

---

## 三大独立工具

三个工具可单独使用，也可通过编排脚本串联：

| # | 工具 | 入口文件 | 产出 |
|---|------|----------|------|
| 1 | **视频审核** | `tools/critic_animation.py` | `critique_result.json` |
| 2 | **参考图生成** | `tools/reference_image_gen.py` | `frames/` + `references/` |
| 3 | **汇总报告** | `tools/report_generator.py` | `report.md` |
| - | **编排器** | `run_critique_and_ref.py` | 串联 1→2→3 |

### 独立调用

```bash
# 1. 审核
python tools/critic_animation.py \
    --video input.mp4 \
    --scene "场景描述" \
    --output session/critique_result.json

# 2. 参考图
python tools/reference_image_gen.py \
    --critique session/critique_result.json \
    --video input.mp4 \
    --scene "场景描述" \
    --output session/

# 3. 报告
python tools/report_generator.py \
    --session session/
```

---

## Session 目录结构

每次审核自动创建一个 session 目录，包含所有产物：

```
视频审核/测试视频1/
├── your_video.mp4                        # 原视频（不动）
└── session_20260802_184651/              # ← 自动创建
    ├── scene_description.txt             # 场景描述
    ├── critique_result.json              # Phase 1: 审核 JSON
    ├── frames/                            # Phase 2: 原始帧
    │   ├── frame_00s12.png
    │   └── frame_00s35.png
    ├── references/                       # Phase 2: 参考图
    │   ├── reference_00s12_xxx.png
    │   └── reference_00s35_xxx.png
    └── report.md                         # Phase 3: 结构化报告
```

---

## 审核维度（7 大维度）

| 维度 | JSON 字段 | 检查要点 |
|------|-----------|----------|
| 动作流畅度 | `motion_fluidity` | 卡顿/跳帧、物理规律、表情连贯 |
| 角色一致性 | `character_consistency` | 面部/服装稳定、特征突变 |
| 场景还原度 | `scene_accuracy` | 关键元素、剧情节点、氛围 |
| 画面质量 | `visual_quality` | 清晰度、色彩、噪点/畸变 |
| 节奏与时间控制 | `pacing_timing` | 动作节奏、镜头切换 |
| 艺术表现力 | `artistic_expression` | 构图、色彩、感染力 |
| 模型穿模 | `model_clipping` | 道具穿透、身体穿插、环境穿模 |

**评分标准**：0-10 分 | `>= 7.0 → ACCEPT` | `4.0-6.9 → RETRY` | `< 4.0 → REJECT`

---

## 项目结构

```
video-review/                       # ← git 仓库根目录
├── README.md                       # 本文件
├── setup.md                        # 原始搭建指南
├── .gitignore
├── 视频审核/                       # 工作目录（测试视频 + session 产物）
│
└── showvi/                         # 所有代码在此目录下
    ├── .env                        # ⚡ 唯一配置文件
    ├── .venv/                      # Python 虚拟环境
    │
    ├── run_critique_and_ref.py     # ⭐ 编排器（串联三工具）
    ├── main.py                     # Dashboard 入口
    │
    ├── clients/
    │   └── custom/
    │       └── dashscope.py        # ⭐ DashScope 视频兼容 Plugin
    │
    ├── prompts/
    │   └── critic_animation.py     # ⭐ 审核 Prompt（7维度+时间戳）
    │
    └── tools/                      # ⭐ 三大工具
        ├── critic_animation.py     #   1. 视频审核
        ├── reference_image_gen.py  #   2. 参考图生成
        └── report_generator.py     #   3. 汇总报告
```

---

## 配置

所有配置集中在 `showvi/.env`：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `LLM_API_KEY` | DashScope API Key | 预填 |
| `LLM_MODEL` | 文本任务模型 | `qwen3.7-plus` |
| `LLM_MODEL_VIDEO_CRITIQUE` | 视频审核模型 | `qwen-vl-max` |
| `LLM_MODEL_IMAGE` | 参考图生成模型 | `qwen-image-2.0-pro-2026-06-22` |
| `LLM_PROVIDER_VIDEO_CRITIQUE` | 视频审核 provider | `custom:dashscope` |
| `LLM_BASE_URL` | DashScope API 地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |

---

## 模型选择

| 用途 | 配置项 | 推荐 |
|------|--------|------|
| 视频审核 | `LLM_MODEL_VIDEO_CRITIQUE` | `qwen-vl-max` |
| 参考图生成 | `LLM_MODEL_IMAGE` | `qwen-image-2.0-pro-2026-06-22` |
| 文本推理 | `LLM_MODEL` | `qwen3.7-plus` |

换模型改 `.env` 即可，无需改代码。

---

## 编排器参数

```bash
python run_critique_and_ref.py \
    --video       视频路径（必填）
    --scene       场景描述（必填）
    --output      视频所在目录（自动创建 session 子目录）
    --session     指定已有 session（用于续跑）
    --model       审核模型（默认 .env）
    --model-image 参考图模型（默认 .env）
    --timeout     审核超时秒数（默认 300）
    --skip-critique  跳过审核
    --skip-ref       跳过参考图
    --skip-report    跳过报告
    --env         .env 路径（默认 .env）
```

---

## 从零搭建

```bash
cd showvi
python3 -m venv .venv
source .venv/bin/activate
pip install openai httpx google-genai google-auth Pillow opencv-python numpy pyyaml
pip install fastapi uvicorn python-multipart
# 编辑 .env 配置 LLM_API_KEY
python -c "from clients import get_llm_client; print('✅ OK')"
ffmpeg -version | head -1
```

---

## 常见问题

| 问题 | 解决方案 |
|------|---------|
| `image format is illegal` | `.env` 中 `LLM_PROVIDER_VIDEO_CRITIQUE=custom:dashscope` |
| `ffmpeg: command not found` | `brew install ffmpeg` |
| API 超时 | 增加 `--timeout` 或检查网络 |
| 参考图与原帧差异大 | 已关闭 `prompt_extend`，如仍偏差可调 `tools/reference_image_gen.py` 的 `_build_edit_prompt()` |
| 审核维度不匹配 | 修改 `prompts/critic_animation.py` 的维度定义 |
