"""即梦账号池 — 单用户模式下的空壳实现。

单用户本地部署不需要多账号负载均衡，所有方法均为 no-op。
session_id 直接从环境变量或用户偏好设置中读取。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

_log = logging.getLogger("dashboard.jimeng_pool")


class JimengAccountPool:
    """单用户模式：不做任何账号调度，直接放行所有 job。"""

    def reload(self) -> None:
        pass

    def try_assign(self, job: dict) -> bool:
        """单用户模式下始终返回 True（直接放行）。"""
        return True

    def release(self, job: Any) -> None:
        pass

    def snapshot(self) -> list:
        return []

    def account_count(self, backend: Optional[str] = None) -> int:
        return 0


jimeng_pool = JimengAccountPool()
