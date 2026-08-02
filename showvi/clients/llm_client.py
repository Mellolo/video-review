"""
LLM / VLM 统一客户端
提供统一的接口用于 LLM 决策和 VLM 评价
支持两种后端：
  - "api_key"            : 使用 GEMINI_API_KEY 直连 (Gemini Developer API)
  - "openai_compatible"  : 任意 OpenAI 兼容 API (默认)
"""

import os
import json
import base64
import time
import shutil
import tempfile
import logging
import threading
from contextlib import contextmanager
from typing import Dict, Any, Optional, List, Callable
from pathlib import Path

import httpx
from google import genai
from google.genai import types

try:
    from openai import OpenAI as _OpenAI
except ImportError:  # pragma: no cover
    _OpenAI = None  # type: ignore[assignment,misc]


_logger = logging.getLogger("video_agent.llm_client")

_cancel_local = threading.local()
_active_clients_lock = threading.Lock()
_active_clients_by_token: Dict[str, set] = {}


def _current_cancel_checker() -> Optional[Callable[[], bool]]:
    return getattr(_cancel_local, "checker", None)


def _current_cancel_token() -> Optional[str]:
    return getattr(_cancel_local, "token", None)


@contextmanager
def llm_cancel_scope(token: str, checker: Optional[Callable[[], bool]] = None):
    prev_token = _current_cancel_token()
    prev_checker = _current_cancel_checker()
    _cancel_local.token = token
    _cancel_local.checker = checker
    try:
        yield
    finally:
        if prev_token is None:
            try:
                delattr(_cancel_local, "token")
            except AttributeError:
                pass
        else:
            _cancel_local.token = prev_token

        if prev_checker is None:
            try:
                delattr(_cancel_local, "checker")
            except AttributeError:
                pass
        else:
            _cancel_local.checker = prev_checker


def _register_active_client(token: Optional[str], client: "LLMClient"):
    if not token:
        return
    with _active_clients_lock:
        clients = _active_clients_by_token.setdefault(token, set())
        clients.add(client)


def _unregister_active_client(token: Optional[str], client: "LLMClient"):
    if not token:
        return
    with _active_clients_lock:
        clients = _active_clients_by_token.get(token)
        if not clients:
            return
        clients.discard(client)
        if not clients:
            _active_clients_by_token.pop(token, None)


def cancel_llm_scope(token: Optional[str]):
    if not token:
        return
    with _active_clients_lock:
        clients = list(_active_clients_by_token.get(token, set()))
    for client in clients:
        try:
            client.close()
        except Exception as exc:
            _logger.warning("Failed to close LLM client for token %s: %s", token, exc)


class ImageGenerationBlockedError(Exception):
    """Raised when image generation is blocked by safety/content filters."""

    def __init__(self, reason: str, message: str = ""):
        self.reason = reason
        super().__init__(message or f"Image generation blocked: {reason}")


class ProhibitedContentError(Exception):
    """Raised when the API blocks a prompt due to PROHIBITED_CONTENT.

    This is a hard block that cannot be bypassed via safety_settings.
    Callers should sanitize the prompt (e.g. strip age references) and retry.
    """

    def __init__(self, message: str = ""):
        super().__init__(message or "Prompt blocked: PROHIBITED_CONTENT")


class LLMTimeoutError(TimeoutError):
    """Raised when an LLM request exceeds the configured timeout."""


