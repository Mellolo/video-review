"""
OpenAI 兼容图片生成客户端。

通过 OpenAI images.generate / images.edit 接口调用 gpt-image-2 等图片模型，
接口签名与 LLMClient.generate_image 保持一致，
方便在现有调用点直接替换。支持任意 OpenAI 兼容的 base_url。
"""

import base64
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional, List

import requests

from .gemini import ImageGenerationBlockedError

_logger = logging.getLogger("video_agent.openai_image")


class OpenAIImageAPIError(RuntimeError):
    """OpenAI Image API 调用失败。"""


class OpenAIImageTimeoutError(TimeoutError):
    """OpenAI Image API 请求超时。"""


class OpenAIImageClient:
    """通过 OpenAI 兼容接口调用 gpt-image-2 等图片模型的客户端。

    接口与 LLMClient 的 generate_image 保持兼容。
    """

    DEFAULT_TIMEOUT_SECONDS = int(os.getenv("OPENAI_IMAGE_TIMEOUT_SECONDS", "120"))
    IMAGE_TIMEOUT_SECONDS = DEFAULT_TIMEOUT_SECONDS
    RETRY_DELAYS_SECONDS = [2, 5, 10]

    _DEFAULT_BASE_URL = "https://api.openai.com/v1"
    _DEFAULT_MODEL = "gpt-image-2"

    # aspect_ratio → (width, height) 映射，gpt-image-2 支持的尺寸
    _ASPECT_TO_SIZE = {
        "1:1":  "1024x1024",
        "16:9": "1536x1024",
        "9:16": "1024x1536",
        "4:3":  "1536x1024",
        "3:4":  "1024x1536",
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = _DEFAULT_MODEL,
        base_url: Optional[str] = None,
    ):
        try:
            from openai import OpenAI as _OpenAI
        except ImportError as exc:
            raise ImportError(
                "openai package is required for OpenAIImageClient. "
                "Install it with: pip install openai"
            ) from exc

        self.api_key = (
            api_key
            or os.getenv("IMAGE_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
        resolved_base = base_url or os.getenv("IMAGE_BASE_URL") or self._DEFAULT_BASE_URL
        self.default_model = model or self._DEFAULT_MODEL
        self.last_remote_url: str = ""

        self._client = _OpenAI(
            api_key=self.api_key,
            base_url=resolved_base,
            timeout=float(self.DEFAULT_TIMEOUT_SECONDS),
            max_retries=0,
        )
        _logger.info(
            "OpenAIImageClient initialized (base=%s, model=%s)",
            resolved_base,
            self.default_model,
        )

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return isinstance(exc, TimeoutError) or "timeout" in msg or "timed out" in msg

    def _call_with_timeout_retry(
        self,
        call: Callable[[], Any],
        *,
        timeout_seconds: Optional[int],
        max_retries: int,
        action_label: str,
    ) -> Any:
        last_err: Optional[Exception] = None
        effective_timeout = timeout_seconds or self.DEFAULT_TIMEOUT_SECONDS

        for attempt in range(1, max_retries + 1):
            try:
                return call()
            except Exception as exc:
                if not self._is_timeout_error(exc):
                    raise
                last_err = exc
                _logger.warning(
                    "%s timeout on attempt %d/%d (timeout=%ss): %s",
                    action_label,
                    attempt,
                    max_retries,
                    effective_timeout,
                    exc,
                )
                if attempt < max_retries:
                    delay = self.RETRY_DELAYS_SECONDS[
                        min(attempt - 1, len(self.RETRY_DELAYS_SECONDS) - 1)
                    ]
                    time.sleep(delay)

        raise OpenAIImageTimeoutError(
            f"{action_label} timed out after {max_retries} attempts "
            f"(timeout={effective_timeout}s): {last_err}"
        ) from last_err

    def _aspect_ratio_to_size(self, aspect_ratio: str) -> str:
        """将 aspect_ratio 字符串转换为 gpt-image-2 支持的 size 参数。"""
        return self._ASPECT_TO_SIZE.get(aspect_ratio, "1024x1024")

    def _download_url(self, url: str, timeout: int = 60) -> bytes:
        """下载远程图片 URL 并返回字节。"""
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.content

    def _load_image_file(self, path_or_url: str) -> bytes:
        """从本地路径或 URL 加载图片字节。"""
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return self._download_url(path_or_url, timeout=60)
        if path_or_url.startswith("data:image/"):
            _, _, b64 = path_or_url.partition(",")
            return base64.b64decode(b64)
        return Path(path_or_url).read_bytes()

    def _extract_result(self, response: Any) -> bytes:
        """从 ImagesResponse 中提取图片字节。"""
        if not response.data:
            raise OpenAIImageAPIError("OpenAI Image API returned empty data")
        item = response.data[0]
        if getattr(item, "b64_json", None):
            self.last_remote_url = ""
            return base64.b64decode(item.b64_json)
        if getattr(item, "url", None):
            self.last_remote_url = item.url
            _logger.info("OpenAI Image API returned URL: %s", item.url)
            return self._download_url(item.url, timeout=60)
        raise OpenAIImageAPIError(
            f"OpenAI Image API response has neither url nor b64_json: {item}"
        )

    def generate_image(
        self,
        prompt: str,
        aspect_ratio: str = "16:9",
        image_size: str = "2K",
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 1,
        reference_images: Optional[List[str]] = None,
    ) -> bytes:
        """文生图：根据 prompt 生成图片。

        当传入 reference_images 时，自动切换为图生图模式（images.edit）。

        Args:
            prompt: 图片描述提示词
            aspect_ratio: 宽高比，如 "16:9"、"1:1"、"9:16"
            image_size: 忽略（保留兼容性，gpt-image-2 通过 aspect_ratio 决定尺寸）
            system_instruction: 附加到 prompt 前的系统指令
            model: 模型 ID，默认 "gpt-image-2"
            timeout_seconds: 超时秒数
            max_retries: 超时重试次数
            reference_images: 参考图片列表（路径/URL/data URL），有值时走图生图

        Returns:
            图片二进制数据（PNG/JPEG）
        """
        if reference_images:
            return self.edit_image(
                prompt=prompt,
                reference_images=reference_images,
                aspect_ratio=aspect_ratio,
                system_instruction=system_instruction,
                model=model,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )

        effective_model = model or self.default_model
        effective_timeout = timeout_seconds or self.IMAGE_TIMEOUT_SECONDS
        size = self._aspect_ratio_to_size(aspect_ratio)

        full_prompt = prompt
        if system_instruction:
            full_prompt = f"{system_instruction}\n\n{prompt}"

        def _call() -> bytes:
            response = self._client.images.generate(
                model=effective_model,
                prompt=full_prompt,
                n=1,
                size=size,
                response_format="url",
            )
            return self._extract_result(response)

        return self._call_with_timeout_retry(
            _call,
            timeout_seconds=effective_timeout,
            max_retries=max_retries,
            action_label=f"OpenAI Image text-to-image ({effective_model})",
        )

    def edit_image(
        self,
        prompt: str,
        reference_images: List[str],
        aspect_ratio: str = "16:9",
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 1,
    ) -> bytes:
        """图生图：基于参考图片和 prompt 生成新图片（images.edit）。

        Args:
            prompt: 编辑指令或描述
            reference_images: 参考图片列表（本地路径 / HTTP URL / data URL），
                              第一张作为主图，其余作为额外参考
            aspect_ratio: 输出宽高比
            system_instruction: 附加到 prompt 前的系统指令
            model: 模型 ID，默认 "gpt-image-2"
            timeout_seconds: 超时秒数
            max_retries: 超时重试次数

        Returns:
            生成的图片二进制数据
        """
        import io

        effective_model = model or self.default_model
        effective_timeout = timeout_seconds or self.IMAGE_TIMEOUT_SECONDS
        size = self._aspect_ratio_to_size(aspect_ratio)

        full_prompt = prompt
        if system_instruction:
            full_prompt = f"{system_instruction}\n\n{prompt}"

        # 加载所有参考图片字节
        image_files: List[bytes] = []
        for ref in reference_images:
            if not ref:
                continue
            try:
                image_files.append(self._load_image_file(ref))
            except Exception as exc:
                _logger.warning("Failed to load reference image %s: %s", ref, exc)

        if not image_files:
            raise OpenAIImageAPIError("edit_image: no valid reference images provided")

        def _call() -> bytes:
            # gpt-image-2 images.edit 支持多图输入
            if len(image_files) == 1:
                img_io = io.BytesIO(image_files[0])
                img_io.name = "image.png"
                response = self._client.images.edit(
                    model=effective_model,
                    image=img_io,
                    prompt=full_prompt,
                    n=1,
                    size=size,
                )
            else:
                # 多图：传 list
                img_ios = []
                for i, img_bytes in enumerate(image_files):
                    buf = io.BytesIO(img_bytes)
                    buf.name = f"image_{i}.png"
                    img_ios.append(buf)
                response = self._client.images.edit(
                    model=effective_model,
                    image=img_ios,
                    prompt=full_prompt,
                    n=1,
                    size=size,
                )
            return self._extract_result(response)

        return self._call_with_timeout_retry(
            _call,
            timeout_seconds=effective_timeout,
            max_retries=max_retries,
            action_label=f"OpenAI Image image-to-image ({effective_model})",
        )


def get_openai_image_client(model: Optional[str] = None) -> OpenAIImageClient:
    """获取 OpenAIImageClient 实例。"""
    return OpenAIImageClient(model=model or OpenAIImageClient._DEFAULT_MODEL)
