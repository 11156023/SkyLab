from __future__ import annotations

import logging
import time
from typing import Any

try:
    from redis.asyncio import ConnectionPool, Redis
except ModuleNotFoundError:  # pragma: no cover - depends on local env
    ConnectionPool = Any  # type: ignore[assignment]
    Redis = Any  # type: ignore[assignment]

from app.core.config import settings as core_settings
from app.features.ai.config import settings

logger = logging.getLogger(__name__)

_redis_pool: ConnectionPool | None = None
_redis_client: Redis | None = None
_redis_backend_available = ConnectionPool is not Any
_redis_enabled: bool = settings.redis_enabled and _redis_backend_available

# 請求路徑上的重連冷卻：Redis 掛掉時，若每個請求都重建連線池並 ping，
# 每個請求先吃滿 socket_connect_timeout（5 秒）才拿到 None。冷卻期內直接
# 回 None，讓限流／撤銷名單立刻依 scope 決定放行或拒絕。
REINIT_COOLDOWN_SECONDS = 10.0
_last_init_failure_at: float | None = None


def redis_failures_are_fatal() -> bool:
    """非 local 環境把 Redis 當必要元件。

    限流與 JWT 撤銷名單都只存在 Redis 裡，連不上就等於這兩道防線不存在；
    正式環境寧可啟動失敗，也不要「看起來有保護、實際上全放行」。
    """
    return core_settings.ENVIRONMENT != "local"


async def init_redis(*, raise_on_failure: bool = True) -> None:
    """建立 Redis 連線池。

    ``raise_on_failure``：lifespan 啟動時用預設值（非 local 連不上就丟例外讓啟動失敗）；
    請求路徑上的重試則傳 False，由呼叫端自行決定放行或拒絕。
    """
    global _redis_pool, _redis_client, _last_init_failure_at

    fatal = raise_on_failure and redis_failures_are_fatal()

    if not _redis_backend_available:
        message = (
            "Redis Python package is not installed. "
            "Rate limiting and token revocation cannot work."
        )
        if settings.redis_enabled and fatal:
            raise RuntimeError(message)
        logger.warning("%s Functionality will be skipped.", message)
        return

    if not _redis_enabled:
        logger.info(
            "Redis is disabled (REDIS_ENABLED=false). "
            "Rate limiting functionality will be skipped."
        )
        return

    try:
        _redis_pool = ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=50,
            socket_connect_timeout=5,
            socket_keepalive=True,
        )
        _redis_client = Redis(connection_pool=_redis_pool)
        await _redis_client.ping()
        _last_init_failure_at = None
        logger.info("Redis connected successfully: %s", settings.redis_url)
    except Exception as exc:
        _redis_client = None
        if _redis_pool:
            await _redis_pool.aclose()
            _redis_pool = None
        _last_init_failure_at = time.monotonic()
        if fatal:
            raise RuntimeError(
                f"Failed to connect to Redis ({settings.redis_url}): {exc}. "
                f"ENVIRONMENT={core_settings.ENVIRONMENT} requires a working Redis; "
                "fix REDIS_URL or set REDIS_ENABLED=false to run without it."
            ) from exc
        logger.error(
            "Failed to connect to Redis: %s. "
            "Rate limiting will be disabled. "
            "To suppress this error, set REDIS_ENABLED=false in .env",
            exc,
        )


async def get_redis() -> Redis | None:
    if not _redis_enabled:
        return None

    if _redis_client is None:
        if _in_reinit_cooldown():
            return None
        logger.warning("Redis not initialized, attempting to initialize now...")
        # 請求路徑上不丟例外：回 None 讓限流／撤銷名單依 scope 決定放行或拒絕，
        # 否則一次 Redis 抖動會讓所有端點變成 500。
        await init_redis(raise_on_failure=False)

    return _redis_client


def _in_reinit_cooldown() -> bool:
    if _last_init_failure_at is None:
        return False
    return (time.monotonic() - _last_init_failure_at) < REINIT_COOLDOWN_SECONDS


async def close_redis() -> None:
    global _redis_client, _redis_pool, _last_init_failure_at

    _last_init_failure_at = None

    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis client closed")

    if _redis_pool is not None:
        await _redis_pool.aclose()
        _redis_pool = None
        logger.info("Redis connection pool closed")


def is_redis_enabled() -> bool:
    return _redis_enabled


def is_redis_available() -> bool:
    return _redis_enabled and _redis_client is not None
