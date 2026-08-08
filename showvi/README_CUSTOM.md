# 本仓库相对上游 Showvi 的定制说明

> **日常如何审核视频**：请看仓库根目录 [`../README.md`](../README.md)。  
> 本文只说明「相对上游多改了什么」，避免和根 README 重复、过时。

## 定制文件

```
showvi/
├── clients/custom/dashscope.py     # DashScope 视频兼容 LLM Plugin
├── prompts/critic_animation.py     # 7 维度审核 Prompt + 严格度
├── tools/
│   ├── critic_animation.py         # 视频审核
│   ├── reference_image_gen.py      # 按时间戳抽帧 + 参考图
│   └── report_generator.py         # 汇总 Markdown 报告
├── run_critique_and_ref.py         # 端到端编排（1→2→3）
├── .env                            # 运行配置（勿提交）
└── README_CUSTOM.md                # 本文档
```

## 为什么需要 `custom:dashscope`

DashScope 的 OpenAI 兼容模式对视频输入使用 `video_url`，不是标准的 `image_url`。  
`clients/custom/dashscope.py` 负责正确组包，避免 `image format is illegal`。

`.env` 关键项：

```
LLM_PROVIDER=custom:dashscope
LLM_PROVIDER_VIDEO_CRITIQUE=custom:dashscope
LLM_MODEL_VIDEO_CRITIQUE=qwen-vl-max
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=sk-...
LLM_MODEL_IMAGE=qwen-image-2.0-pro-2026-06-22
```

## 审核维度（与根 README 一致，共 7 项）

动作流畅度、角色一致性、场景还原度、画面质量、节奏与时间控制、艺术表现力、模型穿模。

严格度：`--strictness 1~4`（默认 3）。定义见 `prompts/critic_animation.py`。

## 参考图流程摘要

1. 从 `审核结果.json` 取关键时间戳  
2. `ffmpeg` 抽原帧 → `原帧/`  
3. 调用 DashScope 图片编辑 → `参考图/`  

模型由 `--model-image` 或 `.env` 的 `LLM_MODEL_IMAGE` 决定。

## 与上游文档的关系

| 文档 | 用途 |
|------|------|
| [`../README.md`](../README.md) | **本审核系统的使用说明（优先读这个）** |
| [`README.md`](README.md) | 上游 Showvi：剧本/分镜/Seedance 成片流水线 |
| 本文 | 定制点索引 |

上游能力（Dashboard、Seedance 生成等）仍以 `README.md` / `README_EN.md` 为准；本仓库默认工作流是「已有视频 → 审核 + 参考图」。
