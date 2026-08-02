"""Notification service — unified WebSocket push layer.

All WebSocket messages to clients go through this module.
Other services call notify_user() / broadcast() instead of
directly accessing connection_manager.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from dashboard.state import connection_manager

_log = logging.getLogger("dashboard.notify")


async def notify_user(user_id: int, msg_type: str, data: Any = None, *, exclude_ws=None) -> int:
    """Send a typed message to all WebSocket connections of a user.

    Returns the number of connections that received the message.
    """
    payload = json.dumps({"type": msg_type, **(data if isinstance(data, dict) else {"data": data})})
    sent = 0
    for ws in connection_manager.sockets_for_user(user_id):
        if ws is exclude_ws:
            continue
        try:
            await ws.send_text(payload)
            sent += 1
        except Exception:
            pass
    return sent


async def broadcast(msg_type: str, data: Any = None) -> int:
    """Send a typed message to all connected users."""
    total = 0
    for user_id in connection_manager.active_user_ids():
        total += await notify_user(user_id, msg_type, data)
    return total


def notify_user_sync(user_id: int, msg_type: str, data: Any = None) -> None:
    """Fire-and-forget sync wrapper for use in non-async contexts (e.g. watchers)."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(notify_user(user_id, msg_type, data))
    except RuntimeError:
        pass
