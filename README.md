# Showvi + DashScope 视频审核系统

基于 [Showvi](https://github.com/sjtuplayer/showvi) 框架 + 阿里云百炼 DashScope，对动画/生成视频做质量审核，并按问题时间戳生成参考图与汇总报告。

> **文档入口**：日常使用看本文件；**视频怎么放、怎么跑**见精炼版 [`工作流.md`](工作流.md)。`showvi/README.md` 是上游 Showvi 视频生成系统文档，不是本审核流程说明。

---

## 你需要准备什么

| 项 | 说明 |
|----|------|
| Python | **3.12+**（推荐；系统自带 3.9 可能跑不通部分模块） |
| 依赖 | `showvi/.venv` + `requirements.txt` |
| 系统工具 | `ffmpeg`、`ffprobe` 在 `PATH` 中 |
| API Key | 阿里云百炼 DashScope Key，写在 `showvi/.env` 的 `LLM_API_KEY` |
| 视频 | 本地文件（如 `.mp4`） |
| 场景描述 | 一段文字，作为审核对照基准（见下方说明） |

获取 API Key：[百炼控制台](https://dashscope.console.aliyun.com/) → API Key 管理。

---

## 快速开始

以下命令均在 **`showvi/` 目录**下执行。路径相对 `showvi/`，或使用绝对路径。

```bash
cd showvi
source .venv/bin/activate

# 建议：把视频放到仓库根目录的工作区（相对 showvi 为 ../视频审核/...）
mkdir -p ../视频审核/测试视频1
# 将 your_video.mp4 拷到 ../视频审核/测试视频1/

# 完整流程：审核 → 参考图 → 汇总报告（带场景描述）
python run_critique_and_ref.py \
    --video  ../视频审核/测试视频1/your_video.mp4 \
    --scene  "角色：小明，穿红色外套。场景：雨夜街道。动作：小明撑伞穿过斑马线，镜头跟随。" \
    --output ../视频审核/测试视频1
```

**完整流程（不提供场景描述，模型自主看视频判断）：**

```bash
python run_critique_and_ref.py \
    --video  ../视频审核/测试视频1/your_video.mp4 \
    --output ../视频审核/测试视频1
```

**仅审核**（跳过参考图和报告）：

```bash
python run_critique_and_ref.py \
    --video  ../视频审核/测试视频1/your_video.mp4 \
    --scene  "……" \
    --output ../视频审核/测试视频1 \
    --skip-ref --skip-report
```

**从已有 session 续跑**（跳过审核，只生成参考图和报告）：

```bash
python run_critique_and_ref.py \
    --video   ../视频审核/测试视频1/your_video.mp4 \
    --scene   "……" \
    --session ../视频审核/测试视频1/审核_20260802_184651 \
    --skip-critique
```

跑通后，在 `--output` 目录下会出现 `审核_YYYYMMDD_HHMMSS/` session 文件夹。

---

## 场景描述（`--scene`）怎么写

`--scene` 是**可选**对照基准。提供后，模型会拿它和画面逐项比对（尤其影响「场景还原度」）；**不提供时，模型会基于视频画面本身自主判断质量**。

建议写清：

1. **角色**：姓名/称呼、外观、服装、关键道具  
2. **环境**：地点、时间、天气、氛围  
3. **动作与镜头**：主要动作、镜头运动、关键剧情节点  
4. **必须出现的元素**：招牌、车辆、表情等硬性要求  

示例（好）：

```text
角色：女孩阿宁，黑色短发，校服。场景：放学后的天桥，傍晚暖光。
动作：阿宁停在栏杆边望向远处列车，风吹动裙摆；镜头由近景缓慢推近面部。
必须包含：天桥栏杆、远处高架列车、暖色晚霞。
```

示例（差）：`好看一点` / `检查一下` —— 信息太少，审核会发散、不可复现。

也可用文件内容作为描述：

```bash
python run_critique_and_ref.py \
    --video  ../视频审核/测试视频1/a.mp4 \
    --scene  "$(cat ../视频审核/测试视频1/场景.txt)" \
    --output ../视频审核/测试视频1
```

---

## 流程与产物

编排器 `run_critique_and_ref.py` 串联三步：

| 阶段 | 工具 | 产物 |
|------|------|------|
| 1 视频审核 | `tools/critic_animation.py` | `审核结果.json` |
| 2 参考图 | `tools/reference_image_gen.py` | `原帧/`、`参考图/` |
| 3 汇总报告 | `tools/report_generator.py` | `审核报告.md` |

### Session 目录结构

```
视频审核/测试视频1/                 # --output（仓库根下，已 gitignore）
├── your_video.mp4                  # 原视频（不改动）
└── 审核_20260802_184651/           # 每次运行自动创建
    ├── 场景描述.txt
    ├── 审核结果.json
    ├── 原帧/
    │   └── 原帧_00s12.png
    ├── 参考图/
    │   └── 参考图_00s12.png
    └── 审核报告.md
```

### 独立调用（可选）

仍在 `showvi/` 下：

```bash
# 1. 审核
python tools/critic_animation.py \
    --video  ../视频审核/测试视频1/a.mp4 \
    --scene  "……" \
    --output ../视频审核/测试视频1/审核结果.json \
    --strictness 3

# 2. 参考图
python tools/reference_image_gen.py \
    --critique ../视频审核/测试视频1/审核结果.json \
    --video    ../视频审核/测试视频1/a.mp4 \
    --scene    "……" \
    --output   ../视频审核/测试视频1/某session/

# 3. 报告
python tools/report_generator.py \
    --session ../视频审核/测试视频1/某session/
```

---

## 审核维度（7 项）

| 维度 | JSON 字段 | 检查要点 |
|------|-----------|----------|
| 动作流畅度 | `motion_fluidity` | 卡顿/跳帧、物理规律、表情连贯 |
| 角色一致性 | `character_consistency` | 面部/服装稳定、特征突变 |
| 场景还原度 | `scene_accuracy` | 关键元素、剧情节点、氛围 |
| 画面质量 | `visual_quality` | 清晰度、色彩、噪点/畸变 |
| 节奏与时间控制 | `pacing_timing` | 动作节奏、镜头切换 |
| 艺术表现力 | `artistic_expression` | 构图、色彩、感染力 |
| 模型穿模 | `model_clipping` | 道具穿透、身体穿插、环境穿模 |

### 严格度（`--strictness`，默认 3）

| 等级 | 名称 | ACCEPT 阈值 | 最少关键问题 | 最少次要问题 | 说明 |
|:----:|:----:|:----------:|:------------:|:------------:|------|
| 1 | 宽松 | ≥ 6.0 | 1 | 2 | 容忍小瑕疵 |
| 2 | 普通 | ≥ 7.0 | 2 | 3 | 标准力度 |
| 3 | **严格** | ≥ 8.0 | 3 | 5 | 默认，逐秒排查 |
| 4 | 极严 | ≥ 9.0 | 5 | 8 | 吹毛求疵 |

```bash
python run_critique_and_ref.py --video ... --scene "..." --output ... --strictness 4
```

---

## 编排器参数一览

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `--video` | ✅ | 视频文件路径 |
| `--scene` | | 场景描述文本（可选；省略时模型自主看视频判断） |
| `--output` | | 产出父目录；默认取视频所在目录；其下自动建 `审核_*` |
| `--session` | | 已有 session 路径（续跑时用） |
| `--model` | | 审核模型，默认读 `.env` |
| `--model-image` | | 参考图模型，默认读 `.env` |
| `--timeout` | | 审核 API 超时秒数，默认 `300` |
| `--strictness` | | `1`–`4`，默认 `3` |
| `--skip-critique` | | 跳过 Phase 1 |
| `--skip-ref` | | 跳过 Phase 2 |
| `--skip-report` | | 跳过 Phase 3 |
| `--env` | | `.env` 路径，默认 `showvi/.env` |

---

## 配置（`showvi/.env`）

| 配置项 | 说明 | 推荐值 |
|--------|------|--------|
| `LLM_API_KEY` | DashScope API Key | 必填 |
| `LLM_PROVIDER` | 默认 LLM 插件 | `custom:dashscope` |
| `LLM_BASE_URL` | DashScope 兼容地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `LLM_PROVIDER_VIDEO_CRITIQUE` | 视频审核 provider | `custom:dashscope` |
| `LLM_MODEL_VIDEO_CRITIQUE` | 视频审核模型 | `qwen-vl-max` |
| `LLM_MODEL_IMAGE` | 参考图模型 | `qwen-image-2.0-pro-2026-06-22` |
| `LLM_MODEL` | 文本任务模型 | `qwen3.7-plus` |

换模型只改 `.env`，不必改代码。`.env` 已被 gitignore，勿提交。

---

## 环境搭建

```bash
# 1) 进入代码目录
cd showvi

# 2) 创建虚拟环境（Python 3.12+）
python3.12 -m venv .venv
source .venv/bin/activate

# 3) 安装依赖（可换国内镜像）
pip install -U pip
pip install -r requirements.txt
# 若要用 Dashboard：
# pip install -r dashboard/requirements.txt

# 4) 配置密钥
cp .env.example .env   # 若尚无 .env
# 编辑 .env：填入 LLM_API_KEY，并按上表设置 DashScope 相关项

# 5) 系统依赖
# macOS 示例：brew install ffmpeg
# 或自行保证 ffmpeg / ffprobe 在 PATH 中

# 6) 验证
python -c "from clients import get_llm_client; get_llm_client(provider='custom:dashscope'); print('OK')"
ffmpeg -version | head -1
ffprobe -version | head -1
```

可选：Seedance / Dashboard 等上游能力见 `showvi/README.md`；本仓库定制点见 [`showvi/README_CUSTOM.md`](showvi/README_CUSTOM.md)。

---

## 项目结构

```
video-review/                         # 仓库根
├── README.md                         # 本文件（审核使用说明）
├── 视频审核/                         # 建议工作目录（gitignore）
└── showvi/                           # 代码与配置
    ├── .env                          # 唯一密钥/模型配置
    ├── .venv/
    ├── run_critique_and_ref.py       # 编排器
    ├── clients/custom/dashscope.py   # DashScope 视频兼容插件
    ├── prompts/critic_animation.py   # 7 维度审核 Prompt
    └── tools/
        ├── critic_animation.py
        ├── reference_image_gen.py
        └── report_generator.py
```

---

## 常见问题

| 问题 | 处理 |
|------|------|
| `image format is illegal` | 确认 `LLM_PROVIDER_VIDEO_CRITIQUE=custom:dashscope` |
| `ffmpeg` / `ffprobe: command not found` | 安装并加入 `PATH`（如 `brew install ffmpeg`） |
| `LLM_API_KEY not found` | 检查 `showvi/.env`，并在 `showvi/` 下运行命令 |
| 视频路径不存在 | 路径相对 **当前工作目录（showvi/）**；推荐 `../视频审核/...` 或绝对路径 |
| API 超时 | 加大 `--timeout`；检查能否访问 `dashscope.aliyuncs.com` |
| Python 3.9 报 `list \| None` 等语法错 | 换用 Python 3.12+ 重建 `.venv` |
| 参考图与原帧差太大 | 可调 `tools/reference_image_gen.py` 中 `_build_edit_prompt()` |
| 想改评分标准 | 编辑 `prompts/critic_animation.py` |

**费用与时长**：审核走 `qwen-vl-max` 视频理解，参考图按问题帧调用图片编辑；视频越长、严格度越高、关键问题越多，耗时与费用通常越高。具体以百炼计费为准。

---

## 推荐工作习惯

1. 每个片子一个目录：`视频审核/<片名>/`，视频与 session 放一起。  
2. 场景描述先写成 `场景.txt`，再用 `"$(cat …)"` 传入，方便复跑。  
3. 先 `--skip-ref --skip-report` 只看分数与问题，确认严格度后再出参考图。  
4. 续跑时用 `--session` 指向已有 `审核_*` 目录，避免重复扣审核费用。
