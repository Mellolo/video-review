# Seeddance 工具 (`tools/seeddance.py`)

Agent 工具层的即梦视频生成工具，封装为 `SeeddanceImageToVideo`，注册在 `ALL_TOOL_CLASSES` 中供 Agent 自动发现和调用。

## 工具信息

| 属性 | 值 |
|------|-----|
| name | `seeddance_image_to_video` |
| category | `generator` |
| 底层客户端 | `clients/seeddance.py` → `SeeddanceClient` |

## 功能

从一张或多张参考图 + 文字 prompt 生成视频。

- **3.0 系列**：单图输入，5/10 秒
- **Seedance 2.0 系列**：多图输入，4-15 秒，prompt 中可用 `@图1` `@图2` 引用图片

## 参数

### 从 ExecutionContext 获取

| 字段 | 用途 | 备注 |
|------|------|------|
| `prompt` | 文字描述 | 可被 params 覆盖 |
| `duration_seconds` | 视频时长 | 可被 params 覆盖，默认 5 |
| `reference_image_path` | 首张参考图 | 当 params 中无 image_paths 时使用 |
| `output_dir` | 输出目录 | |
| `unit_id` | 片段编号 | 用于输出文件命名 |
| `attempt_number` | 尝试次数 | 用于输出文件命名 |

### 通过 `**params` 传入

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `image_paths` | `list[str]` | — | 图片路径列表，优先级高于 context |
| `prompt` | `str` | context.prompt | 覆盖 context 中的 prompt |
| `model` | `str` | `"3.0"` | 模型版本 |
| `duration` | `int` | context.duration_seconds | 视频时长（秒） |
| `aspect_ratio` | `str` | `"16:9"` | 画面比例 |

## 输出

输出文件路径格式：`{output_dir}/segment_{unit_id}_attempt_{attempt_number}.mp4`

返回 `ToolResult`：

```python
ToolResult(
    success=True,
    output_path="path/to/video.mp4",
    metadata={
        "prompt": "...",
        "image_paths": ["ref1.png", "ref2.png"],
        "duration": 5,
        "model": "3.0",
        "aspect_ratio": "16:9",
        "history_id": "...",
        "video_url": "https://...",
    },
)
```

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `SEEDDANCE_SESSION_ID` | 是 | 即梦官网 Cookie 中的 sessionid |

## 与其他工具的关系

```
ImageGen (生成参考图)
        │
        ▼  reference_image_path
SeeddanceImageToVideo (图 → 视频)
        │
        ▼  output video
GeminiCritic (质量评估)
        │
        ▼  critique feedback
PromptRewriter / SceneRewriter (修复重试)
```

典型工作流：先用 `ImageGen` 生成参考图，再用 `SeeddanceImageToVideo` 将参考图动画化为视频，最后由 `GeminiCritic` 评估质量。

## 调用示例

Agent 内部调用方式：

```python
from tools.seeddance import SeeddanceImageToVideo
from tools.base import ExecutionContext

tool = SeeddanceImageToVideo()
ctx = ExecutionContext(
    output_dir="./output",
    unit_id=1,
    attempt_number=0,
    prompt="机甲缓缓转身，陌刀寒光一闪",
    duration_seconds=5,
)

result = tool.execute(
    ctx,
    image_paths=["ref_mecha.png"],
    model="3.0",
    aspect_ratio="16:9",
)

if result.success:
    print(f"Video: {result.output_path}")
```

多图 (Seedance 2.0)：

```python
result = tool.execute(
    ctx,
    image_paths=["character.png", "background.png"],
    model="seedance-2.0-fast",
    duration=8,
)
```
