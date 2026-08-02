"""Request-scoped context — single-user mode stub.

单用户模式下 user_id 永远是 1，workspace 通过 deps.py 传递。
保留接口兼容性，让 helpers/ 里的调用不报错。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from dashboard.workspace import WorkspaceContext


@dataclass(frozen=True)
class CurrentUserInfo:
    id: int = 1
    username: str = "admin"
    role: str = "admin"


_SINGLE_USER = CurrentUserInfo()

# ── ContextBinding stub ──────────────────────────────────────────

class ContextBinding:
    def reset(self) -> None:
        pass


def bind_request_context(*, user_id: int = 1, username: str = "admin", role: str = "admin", workspace: WorkspaceContext = None) -> ContextBinding:
    return ContextBinding()


def clear_request_context() -> None:
    pass


def get_current_user_info() -> CurrentUserInfo:
    return _SINGLE_USER


def require_current_user_info() -> CurrentUserInfo:
    return _SINGLE_USER


# Workspace context — not stored here in single-user mode;
# helpers that call require_current_workspace() will get None and should
# fall back to the workspace passed via DashboardContext.

def get_current_workspace() -> Optional[WorkspaceContext]:
    return None


def require_current_workspace() -> WorkspaceContext:
    raise RuntimeError("require_current_workspace() called outside request context — pass workspace explicitly")
