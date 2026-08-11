"""
DashScope (阿里云百炼) LLM Plugin — 支持视频理解的自定义客户端。

DashScope 的 OpenAI 兼容模式对视频输入使用 ``video_url`` content type，
而非标准 OpenAI 的 ``image_url``，因此需要此自定义 plugin 来正确格式化请求。

配置 (.env):
    LLM_PROVIDER_VIDEO_CRITIQUE=custom:dashscope
    LLM_MODEL_VIDEO_CRITIQUE=qwen-vl-max
    LLM_API_KEY=sk-xxx
    LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
"""

import base64
import json
import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

try:
    from openai import OpenAI as _OpenAI
except ImportError:  # pragma: no cover
    _OpenAI = None  # type: ignore[assignment,misc]

_logger = logging.getLogger("video_agent.clients.dashscope")


def _env(key: str, default: str = "") -> str:
    """Read config from .env file first, fall back to os.environ."""
    try:
        from dashboard.env_store import get_env_value
        return get_env_value(key, default)
    except Exception:
        return os.environ.get(key, default)


class DashScopeClient:
    """DashScope LLM 客户端，支持视频理解。

    使用 OpenAI SDK 进行 API 调用，但对视频输入使用 DashScope 特有的
    ``video_url`` content type，避免 "image format is illegal" 错误。

    支持的模型:
      - qwen-vl-max      (视频审核/分析)
      - qwen-vl-plus      (轻量视频理解)
      - qwen3.8-max       (原生全模态，支持视频/图片/文本)
      - qwen3.7-plus      (原生多模态，支持视频)
      - qwen3.7-max       (纯文本，不支持视频)
      - qwen-turbo/max    (纯文本)
    """

    # ── 超时常量 ──
    DEFAULT_TIMEOUT_SECONDS = 120
    VIDEO_TIMEOUT_SECONDS = 180
    IMAGE_TIMEOUT_SECONDS = 180

    # ── 重试配置 ──
    RETRY_DELAYS = [2, 5, 10]

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ):
        if _OpenAI is None:
            raise ImportError(
                "openai package is required for DashScopeClient. "
                "Install it with: pip install openai"
            )
        self.api_key = api_key or _env("LLM_API_KEY")
        if not self.api_key:
            raise ValueError("LLM_API_KEY not found in environment")

        resolved_base = kwargs.get("base_url") or _env("LLM_BASE_URL")
        if not resolved_base:
            raise ValueError("LLM_BASE_URL not found in environment")

        self.default_model = model or _env("LLM_MODEL", "qwen-vl-max")
        self.base_url = resolved_base

        self._client = _OpenAI(
            api_key=self.api_key,
            base_url=resolved_base,
            timeout=float(self.DEFAULT_TIMEOUT_SECONDS),
            max_retries=0,
        )
        # httpx client for native DashScope APIs (image edit, etc.)
        self._http = httpx.Client(timeout=float(self.VIDEO_TIMEOUT_SECONDS))
        _logger.info(
            "DashScopeClient initialized (model=%s, base=%s)",
            self.default_model, resolved_base,
        )

    # ── 公共接口 ──────────────────────────────────────────────────────

    def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 3,
    ) -> str:
        """生成文本响应。"""
        resolved_model = model or self.default_model
        messages: List[Dict[str, Any]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        kwargs = self._build_kwargs(
            model=resolved_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            response_schema=response_schema,
            timeout_seconds=timeout_seconds or self.DEFAULT_TIMEOUT_SECONDS,
        )
        response = self._call_with_retry(
            lambda: self._client.chat.completions.create(**kwargs),
            timeout_seconds=timeout_seconds or self.DEFAULT_TIMEOUT_SECONDS,
            max_retries=max_retries,
            action_label=f"DashScope text ({resolved_model})",
        )
        return self._extract_text(response)

    def generate_with_vision(
        self,
        text_prompt: str,
        image_paths: List[str],
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
        response_format: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 3,
    ) -> str:
        """使用图片输入生成响应（VLM）。"""
        resolved_model = model or self.default_model
        content_parts: List[Dict[str, Any]] = []

        mime_map = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif",
        }
        for img_path in image_paths:
            if img_path.startswith("data:image"):
                content_parts.append({"type": "image_url", "image_url": {"url": img_path}})
            elif Path(img_path).exists():
                mime = mime_map.get(Path(img_path).suffix.lower(), "image/png")
                with open(img_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })

        content_parts.append({"type": "text", "text": text_prompt})

        messages: List[Dict[str, Any]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": content_parts})

        kwargs = self._build_kwargs(
            model=resolved_model,
            messages=messages,
            temperature=temperature,
            response_format=response_format,
            response_schema=response_schema,
            timeout_seconds=timeout_seconds or self.IMAGE_TIMEOUT_SECONDS,
        )
        response = self._call_with_retry(
            lambda: self._client.chat.completions.create(**kwargs),
            timeout_seconds=timeout_seconds or self.IMAGE_TIMEOUT_SECONDS,
            max_retries=max_retries,
            action_label=f"DashScope vision ({resolved_model})",
        )
        return self._extract_text(response)

    def generate_with_video(
        self,
        text_prompt: str,
        video_paths: List[str],
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
        response_format: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 3,
        **kwargs,
    ) -> str:
        """使用视频输入生成响应。

        DashScope OpenAI 兼容模式要求视频以 ``video_url`` content type 传递，
        而非标准 OpenAI 的 ``image_url``。这是本 plugin 存在的核心原因。
        """
        resolved_model = model or self.default_model
        content_parts: List[Dict[str, Any]] = []

        for video_path in video_paths:
            vp = str(video_path)
            if vp.startswith("http://") or vp.startswith("https://"):
                # 公网 URL — 直接传递
                content_parts.append({
                    "type": "video_url",
                    "video_url": {"url": vp},
                })
            else:
                # 本地文件 — base64 编码
                mime, _ = mimetypes.guess_type(vp)
                if not mime:
                    mime = "video/mp4"

                file_size = os.path.getsize(vp)
                size_mb = file_size / (1024 * 1024)
                if size_mb > 100:
                    raise ValueError(
                        f"视频文件 {vp} 大小为 {size_mb:.1f}MB，超过 100MB 限制"
                    )
                _logger.info(
                    "DashScope video input: %s (%.1fMB, mime=%s)",
                    Path(vp).name, size_mb, mime,
                )
                with open(vp, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                content_parts.append({
                    "type": "video_url",
                    "video_url": {"url": f"data:{mime};base64,{b64}"},
                })

        content_parts.append({"type": "text", "text": text_prompt})

        messages: List[Dict[str, Any]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": content_parts})

        api_kwargs = self._build_kwargs(
            model=resolved_model,
            messages=messages,
            temperature=temperature,
            response_format=response_format,
            response_schema=response_schema,
            timeout_seconds=timeout_seconds or self.VIDEO_TIMEOUT_SECONDS,
        )
        response = self._call_with_retry(
            lambda: self._client.chat.completions.create(**api_kwargs),
            timeout_seconds=timeout_seconds or self.VIDEO_TIMEOUT_SECONDS,
            max_retries=max_retries,
            action_label=f"DashScope video ({resolved_model})",
        )
        return self._extract_text(response)

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 3,
    ) -> str:
        """OpenAI 风格的聊天完成接口。"""
        resolved_model = model or self.default_model
        kwargs = self._build_kwargs(
            model=resolved_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            timeout_seconds=timeout_seconds or self.DEFAULT_TIMEOUT_SECONDS,
        )
        response = self._call_with_retry(
            lambda: self._client.chat.completions.create(**kwargs),
            timeout_seconds=timeout_seconds or self.DEFAULT_TIMEOUT_SECONDS,
            max_retries=max_retries,
            action_label=f"DashScope chat ({resolved_model})",
        )
        return self._extract_text(response)

    # ── 内部工具方法 ──────────────────────────────────────────────────

    def _build_kwargs(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """构建 OpenAI API 调用参数。"""
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        # DashScope 支持 json_object 响应格式
        if response_schema or response_format == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        if timeout_seconds:
            kwargs["timeout"] = float(timeout_seconds)
        return kwargs

    def _call_with_retry(
        self,
        call: Any,
        *,
        timeout_seconds: int,
        max_retries: int,
        action_label: str,
    ) -> Any:
        """带超时重试的 API 调用。"""
        last_err: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                return call()
            except Exception as exc:
                last_err = exc
                if not self._is_timeout_error(exc):
                    raise
                _logger.warning(
                    "%s timeout on attempt %d/%d (timeout=%ss): %s",
                    action_label, attempt, max_retries,
                    timeout_seconds, exc,
                )
                if attempt < max_retries:
                    delay = self.RETRY_DELAYS[min(attempt - 1, len(self.RETRY_DELAYS) - 1)]
                    time.sleep(delay)

        raise TimeoutError(
            f"{action_label} timed out after {max_retries} attempts: {last_err}"
        ) from last_err

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return isinstance(exc, TimeoutError) or "timed out" in msg or "timeout" in msg

    @staticmethod
    def _extract_text(response: Any) -> str:
        """从 OpenAI API 响应中提取文本。"""
        if isinstance(response, str):
            return response
        # 流式响应
        if hasattr(response, "__iter__") and not hasattr(response, "choices"):
            parts: list[str] = []
            try:
                for chunk in response:
                    for choice in getattr(chunk, "choices", []):
                        delta = getattr(choice, "delta", None)
                        if delta:
                            tok = getattr(delta, "content", None)
                            if tok:
                                parts.append(tok)
            except Exception as exc:
                _logger.warning("Error consuming stream: %s", exc)
            return "".join(parts)
        try:
            return response.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError):
            return str(response)

    def close(self) -> None:
        """清理资源。"""
        try:
            self._http.close()
        except Exception:
            pass


# ── Plugin 注册 ────────────────────────────────────────────────────────
PLUGIN_TYPE = "llm"
PLUGIN_CLASS = DashScopeClient
