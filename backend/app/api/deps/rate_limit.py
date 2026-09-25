"""FastAPI dependencies for HTTP rate limiting.

Provides factories that produce dependency callables enforcing IP- or
user-scoped sliding-window rate limits backed by Redis.

Redis 不可用時的行為由 scope 決定（見 ``rate_limiter.FAIL_CLOSED_SCOPES``）：
local 一律放行；非 local 的認證類 scope 會回 503，其餘照舊放行。
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status

from app.core.i18n import t
from app.infrastructure.redis import check_rate_limit_by_key, get_redis
from app.models import User


def _client_ip(request: Request) -> str:
    """Best-effort client IP extraction respecting common reverse-proxy headers.

    Trust X-Real-IP first (nginx sets it to the unforgeable $remote_addr). Only
    fall back to X-Forwarded-For's LAST hop — the entry appended by our own
    nginx — because any leading XFF values are attacker-supplied. Taking the
    first XFF value here would let a caller forge their IP and mint a fresh
    per-IP rate-limit budget on every request, defeating brute-force protection.
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip and real_ip.strip():
        return real_ip.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            return hops[-1]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def rate_limit_by_ip(
    *,
    scope: str,
    limit: int,
    window_seconds: int,
) -> Callable:
    """Build a FastAPI dependency that throttles requests per client IP.

    Args:
        scope: namespace used in the Redis key (e.g. ``"login"``); keep short.
        limit: maximum requests allowed within the window.
        window_seconds: rolling window length in seconds.
    """

    async def _dep(request: Request) -> None:
        ip = _client_ip(request)
        redis = await get_redis()
        allowed, info = await check_rate_limit_by_key(
            redis,
            key=f"ip:{scope}:{ip}",
            limit=limit,
            window_seconds=window_seconds,
            scope=scope,
        )
        if not allowed:
            retry_after = info.get("window_seconds", window_seconds)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=t(
                    "rate_limit.ip_too_many_requests",
                    ip=ip,
                    retry_after=retry_after,
                ),
                headers={"Retry-After": str(retry_after)},
            )

    return _dep


def rate_limit_by_user(
    *,
    scope: str,
    limit: int,
    window_seconds: int,
) -> Callable:
    """Build a FastAPI dependency that throttles requests per authenticated user.

    This dependency assumes ``get_current_user`` has already populated the
    request — it reads ``request.state.user`` if set, otherwise falls back
    to the bearer-token JWT subject claim by re-running the auth lookup is
    intentionally avoided here to keep this dependency cheap. Routes that
    need user-scoped rate limiting should also depend on the auth
    dependency to ensure the user is loaded.
    """
    from app.api.deps.auth import get_current_user  # local import to avoid cycle

    async def _dep(
        request: Request,
        current_user: User = Depends(get_current_user),
    ) -> None:
        redis = await get_redis()
        allowed, info = await check_rate_limit_by_key(
            redis,
            key=f"user:{scope}:{current_user.id}",
            limit=limit,
            window_seconds=window_seconds,
            scope=scope,
        )
        if not allowed:
            retry_after = info.get("window_seconds", window_seconds)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=t(
                    "rate_limit.user_too_many_requests",
                    retry_after=retry_after,
                ),
                headers={"Retry-After": str(retry_after)},
            )

    return _dep


__all__ = ["rate_limit_by_ip", "rate_limit_by_user"]
