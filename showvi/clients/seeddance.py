"""
即梦(Seeddance) 视频生成 API 客户端

支持两大模型系列：
- 视频 3.0 系列: 3.0 / 3.0-pro / 3.0-fast / s2.0 / 2.0-pro  (单图+文字, 5/10秒)
- Seedance 2.0 系列: seedance-2.0 / seedance-2.0-fast  (多图+文字, 4-15秒)

基于即梦官网逆向接口，需要从浏览器 Cookie 获取 session_id。
"""

import concurrent.futures
import hashlib
import hmac
import json
import logging
import os
import random
import re
import string
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests

_logger = logging.getLogger("video_agent.seeddance")

# ================================================================
# 配置常量 (from config.py)
# ================================================================

MODEL_MAP = {
    "3.0": "dreamina_ic_generate_video_model_vgfm_3.0",
    "3.0-pro": "dreamina_ic_generate_video_model_vgfm_3.0_pro",
    "3.0-fast": "dreamina_ic_generate_video_model_vgfm_3.0_fast",
    "s2.0": "dreamina_ic_generate_video_model_vgfm_lite",
    "2.0-pro": "dreamina_ic_generate_video_model_vgfm1.0",
    "seedance-2.0": "dreamina_seedance_40_pro",
    "seedance-2.0-fast": "dreamina_seedance_40",
    "seedance-2.0-vip": "dreamina_seedance_40_pro_vision",
    "seedance-2.0-fast-vip": "dreamina_seedance_40_vision",
}

BENEFIT_TYPE_MAP = {
    "3.0": "basic_video_operation_vgfm_v_three",
    "3.0-pro": "basic_video_operation_vgfm_v_three",
    "3.0-fast": "basic_video_operation_vgfm_v_three",
    "s2.0": "basic_video_operation_vgfm_v_three",
    "2.0-pro": "basic_video_operation_vgfm_v_three",
    "seedance-2.0": "dreamina_video_seedance_20_pro",
    "seedance-2.0-fast": "dreamina_seedance_20_fast",
    "seedance-2.0-vip": "seedance_20_pro_720p_output",
    "seedance-2.0-fast-vip": "seedance_20_fast_720p_output",
}

SEEDANCE_MODELS = {"seedance-2.0", "seedance-2.0-fast", "seedance-2.0-vip", "seedance-2.0-fast-vip"}
SEEDANCE_DRAFT_VERSION = "3.3.9"

JIMENG_BASE_URL = "https://jimeng.jianying.com"
IMAGEX_BASE_URL = "https://imagex.bytedanceapi.com"
UPLOAD_SERVICE_ID = "tb4s082cfz"

DEFAULT_ASSISTANT_ID = 513695
VERSION_CODE = "5.8.0"
PLATFORM_CODE = "7"
DRAFT_VERSION = "3.2.8"

VIDEO_ASPECT_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]
VALID_DURATIONS_V3 = [5, 10]
VALID_DURATIONS_SEEDANCE = list(range(4, 16))

POLL_INTERVAL_MIN = 10.0
POLL_INTERVAL_FALLBACK = 60.0
POLL_MAX_RETRIES = 1000
POLL_INITIAL_DELAY = 10.0

FAKE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Appid": str(DEFAULT_ASSISTANT_ID),
    "Appvr": VERSION_CODE,
    "Origin": "https://jimeng.jianying.com",
    "Pragma": "no-cache",
    "Priority": "u=1, i",
    "Referer": "https://jimeng.jianying.com",
    "Pf": PLATFORM_CODE,
    "Sec-Ch-Ua": '"Google Chrome";v="142", "Chromium";v="142", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    ),
}


# ================================================================
# 异常定义 — 继承自 clients.base 共享层
# ================================================================

from .base import (
    ClientError,
    InsufficientCreditsError as _BaseInsufficientCreditsError,
    ContentFilteredError as _BaseContentFilteredError,
    SessionExpiredError as _BaseSessionExpiredError,
    GenerationTimeoutError as _BaseGenerationTimeoutError,
    GenerationFailedError as _BaseGenerationFailedError,
)
from .browser_workers import get_browser_worker_registry


class SeeddanceError(ClientError):
    """基础异常"""


class InsufficientCreditsError(_BaseInsufficientCreditsError, SeeddanceError):
    """积分不足"""


class ContentFilteredError(_BaseContentFilteredError, SeeddanceError):
    """内容被过滤（违规文本/违规图像/人脸等）

    错误信息中包含 fail_starling_message，上层可根据关键词判断具体类型。
    """


class SessionExpiredError(_BaseSessionExpiredError, SeeddanceError):
    """Session 过期"""


class VideoGenerationTimeout(_BaseGenerationTimeoutError, SeeddanceError):
    """视频生成超时"""


class VideoGenerationFailed(_BaseGenerationFailedError, SeeddanceError):
    """视频生成失败"""


# ================================================================
# CRC32 计算
# ================================================================

_CRC32_TABLE = None


def _build_crc32_table():
    global _CRC32_TABLE
    if _CRC32_TABLE is not None:
        return
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 1:
                crc = 0xEDB88320 ^ (crc >> 1)
            else:
                crc = crc >> 1
        table.append(crc)
    _CRC32_TABLE = table


def _calc_crc32(data: bytes) -> str:
    _build_crc32_table()
    crc = 0xFFFFFFFF
    for b in data:
        crc = _CRC32_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    crc = crc ^ 0xFFFFFFFF
    crc = crc & 0xFFFFFFFF
    return format(crc, "08x")


# ================================================================
# AWS4-HMAC-SHA256 签名
# ================================================================

def _aws4_sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _generate_aws_authorization(
    method: str,
    query_params: dict,
    access_key_id: str,
    secret_access_key: str,
    session_token: str,
    payload: str = "",
) -> dict:
    """生成 AWS4 签名请求头，用于 ImageX 上传"""
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    region = "cn-north-1"
    service = "imagex"

    headers_to_sign = {"x-amz-date": amz_date, "x-amz-security-token": session_token}

    payload_hash = hashlib.sha256(payload.encode("utf-8") if payload else b"").hexdigest()
    if method.upper() == "POST" and payload:
        headers_to_sign["x-amz-content-sha256"] = payload_hash

    signed_headers_list = sorted(headers_to_sign.keys())
    signed_headers = ";".join(signed_headers_list)
    canonical_headers = "".join(f"{k}:{headers_to_sign[k]}\n" for k in signed_headers_list)

    canonical_qs = "&".join(f"{k}={v}" for k, v in sorted(query_params.items()))

    canonical_request = "\n".join([
        method.upper(),
        "/",
        canonical_qs,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])

    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    k_date = _aws4_sign(("AWS4" + secret_access_key).encode("utf-8"), date_stamp)
    k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode("utf-8"), hashlib.sha256).digest()
    k_signing = hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 "
        f"Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    result_headers = {
        "Authorization": authorization,
        "X-Amz-Date": amz_date,
        "X-Amz-Security-Token": session_token,
    }
    if "x-amz-content-sha256" in headers_to_sign:
        result_headers["X-Amz-Content-Sha256"] = headers_to_sign["x-amz-content-sha256"]

    return result_headers


