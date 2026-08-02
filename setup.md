# Showvi 项目搭建指南

> 本文档描述如何从零开始在任意机器上搭建 Showvi + DashScope 自定义审核系统。
> 按步骤执行，约 10-15 分钟可完成。

---

## ⚡ 快速配置（改这里就行）

> **所有需要修改的配置都集中在 `.env` 文件中。**
> 打开项目根目录的 `.env`，改下面两行即可：

### 🔑 API Key（第 3 行）

```
LLM_API_KEY=***REMOVED***
```

替换为你自己的 DashScope API Key。获取方式：[阿里云百炼控制台
](https: //dashscope.console.aliyun.com/) → API Key 管理 → 创建。

### 🤖 模型选择（第 4、11、12 行）

```
LLM_MODEL=qwen3.7-plus                    # 文本任务（剧本/分镜/改写）
LLM_MODEL_VIDEO_CRITIQUE=qwen-vl-max      # 视频审核
LLM_MODEL_VIDEO_ANALYSIS=qwen-vl-max      # 视频分析
```

**可选模型一览：**

| 用途 | 配置项 | 可选值 | 推荐 |
|---|---|---|---|
| 文本推理 | `LLM_MODEL` | `qwen3.7-plus` / `qwen-turbo` / `qwen-max` | `qwen3.7-plus` ✅ |
| 视频审核 | `LLM_MODEL_VIDEO_CRITIQUE` | `qwen-vl-max` / `qwen3.7-plus` | `qwen-vl-max` ✅ |
| 视频分析 | `LLM_MODEL_VIDEO_ANALYSIS` | `qwen-vl-max` / `qwen3.7-plus` | `qwen-vl-max` ✅ |
| 参考图生成 | `run_critique_and_ref.py --model-image` | `qwen-image-2.0-pro-2026-06-22` / `qwen-image-max` | `qwen-image-2.0-pro-2026-06-22` ✅ |

> 参考图生成模型不在 .env 中，通过命令行参数 `--model-image` 指定。

---

## 目录

- [快速配置
](#-快速配置改这里就行)
- [前置要求
](#前置要求)
- [Step 1: 克隆项目
](#step-1-克隆项目)
- [Step 2: 创建虚拟环境
](#step-2-创建虚拟环境)
- [Step 3: 安装依赖
](#step-3-安装依赖)
- [Step 4: 安装系统工具 (ffmpeg)
](#step-4-安装系统工具-ffmpeg)
- [Step 5: 配置环境变量
](#step-5-配置环境变量)
- [Step 6: 验证搭建
](#step-6-验证搭建)
- [Step 7: 运行审核流程
](#step-7-运行审核流程)
- [可选: 启动 Dashboard
](#可选-启动-dashboard)
- [常见问题排查
](#常见问题排查)

---

## 前置要求

| 项目 | 要求 | 说明 |
|---|---|---|
| 操作系统 | macOS / Linux / Windows (WSL) | |
| Python | **3.9+** | 推荐 3.11+，3.9 有兼容性问题（见 FAQ） |
| Git | 任意版本 | |
| ffmpeg | 任意版本 | 参考图生成需要（提取视频帧） |
| 网络 | 可访问 `dashscope.aliyuncs.com` | |
| API Key | 阿里云百炼 DashScope API Key | [获取地址
](https: //dashscope.console.aliyun.com/) |

---

## Step 1: 克隆项目

```bash
# 方式 A: 从 GitHub 克隆原版 Showvi
git clone https: //github.com/sjtuplayer/showvi.git
cd showvi

# 方式 B: 从打包 JSON 恢复（如果有 showvi.json）
python -c "
import json, base64, zipfile, io, os
with open('showvi.json', 'r') as f:
    data = json.load(f)
zip_bytes = base64.b64decode(data['data_base64'
])
with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
    zf.extractall('.')
print(f'已恢复 {data[\"n_files\"]} 个文件')
"
cd showvi
```

---

## Step 2: 创建虚拟环境

```bash
# 创建虚拟环境
python3 -m venv .venv

# 激活虚拟环境
source .venv/bin/activate  # macOS/Linux
# Windows: .venv\Scripts\activate

# 验证
python --version  # 应显示 3.9+
```

---

## Step 3: 安装依赖

```bash
# 核心依赖（必装）
pip install openai httpx google-genai google-auth Pillow opencv-python numpy pyyaml

# Web Dashboard 依赖（可选）
pip install fastapi uvicorn pydantic python-multipart

# 完整依赖（一键安装）
pip install -r requirements.txt
```

**依赖说明：**

| 包 | 用途 |
|---|---|
| `openai` | DashScope OpenAI 兼容模式调用 |
| `httpx` | DashScope 原生图片编辑 API |
| `google-genai` | Gemini 图片生成（可选） |
| `Pillow`, `opencv-python`, `numpy` | 图片/视频处理 |
| `pyyaml` | 配置文件解析 |
| `fastapi`, `uvicorn` | Web Dashboard |

---

## Step 4: 安装系统工具 (ffmpeg)

ffmpeg 用于从视频中提取帧（参考图生成必需）。

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt update && sudo apt install ffmpeg

# CentOS/RHEL
sudo yum install ffmpeg

# 验证
ffmpeg -version
```

---

## Step 5: 配置环境变量

### 5.1 编辑 .env（唯一配置文件）

> **所有配置都在 `.env` 这一个文件里，改它就够了。**

```bash
# ── 🔑 API Key（改这里）──
LLM_API_KEY=***REMOVED***

# ── 🤖 模型选择（改这里切换模型）──
LLM_MODEL=qwen3.7-plus
LLM_MODEL_VIDEO_CRITIQUE=qwen-vl-max
LLM_MODEL_VIDEO_SELECT=qwen-vl-max
LLM_MODEL_VIDEO_ANALYSIS=qwen-vl-max

# ── 以下一般不需要改 ──
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https: //dashscope.aliyuncs.com/compatible-mode/v1

LLM_PROVIDER_VIDEO_CRITIQUE=custom:dashscope
LLM_PROVIDER_VIDEO_SELECT=custom:dashscope
LLM_PROVIDER_VIDEO_ANALYSIS=custom:dashscope

LLM_MODEL_SCREENPLAY_GEN=qwen3.7-plus
LLM_MODEL_STORYBOARD_GEN=qwen3.7-plus
LLM_MODEL_PROMPT_REWRITE=qwen3.7-plus
LLM_MODEL_SCENE_REWRITE=qwen3.7-plus
LLM_MODEL_SCENE_EDIT=qwen3.7-plus
LLM_MODEL_STYLE_CHECK=qwen3.7-plus

IMAGE_PROVIDER=google
IMAGE_MODEL=gemini-2.0-flash-preview-image-generation

SEEDDANCE_SESSION_ID=
SEEDDANCE_BACKEND=jimeng
```

### 5.2 获取 API Key

1. 访问 [阿里云百炼控制台
        ](https: //dashscope.console.aliyun.com/)
2. 注册/登录阿里云账号
3. 进入「API Key 管理」创建新 Key
4. 将 `sk-xxx` 填入 `.env` 的 `LLM_API_KEY`

### 5.3 确认自定义 Plugin 存在

确保以下文件存在：

```
showvi/
├── clients/
│   └── custom/
│       ├── __init__.py
│       ├── dashscope.py      # DashScope 视频兼容 Plugin
│       └── README.md
├── prompts/
│   └── critic_animation.py   # 动画审核 prompt
└── tools/
    ├── critic_animation.py   # 动画审核工具
    └── reference_image_gen.py # 参考图生成工具
```

如果这些文件缺失，需要从打包 JSON 中恢复，或手动创建（见 `README_CUSTOM.md`）。

---

## Step 6: 验证搭建

### 6.1 验证 Python 环境

```bash
source .venv/bin/activate

python << 'EOF'
import openai, httpx
print("✅ 核心依赖已安装")

from clients import get_llm_client
print("✅ Showvi clients 模块可导入")

import importlib.util
spec = importlib.util.spec_from_file_location("ref",
        "tools/reference_image_gen.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print("✅ 参考图生成模块可导入")
EOF
```

### 6.2 验证 API 连通

```bash
python << 'EOF'
import os
from pathlib import Path

for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ[k.strip()
        ] = v.strip()

from openai import OpenAI
client = OpenAI(
    api_key=os.environ[
            "LLM_API_KEY"
        ],
    base_url=os.environ[
            "LLM_BASE_URL"
        ],
)

resp = client.chat.completions.create(
    model="qwen3.7-plus",
    messages=[
            {
                "role": "user",
                "content": "说'搭建成功'"
            }
        ],
    max_tokens=10,
)
print(f"✅ API 连通: {resp.choices[0].message.content}")
EOF
```

### 6.3 验证 ffmpeg

```bash
ffmpeg -version | head -1
```

---

## Step 7: 运行审核流程

### 7.1 端到端运行

```bash
source .venv/bin/activate

python run_critique_and_ref.py \
    --video ~/Downloads/normal_video.mp4 \
    --scene "场景描述" \
    --output ~/Downloads
```

**预期输出：**

```
============================================================
Phase 1: 视频审核
============================================================
审核完成: 7.5/10 (RETRY)，耗时 28.4s
审核报告 → ~/Downloads/critique_result.json

============================================================
Phase 2: 参考图生成（基于原视频帧编辑）
============================================================
[REF IMAGE
        ] Critical timestamp: 00: 35
[REF IMAGE
        ] Done in 15.9s → ~/Downloads/reference_image_*.png

============================================================
流程完成
============================================================
```

### 7.2 单独运行审核

```python
import os, json
from pathlib import Path
for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ[k.strip()
        ] = v.strip()

os.environ[
            "LLM_PROVIDER"
        ] = "custom:dashscope"
from clients import get_llm_client
from prompts.critic_animation import build_animation_critique_prompt

client = get_llm_client(step="video_critique")
result = client.generate_with_video(
    text_prompt="Evaluate this video.",
    video_paths=[
            "你的视频路径.mp4"
        ],
    system_instruction=build_animation_critique_prompt("场景描述"),
    temperature=0.3,
    model="qwen-vl-max",
    timeout_seconds=180,
)
print(json.dumps(json.loads(result), ensure_ascii=False, indent=2))
```

### 7.3 单独运行参考图生成

```python
import json
from tools.reference_image_gen import generate_reference_image

with open("critique_result.json",
        "r") as f:
    critique_data = json.load(f)

path = generate_reference_image(
    critique_data=critique_data,
    video_path="你的视频路径.mp4",
    scene_description="场景描述",
    output_dir="./output",
)
print(f"参考图: {path}")
```

---

## 可选: 启动 Dashboard

```bash
source .venv/bin/activate
python main.py
# 访问 http: //localhost:8000
```

> 注意：Dashboard 的视频生成功能需要配置 `SEEDDANCE_SESSION_ID`（即梦平台 Session），审核功能不需要。

---

## 常见问题排查

### Q1: `ModuleNotFoundError: No module named 'tools'`

**原因：** Python 3.9 下 `tools/seeddance.py` 使用了 `list | None` 语法（3.10+）。

**解决：** 用 `importlib` 直接加载自定义模块：

```python
import importlib.util
spec = importlib.util.spec_from_file_location("mod",
        "tools/xxx.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
```

### Q2: 视频审核报 "image format is illegal"

**解决：** 确保 `.env` 中视频审核使用 `custom:dashscope` plugin：

```
LLM_PROVIDER_VIDEO_CRITIQUE=custom:dashscope
```

### Q3: 图片生成报 "Field 'text' cannot be an empty string"

**解决：** `reference_image_gen.py` v2 已改为基于原帧编辑模式，确认是最新版本。

### Q4: `ffmpeg: command not found`

**解决：** 安装 ffmpeg（见 Step 4）。

### Q5: API 调用超时

```bash
curl -I https: //dashscope.aliyuncs.com/compatible-mode/v1/models
```

---

## 项目结构总览

```
showvi/
├── .env                          # ⚡ 唯一配置文件（API Key、模型选择都在这里）
├── .venv/                        # Python 虚拟环境
├── requirements.txt              # Python 依赖
├── main.py                       # Dashboard 入口
├── agent.py                      # 核心 Agent 逻辑
├── pipeline.py                   # 流程编排
│
├── clients/                      # LLM 客户端
│   ├── custom/
│   │   ├── dashscope.py          # ⭐ DashScope 视频兼容 Plugin
│   │   └── README.md
│   ├── llm_client.py             # 客户端工厂
│   └── ...
│
├── prompts/                      # Prompt 模板
│   ├── critic_animation.py       # ⭐ 动画审核 prompt（6维度 + 时间戳）
│   └── ...
│
├── tools/                        # 工具模块
│   ├── critic_animation.py       # ⭐ 动画审核工具
│   ├── reference_image_gen.py    # ⭐ 参考图生成（基于原帧编辑）
│   └── ...
│
├── scripts/
│   └── pack_to_json.py           # 项目打包工具
│
├── run_critique_and_ref.py       # ⭐ 端到端编排脚本
├── README_CUSTOM.md              # ⭐ 自定义系统文档
└── SETUP_GUIDE.md                # 本文档
```

**⭐ 标记** = 自定义新增文件（非 Showvi 原版）

---

## 快速检查清单

- [] `python --version` → 3.9+
- [] `ffmpeg -version` → 任意版本
- [] `.venv/` 存在且已激活
- [] `pip list | grep openai` → 有输出
- [] `.env` 中 `LLM_API_KEY` 已填写
- [] `clients/custom/dashscope.py` 存在
- [] `prompts/critic_animation.py` 存在
- [] `tools/reference_image_gen.py` 存在
- [] API 连通测试通过（Step 6.2）
- [] 端到端流程可运行（Step 7.1）

全部通过 = 搭建成功。
