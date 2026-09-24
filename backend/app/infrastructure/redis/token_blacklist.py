"""JWT token revocation blacklist backed by Redis.

Stores ``revoked:<jti>`` keys with a TTL aligned to the token's natural
expiry, so revoked tokens are forgotten automatically once they would
have expired anyway.

Redis 不可用時的行為依環境而定：local 維持 fail-open（本機沒有 Redis 也
要能開發），非 local 則視為「無法確認這張 token 有沒有被撤銷」，一律當成
已撤銷處理（呼叫端會回 401）。撤銷名單只存在 Redis 裡，fail-open 等於登出
與 refresh 輪替在正式環境形同虛設。``token_version`` 只能整批作廢某使用者
的所有 token，補不上單張 jti 的撤銷。
"""

from __future__ import annotations

import logging
import time
from typing import Any

try:
    from redis.asyncio import Redis
except ModuleNotFoundError:  # pragma: no cover
    Redis = Any  # type: ignore[assignment]

from app.infrastructure.redis.client import redis_failures_are_fatal

logger = logging.getLogger(__name__)

_KEY_PREFIX = "revoked_jti:"
_REVOKED_VALUE = "1"


async def revoke_jti(redis: Redis | None, jti: str, exp_unix: int) -> bool:
    """Mark a JWT as revoked until its natural expiry time.

    Args:
        redis: Redis client (or None when disabled).
        jti: JWT ID claim.
        exp_unix: Token expiry as Unix seconds (the ``exp`` claim).

    Returns:
        True if the key was written; False when Redis is disabled or the
        token already expired.
    """
    if redis is None:
        # 撤銷寫不進去時只能出聲：正式環境代表這次登出其實沒有讓 token 失效
        if redis_failures_are_fatal():
            logger.error("Redis unavailable — cannot revoke jti=%s", jti)
        else:
            logger.debug("Redis disabled — revocation skipped for jti=%s", jti)
        return False

    ttl = exp_unix - int(time.time())
    if ttl <= 0:
        return False

    try:
        await redis.set(f"{_KEY_PREFIX}{jti}", _REVOKED_VALUE, ex=ttl)
        return True
    except Exception as exc:
        logger.error("Failed to revoke jti=%s: %s", jti, exc)
        return False


async def is_jti_revoked(redis: Redis | None, jti: str) -> bool:
    """Check whether a JWT ID has been revoked.

    非 local 環境查不到名單就回 True（呼叫端回 401）：撤銷狀態無法確認時，
    放行等於把已登出的 token 當成有效。local 維持 fail-open。
    """
    if redis is None:
        if redis_failures_are_fatal():
            logger.error(
                "Redis unavailable — treating jti=%s as revoked (fail-closed)", jti
            )
            return True
        return False
    try:
        return bool(await redis.exists(f"{_KEY_PREFIX}{jti}"))
    except Exception as exc:
        if redis_failures_are_fatal():
            logger.error(
                "Failed to check revocation for jti=%s (rejecting): %s", jti, exc
            )
            return True
        logger.error(
            "Failed to check revocation for jti=%s (allowing): %s", jti, exc
        )
        return False


_REFRESH_USED_PREFIX = "refresh_used:"
# 多分頁同時觸發 refresh 時，舊 refresh token 在這段寬限期內仍可重用一次，
# 之後即視為已輪替（等同撤銷）。
REFRESH_ROTATION_GRACE_SECONDS = 30


async def mark_refresh_token_used(
    redis: Redis | None,
    jti: str,
    exp_unix: int,
    *,
    grace_seconds: int = REFRESH_ROTATION_GRACE_SECONDS,
) -> bool:
    """Record that a refresh token was exchanged; return False on stale reuse.

    Implements refresh-token rotation: the first exchange stores the
    timestamp under ``refresh_used:<jti>``; later exchanges of the same
    token are only tolerated within ``grace_seconds`` (concurrent tabs),
    otherwise the token is treated as revoked。非 local 沒有 Redis 就無從判斷
    是否重用，一律拒絕；local 維持 fail-open。
    """
    if redis is None:
        if redis_failures_are_fatal():
            logger.error(
                "Redis unavailable — refusing refresh rotation for jti=%s", jti
            )
            return False
        return True

    now = int(time.time())
    ttl = exp_unix - now
    if ttl <= 0:
        return False

    key = f"{_REFRESH_USED_PREFIX}{jti}"
    try:
        first_use = await redis.set(key, str(now), ex=ttl, nx=True)
        if first_use:
            return True
        raw = await redis.get(key)
        if raw is None:
            return True
        used_at = int(raw.decode() if isinstance(raw, bytes) else raw)
        return (now - used_at) <= grace_seconds
    except Exception as exc:
        if redis_failures_are_fatal():
            logger.error(
                "Failed to record refresh-token use for jti=%s (rejecting): %s",
                jti,
                exc,
            )
            return False
        logger.error(
            "Failed to record refresh-token use for jti=%s (allowing): %s",
            jti,
            exc,
        )
        return True


__all__ = ["revoke_jti", "is_jti_revoked", "mark_refresh_token_used"]