# ================================================================
# 浏览器代理服务 (仅 Seedance 2.0 需要)
# ================================================================

_BDMS_READY_TIMEOUT = 30000
_BLOCKED_RESOURCE_TYPES = {"image", "font", "stylesheet", "media"}
_SCRIPT_WHITELIST_DOMAINS = [
    "vlabstatic.com",
    "bytescm.com",
    "jianying.com",
    "byteimg.com",
]


class _BrowserService:
    """
    管理 Playwright 无头浏览器会话，用于代理需要 a_bogus 签名的请求。

    所有 Playwright 操作在独立线程中执行，避免与 google.genai (httpx)
    等库创建的 asyncio 事件循环冲突。
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._initialized = False
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="playwright",
        )

    def _run_in_thread(self, fn, *args, **kwargs):
        """Run fn in the dedicated Playwright thread (no asyncio loop)."""
        future = self._executor.submit(fn, *args, **kwargs)
        return future.result(timeout=120)

    # ── internal methods (always called inside _executor thread) ──

    def _ensure_browser(self):
        if self._browser:
            return
        from playwright.sync_api import sync_playwright
        _logger.info("正在启动 Chromium 无头浏览器...")
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-first-run",
            ],
        )
        _logger.info("Chromium 已启动")

    def _init_page(self, session_id: str, web_id: str, user_id: str):
        if self._page and self._initialized:
            return self._page

        self._ensure_browser()

        if self._context:
            try:
                self._context.close()
            except Exception:
                pass

        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
            ),
        )

        cookies = [
            {"name": "_tea_web_id", "value": str(web_id), "domain": ".jianying.com", "path": "/"},
            {"name": "is_staff_user", "value": "false", "domain": ".jianying.com", "path": "/"},
            {"name": "store-region", "value": "cn-gd", "domain": ".jianying.com", "path": "/"},
            {"name": "uid_tt", "value": str(user_id), "domain": ".jianying.com", "path": "/"},
            {"name": "sid_tt", "value": session_id, "domain": ".jianying.com", "path": "/"},
            {"name": "sessionid", "value": session_id, "domain": ".jianying.com", "path": "/"},
            {"name": "sessionid_ss", "value": session_id, "domain": ".jianying.com", "path": "/"},
        ]
        self._context.add_cookies(cookies)

        def handle_route(route):
            try:
                request = route.request
                resource_type = request.resource_type
                url = request.url

                if resource_type == "document":
                    route.continue_()
                    return
                if resource_type in _BLOCKED_RESOURCE_TYPES:
                    route.abort()
                    return
                if resource_type == "script":
                    is_whitelisted = any(d in url for d in _SCRIPT_WHITELIST_DOMAINS)
                    if not is_whitelisted:
                        route.abort()
                        return
                route.continue_()
            except Exception:
                try:
                    route.continue_()
                except Exception:
                    pass

        self._context.route("**/*", handle_route)

        self._page = self._context.new_page()

        _logger.info(f"正在导航到 jimeng.jianying.com (session: {session_id[:8]}...)")
        self._page.goto("https://jimeng.jianying.com", wait_until="domcontentloaded", timeout=30000)

        try:
            self._page.wait_for_function(
                """() => {
                    return (
                        (window.bdms && window.bdms.init) ||
                        window.byted_acrawler ||
                        (window.fetch && window.fetch.toString().indexOf('native code') === -1)
                    );
                }""",
                timeout=_BDMS_READY_TIMEOUT,
            )
            _logger.info("bdms SDK 已就绪")
        except Exception:
            _logger.warning("bdms SDK 等待超时，继续尝试...")

        self._initialized = True
        return self._page

    def _do_fetch(
        self,
        session_id: str,
        web_id: str,
        user_id: str,
        url: str,
        method: str,
        headers: Optional[dict],
        body: Optional[str],
    ) -> dict:
        page = self._init_page(session_id, web_id, user_id)

        _logger.info(f"通过浏览器代理请求: {method} {url[:80]}...")

        result = page.evaluate(
            """async ({url, method, headers, body}) => {
                const resp = await fetch(url, {
                    method: method,
                    headers: headers,
                    body: body || undefined,
                    credentials: 'include',
                });
                return await resp.json();
            }""",
            {"url": url, "method": method, "headers": headers or {}, "body": body},
        )
        return result

    def _do_close(self):
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
            self._page = None

        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        self._initialized = False
        _logger.info("浏览器已关闭")

    # ── public API (thread-safe, delegates to Playwright thread) ──

    def fetch(
        self,
        session_id: str,
        web_id: str,
        user_id: str,
        url: str,
        method: str = "POST",
        headers: Optional[dict] = None,
        body: Optional[str] = None,
    ) -> dict:
        """通过浏览器代理发送请求，自动附带 a_bogus 签名"""
        return self._run_in_thread(
            self._do_fetch, session_id, web_id, user_id,
            url, method, headers, body,
        )

    def close(self):
        try:
            self._run_in_thread(self._do_close)
        except Exception:
            pass


# ================================================================
# 核心 API 客户端
# ================================================================

class SeeddanceClient:
    """
    即梦(Seeddance) 视频生成统一客户端

    支持视频 3.0 系列和 Seedance 2.0 系列模型。

    核心接口:
        submit_video()    — 提交图片+文字，生成视频
        check_progress()  — 查询生成进度/状态
        wait_for_video()  — 轮询等待完成，返回 {"url": ...} (与 Sora 接口对齐)
        download_video()  — 等待完成并下载视频到本地
    """

    def __init__(self, session_id: Optional[str] = None):
        """
        初始化客户端

        Args:
            session_id: 即梦官网 Cookie 中的 sessionid。
                        若为 None，则从环境变量 SEEDDANCE_SESSION_ID 读取。
        """
        self.session_id = session_id or os.getenv("SEEDDANCE_SESSION_ID", "")
        if not self.session_id:
            raise ValueError(
                "session_id 不能为空，请设置环境变量 SEEDDANCE_SESSION_ID "
                "或从即梦官网 Cookie 中获取"
            )
        self.web_id = str(random.randint(7000000000000000000, 7999999999999999999))
        self.user_id = uuid.uuid4().hex
        self.http = requests.Session()
        self._browser_worker = get_browser_worker_registry().get_or_create(
            provider="jimeng",
            account_key=self.session_id,
            factory=_BrowserService,
        )

    # ── 浏览器代理 (按 provider/account 隔离 worker) ─────────────────

    def _browser_request(self, url: str, data: dict) -> dict:
        body = json.dumps(data)

        result = self._browser_worker.run(
            lambda bs: bs.fetch(
                session_id=self.session_id,
                web_id=self.web_id,
                user_id=self.user_id,
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            ),
            task_name="jimeng_fetch",
        )

        ret = result.get("ret")
        if ret is not None:
            ret_str = str(ret)
            if ret_str == "0":
                return result.get("data", {})
            errmsg = result.get("errmsg", ret_str)
            if ret_str in ("5000", "1006"):
                raise InsufficientCreditsError(f"即梦积分不足: {errmsg}")
            raise SeeddanceError(f"即梦API错误 (ret={ret_str}): {errmsg}")

        return result

    def close(self):
        """释放客户端资源（浏览器 worker 由全局注册表复用）"""
        self.http.close()

    def __del__(self):
        self.close()

    # ── 内部工具 ─────────────────────────────────────────────

    def _generate_cookie(self) -> str:
        return "; ".join([
            f"_tea_web_id={self.web_id}",
            "is_staff_user=false",
            "store-region=cn-gd",
            "store-region-src=uid",
            f"uid_tt={self.user_id}",
            f"uid_tt_ss={self.user_id}",
            f"sid_tt={self.session_id}",
            f"sessionid={self.session_id}",
            f"sessionid_ss={self.session_id}",
        ])

    @staticmethod
    def _generate_sign(uri: str) -> tuple:
        device_time = int(time.time())
        raw = f"9e2c|{uri[-7:]}|{PLATFORM_CODE}|{VERSION_CODE}|{device_time}||11ac"
        sign = hashlib.md5(raw.encode()).hexdigest()
        return device_time, sign

    @staticmethod
    def _uuid() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _random_string(length: int = 11) -> str:
        return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))

    # ── 即梦 API 通用请求 ────────────────────────────────────

    def _jimeng_request(
        self,
        method: str,
        uri: str,
        data: Optional[dict] = None,
        extra_params: Optional[dict] = None,
        max_retries: int = 3,
    ) -> dict:
        device_time, sign = self._generate_sign(uri)

        params = {
            "aid": DEFAULT_ASSISTANT_ID,
            "device_platform": "web",
            "region": "CN",
            "webId": self.web_id,
        }
        if extra_params:
            params.update(extra_params)

        headers = {
            **FAKE_HEADERS,
            "Cookie": self._generate_cookie(),
            "Device-Time": str(device_time),
            "Sign": sign,
            "Sign-Ver": "1",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"

        url = f"{JIMENG_BASE_URL}{uri}"

        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    wait = min(attempt * 1.0, 5.0)
                    _logger.info(f"重试 {uri} (第{attempt}次), 等待 {wait}s")
                    time.sleep(wait)

                resp = self.http.request(
                    method.upper(),
                    url,
                    params=params,
                    json=data,
                    headers=headers,
                    timeout=45,
                )
                
                # 检查响应状态
                if resp.status_code != 200:
                    raise SeeddanceError(
                        f"HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                
                # 尝试解析 JSON
                try:
                    result = resp.json()
                except json.JSONDecodeError as e:
                    # 如果不是 JSON，可能是 HTML 登录页面
                    response_text = resp.text
                    response_preview = response_text[:1000]
                    
                    # 打印详细的调试信息
                    _logger.error(f"JSON 解析失败: {e}")
                    _logger.error(f"响应状态码: {resp.status_code}")
                    _logger.error(f"响应头: {dict(resp.headers)}")
                    _logger.error(f"响应内容 (前1000字符): {response_preview}")
                    
                    if "<html" in response_preview.lower() or "<!doctype" in response_preview.lower():
                        raise SessionExpiredError(
                            "Session ID 已过期或无效，请重新获取。响应返回了 HTML 页面而不是 JSON。"
                        )
                    
                    # 检查是否是多个 JSON 对象
                    if response_text.count('{') > 1:
                        _logger.warning("响应可能包含多个 JSON 对象，尝试解析第一个")
                        try:
                            # 尝试只解析第一个 JSON 对象
                            first_brace = response_text.index('{')
                            # 找到匹配的右括号
                            brace_count = 0
                            end_pos = first_brace
                            for i, char in enumerate(response_text[first_brace:], start=first_brace):
                                if char == '{':
                                    brace_count += 1
                                elif char == '}':
                                    brace_count -= 1
                                    if brace_count == 0:
                                        end_pos = i + 1
                                        break
                            
                            first_json = response_text[first_brace:end_pos]
                            result = json.loads(first_json)
                            _logger.info("成功解析第一个 JSON 对象")
                        except Exception as parse_error:
                            raise SeeddanceError(
                                f"无法解析 JSON 响应: {e}\n"
                                f"尝试解析第一个对象也失败: {parse_error}\n"
                                f"响应内容: {response_preview}"
                            )
                    else:
                        raise SeeddanceError(
                            f"无法解析 JSON 响应: {e}\n响应内容: {response_preview}"
                        )

                ret = result.get("ret")
                if ret is not None:
                    ret_str = str(ret)
                    if ret_str == "0":
                        return result.get("data", {})

                    errmsg = result.get("errmsg", ret_str)
                    if ret_str in ("5000", "1006"):
                        raise InsufficientCreditsError(
                            f"即梦积分不足，请前往即梦官网领取或购买积分: {errmsg}"
                        )
                    raise SeeddanceError(f"即梦API错误 (ret={ret_str}): {errmsg}")

                return result

            except (InsufficientCreditsError, ContentFilteredError, SessionExpiredError):
                raise
            except SeeddanceError:
                if attempt == max_retries:
                    raise
            except Exception as e:
                if attempt == max_retries:
                    raise SeeddanceError(f"请求 {uri} 失败: {e}") from e
                _logger.warning(f"请求 {uri} 出错 (第{attempt + 1}次): {e}")

        raise SeeddanceError(f"请求 {uri} 超过最大重试次数")

    # ── 图片上传 (4步 ImageX 流程) ───────────────────────────

    def _upload_image(self, image_path: str) -> str:
        with open(image_path, "rb") as f:
            file_data = f.read()
        file_size = len(file_data)
        crc32_hex = _calc_crc32(file_data)

        _logger.info(f"上传图片: {image_path} ({file_size} bytes, crc32={crc32_hex})")

        # 步骤 1: 获取上传令牌
        token_result = self._jimeng_request(
            "POST",
            "/mweb/v1/get_upload_token",
            data={"scene": 2},
            extra_params={"da_version": "3.2.2", "aigc_features": "app_lip_sync"},
        )
        access_key_id = token_result.get("access_key_id")
        secret_access_key = token_result.get("secret_access_key")
        session_token_upload = token_result.get("session_token")
        service_id = token_result.get("service_id") or UPLOAD_SERVICE_ID

        if not all([access_key_id, secret_access_key, session_token_upload]):
            raise SeeddanceError("获取上传令牌失败，session可能已过期")

        _logger.info(f"上传令牌获取成功 (serviceId={service_id})")

        # 步骤 2: 申请上传权限 (AWS4 签名 GET 请求)
        apply_params = {
            "Action": "ApplyImageUpload",
            "Version": "2018-08-01",
            "ServiceId": service_id,
            "FileSize": str(file_size),
            "s": self._random_string(),
        }

        auth_headers = _generate_aws_authorization(
            "GET", apply_params, access_key_id, secret_access_key, session_token_upload
        )
        apply_headers = {
            **auth_headers,
            "Accept": "*/*",
            "Origin": JIMENG_BASE_URL,
            "Referer": f"{JIMENG_BASE_URL}/ai-tool/video/generate",
            "User-Agent": FAKE_HEADERS["User-Agent"],
        }

        apply_resp = self.http.get(
            IMAGEX_BASE_URL,
            params=apply_params,
            headers=apply_headers,
            timeout=30,
        )
        apply_result = apply_resp.json()

        if apply_result.get("ResponseMetadata", {}).get("Error"):
            error_info = apply_result["ResponseMetadata"]["Error"]
            raise SeeddanceError(f"申请上传权限失败: {json.dumps(error_info, ensure_ascii=False)}")

        upload_address = apply_result.get("Result", {}).get("UploadAddress", {})
        store_infos = upload_address.get("StoreInfos", [])
        upload_hosts = upload_address.get("UploadHosts", [])
        session_key = upload_address.get("SessionKey")

        if not store_infos or not upload_hosts:
            raise SeeddanceError("获取上传地址失败")

        store_info = store_infos[0]
        upload_host = upload_hosts[0]
        upload_url = f"https://{upload_host}/upload/v1/{store_info['StoreUri']}"

        _logger.info(f"上传图片到: {upload_host}")

        # 步骤 3: 上传图片文件
        upload_resp = self.http.post(
            upload_url,
            data=file_data,
            headers={
                "Accept": "*/*",
                "Authorization": store_info["Auth"],
                "Content-CRC32": crc32_hex,
                "Content-Disposition": 'attachment; filename="image.jpg"',
                "Content-Type": "application/octet-stream",
                "Origin": JIMENG_BASE_URL,
                "Referer": f"{JIMENG_BASE_URL}/ai-tool/video/generate",
                "User-Agent": FAKE_HEADERS["User-Agent"],
            },
            timeout=60,
        )
        if upload_resp.status_code != 200:
            raise SeeddanceError(f"图片上传失败: HTTP {upload_resp.status_code}")

        _logger.info("图片文件上传成功")

        # 步骤 4: 提交上传 (AWS4 签名 POST 请求)
        commit_params = {
            "Action": "CommitImageUpload",
            "Version": "2018-08-01",
            "ServiceId": service_id,
        }
        commit_body = json.dumps({"SessionKey": session_key})

        commit_auth_headers = _generate_aws_authorization(
            "POST", commit_params, access_key_id, secret_access_key,
            session_token_upload, payload=commit_body,
        )
        commit_headers = {
            **commit_auth_headers,
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": JIMENG_BASE_URL,
            "Referer": f"{JIMENG_BASE_URL}/ai-tool/video/generate",
            "User-Agent": FAKE_HEADERS["User-Agent"],
        }

        commit_resp = self.http.post(
            IMAGEX_BASE_URL,
            params=commit_params,
            data=commit_body,
            headers=commit_headers,
            timeout=30,
        )
        commit_result = commit_resp.json()

        if commit_result.get("ResponseMetadata", {}).get("Error"):
            error_info = commit_result["ResponseMetadata"]["Error"]
            raise SeeddanceError(f"提交上传失败: {json.dumps(error_info, ensure_ascii=False)}")

        results = commit_result.get("Result", {}).get("Results", [])
        if not results:
            raise SeeddanceError("提交上传响应缺少结果")
        if results[0].get("UriStatus") != 2000:
            raise SeeddanceError(f"图片上传状态异常: UriStatus={results[0].get('UriStatus')}")

        image_uri = results[0].get("Uri", "")
        plugin_results = commit_result.get("Result", {}).get("PluginResult", [])
        if plugin_results:
            image_uri = plugin_results[0].get("ImageUri", image_uri)

        _logger.info(f"图片上传完成: {image_uri}")
        return image_uri

    # ── 接口 1: 提交视频生成 ─────────────────────────────────

    def submit_video(
        self,
        image_path: "str | list[str]",
        prompt: str = "",
        duration: int = 5,
        aspect_ratio: str = "16:9",
        model: str = "3.0",
    ) -> str:
        """
        提交图片+文字生成视频。

        支持两大模型系列：
        - 视频 3.0 系列: "3.0" / "3.0-pro" / "3.0-fast" / "s2.0" / "2.0-pro"
        - Seedance 2.0 系列: "seedance-2.0" / "seedance-2.0-fast"

        Seedance 2.0 支持多张参考图，prompt 中可用 @图1、@图2 引用。

        Args:
            image_path: 本地图片路径，str 或 list[str]（多图仅 Seedance 2.0 支持）
            prompt:     提示词（可选）
            duration:   视频时长。3.0系列 5/10秒，Seedance系列 4-15秒
            aspect_ratio: 画面比例，如 "16:9", "9:16", "1:1"
            model:      模型版本

        Returns:
            history_id: 用于后续查询进度和下载
        """
        image_paths = [image_path] if isinstance(image_path, str) else list(image_path)

        if not image_paths:
            raise ValueError("至少需要提供一张图片")
        for p in image_paths:
            if not os.path.isfile(p):
                raise FileNotFoundError(f"图片文件不存在: {p}")

        if aspect_ratio not in VIDEO_ASPECT_RATIOS:
            raise ValueError(f"aspect_ratio 必须是 {VIDEO_ASPECT_RATIOS} 之一")

        model_key = MODEL_MAP.get(model)
        if not model_key:
            raise ValueError(f"model 必须是 {list(MODEL_MAP.keys())} 之一")

        is_seedance = model in SEEDANCE_MODELS

        if not is_seedance and len(image_paths) > 1:
            raise ValueError(f"视频 3.0 系列仅支持单张图片，收到 {len(image_paths)} 张")

        valid_durations = VALID_DURATIONS_SEEDANCE if is_seedance else VALID_DURATIONS_V3
        if duration not in valid_durations:
            raise ValueError(f"model={model} 的 duration 必须是 {valid_durations} 之一")

        benefit_type = BENEFIT_TYPE_MAP.get(model, "basic_video_operation_vgfm_v_three")
        duration_ms = duration * 1000

        _logger.info(
            f"提交视频生成: model={model}, ratio={aspect_ratio}, "
            f"duration={duration}s, images={len(image_paths)}, prompt={prompt[:50]}..."
        )
        print(f"[SEEDDANCE] 提交视频生成: model={model}, {duration}s, {len(image_paths)}张图片")

        # 上传图片
        image_uris: list = []
        for i, p in enumerate(image_paths):
            if len(image_paths) > 1:
                _logger.info(f"上传第 {i + 1}/{len(image_paths)} 张图片...")
                print(f"[SEEDDANCE]   上传图片 {i + 1}/{len(image_paths)}...")
            else:
                print(f"[SEEDDANCE]   上传图片...")
            image_uris.append(self._upload_image(p))

        # 根据模型系列构建 draft_content
        if is_seedance:
            draft_content, metrics_extra, submit_id = self._build_seedance_draft(
                model_key, image_uris, prompt, duration_ms, aspect_ratio,
            )
            da_version = SEEDANCE_DRAFT_VERSION
        else:
            draft_content, metrics_extra, submit_id = self._build_v3_draft(
                model_key, image_uris[0], prompt, duration_ms, aspect_ratio,
            )
            da_version = DRAFT_VERSION

        request_body = {
            "extend": {
                "root_model": model_key,
                "m_video_commerce_info": {
                    "benefit_type": benefit_type,
                    "resource_id": "generate_video",
                    "resource_id_type": "str",
                    "resource_sub_type": "aigc",
                },
                "m_video_commerce_info_list": [
                    {
                        "benefit_type": benefit_type,
                        "resource_id": "generate_video",
                        "resource_id_type": "str",
                        "resource_sub_type": "aigc",
                    }
                ],
            },
            "submit_id": submit_id,
            "metrics_extra": metrics_extra,
            "draft_content": draft_content,
            "http_common_info": {
                "aid": DEFAULT_ASSISTANT_ID,
            },
        }

        generate_params = {
            "aid": str(DEFAULT_ASSISTANT_ID),
            "device_platform": "web",
            "region": "CN",
            "webId": str(self.web_id),
            "da_version": da_version,
            "web_component_open_flag": "1",
            "web_version": "7.5.0",
            "aigc_features": "app_lip_sync",
        }

        if is_seedance:
            generate_url = (
                f"{JIMENG_BASE_URL}/mweb/v1/aigc_draft/generate?"
                + "&".join(f"{k}={v}" for k, v in generate_params.items())
            )
            _logger.info("使用浏览器代理提交 Seedance 2.0 生成请求...")
            result = self._browser_request(generate_url, request_body)
        else:
            result = self._jimeng_request(
                "POST",
                "/mweb/v1/aigc_draft/generate",
                data=request_body,
                extra_params=generate_params,
            )

        aigc_data = result.get("aigc_data", {})
        history_id = aigc_data.get("history_record_id")
        if not history_id:
            raise SeeddanceError(
                f"未获取到历史记录ID, 返回数据: {json.dumps(result, ensure_ascii=False)[:200]}"
            )

        _logger.info(f"视频生成请求已提交, history_id={history_id}")
        print(f"[SEEDDANCE] 任务已提交: {history_id}")
        return history_id

    # ── draft 构建: 视频 3.0 系列 ─────────────────────────────

    def _build_v3_draft(
        self, model_key: str, image_uri: str, prompt: str,
        duration_ms: int, aspect_ratio: str,
    ) -> tuple:
        first_frame_image = {
            "format": "",
            "height": 1024,
            "id": self._uuid(),
            "image_uri": image_uri,
            "name": "",
            "platform_type": 1,
            "source_from": "upload",
            "type": "image",
            "uri": image_uri,
            "width": 1024,
        }

        component_id = self._uuid()
        submit_id = self._uuid()

        metrics_extra = json.dumps({
            "enterFrom": "click",
            "isDefaultSeed": 1,
            "promptSource": "custom",
            "isRegenerate": False,
            "originSubmitId": submit_id,
        })

        draft_content = json.dumps({
            "type": "draft",
            "id": self._uuid(),
            "min_version": "3.0.5",
            "is_from_tsn": True,
            "version": DRAFT_VERSION,
            "main_component_id": component_id,
            "component_list": [
                {
                    "type": "video_base_component",
                    "id": component_id,
                    "min_version": "1.0.0",
                    "aigc_mode": "workbench",
                    "metadata": {
                        "type": "",
                        "id": self._uuid(),
                        "created_platform": 3,
                        "created_platform_version": "",
                        "created_time_in_ms": str(int(time.time() * 1000)),
                        "created_did": "",
                    },
                    "generate_type": "gen_video",
                    "abilities": {
                        "type": "",
                        "id": self._uuid(),
                        "gen_video": {
                            "id": self._uuid(),
                            "type": "",
                            "text_to_video_params": {
                                "type": "",
                                "id": self._uuid(),
                                "model_req_key": model_key,
                                "priority": 0,
                                "seed": random.randint(2500000000, 3500000000),
                                "video_aspect_ratio": aspect_ratio,
                                "video_gen_inputs": [
                                    {
                                        "duration_ms": duration_ms,
                                        "first_frame_image": first_frame_image,
                                        "fps": 24,
                                        "id": self._uuid(),
                                        "min_version": "3.0.5",
                                        "prompt": prompt,
                                        "type": "",
                                        "video_mode": 2,
                                    }
                                ],
                            },
                            "video_task_extra": metrics_extra,
                        },
                    },
                    "process_type": 1,
                }
            ],
        })

        return draft_content, metrics_extra, submit_id

    # ── meta_list 构建: prompt 中的图片占位符 ─────────────────

    @staticmethod
    def _build_meta_list(prompt: str, image_count: int) -> list:
        meta_list: list = []
        placeholder_re = re.compile(r"@(?:图片?|image)(\d+)", re.IGNORECASE)
        last_index = 0
        found_placeholder = False

        for m in placeholder_re.finditer(prompt):
            found_placeholder = True
            if m.start() > last_index:
                text_before = prompt[last_index:m.start()]
                if text_before.strip():
                    meta_list.append({"meta_type": "text", "text": text_before})

            img_idx = int(m.group(1)) - 1
            if 0 <= img_idx < image_count:
                meta_list.append({
                    "meta_type": "image",
                    "text": "",
                    "material_ref": {"material_idx": img_idx},
                })
            last_index = m.end()

        if found_placeholder:
            remaining = prompt[last_index:]
            if remaining.strip():
                meta_list.append({"meta_type": "text", "text": remaining})
            return meta_list

        for i in range(image_count):
            if i == 0:
                meta_list.append({"meta_type": "text", "text": "使用"})
            meta_list.append({
                "meta_type": "image",
                "text": "",
                "material_ref": {"material_idx": i},
            })
            if i < image_count - 1:
                meta_list.append({"meta_type": "text", "text": "和"})

        if prompt and prompt.strip():
            meta_list.append({"meta_type": "text", "text": f"图片，{prompt}"})
        else:
            meta_list.append({"meta_type": "text", "text": "图片生成视频"})

        return meta_list

    # ── draft 构建: Seedance 2.0 系列 ─────────────────────────

    def _build_seedance_draft(
        self, model_key: str, image_uris: list, prompt: str,
        duration_ms: int, aspect_ratio: str,
    ) -> tuple:
        component_id = self._uuid()
        submit_id = self._uuid()

        material_list = [
            {
                "type": "",
                "id": self._uuid(),
                "material_type": "image",
                "image_info": {
                    "type": "image",
                    "id": self._uuid(),
                    "source_from": "upload",
                    "platform_type": 1,
                    "name": "",
                    "image_uri": uri,
                    "aigc_image": {"type": "", "id": self._uuid()},
                    "width": 1024,
                    "height": 1024,
                    "format": "",
                    "uri": uri,
                },
            }
            for uri in image_uris
        ]

        meta_list = self._build_meta_list(prompt, len(image_uris))

        scene_options = json.dumps([
            {
                "type": "video",
                "scene": "BasicVideoGenerateButton",
                "modelReqKey": model_key,
                "videoDuration": duration_ms // 1000,
                "reportParams": {
                    "enterSource": "generate",
                    "vipSource": "generate",
                    "extraVipFunctionKey": model_key,
                    "useVipFunctionDetailsReporterHoc": True,
                },
                "materialTypes": [1],
            }
        ])

        metrics_extra = json.dumps({
            "isDefaultSeed": 1,
            "originSubmitId": submit_id,
            "isRegenerate": False,
            "enterFrom": "click",
            "position": "page_bottom_box",
            "functionMode": "omni_reference",
            "sceneOptions": scene_options,
        })

        ratio_parts = aspect_ratio.split(":")
        w_ratio, h_ratio = int(ratio_parts[0]), int(ratio_parts[1])

        draft_content = json.dumps({
            "type": "draft",
            "id": self._uuid(),
            "min_version": SEEDANCE_DRAFT_VERSION,
            "min_features": ["AIGC_Video_UnifiedEdit"],
            "is_from_tsn": True,
            "version": SEEDANCE_DRAFT_VERSION,
            "main_component_id": component_id,
            "component_list": [
                {
                    "type": "video_base_component",
                    "id": component_id,
                    "min_version": "1.0.0",
                    "aigc_mode": "workbench",
                    "metadata": {
                        "type": "",
                        "id": self._uuid(),
                        "created_platform": 3,
                        "created_platform_version": "",
                        "created_time_in_ms": str(int(time.time() * 1000)),
                        "created_did": "",
                    },
                    "generate_type": "gen_video",
                    "abilities": {
                        "type": "",
                        "id": self._uuid(),
                        "gen_video": {
                            "type": "",
                            "id": self._uuid(),
                            "text_to_video_params": {
                                "type": "",
                                "id": self._uuid(),
                                "video_gen_inputs": [
                                    {
                                        "type": "",
                                        "id": self._uuid(),
                                        "min_version": SEEDANCE_DRAFT_VERSION,
                                        "prompt": "",
                                        "video_mode": 2,
                                        "fps": 24,
                                        "duration_ms": duration_ms,
                                        "idip_meta_list": [],
                                        "unified_edit_input": {
                                            "type": "",
                                            "id": self._uuid(),
                                            "material_list": material_list,
                                            "meta_list": meta_list,
                                        },
                                    }
                                ],
                                "video_aspect_ratio": aspect_ratio,
                                "seed": random.randint(100000000, 999999999),
                                "model_req_key": model_key,
                                "priority": 0,
                            },
                            "video_task_extra": metrics_extra,
                        },
                    },
                    "process_type": 1,
                }
            ],
        })

        return draft_content, metrics_extra, submit_id

    # ── 接口 2: 查询进度 ─────────────────────────────────────

    def check_progress(self, history_id: str) -> dict:
        """
        查询视频生成进度。

        Args:
            history_id: submit_video() 返回的历史记录ID

        Returns:
            dict: status, progress, error, video_url, fail_code, estimated_time
        """
        result = self._jimeng_request(
            "POST",
            "/mweb/v1/get_history_by_ids",
            data={"history_ids": [history_id]},
        )

        history_data = None
        history_list = result.get("history_list", [])
        if history_list:
            history_data = history_list[0]
        elif history_id in result:
            history_data = result[history_id]

        if not history_data:
            return {
                "status": "processing",
                "progress": "等待数据就绪...",
                "error": None,
                "video_url": None,
                "fail_code": None,
                "estimated_time": None,
            }

        _logger.debug(
            f"history_data: {json.dumps(history_data, ensure_ascii=False, default=str)[:2000]}"
        )

        status_code = history_data.get("status")
        fail_code = self._normalize_fail_code(history_data.get("fail_code"))
        item_list = history_data.get("item_list", [])
        estimated_time = self._extract_estimated_time(history_data)

        # 优先使用 jimeng 返回的建议轮询间隔
        poll_interval: Optional[float] = None
        queue_idx: Optional[int] = None
        queue_status: Optional[int] = None
        queue_length: Optional[int] = None
        qi = history_data.get("queue_info")
        if isinstance(qi, dict):
            pc = qi.get("polling_config")
            if isinstance(pc, dict):
                v = pc.get("interval_seconds")
                if v is not None:
                    try:
                        poll_interval = float(v)
                    except (ValueError, TypeError):
                        pass
            try:
                queue_idx = int(qi.get("queue_idx")) if qi.get("queue_idx") is not None else None
                queue_status = int(qi.get("queue_status")) if qi.get("queue_status") is not None else None
                queue_length = int(qi.get("queue_length")) if qi.get("queue_length") is not None else None
            except (ValueError, TypeError):
                pass

        if status_code == 30:
            fail_msg = history_data.get("fail_msg", "")
            fail_starling_message = history_data.get("fail_starling_message", "")
            error_msg = self._parse_fail_code(fail_code, fail_msg, fail_starling_message)
            return {
                "status": "failed",
                "progress": "生成失败",
                "error": error_msg,
                "video_url": None,
                "fail_code": fail_code,
                "fail_msg": fail_msg,
                "fail_starling_message": fail_starling_message,
                "estimated_time": None,
                "poll_interval": None,
                "queue_status": queue_status,
                "queue_idx": queue_idx,
                "queue_length": queue_length,
            }

        video_url = self._extract_video_url(item_list, result)
        if status_code == 50 or video_url:
            if not video_url:
                video_url = self._extract_video_url(item_list, result)
            return {
                "status": "done",
                "progress": "生成完成",
                "error": None,
                "video_url": video_url,
                "fail_code": None,
                "estimated_time": None,
                "poll_interval": None,
                "queue_status": queue_status,
                "queue_idx": queue_idx,
                "queue_length": queue_length,
            }

        # queue_status: 1=排队中, 2=生成中
        if queue_status == 1 and queue_idx is not None:
            progress_msg = f"排队中，当前位置 #{queue_idx}..."
        elif estimated_time is not None:
            progress_msg = f"AI正在生成视频，预计还需 {estimated_time:.0f} 秒..."
        else:
            progress_msg = "AI正在生成视频，请耐心等待..."
        return {
            "status": "processing",
            "progress": progress_msg,
            "error": None,
            "video_url": None,
            "fail_code": None,
            "estimated_time": estimated_time,
            "poll_interval": poll_interval,
            "queue_status": queue_status,
            "queue_idx": queue_idx,
            "queue_length": queue_length,
        }

    @staticmethod
    def _extract_estimated_time(history_data: dict) -> Optional[float]:
        candidate_keys = [
            "predict_remaining_time", "estimated_remaining_time",
            "estimated_time", "remaining_time", "predict_time",
            "eta", "queue_remaining_time", "pending_time",
            "wait_time", "cost_time_predict",
        ]
        for key in candidate_keys:
            val = history_data.get(key)
            if val is not None:
                try:
                    t = float(val)
                    if t > 0:
                        return t
                except (ValueError, TypeError):
                    pass

        for key in ("queue_info", "task_info", "progress_info", "extra_info"):
            sub = history_data.get(key)
            if isinstance(sub, dict):
                for ck in candidate_keys:
                    val = sub.get(ck)
                    if val is not None:
                        try:
                            t = float(val)
                            if t > 0:
                                return t
                        except (ValueError, TypeError):
                            pass

        # jimeng 专用字段：forecast_generate_cost / forecast_queue_cost（单位毫秒）
        for ms_key in ("forecast_generate_cost", "forecast_queue_cost"):
            val = history_data.get(ms_key)
            if val is not None:
                try:
                    t = float(val) / 1000.0
                    if t > 0:
                        return t
                except (ValueError, TypeError):
                    pass

        return None

    _FAIL_CODE_CONTENT_FILTER = {2038, 4011}
    _FAIL_CODE_CREDITS = {1006, 2039}

    @staticmethod
    def _normalize_fail_code(fail_code) -> Optional[int]:
        """Convert fail_code to int — the API sometimes returns it as a string."""
        if fail_code is None:
            return None
        try:
            return int(fail_code)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_fail_code(fail_code, fail_msg: str = "", fail_starling_message: str = "") -> str:
        fail_code_map = {
            2038: "文本违规：输入文字不符合平台规则",
            4011: "图片违规：素材中包含人脸信息或不合规内容",
            2039: "积分不足",
            1006: "积分不足",
        }
        try:
            code = int(fail_code)
        except (ValueError, TypeError):
            code = fail_code
        base_msg = fail_code_map.get(code) if isinstance(code, int) else None
        if not base_msg:
            _logger.warning("未知 fail_code=%s (fail_msg=%s)，请记录并补充映射", fail_code, fail_msg)
            base_msg = f"视频生成失败，错误码: {fail_code}"
        if fail_starling_message:
            return f"{base_msg} ({fail_starling_message})"
        return base_msg

    _CONTENT_FILTER_KEYWORDS = (
        "不符合", "违规", "不合规", "图片", "人脸", "素材",
        "文字", "文本", "敏感", "审核", "未通过",
    )
    _CREDITS_KEYWORDS = ("积分不足", "积分", "余额不足", "额度")

    @classmethod
    def _text_indicates_content_filter(cls, text: str) -> bool:
        """Classify by error text: content / image / text violations."""
        if not text:
            return False
        return any(kw in text for kw in cls._CONTENT_FILTER_KEYWORDS)

    @classmethod
    def _text_indicates_credits_issue(cls, text: str) -> bool:
        """Classify by error text: insufficient credits."""
        if not text:
            return False
        if cls._text_indicates_content_filter(text):
            return False
        return any(kw in text for kw in cls._CREDITS_KEYWORDS)

    @staticmethod
    def _extract_video_url(item_list: list, full_result: dict) -> Optional[str]:
        if item_list:
            item = item_list[0]
            video = item.get("video", {})

            transcoded = video.get("transcoded_video", {}).get("origin", {})
            url = transcoded.get("video_url")
            if url:
                return url

            for key in ("play_url", "download_url", "url"):
                url = video.get(key)
                if url:
                    return url

        response_str = json.dumps(full_result)
        patterns = [
            r'https://v[0-9]+-artist\.vlabvod\.com/[^"\s\\]+',
            r'https://v[0-9]+-dreamnia\.jimeng\.com/[^"\s\\]+',
            r'https://v[0-9]+-[^"\\]*\.jimeng\.com/[^"\s\\]+',
        ]
        for pattern in patterns:
            match = re.search(pattern, response_str)
            if match:
                return match.group(0)
        return None

    # ── 接口 3: 等待完成 (与 Sora 接口对齐) ──────────────────

    def wait_for_video(
        self,
        history_id: str,
        check_interval: int = 10,
        timeout: int = 36000,
        on_progress=None,
    ) -> Optional[Dict]:
        """
        等待视频生成完成，返回与 Sora 接口对齐的结果格式。

        Args:
            history_id: submit_video() 返回的ID
            check_interval: 检查间隔（秒），会被预估时间自适应覆盖
            timeout: 超时时间（秒），默认36000秒（10小时）
            on_progress: 可选回调 fn(progress_dict)，每次轮询后调用，
                         progress_dict 包含 queue_status/queue_idx/queue_length/estimated_time

        Returns:
            {"url": video_url} 或 None
        """
        print(f"[SEEDDANCE] 等待视频生成...")
        _logger.info(f"等待视频生成完成 (history_id={history_id}, timeout={timeout}s)")

        time.sleep(POLL_INITIAL_DELAY)
        start_time = time.time()
        retry_count = 0

        while retry_count < POLL_MAX_RETRIES:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                print(f"[SEEDDANCE] 超时（{timeout}秒）")
                _logger.error(f"视频生成超时 ({elapsed:.0f}s), history_id={history_id}")
                raise VideoGenerationTimeout(
                    f"视频生成超时 ({elapsed:.0f}s > {timeout}s)"
                )

            estimated_time = None
            poll_interval_hint = None
            try:
                progress = self.check_progress(history_id)

                if progress["status"] == "done":
                    elapsed_time = time.time() - start_time
                    print(
                        f"[SEEDDANCE] 生成完成！耗时: {elapsed_time:.1f}秒 "
                        f"({elapsed_time / 60:.1f}分钟)"
                    )
                    _logger.info(f"视频生成完成 (耗时 {elapsed_time:.0f}s)")
                    return {"url": progress["video_url"]}

                if progress["status"] == "failed":
                    error = progress["error"] or "未知错误"
                    fail_code = self._normalize_fail_code(progress.get("fail_code"))
                    fail_msg = progress.get("fail_msg", "")
                    fail_starling_message = progress.get("fail_starling_message", "")
                    print(f"[SEEDDANCE] 生成失败: {error}")
                    _logger.error(
                        f"视频生成失败: {error} "
                        f"(fail_code={fail_code}, fail_msg={fail_msg})"
                    )
                    combined_text = f"{error} {fail_starling_message} {fail_msg}"
                    if self._text_indicates_content_filter(combined_text):
                        raise ContentFilteredError(error)
                    if self._text_indicates_credits_issue(combined_text):
                        raise InsufficientCreditsError(error)
                    raise VideoGenerationFailed(error)

                estimated_time = progress.get("estimated_time")
                poll_interval_hint = progress.get("poll_interval")
                mins = int(elapsed) // 60
                secs = int(elapsed) % 60
                print(f"[SEEDDANCE]   {progress['progress']} ({mins}分{secs}秒)")
                _logger.info(f"轮询 #{retry_count + 1}: {progress['progress']}")

                # 回调通知外部（用于更新 checkpoint 里的 queue 信息）
                if on_progress is not None:
                    try:
                        on_progress({
                            "queue_status": progress.get("queue_status"),
                            "queue_idx": progress.get("queue_idx"),
                            "queue_length": progress.get("queue_length"),
                            "estimated_time": estimated_time,
                        })
                    except Exception as _cb_err:
                        _logger.warning("on_progress callback failed: %s", _cb_err)

            except (InsufficientCreditsError, ContentFilteredError,
                    VideoGenerationFailed) as e:
                print(f"[SEEDDANCE] 错误: {e}")
                _logger.error(f"不可恢复错误: {e}")
                raise
            except Exception as e:
                _logger.warning(f"轮询出错 (第{retry_count + 1}次): {e}")

            if poll_interval_hint is not None and poll_interval_hint > 0:
                # 优先使用 jimeng 返回的建议轮询间隔
                wait_time = max(poll_interval_hint, POLL_INTERVAL_MIN)
            elif estimated_time is not None and estimated_time > 0:
                wait_time = max(estimated_time / 2, POLL_INTERVAL_MIN)
            else:
                wait_time = max(check_interval, POLL_INTERVAL_FALLBACK)

            time.sleep(wait_time)
            retry_count += 1

        print(f"[SEEDDANCE] 超过最大轮询次数 ({POLL_MAX_RETRIES})")
        raise VideoGenerationTimeout(
            f"超过最大轮询次数 ({POLL_MAX_RETRIES})"
        )

    # ── 接口 4: 下载视频 ─────────────────────────────────────

    def download_video_file(
        self,
        video_url: str,
        save_path: str,
    ) -> bool:
        """
        下载视频到本地文件。

        Args:
            video_url: 视频URL
            save_path: 保存路径

        Returns:
            是否成功
        """
        _logger.info(f"正在下载视频到: {save_path}")
        print(f"[SEEDDANCE] 下载视频: {save_path}")

        max_retries = 5
        retry_interval = 5

        for attempt in range(1, max_retries + 1):
            try:
                if attempt > 1:
                    print(f"[SEEDDANCE]   重试下载 ({attempt}/{max_retries})...")

                resp = self.http.get(
                    video_url,
                    headers={
                        "User-Agent": FAKE_HEADERS["User-Agent"],
                        "Referer": f"{JIMENG_BASE_URL}/",
                    },
                    stream=True,
                    timeout=120,
                )
                resp.raise_for_status()

                total_size = int(resp.headers.get("content-length", 0))
                downloaded = 0

                Path(save_path).parent.mkdir(parents=True, exist_ok=True)
                with open(save_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)

                file_size_mb = downloaded / (1024 * 1024)
                print(f"[SEEDDANCE] 下载完成: {file_size_mb:.2f} MB")
                _logger.info(f"视频下载完成: {save_path} ({downloaded} bytes)")
                return True

            except Exception as e:
                print(f"[SEEDDANCE]   下载失败: {e}")
                _logger.error(f"下载失败: {e}")
                if attempt < max_retries:
                    time.sleep(retry_interval)

        return False

    def get_capabilities(self) -> Dict[str, object]:
        """获取当前客户端的功能支持情况"""
        return {
            "models": list(MODEL_MAP.keys()),
            "seedance_models": list(SEEDANCE_MODELS),
            "v3_durations": VALID_DURATIONS_V3,
            "seedance_durations": VALID_DURATIONS_SEEDANCE,
            "aspect_ratios": VIDEO_ASPECT_RATIOS,
            "multi_image": True,
        }


def get_seeddance_client(session_id: Optional[str] = None) -> SeeddanceClient:
    """获取 SeeddanceClient 实例（工厂函数）"""
    return SeeddanceClient(session_id=session_id)