class LLMClient:
    """统一的 LLM / VLM 客户端。

    支持两种后端:
      - "api_key"            : 使用 GEMINI_API_KEY 直连
      - "openai_compatible"  : 任意 OpenAI 兼容 API (默认)
    通过环境变量 LLM_PROVIDER 切换，或构造时传入 backend 参数。
    """

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass

    DEFAULT_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", os.getenv("GEMINI_TIMEOUT_SECONDS", "120")))
    IMAGE_TIMEOUT_SECONDS = int(os.getenv("LLM_IMAGE_TIMEOUT_SECONDS", os.getenv("GEMINI_IMAGE_TIMEOUT_SECONDS", "180")))
    VIDEO_TIMEOUT_SECONDS = int(os.getenv("LLM_VIDEO_TIMEOUT_SECONDS", os.getenv("GEMINI_VIDEO_TIMEOUT_SECONDS", "180")))
    VIDEO_FILE_PROCESSING_TIMEOUT_SECONDS = int(
        os.getenv("LLM_VIDEO_FILE_PROCESSING_TIMEOUT_SECONDS", os.getenv("GEMINI_VIDEO_FILE_PROCESSING_TIMEOUT_SECONDS", "300"))
    )
    FILE_POLL_INTERVAL_SECONDS = float(os.getenv("LLM_FILE_POLL_INTERVAL_SECONDS", os.getenv("GEMINI_FILE_POLL_INTERVAL_SECONDS", "2")))
    RETRY_DELAYS_SECONDS = [2, 5, 10]

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-3-flash-preview",
        backend: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        """
        初始化 LLM 客户端

        Args:
            api_key: API 密钥
            model: 默认模型
            backend: "api_key" | "openai_compatible"
                     None 则读 LLM_PROVIDER / GEMINI_BACKEND 环境变量
            base_url: OpenAI 兼容模式的 base URL（仅 openai_compatible 使用）
        """
        self.backend = (
            backend
            or os.getenv("LLM_PROVIDER")
            or os.getenv("GEMINI_BACKEND", "api_key")
        ).strip().lower()
        self.default_model = model

        if api_key and self.backend not in ("api_key", "openai_compatible"):
            self.backend = "api_key"

        if self.backend == "openai_compatible":
            self._init_openai_compatible(api_key, base_url)
            return

        max_timeout_seconds = max(
            self.DEFAULT_TIMEOUT_SECONDS,
            self.IMAGE_TIMEOUT_SECONDS,
            self.VIDEO_TIMEOUT_SECONDS,
            self.VIDEO_FILE_PROCESSING_TIMEOUT_SECONDS,
        )
        timeout = httpx.Timeout(
            float(max_timeout_seconds),
            connect=min(60.0, float(max_timeout_seconds)),
            write=float(max_timeout_seconds),
            read=float(max_timeout_seconds),
            pool=float(max_timeout_seconds),
        )
        http_options = types.HttpOptions(
            timeout=self._timeout_ms(max_timeout_seconds),
            httpx_client=httpx.Client(timeout=timeout),
        )

        self._init_api_key(api_key, http_options)

    def _init_api_key(self, api_key: Optional[str], http_options: types.HttpOptions):
        """使用 API Key 直连模式初始化"""
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not found in environment")
        self.client = genai.Client(api_key=self.api_key, http_options=http_options)
        _logger.info("LLMClient initialized with API Key backend")

    def _init_openai_compatible(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        """通用 OpenAI 兼容模式 — base_url 和 model 完全由用户指定。"""
        if _OpenAI is None:
            raise ImportError(
                "openai package is required for openai_compatible backend. "
                "Install it with: pip install openai"
            )
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        if not self.api_key:
            raise ValueError("LLM_API_KEY not found in environment")
        resolved_base = base_url or os.getenv("LLM_BASE_URL")
        if not resolved_base:
            raise ValueError("LLM_BASE_URL not found in environment")
        self._openai_client = _OpenAI(
            api_key=self.api_key,
            base_url=resolved_base,
            timeout=float(self.DEFAULT_TIMEOUT_SECONDS),
            max_retries=0,
        )
        self.client = None  # type: ignore[assignment]
        _logger.info("LLMClient initialized with OpenAI-compatible backend (base=%s)", resolved_base)

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Safely extract text from an OpenAI-compatible API response.

        Handles all common response shapes:
        1. Standard ChatCompletion object  → .choices[0].message.content
        2. Stream / iterator of chunks     → consume and concatenate delta tokens
        3. Raw SSE text (``data: {...}``)   → parse JSON lines, extract content
        4. Plain string                     → return as-is
        """
        # 1) Standard ChatCompletion object
        if not isinstance(response, str):
            # Stream object (iterable of chunks) — consume it
            if hasattr(response, "__iter__") and not hasattr(response, "choices"):
                return LLMClient._consume_stream(response)
            try:
                return response.choices[0].message.content or ""
            except (AttributeError, IndexError, TypeError):
                pass
            return str(response)

        # 2) String — might be raw SSE or plain text
        return LLMClient._parse_sse_text(response)

    @staticmethod
    def _consume_stream(stream) -> str:
        """Consume an OpenAI-style streaming response and return full text."""
        parts: list[str] = []
        try:
            for chunk in stream:
                for choice in getattr(chunk, "choices", []):
                    delta = getattr(choice, "delta", None)
                    if delta:
                        tok = getattr(delta, "content", None)
                        if tok:
                            parts.append(tok)
        except Exception as exc:
            _logger.warning("Error consuming stream: %s", exc)
        return "".join(parts)

    @staticmethod
    def _parse_sse_text(raw: str) -> str:
        """Extract concatenated content from raw SSE stream text.

        If *raw* doesn't look like SSE data, return it unchanged.
        Handles multi-line data payloads by buffering until a valid JSON
        object is found.
        """
        if not raw.lstrip().startswith("data:"):
            return raw

        parts: list[str] = []
        json_buf = ""

        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.startswith("data:"):
                payload = stripped[len("data:"):].strip()
                if payload == "[DONE]":
                    continue
                json_buf = payload
            elif json_buf and stripped:
                json_buf += stripped
            else:
                json_buf = ""
                continue

            try:
                obj = json.loads(json_buf)
            except json.JSONDecodeError:
                continue

            json_buf = ""
            for choice in obj.get("choices", []):
                delta = choice.get("delta") or {}
                tok = delta.get("content")
                if tok:
                    parts.append(tok)
                msg = choice.get("message") or {}
                tok = msg.get("content")
                if tok:
                    parts.append(tok)

        return "".join(parts) if parts else raw

    def _resolve_model(self, model: Optional[str] = None) -> str:
        """Return the effective model name."""
        return model or self.default_model

    _LANGUAGE_FOLLOW_INSTRUCTION = (
        "\n\n[LANGUAGE RULE] You MUST respond in the same language as the user's input. "
        "If the user writes in English, ALL your output — including narrative, character names, "
        "location names, prop names, descriptions, dialogue, JSON field values, etc. — MUST be in English. "
        "If the user writes in Chinese, respond in Chinese. "
        "This rule applies to ALL text you generate, regardless of the language used in this system prompt."
    )

    @classmethod
    def _inject_language_instruction(cls, system_instruction: Optional[str]) -> Optional[str]:
        if not system_instruction:
            return system_instruction
        return system_instruction + cls._LANGUAGE_FOLLOW_INSTRUCTION

    _DEFAULT_SAFETY_SETTINGS = [
        types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",
                            threshold="OFF"),
        types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",
                            threshold="OFF"),
        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                            threshold="OFF"),
        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT",
                            threshold="OFF"),
    ]

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return isinstance(exc, TimeoutError) or "timed out" in msg or "timeout" in msg

    @staticmethod
    def _timeout_ms(timeout_seconds: Optional[int]) -> int:
        timeout = timeout_seconds or LLMClient.DEFAULT_TIMEOUT_SECONDS
        return int(timeout * 1000)

    def _http_options(self, timeout_seconds: Optional[int]) -> types.HttpOptions:
        timeout = timeout_seconds or self.DEFAULT_TIMEOUT_SECONDS
        return types.HttpOptions(timeout=self._timeout_ms(timeout))

    @staticmethod
    def _is_ascii_safe(text: str) -> bool:
        try:
            text.encode("ascii")
            return True
        except UnicodeEncodeError:
            return False

    def _prepare_ascii_safe_upload_file(self, file_path: str) -> tuple[str, Optional[str], str]:
        """Return an ASCII-safe upload path and display name for SDK file uploads."""
        resolved = str(Path(file_path).resolve())
        original_name = Path(file_path).name
        if self._is_ascii_safe(resolved) and self._is_ascii_safe(original_name):
            return resolved, None, original_name

        suffix = Path(file_path).suffix or ".bin"
        temp_dir = tempfile.mkdtemp(prefix="llm_upload_")
        safe_name = f"upload{suffix.lower()}"
        safe_path = Path(temp_dir) / safe_name
        shutil.copy2(file_path, safe_path)
        return str(safe_path), temp_dir, safe_name

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
        cancel_token = _current_cancel_token()

        _register_active_client(cancel_token, self)
        try:
            for attempt in range(1, max_retries + 1):
                cancel_checker = _current_cancel_checker()
                if cancel_checker and cancel_checker():
                    raise LLMTimeoutError(f"{action_label} cancelled by user")
                try:
                    return call()
                except Exception as exc:
                    if cancel_checker and cancel_checker():
                        raise LLMTimeoutError(f"{action_label} cancelled by user") from exc
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
                        delay = self.RETRY_DELAYS_SECONDS[min(attempt - 1, len(self.RETRY_DELAYS_SECONDS) - 1)]
                        time.sleep(delay)
        finally:
            _unregister_active_client(cancel_token, self)

        raise LLMTimeoutError(
            f"{action_label} timed out after {max_retries} attempts "
            f"(timeout={effective_timeout}s): {last_err}"
        ) from last_err

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
        """
        生成文本响应

        Args:
            prompt: 用户提示
            system_instruction: 系统指令
            temperature: 温度参数
            max_tokens: 最大 token 数
            response_format: 响应格式（"json_object" 或 None）
            response_schema: JSON Schema 定义（用于结构化输出）
            model: 使用的模型（None = 使用默认）

        Returns:
            生成的文本
        """
        system_instruction = self._inject_language_instruction(system_instruction)
        if self.backend == "openai_compatible":
            return self._openai_generate_text(
                prompt=prompt,
                system_instruction=system_instruction,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                response_schema=response_schema,
                model=model,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )

        model_name = model or self.default_model

        # 构建配置
        config_params = {
            "temperature": temperature,
            "safety_settings": self._DEFAULT_SAFETY_SETTINGS,
            "http_options": self._http_options(timeout_seconds),
        }

        if max_tokens:
            config_params["max_output_tokens"] = max_tokens

        # 使用结构化输出（优先）
        if response_schema:
            config_params["response_mime_type"] = "application/json"
            config_params["response_json_schema"] = response_schema
        elif response_format == "json_object":
            config_params["response_mime_type"] = "application/json"

        if system_instruction:
            config_params["system_instruction"] = system_instruction

        config = types.GenerateContentConfig(**config_params)
        # 调用 API
        response = self._call_with_timeout_retry(
            lambda: self.client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            ),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            action_label=f"Gemini text generation ({model_name})",
        )

        if response.candidates:
            c = response.candidates[0]
            fr = getattr(c, "finish_reason", None)
            if fr and str(fr) != "STOP" and str(fr) != "FinishReason.STOP":
                print(f"  [GEMINI] ⚠ finish_reason={fr}  "
                      f"text_len={len(response.text) if response.text else 0}  "
                      f"model={model_name}")
                if "MAX_TOKENS" in str(fr) and response_schema:
                    usage = getattr(response, "usage_metadata", None)
                    think_tok = getattr(usage, "thoughts_token_count", 0) if usage else 0
                    raise ValueError(
                        f"Structured output truncated (finish_reason={fr}, "
                        f"text_len={len(response.text) if response.text else 0}, "
                        f"thinking_tokens={think_tok}). "
                        f"Increase max_tokens or remove the limit."
                    )

        # Check for PROHIBITED_CONTENT hard block (cannot be bypassed)
        n_cand = len(response.candidates) if response.candidates else 0
        pf = getattr(response, "prompt_feedback", None)
        if n_cand == 0 and pf:
            block_reason = str(getattr(pf, "block_reason", ""))
            if "PROHIBITED_CONTENT" in block_reason:
                print(f"  [LLM] ✘ PROHIBITED_CONTENT — prompt blocked by content policy")
                raise ProhibitedContentError(
                    f"Prompt blocked: {block_reason}"
                )

        text = response.text
        if not text or len(text.strip()) == 0:
            usage = getattr(response, "usage_metadata", None)
            fr = None
            if response.candidates:
                fr = getattr(response.candidates[0], "finish_reason", None)
            print(f"  [GEMINI] ⚠ empty response  "
                  f"finish_reason={fr}  candidates={n_cand}  "
                  f"model={model_name}  "
                  f"usage={usage}")
            if pf:
                print(f"  [GEMINI] prompt_feedback={pf}")

        return text or ""

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
        """
        使用视觉能力生成响应（VLM）
        """
        system_instruction = self._inject_language_instruction(system_instruction)
        if self.backend == "openai_compatible":
            return self._openai_generate_with_vision(
                text_prompt=text_prompt,
                image_paths=image_paths,
                system_instruction=system_instruction,
                temperature=temperature,
                response_format=response_format,
                response_schema=response_schema,
                model=model,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )

        model_name = model or self.default_model

        # 构建内容部分
        parts = [text_prompt]

        # 添加图片
        for img_path in image_paths:
            if img_path.startswith('data:image'):
                # Base64 编码的图片
                parts.append(types.Part.from_uri(img_path))
            else:
                # 本地文件
                try:
                    from PIL import Image
                    image = Image.open(img_path)
                    parts.append(image)
                except Exception as e:
                    print(f"Warning: Failed to load image {img_path}: {e}")

        # 构建配置
        config_params = {
            "temperature": temperature,
            "http_options": self._http_options(timeout_seconds),
        }

        # 使用结构化输出（优先）
        if response_schema:
            config_params["response_mime_type"] = "application/json"
            config_params["response_json_schema"] = response_schema
        elif response_format == "json_object":
            config_params["response_mime_type"] = "application/json"

        if system_instruction:
            config_params["system_instruction"] = system_instruction

        config = types.GenerateContentConfig(**config_params)

        # 调用 API
        response = self._call_with_timeout_retry(
            lambda: self.client.models.generate_content(
                model=model_name,
                contents=parts,
                config=config,
            ),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            action_label=f"Gemini vision generation ({model_name})",
        )

        return response.text or ""

    def generate_with_video(
        self,
        text_prompt: str,
        video_paths: List[str],
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
        response_format: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        auto_adjust_fps: bool = True,
        use_low_resolution: bool = True,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 3,
        leading_parts: Optional[List[Any]] = None,
    ) -> str:
        """
        使用视频进行分析。
        - api_key 后端: 使用 Gemini Files API 上传视频
        - openai_compatible 后端: 使用 OpenAI 兼容 image_url + base64
        """
        system_instruction = self._inject_language_instruction(system_instruction)
        if self.backend == "openai_compatible":
            return self._openai_generate_with_video(
                text_prompt=text_prompt,
                video_paths=video_paths,
                system_instruction=system_instruction,
                temperature=temperature,
                response_format=response_format,
                response_schema=response_schema,
                model=model,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )

        return self._generate_with_video_file_api(
            text_prompt=text_prompt,
            video_paths=video_paths,
            system_instruction=system_instruction,
            temperature=temperature,
            response_format=response_format,
            response_schema=response_schema,
            model=model,
            auto_adjust_fps=auto_adjust_fps,
            use_low_resolution=use_low_resolution,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            leading_parts=leading_parts,
        )

    def _generate_with_video_file_api(
        self,
        text_prompt: str,
        video_paths: List[str],
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
        response_format: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        auto_adjust_fps: bool = True,
        use_low_resolution: bool = True,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 3,
        leading_parts: Optional[List[Any]] = None,
    ) -> str:
        """API Key 模式: 使用 Gemini Files API 上传视频。"""
        import mimetypes
        import cv2

        model_name = model or self.default_model
        effective_timeout = timeout_seconds or self.VIDEO_TIMEOUT_SECONDS
        parts = list(leading_parts or [])
        uploaded_files = []
        temp_upload_dirs: list[str] = []

        try:
            for video_path in video_paths:
                original_video_path = str(video_path)
                file_size = os.path.getsize(original_video_path)
                if file_size > 100 * 1024 * 1024:
                    raise ValueError(f"视频文件 {original_video_path} 大小为 {file_size/(1024*1024):.1f}MB，超过 100MB 限制")

                cap = cv2.VideoCapture(original_video_path)
                fps = cap.get(cv2.CAP_PROP_FPS)
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                duration = frame_count / fps if fps > 0 else 0
                cap.release()

                mime_type, _ = mimetypes.guess_type(original_video_path)
                if not mime_type:
                    mime_type = "video/mp4"

                display_name = Path(original_video_path).name
                video_metadata = None
                if auto_adjust_fps and duration < 3.0 and duration > 0:
                    target_fps = 3.0 / duration
                    video_metadata = types.VideoMetadata(fps=target_fps)
                    print(f"   [视频 {display_name}] 时长 {duration:.2f}s < 3s，设置 fps={target_fps:.2f} (采样 ~3 帧)")
                else:
                    print(f"   [视频 {display_name}] 时长 {duration:.2f}s，使用默认 fps=1")

                upload_file_path, temp_dir, safe_display_name = self._prepare_ascii_safe_upload_file(original_video_path)
                if temp_dir:
                    temp_upload_dirs.append(temp_dir)
                    _logger.info(
                        "检测到非 ASCII 视频路径/文件名，已复制到临时 ASCII 路径上传: %s -> %s",
                        original_video_path,
                        upload_file_path,
                    )

                upload_config = types.UploadFileConfig(
                    mime_type=mime_type,
                    display_name=safe_display_name,
                    http_options=self._http_options(effective_timeout),
                )
                uploaded = self.client.files.upload(file=upload_file_path, config=upload_config)
                uploaded = self.wait_for_file_processing(
                    uploaded.name,
                    timeout_seconds=self.VIDEO_FILE_PROCESSING_TIMEOUT_SECONDS,
                )
                uploaded_files.append(uploaded)

                part = types.Part(
                    file_data=types.FileData(
                        file_uri=uploaded.uri,
                        mime_type=uploaded.mime_type or mime_type,
                    )
                )
                if video_metadata:
                    part.video_metadata = video_metadata
                parts.append(part)

            parts.append(text_prompt)

            config_params = {
                "temperature": temperature,
                "http_options": self._http_options(effective_timeout),
            }

            if use_low_resolution:
                config_params["media_resolution"] = "MEDIA_RESOLUTION_LOW"

            if response_schema:
                config_params["response_mime_type"] = "application/json"
                config_params["response_json_schema"] = response_schema
            elif response_format == "json_object":
                config_params["response_mime_type"] = "application/json"

            if system_instruction:
                config_params["system_instruction"] = system_instruction

            config = types.GenerateContentConfig(**config_params)
            response = self._call_with_timeout_retry(
                lambda: self.client.models.generate_content(
                    model=model_name,
                    contents=parts,
                    config=config,
                ),
                timeout_seconds=effective_timeout,
                max_retries=max_retries,
                action_label=f"Gemini video generation ({model_name})",
            )
            return response.text or ""
        finally:
            for uploaded in uploaded_files:
                try:
                    self.client.files.delete(name=uploaded.name)
                except Exception:
                    pass
            for temp_dir in temp_upload_dirs:
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass

    def wait_for_file_processing(
        self,
        file_name: str,
        *,
        timeout_seconds: Optional[int] = None,
        poll_interval_seconds: Optional[float] = None,
    ) -> Any:
        timeout = timeout_seconds or self.VIDEO_FILE_PROCESSING_TIMEOUT_SECONDS
        poll_interval = poll_interval_seconds or self.FILE_POLL_INTERVAL_SECONDS
        started_at = time.time()
        file_obj = self.client.files.get(name=file_name)

        while getattr(file_obj, "state", None) == "PROCESSING":
            elapsed = time.time() - started_at
            if elapsed >= timeout:
                raise LLMTimeoutError(
                    f"Gemini file processing timed out after {timeout}s: {file_name}"
                )
            time.sleep(poll_interval)
            file_obj = self.client.files.get(name=file_name)

        if getattr(file_obj, "state", None) == "FAILED":
            raise RuntimeError(f"Gemini file processing failed: {file_name}")

        return file_obj

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
        """OpenAI 风格的聊天完成接口"""
        messages = [
            {**msg, "content": msg["content"] + self._LANGUAGE_FOLLOW_INSTRUCTION}
            if msg["role"] == "system" else msg
            for msg in messages
        ]
        if self.backend == "openai_compatible":
            return self._openai_chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                model=model,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )

        # 提取 system instruction
        system_instruction = None
        conversation_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            else:
                conversation_messages.append(msg)

        # 如果只有一条用户消息，使用简单模式
        if len(conversation_messages) == 1 and conversation_messages[0]["role"] == "user":
            return self.generate_text(
                prompt=conversation_messages[0]["content"],
                system_instruction=system_instruction,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                model=model,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )

        # 多轮对话模式
        model_name = model or self.default_model

        # 构建历史
        history = []
        for msg in conversation_messages[:-1]:  # 除了最后一条
            role = "user" if msg["role"] == "user" else "model"
            history.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })

        # 最后一条消息
        last_message = conversation_messages[-1]["content"]

        # 构建配置
        config_params = {
            "temperature": temperature,
            "http_options": self._http_options(timeout_seconds),
        }

        if max_tokens:
            config_params["max_output_tokens"] = max_tokens

        if response_format == "json_object":
            config_params["response_mime_type"] = "application/json"

        if system_instruction:
            config_params["system_instruction"] = system_instruction

        config = types.GenerateContentConfig(**config_params)

        # 使用聊天会话
        chat = self.client.chats.create(
            model=model_name,
            config=config,
            history=history
        )

        response = self._call_with_timeout_retry(
            lambda: chat.send_message(last_message),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            action_label=f"Gemini chat completion ({model_name})",
        )

        return response.text or ""

    def generate_image(
        self,
        prompt: str,
        aspect_ratio: str = "16:9",
        image_size: str = "2K",
        system_instruction: Optional[str] = None,
        model: str = "gemini-3-pro-image-preview",
        timeout_seconds: Optional[int] = None,
        max_retries: int = 1,
        reference_images: Optional[List[str]] = None,
    ) -> bytes:
        """使用 Gemini 原生图片生成（Nano Banana Pro）"""
        if self.backend == "openai_compatible":
            raise NotImplementedError(
                "OpenAI 兼容后端不支持 LLMClient.generate_image，"
                "请使用 OpenAIImageClient 或切换到 google 后端"
            )

        # 构建配置
        config_params = {
            "response_modalities": ["IMAGE"],
            "image_config": types.ImageConfig(
                aspect_ratio=aspect_ratio,
                image_size=image_size
            ),
            "http_options": self._http_options(timeout_seconds or self.IMAGE_TIMEOUT_SECONDS),
        }

        if system_instruction:
            config_params["system_instruction"] = system_instruction

        config = types.GenerateContentConfig(**config_params)

        contents = prompt
        if reference_images:
            mime_map = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".gif": "image/gif",
            }
            parts = []
            for ref_path in reference_images[:3]:
                if not ref_path or not Path(ref_path).exists():
                    continue
                mime = mime_map.get(Path(ref_path).suffix.lower(), "image/png")
                with open(ref_path, "rb") as f:
                    ref_data = f.read()
                parts.append(types.Part(inline_data=types.Blob(data=ref_data, mime_type=mime)))
            parts.append(types.Part(text=prompt))
            contents = [types.Content(parts=parts)]

        # 调用 API
        response = self._call_with_timeout_retry(
            lambda: self.client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            ),
            timeout_seconds=timeout_seconds or self.IMAGE_TIMEOUT_SECONDS,
            max_retries=max_retries,
            action_label=f"Gemini image generation ({model})",
        )

        # Check for blocked responses before iterating parts
        if response.candidates:
            candidate = response.candidates[0]
            finish_reason = getattr(candidate, "finish_reason", None)
            fr_str = str(finish_reason) if finish_reason else ""
            blocked_reasons = {"IMAGE_SAFETY", "SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT"}
            if any(r in fr_str for r in blocked_reasons):
                raise ImageGenerationBlockedError(
                    reason=fr_str,
                    message=f"Image generation blocked (finish_reason={fr_str}), prompt may violate content policy",
                )

        # 提取图片数据
        if response.parts:
            for part in response.parts:
                if part.inline_data is not None:
                    return part.inline_data.data

        # Fallback: check finish_reason for any other non-STOP outcome
        fr_str = ""
        if response.candidates:
            fr_str = str(getattr(response.candidates[0], "finish_reason", ""))
        raise ImageGenerationBlockedError(
            reason=fr_str or "UNKNOWN",
            message=f"No image data in response (finish_reason={fr_str})",
        )

    # ═══════════════════════════════════════════════════════════════════
    #  OpenAI 兼容后端实现
    # ═══════════════════════════════════════════════════════════════════

    def _openai_response_format(
        self,
        response_format: Optional[str],
        response_schema: Optional[Dict[str, Any]],
    ) -> Optional[dict]:
        """构建 OpenAI 兼容的 response_format 参数。

        Many third-party OpenAI-compatible APIs do not support the
        ``json_schema`` structured output mode.  We fall back to the
        simpler ``json_object`` mode which is more widely supported.
        """
        if response_schema or response_format == "json_object":
            return {"type": "json_object"}
        return None

    def _openai_generate_text(
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
        resolved_model = self._resolve_model(model)
        messages: List[Dict[str, Any]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        rf = self._openai_response_format(response_format, response_schema)
        kwargs: Dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if rf:
            kwargs["response_format"] = rf

        def _call():
            return self._openai_client.chat.completions.create(**kwargs)

        response = self._call_with_timeout_retry(
            _call,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            action_label=f"OpenAI text generation ({resolved_model})",
        )
        text = self._extract_text(response)
        if not text.strip():
            _logger.warning("OpenAI API returned empty response for model=%s", resolved_model)
        return text

    def _openai_generate_with_vision(
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
        resolved_model = self._resolve_model(model)
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

        rf = self._openai_response_format(response_format, response_schema)
        kwargs: Dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if rf:
            kwargs["response_format"] = rf

        def _call():
            return self._openai_client.chat.completions.create(**kwargs)

        response = self._call_with_timeout_retry(
            _call,
            timeout_seconds=timeout_seconds or self.IMAGE_TIMEOUT_SECONDS,
            max_retries=max_retries,
            action_label=f"OpenAI vision generation ({resolved_model})",
        )
        return self._extract_text(response)

    def _openai_generate_with_video(
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
    ) -> str:
        """OpenAI 兼容视频输入：通过 image_url + video base64 传递"""
        import mimetypes as _mt

        resolved_model = self._resolve_model(model)
        content_parts: List[Dict[str, Any]] = []

        for video_path in video_paths:
            vp = str(video_path)
            mime, _ = _mt.guess_type(vp)
            if not mime:
                mime = "video/mp4"
            with open(vp, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            size_mb = os.path.getsize(vp) / (1024 * 1024)
            _logger.info("OpenAI video input: %s (%.1fMB)", Path(vp).name, size_mb)
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })

        content_parts.append({"type": "text", "text": text_prompt})

        messages: List[Dict[str, Any]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": content_parts})

        rf = self._openai_response_format(response_format, response_schema)
        kwargs: Dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if rf:
            kwargs["response_format"] = rf

        def _call():
            return self._openai_client.chat.completions.create(**kwargs)

        response = self._call_with_timeout_retry(
            _call,
            timeout_seconds=timeout_seconds or self.VIDEO_TIMEOUT_SECONDS,
            max_retries=max_retries,
            action_label=f"OpenAI video generation ({resolved_model})",
        )
        return self._extract_text(response)

    def _openai_chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 3,
    ) -> str:
        resolved_model = self._resolve_model(model)
        kwargs: Dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if response_format == "json_object":
            kwargs["response_format"] = {"type": "json_object"}

        def _call():
            return self._openai_client.chat.completions.create(**kwargs)

        response = self._call_with_timeout_retry(
            _call,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            action_label=f"OpenAI chat completion ({resolved_model})",
        )
        return self._extract_text(response)


def get_llm_client_by_backend(backend: Optional[str] = None) -> LLMClient:
    """获取 LLM 客户端实例。

    Args:
        backend: "api_key" | "openai_compatible"，None 则读 LLM_PROVIDER 环境变量
    """
    return LLMClient(backend=backend)


# ── Backward-compatible aliases ───────────────────────────────────────
GeminiClient = LLMClient
GeminiTimeoutError = LLMTimeoutError
gemini_cancel_scope = llm_cancel_scope
cancel_gemini_scope = cancel_llm_scope
get_gemini_client = get_llm_client_by_backend
