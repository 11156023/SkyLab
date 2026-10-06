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
        "ai-proxy-models",
        "ai-api-request",
        "ai-api-rotate",
        "login",
        "login-ldap",
        "pwd-recovery",
        "pwd-reset",
        "signup",
    }
)

AI_PROXY_SCOPE = "ai-proxy"
_KEY_PREFIX = "rate_limit:"

# 合併部署前 credential buckets 的原 member／score；ZADD NX 使重複查詢與
# 新舊 key 交錯遷移不會重複計數。保留舊 bucket，直到原 TTL 自然到期。
_MERGE_LEGACY_WINDOW_SCRIPT = """
for i = 2, #KEYS do
    local entries = redis.call('ZRANGEBYSCORE', KEYS[i], '(' .. ARGV[2], '+inf', 'WITHSCORES')
    for j = 1, #entries, 2 do
        redis.call('ZADD', KEYS[1], 'NX', entries[j + 1], entries[j])
    end
end
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[2])
if redis.call('EXISTS', KEYS[1]) == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[4])
end
"""

# 滑動視窗：ZSET 以毫秒時間戳為 score，先淘汰視窗外成員再計數；
# 整段在 Lua 內原子執行，避免「讀到 limit-1 後兩個請求同時寫入」。
_SLIDING_WINDOW_SCRIPT = (
    _MERGE_LEGACY_WINDOW_SCRIPT
    + """
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
)


def ai_proxy_rate_limit_key(request_id: str) -> str:
    """核准申請的穩定配額 identity，輪替 credential 不重置額度。"""
    return f"ai-request:{request_id}"


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
    logger.warning("Redis unavailable; allowing request on fail-open scope=%s", scope)


def _reset_at(now_ms: int, window_seconds: int) -> datetime:
    return datetime.fromtimestamp(
        (now_ms + window_seconds * 1000) / 1000, tz=timezone.utc
    )


def _bypass_info(
    *, limit: int, window_seconds: int, reset_at: datetime, **extra: Any
) -> dict[str, Any]:
    return {
        "limit": limit,
        "current": 0,
        "remaining": limit,
        "reset_at": reset_at,
        "window_seconds": window_seconds,
        **extra,
    }


async def check_rate_limit_by_key(
    redis: Redis | None,
    *,
    key: str,
    limit: int,
    window_seconds: int,
    scope: str = "",
    legacy_keys: tuple[str, ...] = (),
) -> tuple[bool, dict[str, Any]]:
    """Sliding-window rate limit check keyed by an arbitrary string.

    Use this for IP-based limits (e.g. login brute-force protection), per-user
    throttling, or any other scope; ``key`` is namespaced under ``rate_limit:``.

    Redis 不可用時的行為由 ``scope`` 決定：local 一律放行；非 local 只有
    ``FAIL_CLOSED_SCOPES`` 裡的認證類 scope 會拋 503，其餘仍放行並留 log。
    """
    now_ms = int(time.time() * 1000)
    reset_at = _reset_at(now_ms, window_seconds)

    if redis is None:
        _require_redis_or_fail_closed(scope)
        return True, _bypass_info(
            limit=limit,
            window_seconds=window_seconds,
            reset_at=reset_at,
            disabled=True,
        )

    window_start_ms = now_ms - (window_seconds * 1000)
    redis_key = f"{_KEY_PREFIX}{key}"

    try:
        result = await redis.eval(
            _SLIDING_WINDOW_SCRIPT,
            1 + len(legacy_keys),
            redis_key,
            *(f"{_KEY_PREFIX}{legacy_key}" for legacy_key in legacy_keys),
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
    except Exception as exc:
        logger.error("Redis rate limit check failed for key=%s: %s.", key, exc)
        # 連線中途壞掉跟一開始就沒有 Redis 是同一件事，套同一套政策
        _require_redis_or_fail_closed(scope)
        return True, _bypass_info(
            limit=limit,
            window_seconds=window_seconds,
            reset_at=reset_at,
            error=str(exc),
        )


async def check_rate_limit_sliding_window(
    redis: Redis | None,
    request_id: str,
    limit: int = 20,
    window_seconds: int = 60,
    *,
    legacy_credential_ids: tuple[str, ...] = (),
) -> tuple[bool, dict[str, Any]]:
    """AI Proxy 每核准申請配額，原子合併舊 credential 窗口後判斷。

    非 local 少了 Redis 就是無上限用量，所以 scope 固定為 ``ai-proxy``。
    """
    return await check_rate_limit_by_key(
        redis,
        key=ai_proxy_rate_limit_key(request_id),
        limit=limit,
        window_seconds=window_seconds,
        scope=AI_PROXY_SCOPE,
        legacy_keys=tuple(f"credential:{item}" for item in legacy_credential_ids),
    )


async def peek_rate_limit_by_key(
    redis: Redis | None,
    *,
    key: str,
    window_seconds: int,
    legacy_keys: tuple[str, ...] = (),
) -> int | None:
    """回傳 ``key`` 目前視窗內的計數，不佔用額度；Redis 不可用回 None。

    給「查看剩餘額度」這類唯讀端點用，避免查詢本身吃掉一次配額。
    """
    if redis is None:
        return None
    now_ms = int(time.time() * 1000)
    window_start_ms = now_ms - (window_seconds * 1000)
    redis_key = f"{_KEY_PREFIX}{key}"
    try:
        return int(
            await redis.eval(
                _MERGE_LEGACY_WINDOW_SCRIPT + "return redis.call('ZCARD', KEYS[1])",
                1 + len(legacy_keys),
                redis_key,
                *(f"{_KEY_PREFIX}{legacy_key}" for legacy_key in legacy_keys),
                now_ms,
                window_start_ms,
                0,
                window_seconds * 2,
            )
        )
    except Exception as exc:
        logger.error("Redis rate limit peek failed for key=%s: %s.", key, exc)
        return None
