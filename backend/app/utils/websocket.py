"""WebSocket 連線的共用小工具（信令 hub 與進度 hub 共用）。"""

import contextlib
from typing import Any


async def close_quietly(websocket: Any) -> None:
    """盡力關閉連線；對端早就斷了或物件沒有 close 都不是問題。"""
    close = getattr(websocket, "close", None)
    if close is None:
        return
    with contextlib.suppress(Exception):
        await close()
