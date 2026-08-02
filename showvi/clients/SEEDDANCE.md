# Seeddance 客户端 (`clients/seeddance.py`)

即梦 (Jimeng) 视频生成逆向 API 客户端，封装为 `SeeddanceClient`。

## 认证

使用即梦官网的 Cookie `sessionid` 进行认证。

**获取方法：**

1. 浏览器访问 https://jimeng.jianying.com 并登录
2. F12 打开开发者工具 → Application → Cookies → `jimeng.jianying.com`
3. 复制 `sessionid` 字段的值
4. 设置环境变量：
   ```bash
   export SEEDDANCE_SESSION_ID="你的sessionid值"
   ```

> session 有效期有限，过期后需要重新获取。

## 支持的模型

| 模型 | 系列 | 图片输入 | 时长 | 说明 |
|------|------|---------|------|------|
| `3.0` | 视频 3.0 | 单图 | 5 / 10 秒 | 标准版 |
| `3.0-pro` | 视频 3.0 | 单图 | 5 / 10 秒 | 高质量 |
| `3.0-fast` | 视频 3.0 | 单图 | 5 / 10 秒 | 快速 |
| `s2.0` | 视频 3.0 | 单图 | 5 / 10 秒 | 轻量版 |
| `2.0-pro` | 视频 3.0 | 单图 | 5 / 10 秒 | 2.0 高质量 |
| `seedance-2.0` | Seedance 2.0 | 多图 | 4-15 秒 | 全能参考，需 Playwright |
| `seedance-2.0-fast` | Seedance 2.0 | 多图 | 4-15 秒 | 快速版，需 Playwright |

**画面比例：** `16:9`、`9:16`、`1:1`、`4:3`、`3:4`、`21:9`

## 快速使用

```python
from clients.seeddance import SeeddanceClient

client = SeeddanceClient()  # 自动从 SEEDDANCE_SESSION_ID 环境变量读取

# 单图 + 文字 → 视频 (3.0 系列)
history_id = client.submit_video(
    image_path="reference.png",
    prompt="镜头缓缓推进，花瓣在风中飘落",
    duration=5,
    model="3.0",
)

# 等待完成（返回格式与 Sora 对齐）
result = client.wait_for_video(history_id)
# result = {"url": "https://..."}

# 下载
client.download_video_file(result["url"], "output.mp4")
```

### 多图输入 (Seedance 2.0)

```python
history_id = client.submit_video(
    image_path=["character.png", "background.png"],
    prompt="@图1 是主角机甲，@图2 是战场背景，机甲在战场中缓缓前行",
    duration=8,
    model="seedance-2.0-fast",
)
```

prompt 中使用 `@图1`、`@图2`（或 `@image1`、`@image2`）占位符引用对应图片。不写占位符时会自动组织引用格式。

## 核心接口

| 方法 | 说明 | 返回 |
|------|------|------|
| `submit_video(image_path, prompt, duration, aspect_ratio, model)` | 提交生成任务 | `history_id: str` |
| `check_progress(history_id)` | 查询进度 | `dict` (status/progress/video_url/...) |
| `wait_for_video(history_id, timeout=1200)` | 轮询等待完成 | `{"url": "..."} \| None` |
| `download_video_file(video_url, save_path)` | 下载视频 | `bool` |
| `get_capabilities()` | 查询支持的模型和参数 | `dict` |

## 依赖

- `requests` — 所有模型
- `playwright` — 仅 Seedance 2.0 系列需要（绕过 shark 反爬签名）

安装 Playwright（如需使用 Seedance 2.0）：
```bash
pip install playwright
playwright install chromium
```

## 内部流程

```
submit_video()
  ├── 上传图片 (4步 ImageX 流程: 令牌 → 申请 → 上传 → 提交)
  ├── 构建 draft_content (v3 / seedance 格式)
  └── 提交生成请求
       ├── 3.0 系列: 直接 HTTP POST
       └── Seedance 2.0: 通过 Playwright 浏览器代理 (绕过 a_bogus)

wait_for_video()
  └── 轮询 check_progress() 直到 done/failed/timeout
       └── 自适应间隔: 有预估时间时按 estimated_time/2 轮询
```

## 异常类型

| 异常 | 含义 |
|------|------|
| `SeeddanceError` | 基础异常 |
| `InsufficientCreditsError` | 即梦积分不足 |
| `ContentFilteredError` | 内容违规（人脸/敏感内容） |
| `SessionExpiredError` | Session 过期 |
| `VideoGenerationTimeout` | 生成超时 |
| `VideoGenerationFailed` | 生成失败 |
