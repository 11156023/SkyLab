"""跨行程的短期 key-value 暫存（sync 路由用）。

device code、AI 確認 token 這類「A 副本發、B 副本核」的狀態原本放在模組層
dict，多開 backend 就對不上。這裡以 Redis 為主、行程內 dict 為備援：
- REDIS_ENABLED=false：純記憶體（單行程開發），行為與原本的 dict 相同。
- Redis 啟用但當下連不上：該次操作退回記憶體並留 warning，不讓登入流程 500。

用 sync 的 redis client：呼叫端是跑在 threadpool 的 sync 路由，沒有 event loop。
值一律是 JSON 物件（dict）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

try:
    import redis as redis_sync
except ModuleNotFoundError:  # pragma: no cover - depends on local env
    redis_sync = None  # type: ignore[assignment]

from app.features.ai.config import settings

logger = logging.getLogger(__name__)

_SOCKET_TIMEOUT_SECONDS = 2.0


class ExpiringKV:
    def __init__(self, namespace: str, *, ttl_seconds: int) -> None:
        self._namespace = namespace
        self._ttl = ttl_seconds
        self._memory: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._client: Any | None = None

    # ─── Redis 端 ─────────────────────────────────────────────────────────

    def _redis(self) -> Any | None:
        if redis_sync is None or not settings.redis_enabled:
            return None
        if self._client is None:
            self._client = redis_sync.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=_SOCKET_TIMEOUT_SECONDS,
                socket_timeout=_SOCKET_TIMEOUT_SECONDS,
            )
        return self._client

    def _key(self, key: str) -> str:
        return f"kv:{self._namespace}:{key}"

    # ─── 公開 API ─────────────────────────────────────────────────────────

    def get(self, key: str) -> dict[str, Any] | None:
        client = self._redis()
        if client is not None:
            try:
                raw = client.get(self._key(key))
                if raw:
                    return json.loads(raw)
                # Redis 沒有 → 可能是 Redis 短暫故障期間寫進記憶體的，往下找
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Redis KV get failed (namespace=%s); using in-memory fallback",
                    self._namespace, exc_info=True,
                )
        return self._memory_get(key)


    def set(
        self, key: str, value: dict[str, Any], *, ttl_seconds: int | None = None
    ) -> None:
        ttl = max(1, int(ttl_seconds if ttl_seconds is not None else self._ttl))
        client = self._redis()
        if client is not None:
            try:
                client.set(self._key(key), json.dumps(value), ex=ttl)
                return
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Redis KV set failed (namespace=%s); using in-memory fallback",
                    self._namespace, exc_info=True,
                )
        self._memory_set(key, value, ttl)

    def delete(self, key: str) -> None:
        client = self._redis()
        if client is not None:
            try:
                client.delete(self._key(key))
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Redis KV delete failed (namespace=%s)", self._namespace, exc_info=True
                )
        with self._lock:
            self._memory.pop(key, None)

    def count(self, *, limit: int = 10_000) -> int:
        """namespace 內目前存活的 key 數（掃到 ``limit`` 就停，供上限檢查用）。"""
        client = self._redis()
        if client is not None:
            try:
                total = 0
                for _ in client.scan_iter(match=self._key("*"), count=500):
                    total += 1
                    if total >= limit:
                        break
                return total
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Redis KV count failed (namespace=%s); using in-memory fallback",
                    self._namespace, exc_info=True,
                )
        self._memory_sweep()
        with self._lock:
            return len(self._memory)

    # ─── 記憶體備援 ───────────────────────────────────────────────────────

    def _memory_get(self, key: str) -> dict[str, Any] | None:
        self._memory_sweep()
        with self._lock:
            entry = self._memory.get(key)
            return dict(entry[1]) if entry else None

    def _memory_set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        with self._lock:
            self._memory[key] = (time.monotonic() + ttl, dict(value))
        self._memory_sweep()

    def _memory_sweep(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [k for k, (deadline, _) in self._memory.items() if deadline <= now]
            for k in expired:
                del self._memory[k]


__all__ = ["ExpiringKV"]
