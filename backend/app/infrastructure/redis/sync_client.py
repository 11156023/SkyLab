"""共用的同步 Redis client（給跑在執行緒裡的健康檢查／排程 tick 用）。

請求路徑上的程式請用 ``client.get_redis()``（async）；這裡只服務沒有 event
loop 的同步呼叫端。socket 逾時設短：健康檢查寧可快速回報「連不上」，也不要
卡住整輪排程。
"""

from __future__ import annotations

import threading
from typing import Any

try:
    import redis as redis_sync
except ModuleNotFoundError:  # pragma: no cover - depends on local env
    redis_sync = None  # type: ignore[assignment]

from app.features.ai.config import settings

_SOCKET_TIMEOUT_SECONDS = 2.0
_lock = threading.Lock()
_state: dict[str, Any] = {"client": None}


def get_sync_redis() -> Any | None:
    """回傳同步 client；Redis 停用或套件沒裝時回 None（不代表連得上）。"""
    if redis_sync is None or not settings.redis_enabled:
        return None
    with _lock:
        if _state["client"] is None:
            _state["client"] = redis_sync.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=_SOCKET_TIMEOUT_SECONDS,
                socket_timeout=_SOCKET_TIMEOUT_SECONDS,
            )
        return _state["client"]


__all__ = ["get_sync_redis"]
