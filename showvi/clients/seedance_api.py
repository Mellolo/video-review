"""
Seedance 官方 API 客户端 (doubao-seedance-2-0)

基于 https://app.ppapi.ai/v1/video/generations 官方接口，
使用 Bearer token 认证，无需逆向 Cookie。

支持多模态生成（多视频续接/参考视频），以及标准的图生视频流程。

环境变量:
    SEEDANCE_API_KEY  — Bearer token，例如 sk-xxxxxxx
    SEEDANCE_API_BASE — 可选，默认 https://app.ppapi.ai
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests

from .base import (
    ClientError,
    ContentFilteredError as _BaseContentFilteredError,
    GenerationFailedError as _BaseGenerationFailedError,
    GenerationTimeoutError as _BaseGenerationTimeoutError,
    InsufficientCreditsError as _BaseInsufficientCreditsError,
    SessionExpiredError as _BaseSessionExpiredError,
)

_logger = logging.getLogger("video_agent.seedance_api")

# ── 常量 ──────────────────────────────────────────────────────────

DEFAULT_API_BASE = "https://app.ppapi.ai"
DEFAULT_MODEL = "doubao-seedance-2-0-260128"

VALID_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]
VALID_DURATIONS = list(range(4, 16))  # 4-15 秒

POLL_INITIAL_DELAY = 5.0
POLL_INTERVAL = 10.0
POLL_MAX_RETRIES = 1000

# ── 异常 ──────────────────────────────────────────────────────────


class SeedanceApiError(ClientError):
    """官方 API 基础异常"""


class InsufficientCreditsError(_BaseInsufficientCreditsError, SeedanceApiError):
    pass


class ContentFilteredError(_BaseContentFilteredError, SeedanceApiError):
    pass


class SessionExpiredError(_BaseSessionExpiredError, SeedanceApiError):
    pass


class VideoGenerationTimeout(_BaseGenerationTimeoutError, SeedanceApiError):
    pass


class VideoGenerationFailed(_BaseGenerationFailedError, SeedanceApiError):
    pass


# ── 客户端 ────────────────────────────────────────────────────────


class SeedanceApiClient:
    """
    Seedance 官方 API 客户端

    核心接口:
        submit_video()   — 提交生成任务，返回 task_id
        check_progress() — 查询任务状态
        wait_for_video() — 轮询等待完成，返回 {"url": ...}
        download_video_file() — 下载视频到本地
    """

    def __init__(self, api_key: Optional[str] = None, api_base: Optional[str] = None):
        self.api_key = api_key or os.getenv("SEEDANCE_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "api_key 不能为空，请设置环境变量 SEEDANCE_API_KEY"
            )
        self.api_base = (api_base or os.getenv("SEEDANCE_API_BASE", DEFAULT_API_BASE)).rstrip("/")
        self.http = requests.Session()
        self.http.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def close(self):
        self.http.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # ── 内部请求 ──────────────────────────────────────────────────

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.api_base}{path}"
        try:
            resp = self.http.request(method, url, timeout=60, **kwargs)
        except requests.RequestException as e:
            raise SeedanceApiError(f"请求失败: {e}") from e

        if resp.status_code == 401:
            raise SessionExpiredError("API Key 无效或已过期")
        if resp.status_code == 402:
            raise InsufficientCreditsError("账户余额不足")
        if resp.status_code == 429:
            raise SeedanceApiError("请求频率超限，请稍后重试")

        try:
            data = resp.json()
        except json.JSONDecodeError:
            raise SeedanceApiError(
                f"HTTP {resp.status_code}: 无法解析响应 — {resp.text[:200]}"
            )

        if resp.status_code >= 400:
            err_msg = data.get("message") or data.get("error") or str(data)
            raise SeedanceApiError(f"HTTP {resp.status_code}: {err_msg}")

        return data

    # ── 接口 1: 提交生成任务 ──────────────────────────────────────

    def submit_video(
        self,
        prompt: str,
        *,
        model: str = DEFAULT_MODEL,
        ratio: str = "16:9",
        duration: int = 5,
        generate_audio: bool = True,
        watermark: bool = False,
        reference_videos: Optional[List[Dict[str, str]]] = None,
        content: Optional[List[Dict]] = None,
    ) -> str:
        """
        提交视频生成任务。

        Args:
            prompt:           文字描述
            model:            模型 ID，默认 doubao-seedance-2-0-260128
            ratio:            画面比例，如 "16:9"、"9:16"
            duration:         视频时长（秒），4-15
            generate_audio:   是否生成音频
            watermark:        是否添加水印
            reference_videos: 参考视频列表，每项 {"url": "...", "role": "reference_video"}
            content:          多模态内容列表（覆盖 reference_videos 的高级用法）
                              每项可为 text / video_url 类型，参见 API 文档

        Returns:
            task_id: 用于后续查询进度
        """
        if ratio not in VALID_RATIOS:
            raise ValueError(f"ratio 必须是 {VALID_RATIOS} 之一")
        if duration not in VALID_DURATIONS:
            raise ValueError(f"duration 必须在 4-15 秒之间，收到 {duration}")

        # 构建 content 列表
        if content is None:
            content = [{"type": "text", "text": prompt}]
            for rv in (reference_videos or []):
                content.append({
                    "type": "video_url",
                    "video_url": {"url": rv["url"]},
                    "role": rv.get("role", "reference_video"),
                })

        body = {
            "model": model,
            "content": content,
            "generate_audio": generate_audio,
            "ratio": ratio,
            "duration": duration,
            "watermark": watermark,
            "prompt": prompt,
            "reference_videos": reference_videos or [],
        }

        _logger.info(
            "提交 Seedance API 任务: model=%s ratio=%s duration=%ds prompt=%s...",
            model, ratio, duration, prompt[:60],
        )
        print(f"[SEEDANCE-API] 提交任务: model={model}, {duration}s, ratio={ratio}")

        data = self._request("POST", "/v1/video/generations", json=body)

        task_id = data.get("id") or data.get("task_id")
        if not task_id:
            raise SeedanceApiError(
                f"未获取到 task_id，响应: {json.dumps(data, ensure_ascii=False)[:200]}"
            )

        _logger.info("任务已提交: task_id=%s", task_id)
        print(f"[SEEDANCE-API] 任务已提交: {task_id}")
        return task_id

    # ── 接口 2: 查询进度 ──────────────────────────────────────────

    def check_progress(self, task_id: str) -> dict:
        """
        查询任务状态。

        Returns:
            dict with keys: status, video_url, error, progress
            status: "processing" | "done" | "failed"
        """
        raw = self._request("GET", f"/v1/video/generations/{task_id}")

        # API 响应格式: {"code": "success", "data": {...}} 或直接是任务对象
        data = raw.get("data", raw) if isinstance(raw.get("data"), dict) else raw

        status = str(data.get("status", "")).upper()
        video_url: Optional[str] = None
        error: Optional[str] = None

        if status in ("SUCCESS", "SUCCEEDED", "COMPLETED", "DONE"):
            video_url = (
                data.get("result_url")
                or data.get("video_url")
                or data.get("output", {}).get("video_url")
                or data.get("result", {}).get("url")
            )
            return {
                "status": "done",
                "video_url": video_url,
                "error": None,
                "progress": "生成完成",
            }

        if status in ("FAILED", "ERROR", "FAIL"):
            error = (
                data.get("fail_reason")
                or data.get("error")
                or data.get("message")
                or "未知错误"
            )
            return {
                "status": "failed",
                "video_url": None,
                "error": error,
                "progress": "生成失败",
            }

        # processing / pending / queued / IN_PROGRESS 等
        progress_pct = data.get("progress") or data.get("percent")
        if progress_pct is not None:
            progress_msg = f"生成中 {progress_pct}..."
        else:
            progress_msg = "AI 正在生成视频，请耐心等待..."

        return {
            "status": "processing",
            "video_url": None,
            "error": None,
            "progress": progress_msg,
        }

    # ── 接口 3: 等待完成 ──────────────────────────────────────────

    def wait_for_video(
        self,
        task_id: str,
        timeout: int = 36000,
        on_progress: Optional[Callable[[dict], None]] = None,
    ) -> Optional[Dict]:
        """
        轮询等待视频生成完成。

        Args:
            task_id:     submit_video() 返回的 ID
            timeout:     超时秒数，默认 36000（10 小时）
            on_progress: 可选回调，每次轮询后调用

        Returns:
            {"url": video_url} 或抛出异常
        """
        print("[SEEDANCE-API] 等待视频生成...")
        _logger.info("等待任务完成: task_id=%s timeout=%ds", task_id, timeout)

        time.sleep(POLL_INITIAL_DELAY)
        start = time.time()
        retry = 0

        while retry < POLL_MAX_RETRIES:
            elapsed = time.time() - start
            if elapsed > timeout:
                raise VideoGenerationTimeout(
                    f"视频生成超时 ({elapsed:.0f}s > {timeout}s)"
                )

            try:
                progress = self.check_progress(task_id)

                if progress["status"] == "done":
                    elapsed_total = time.time() - start
                    print(
                        f"[SEEDANCE-API] 生成完成！耗时: {elapsed_total:.1f}s "
                        f"({elapsed_total / 60:.1f}min)"
                    )
                    return {"url": progress["video_url"]}

                if progress["status"] == "failed":
                    error = progress["error"] or "未知错误"
                    print(f"[SEEDANCE-API] 生成失败: {error}")
                    _logger.error("任务失败: %s", error)
                    _content_kws = ("违规", "不符合", "审核", "敏感", "不合规")
                    _credits_kws = ("余额", "积分", "额度")
                    if any(kw in error for kw in _content_kws):
                        raise ContentFilteredError(error)
                    if any(kw in error for kw in _credits_kws):
                        raise InsufficientCreditsError(error)
                    raise VideoGenerationFailed(error)

                mins, secs = divmod(int(elapsed), 60)
                print(f"[SEEDANCE-API]   {progress['progress']} ({mins}分{secs}秒)")
                _logger.info("轮询 #%d: %s", retry + 1, progress["progress"])

                if on_progress:
                    try:
                        on_progress(progress)
                    except Exception as cb_err:
                        _logger.warning("on_progress callback failed: %s", cb_err)

            except (InsufficientCreditsError, ContentFilteredError,
                    VideoGenerationFailed, VideoGenerationTimeout):
                raise
            except Exception as e:
                _logger.warning("轮询出错 (#%d): %s", retry + 1, e)

            time.sleep(POLL_INTERVAL)
            retry += 1

        raise VideoGenerationTimeout(f"超过最大轮询次数 ({POLL_MAX_RETRIES})")

    # ── 接口 4: 下载视频 ──────────────────────────────────────────

    def download_video_file(self, video_url: str, save_path: str) -> bool:
        """
        下载视频到本地文件。

        Returns:
            True 表示成功
        """
        _logger.info("下载视频: %s → %s", video_url, save_path)
        print(f"[SEEDANCE-API] 下载视频: {save_path}")

        for attempt in range(1, 6):
            try:
                if attempt > 1:
                    print(f"[SEEDANCE-API]   重试下载 ({attempt}/5)...")
                resp = requests.get(video_url, stream=True, timeout=120)
                resp.raise_for_status()

                Path(save_path).parent.mkdir(parents=True, exist_ok=True)
                downloaded = 0
                with open(save_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)

                print(f"[SEEDANCE-API] 下载完成: {downloaded / 1024 / 1024:.2f} MB")
                _logger.info("下载完成: %s (%d bytes)", save_path, downloaded)
                return True

            except Exception as e:
                _logger.error("下载失败 (attempt %d): %s", attempt, e)
                if attempt < 5:
                    time.sleep(5)

        return False


# ── 工厂函数 ──────────────────────────────────────────────────────


def get_seedance_api_client(api_key: Optional[str] = None) -> SeedanceApiClient:
    """获取 SeedanceApiClient 实例"""
    return SeedanceApiClient(api_key=api_key)
