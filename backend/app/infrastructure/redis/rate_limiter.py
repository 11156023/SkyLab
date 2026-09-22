from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

try:
    from redis.asyncio import Redis
except ModuleNotFoundError:  # pragma: no cover - depends on local env
    Redis = Any  # type: ignore[assignment]

from app.core.i18n import t
from app.exceptions import AppError
from app.infrastructure.redis.client import redis_failures_are_fatal

logger = logging.getLogger(__name__)

# 這些 scope 沒有第二道防線：Redis 不見了就等於暴力破解與配額完全不設防，
# 所以非 local 環境寧可整條路徑回 503，也不放行。
# 其餘 scope（一般操作節流）照舊 fail-open，只留 log。
FAIL_CLOSED_SCOPES = frozenset(
    {
        "ai-proxy",
        "login",
        "login-ldap",
        "pwd-recovery",
        "pwd-reset",
    }
)


def _require_redis_or_fail_closed(scope: str) -> None:
    """Redis 不可用時，依 scope 決定放行或直接擋下。"""
    if not redis_failures_are_fatal():
        logger.debug("Redis is disabled. Rate limiting skipped for scope=%s", scope)
        return
    if scope in FAIL_CLOSED_SCOPES:
        logger.error(
            "Redis unavailable; refusing request on fail-closed scope=%s", scope
        )
        raise AppError(t("rate_limit.backend_unavailable"), 503)
    logger.warning(
        "Redis unavailable; allowing request on fail-open scope=%s", scope
    )


async def check_rate_limit_sliding_window(
    redis: Redis | None,
    user_id: str,
    limit: int = 20,
    window_seconds: int = 60,
) -> tuple[bool, dict[str, Any]]:
    now_ms = int(time.time() * 1000)
    reset_at = datetime.fromtimestamp(
        (now_ms + window_seconds * 1000) / 1000,
        tz=timezone.utc,
    )

    if redis is None:
        # 這支專用於 AI Proxy 配額：非 local 少了 Redis 就是無上限用量，必須擋。
        _require_redis_or_fail_closed("ai-proxy")
        return True, {
            "limit": limit,
            "current": 0,
            "remaining": limit,
            "reset_at": reset_at,
            "window_seconds": window_seconds,
            "disabled": True,
        }

    window_start_ms = now_ms - (window_seconds * 1000)
    key = f"rate_limit:user:{user_id}"

    lua_script = """
    local key = KEYS[1]
    local now_ms = tonumber(ARGV[1])
    local window_start_ms = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local ttl = tonumber(ARGV[4])
    local member = ARGV[5]

    redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start_ms)
    local current = redis.call('ZCARD', key)

    if current >= limit then
        return {0, current}
    end

    redis.call('ZADD', key, now_ms, member)
    redis.call('EXPIRE', key, ttl)

    return {1, current + 1}
    """

    try:
        result = await redis.eval(
            lua_script,
            1,
            key,
            now_ms,
            window_start_ms,
            limit,
            window_seconds * 2,
            f"{now_ms}:{uuid.uuid4().hex}",
        )

        allowed_int, current_count = result[0], result[1]
        allowed = allowed_int == 1
        rate_info = {
            "limit": limit,
            "current": current_count,
            "remaining": max(0, limit - current_count),
            "reset_at": datetime.fromtimestamp(
                (now_ms + window_seconds * 1000) / 1000,
                tz=timezone.utc,
            ),
            "window_seconds": window_seconds,
        }

        if not allowed:
            logger.warning(
                "Rate limit exceeded for user %s: %d/%d requests in %ds window",
                user_id,
                current_count,
                limit,
                window_seconds,
            )
        else:
            logger.debug(
                "Rate limit check passed for user %s: %d/%d requests",
                user_id,
                current_count,
                limit,
            )

        return allowed, rate_info
    except Exception as exc:
        logger.error(
            "Redis rate limit check failed for user %s: %s.",
            user_id,
            str(exc),
        )
        # 連線中途壞掉跟一開始就沒有 Redis 是同一件事，套同一套政策
        _require_redis_or_fail_closed("ai-proxy")
        return True, {
            "limit": limit,
            "current": 0,
            "remaining": limit,
            "reset_at": reset_at,
            "window_seconds": window_seconds,
            "error": str(exc),
        }


async def clear_user_rate_limit(redis: Redis | None, user_id: str) -> bool:
    if redis is None:
        logger.debug("Redis is disabled. Cannot clear rate limit for user %s", user_id)
        return False

    key = f"rate_limit:user:{user_id}"
    try:
        deleted = await redis.delete(key)
        logger.info("Cleared rate limit for user %s (deleted=%d)", user_id, deleted)
        return deleted > 0
    except Exception as exc:
        logger.error("Failed to clear rate limit for user %s: %s", user_id, str(exc))
        return False


async def check_rate_limit_by_key(
    redis: Redis | None,
    *,
    key: str,
    limit: int,
    window_seconds: int,
    scope: str = "",
) -> tuple[bool, dict[str, Any]]:
    """Generic sliding-window rate limit check keyed by an arbitrary string.

    Use this for IP-based limits (e.g. login brute-force protection) or any
    non-user-scoped throttling.

    Redis 不可用時的行為由 ``scope`` 決定：local 一律放行；非 local 只有
    ``FAIL_CLOSED_SCOPES`` 裡的認證類 scope 會拋 503，其餘仍放行並留 log。
    """
    now_ms = int(time.time() * 1000)
    reset_at = datetime.fromtimestamp(
        (now_ms + window_seconds * 1000) / 1000,
        tz=timezone.utc,
    )

    if redis is None:
        _require_redis_or_fail_closed(scope)
        return True, {
            "limit": limit,
            "current": 0,
            "remaining": limit,
            "reset_at": reset_at,
            "window_seconds": window_seconds,
            "disabled": True,
        }

    window_start_ms = now_ms - (window_seconds * 1000)
    redis_key = f"rate_limit:{key}"

    lua_script = """
    local key = KEYS[1]
    local now_ms = tonumber(ARGV[1])
    local window_start_ms = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local ttl = tonumber(ARGV[4])
    local member = ARGV[5]

    redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start_ms)
    local current = redis.call('ZCARD', key)

    if current >= limit then
        return {0, current}
    end

    redis.call('ZADD', key, now_ms, member)
    redis.call('EXPIRE', key, ttl)

    return {1, current + 1}
    """

    try:
        result = await redis.eval(
            lua_script,
            1,
            redis_key,
            now_ms,
            window_start_ms,
            limit,
            window_seconds * 2,
            f"{now_ms}:{uuid.uuid4().hex}",
        )
        allowed_int, current_count = result[0], result[1]
        allowed = allowed_int == 1
        info = {
            "limit": limit,
            "current": current_count,
            "remaining": max(0, limit - current_count),
            "reset_at": reset_at,
            "window_seconds": window_seconds,
        }
        if not allowed:
            logger.warning(
                "Rate limit exceeded for key=%s: %d/%d in %ds",
                key,
                current_count,
                limit,
                window_seconds,
            )
        return allowed, info
    except Exception as exc:  # noqa: BLE001
        logger.error("Redis rate limit check failed for key=%s: %s.", key, exc)
        # 連線中途壞掉跟一開始就沒有 Redis 是同一件事，套同一套政策
        _require_redis_or_fail_closed(scope)
        return True, {
            "limit": limit,
            "current": 0,
            "remaining": limit,
            "reset_at": reset_at,
            "window_seconds": window_seconds,
            "error": str(exc),
        }
