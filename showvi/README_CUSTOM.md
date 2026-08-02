# Showvi + DashScope 自定义审核系统

基于 Showvi 框架 + 阿里云百炼 DashScope 的视频审核与参考图生成系统。

## ⭐ 自定义新增文件

以下文件为非 Showvi 原版的自定义新增：

```
showvi/
├── clients/custom/
│   └── dashscope.py              # DashScope 视频兼容 Plugin
├── prompts/
│   └── critic_animation.py      # 动画审核 prompt（6维度 + 时间戳）
├── tools/
│   ├── critic_animation.py      # 动画审核工具
│   └── reference_image_gen.py    # 参考图生成（基于原帧编辑）
├── run_critique_and_ref.py       # 端到端编排脚本
├── .env                          # 唯一配置文件
└── README_CUSTOM.md              # 本文档
```

## 快速开始

### 1. 配置 .env

编辑 `.env`，填入你的 DashScope API Key：

```
LLM_API_KEY=sk-your-api-key-here
```

获取方式：[阿里云百炼控制台](https://dashscope.console.aliyun.com/) → API Key 管理 → 创建

### 2. 运行端到端流程

```bash
source .venv/bin/activate

python run_critique_and_ref.py \
    --video ~/Downloads/your_video.mp4 \
    --scene "场景描述文本" \
    --output ~/Downloads
```

### 3. 预期输出

```
Phase 1: 视频审核
审核完成: 7.5/10 (RETRY)，耗时 28.4s
审核报告 → ~/Downloads/critique_result.json

Phase 2: 参考图生成（基于原视频帧编辑）
[REF IMAGE] Critical timestamp: 00:35
[REF IMAGE] Done in 15.9s → ~/Downloads/reference_image_*.png

流程完成
```

## 组件说明

### DashScope Plugin (`clients/custom/dashscope.py`)

DashScope 的 OpenAI 兼容模式对视频输入使用 `video_url` content type，
而非标准 OpenAI 的 `image_url`。此 plugin 正确处理 DashScope 的视频格式，
避免 "image format is illegal" 错误。

- **PLUGIN_TYPE**: `llm`
- **PLUGIN_CLASS**: `DashScopeClient`
- **支持方法**: `generate_text`, `generate_with_vision`, `generate_with_video`, `chat_completion`
- **默认模型**: `qwen-vl-max`

配置方式：
```
LLM_PROVIDER_VIDEO_CRITIQUE=custom:dashscope
LLM_MODEL_VIDEO_CRITIQUE=qwen-vl-max
```

### 动画审核 Prompt (`prompts/critic_animation.py`)

6 维度评分体系 + 关键时间戳标记：

| 维度 | 字段 | 说明 |
|------|------|------|
| 动作流畅度 | `motion_fluidity` | 运动质量、物理规律 |
| 角色一致性 | `character_consistency` | 外观、服装稳定性 |
| 场景还原度 | `scene_accuracy` | 对场景描述的还原 |
| 画面质量 | `visual_quality` | 清晰度、色彩、渲染 |
| 节奏控制 | `pacing_timing` | 动作节奏、镜头切换 |
| 艺术表现力 | `artistic_expression` | 构图、色彩、感染力 |

审核结果包含 `critical_timestamps` 字段，标注关键问题出现的时间点。

### 参考图生成 (`tools/reference_image_gen.py`)

基于原视频帧编辑模式：

1. 从审核结果中提取关键时间戳
2. 用 ffmpeg 从视频提取该帧
3. 调用 DashScope 图片编辑 API（httpx）对帧进行优化
4. 保存参考图

图片模型通过 `--model-image` 参数指定：
- `qwen-image-2.0-pro-2026-06-22`（推荐）
- `qwen-image-max`

## 模型配置

| 用途 | 配置项 | 可选值 | 推荐 |
|------|--------|--------|------|
| 文本推理 | `LLM_MODEL` | `qwen3.7-plus` / `qwen-turbo` / `qwen-max` | `qwen3.7-plus` |
| 视频审核 | `LLM_MODEL_VIDEO_CRITIQUE` | `qwen-vl-max` / `qwen3.7-plus` | `qwen-vl-max` |
| 视频分析 | `LLM_MODEL_VIDEO_ANALYSIS` | `qwen-vl-max` / `qwen3.7-plus` | `qwen-vl-max` |
| 参考图生成 | `--model-image` | `qwen-image-2.0-pro-2026-06-22` / `qwen-image-max` | `qwen-image-2.0-pro-2026-06-22` |

## 独立使用各组件

### 单独运行审核

```python
import os
from pathlib import Path

# 加载 .env
for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ[k.strip()] = v.strip()

os.environ["LLM_PROVIDER"] = "custom:dashscope"
from clients import get_llm_client
from prompts.critic_animation import build_animation_critique_prompt

client = get_llm_client(step="video_critique")
result = client.generate_with_video(
    text_prompt="Evaluate this video.",
    video_paths=["your_video.mp4"],
    system_instruction=build_animation_critique_prompt("场景描述"),
    temperature=0.3,
    model="qwen-vl-max",
    timeout_seconds=180,
)
print(result)
```

### 单独运行参考图生成

```python
import json
from tools.reference_image_gen import generate_reference_image

with open("critique_result.json", "r") as f:
    critique_data = json.load(f)

path = generate_reference_image(
    critique_data=critique_data,
    video_path="your_video.mp4",
    scene_description="场景描述",
    output_dir="./output",
)
print(f"参考图: {path}")
```

## 常见问题

### Q: 视频审核报 "image format is illegal"

确保 `.env` 中视频审核使用 `custom:dashscope` plugin：
```
LLM_PROVIDER_VIDEO_CRITIQUE=custom:dashscope
```

### Q: 图片生成报 "Field 'text' cannot be an empty string"

`reference_image_gen.py` v2 已改为基于原帧编辑模式，确认是最新版本。

### Q: `ffmpeg: command not found`

安装 ffmpeg：
```bash
brew install ffmpeg  # macOS
```

### Q: API 调用超时

检查网络连通性：
```bash
curl -I https://dashscope.aliyuncs.com/compatible-mode/v1/models
```

### Q: Python 3.9 兼容性问题

使用 `importlib` 直接加载自定义模块：
```python
import importlib.util
spec = importlib.util.spec_from_file_location("mod", "tools/xxx.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
```
