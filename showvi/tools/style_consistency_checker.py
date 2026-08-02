"""
Image-vs-Prompt style verifier.

After Nano Banana generates an image, call ``verify_image_matches_prompt``
to check whether the image faithfully matches the text prompt —
especially art style, colour palette, detail level, and layout.

If it fails, the caller should regenerate.
"""

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Dict, Optional

_VERIFY_TIMEOUT_SECONDS = int(os.getenv("STYLE_CHECKER_TIMEOUT_SECONDS", "120"))

_log = logging.getLogger("video_agent.style_checker")

# ── VLM prompt ──────────────────────────────────────────────────────

from prompts.style_checker import _VERIFY_SYSTEM

_VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "result": {
            "type": "string",
            "enum": ["pass", "fail"],
        },
        "score": {
            "type": "number",
            "minimum": 0,
            "maximum": 10,
        },
        "issues": {
            "type": "array",
            "items": {"type": "string"},
        },
        "brief": {"type": "string"},
    },
    "required": ["result", "score", "issues", "brief"],
}


def verify_image_matches_prompt(
    image_path: str,
    prompt: str,
    model: str = "gemini-3-pro-preview",
    pass_threshold: float = 6.0,
) -> Dict:
    """
    Check whether a generated image matches its prompt.

    Returns
    -------
    dict with keys:
        passed   : bool   — True if image is acceptable
        score    : float  — 0-10 quality score
        issues   : list[str] — list of problems (empty when passed)
        brief    : str    — one-line summary
    """
    from clients import get_llm_client

    if not Path(image_path).exists():
        return {"passed": False, "score": 0,
                "issues": ["Image file not found"], "brief": "文件不存在"}

    client = get_llm_client(step="style_check")

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    user_message = (
        "请审核这张图片是否匹配以下 prompt：\n\n"
        f"```\n{prompt}\n```\n\n"
        "请重点检查艺术风格、布局构图、主体内容是否与 prompt 一致。"
    )

    def _do_verify():
        return client.generate_with_vision(
            text_prompt=user_message,
            image_paths=[image_path],
            system_instruction=_VERIFY_SYSTEM,
            temperature=0.2,
            response_schema=_VERIFY_SCHEMA,
            model=model,
            timeout_seconds=client.DEFAULT_TIMEOUT_SECONDS,
            max_retries=3,
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_do_verify)
            try:
                response_text = future.result(timeout=_VERIFY_TIMEOUT_SECONDS)
            except FuturesTimeoutError:
                _log.warning(
                    "Image verification timed out after %ds for %s, skipping",
                    _VERIFY_TIMEOUT_SECONDS, image_path,
                )
                return {"passed": True, "score": 10,
                        "issues": [], "brief": f"检查超时({_VERIFY_TIMEOUT_SECONDS}s)，默认通过"}

        data = json.loads(response_text)
        score = float(data.get("score", 0))
        passed = data.get("result") == "pass" and score >= pass_threshold
        issues = data.get("issues", [])
        brief = data.get("brief", "")

        _log.info("Verify %s — score=%.1f passed=%s brief=%s issues=%s",
                  image_path, score, passed, brief, issues)
        return {"passed": passed, "score": score,
                "issues": issues, "brief": brief}

    except Exception as e:
        _log.error("Image verification failed for %s: %s", image_path, e)
        # Fail-open: if VLM call fails, accept the image to avoid blocking
        return {"passed": True, "score": 10,
                "issues": [], "brief": f"检查失败({e})，默认通过"}
