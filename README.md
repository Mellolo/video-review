# Showvi + DashScope 视频审核系统

基于 [Showvi](https://github.com/sjtuplayer/showvi) 框架 + 阿里云百炼 DashScope 的视频质量审核与参考图生成系统。

---

## 快速开始

```bash
cd showvi
source .venv/bin/activate

# 端到端：视频审核 + 参考图生成
python run_critique_and_ref.py \
    --video ~/Downloads/your_video.mp4 \
    --scene "场景描述文本" \
    --output ~/Downloads

# 仅审核（跳过参考图生成）
python run_critique_and_ref.py \
    --video ~/Downloads/your_video.mp4 \
    --scene "场景描述" \
    --output ~/Downloads \
    --skip-ref

# 启动 Web Dashboard
python main.py
# → http://localhost:8000
```

**前提**：`.env` 中的 `LLM_API_KEY` 已配置（默认已预填 DashScope Key）。

---

## 项目结构

```
video-review/                       # ← git 仓库根目录
├── README.md                       # 本文件
├── setup.md                        # 原始搭建指南
├── .gitignore
│
└── showvi/                         # 所有代码在此目录下
    ├── .env                        # ⚡ 唯一配置文件（API Key、模型选择）
    ├── .venv/                      # Python 虚拟环境
    ├── requirements.txt            # Python 依赖
    ├── README_CUSTOM.md            # 自定义系统说明
    │
    ├── run_critique_and_ref.py     # ⭐ 端到端编排脚本（审核+参考图）
    ├── main.py                     # Dashboard 入口
    ├── agent.py                    # 核心 Agent 逻辑
    ├── pipeline.py                 # 流程编排
    ├── config.py                   # 环境配置加载
    ├── models.py                   # 数据模型
    │
    ├── clients/                    # LLM/API 客户端
    │   ├── __init__.py             # 客户端工厂 get_llm_client()
    │   ├── base.py                 # Protocol 定义 & 异常
    │   ├── llm_client.py           # LLM/VLM 统一客户端
    │   └── custom/
    │       └── dashscope.py        # ⭐ DashScope 视频兼容 Plugin
    │
    ├── prompts/                    # Prompt 模板
    │   ├── critic.py               # 原版审核 prompt
    │   └── critic_animation.py     # ⭐ 动画审核 prompt（6维度+时间戳）
    │
    ├── tools/                      # 工具模块
    │   ├── critic.py               # 原版审核工具
    │   ├── critic_animation.py     # ⭐ 动画审核工具
    │   ├── reference_image_gen.py  # ⭐ 参考图生成（原帧编辑）
    │   └── ...
    │
    ├── utils/                      # 工具函数
    │   ├── ffmpeg.py               # ffprobe/ffmpeg 封装
    │   ├── io.py
    │   └── logger.py
    │
    ├── dashboard/                  # Web Dashboard
    │   ├── server.py
    │   └── ...
    │
    └── assets/                     # 静态资源
```

> **⭐ 标记** = 自定义新增文件（非 Showvi 原版）

---

## 配置

所有配置集中在 `showvi/.env` 一个文件中：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `LLM_API_KEY` | DashScope API Key | `sk-649c...` |
| `LLM_MODEL` | 文本任务模型 | `qwen3.7-plus` |
| `LLM_MODEL_VIDEO_CRITIQUE` | 视频审核模型 | `qwen-vl-max` |
| `LLM_MODEL_VIDEO_ANALYSIS` | 视频分析模型 | `qwen-vl-max` |
| `LLM_PROVIDER` | 文本任务 provider | `openai_compatible` |
| `LLM_PROVIDER_VIDEO_CRITIQUE` | 视频审核 provider | `custom:dashscope` |
| `LLM_BASE_URL` | DashScope API 地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `IMAGE_PROVIDER` | 图片生成 provider | `google` |
| `SEEDDANCE_SESSION_ID` | 即梦平台 Session（视频生成用） | 空 |

参考图生成模型不在 `.env` 中，通过命令行 `--model-image` 指定。

---

## 核心组件

### 1. DashScope Plugin — `clients/custom/dashscope.py`

DashScope 的 OpenAI 兼容模式对视频输入使用 `video_url` content type（非标准 `image_url`），此 plugin 正确处理该格式，避免 "image format is illegal" 错误。

- 类型：`PLUGIN_TYPE = "llm"`
- 类名：`DashScopeClient`
- 关键方法：`generate_with_video()` — 接受本地视频文件，base64 编码后以 `video_url` 格式发送

### 2. 动画审核 Prompt — `prompts/critic_animation.py`

6 维度评分体系 + 关键时间戳：

| 维度 | JSON 字段 | 说明 |
|------|-----------|------|
| 动作流畅度 | `motion_fluidity` | 运动质量、物理规律 |
| 角色一致性 | `character_consistency` | 外观、服装稳定性 |
| 场景还原度 | `scene_accuracy` | 对场景描述的还原 |
| 画面质量 | `visual_quality` | 清晰度、色彩、渲染 |
| 节奏控制 | `pacing_timing` | 动作节奏、镜头切换 |
| 艺术表现力 | `artistic_expression` | 构图、色彩、感染力 |

入口函数：`build_animation_critique_prompt(scene_description: str) -> str`

### 3. 动画审核工具 — `tools/critic_animation.py`

入口函数：`critique_animation_video(video_path, scene_description, ...) -> dict`

审核结果 JSON 结构：
```json
{
  "overall_score": 7.5,
  "motion_fluidity": 7.0,
  "character_consistency": 8.0,
  "scene_accuracy": 7.5,
  "visual_quality": 8.0,
  "pacing_timing": 7.0,
  "artistic_expression": 7.5,
  "critical_issues": [{"timestamp": "00:15", "dimension": "...", "description": "..."}],
  "minor_issues": [...],
  "strengths": ["..."],
  "recommendation": "RETRY",
  "feedback": "...",
  "critical_timestamps": ["00:15", "00:30"],
  "improvement_suggestions": "..."
}
```

辅助函数：
- `get_critical_timestamps(critique_data) -> List[str]` — 提取关键时间戳
- `timestamp_to_seconds(timestamp) -> float` — 时间戳转秒数

### 4. 参考图生成 — `tools/reference_image_gen.py`

入口函数：`generate_reference_image(critique_data, video_path, scene_description, ...) -> str`

工作流程：
1. 从审核结果提取关键时间戳
2. 用 ffmpeg 从视频提取该帧
3. 调用 DashScope 图片编辑 API（httpx）对帧进行优化
4. 下载并保存参考图

### 5. 端到端编排 — `run_critique_and_ref.py`

两阶段流程：
- **Phase 1**：视频审核 → 输出 `critique_result.json`
- **Phase 2**：参考图生成 → 输出 `reference_image_*.png`

参数：
```
--video       视频文件路径（必填）
--scene       场景描述文本（必填）
--output      输出目录（默认 ./output）
--model       审核模型（默认从 .env 读取）
--model-image 参考图模型（默认 qwen-image-2.0-pro-2026-06-22）
--timeout     API 超时秒数（默认 180）
--skip-ref    跳过参考图生成
--env         .env 文件路径（默认 .env）
```

---

## 模型选择

| 用途 | 配置项 | 可选值 | 推荐 |
|------|--------|--------|------|
| 文本推理 | `LLM_MODEL` | `qwen3.7-plus` / `qwen-turbo` / `qwen-max` | `qwen3.7-plus` |
| 视频审核 | `LLM_MODEL_VIDEO_CRITIQUE` | `qwen-vl-max` / `qwen3.7-plus` | `qwen-vl-max` |
| 视频分析 | `LLM_MODEL_VIDEO_ANALYSIS` | `qwen-vl-max` / `qwen3.7-plus` | `qwen-vl-max` |
| 参考图生成 | `--model-image` | `qwen-image-2.0-pro-2026-06-22` / `qwen-image-max` | `qwen-image-2.0-pro-2026-06-22` |

---

## 从零搭建

```bash
# 1. 克隆项目后进入 showvi 目录
cd showvi

# 2. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 3. 安装依赖
pip install openai httpx google-genai google-auth Pillow opencv-python numpy pyyaml
pip install fastapi uvicorn python-multipart

# 4. 配置 .env（已预填，仅需替换 API Key）
# 编辑 .env 中的 LLM_API_KEY

# 5. 验证
python -c "from clients import get_llm_client; print('✅ OK')"
ffmpeg -version | head -1
```

---

## 常见问题

| 问题 | 解决方案 |
|------|---------|
| `image format is illegal` | 确保 `.env` 中 `LLM_PROVIDER_VIDEO_CRITIQUE=custom:dashscope` |
| `ffmpeg: command not found` | `brew install ffmpeg`（macOS） |
| `ModuleNotFoundError: No module named 'tools'` | Python 3.9 兼容性问题，用 `importlib` 加载 |
| API 超时 | 检查 `curl -I https://dashscope.aliyuncs.com/compatible-mode/v1/models` |
| 图片生成报空 text | 确认 `reference_image_gen.py` 为 v2（原帧编辑模式） |
