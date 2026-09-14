"""JWT token revocation blacklist backed by Redis.

Stores ``revoked:<jti>`` keys with a TTL aligned to the token's natural
expiry, so revoked tokens are forgotten automatically once they would
have expired anyway.

When Redis is unavailable, revocation is a no-op (fail-open) — the
existing ``token_version`` mechanism on the User model still provides a
hard kill switch (incrementing ``token_version`` invalidates *all*
tokens for that user, regardless of jti).
"""

from __future__ import annotations

import logging
import time
from typing import Any

try:
    from redis.asyncio import Redis
except ModuleNotFoundError:  # pragma: no cover
    Redis = Any  # type: ignore[assignment]

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
        logger.debug("Redis disabled — revocation skipped for jti=%s", jti)
        return False

    ttl = exp_unix - int(time.time())
    if ttl <= 0:
        return False

    try:
        await redis.set(f"{_KEY_PREFIX}{jti}", _REVOKED_VALUE, ex=ttl)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to revoke jti=%s: %s", jti, exc)
        return False


async def is_jti_revoked(redis: Redis | None, jti: str) -> bool:
    """Check whether a JWT ID has been revoked. Fails-open on Redis errors."""
    if redis is None:
        return False
    try:
        return bool(await redis.exists(f"{_KEY_PREFIX}{jti}"))
    except Exception as exc:  # noqa: BLE001
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
    otherwise the token is treated as revoked. Fails-open without Redis.
    """
    if redis is None:
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
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to record refresh-token use for jti=%s (allowing): %s",
            jti,
            exc,
        )
        return True


__all__ = ["revoke_jti", "is_jti_revoked", "mark_refresh_token_used"]
