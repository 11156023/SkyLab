"""FastAPI dependencies for HTTP rate limiting.

Provides factories that produce dependency callables enforcing IP- or
user-scoped sliding-window rate limits backed by Redis.

Redis 不可用時的行為由 scope 決定（見 ``rate_limiter.FAIL_CLOSED_SCOPES``）：
local 一律放行；非 local 的認證類 scope 會回 503，其餘照舊放行。
"""

from __future__ import annotations

import hashlib
import ipaddress
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


def _client_network(ip: str) -> str:
    """將來源收旂為 IPv4 /24 或 IPv6 /64，擋住輪換單一 IP 的分散源。"""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown"
    prefix = 24 if address.version == 4 else 64
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))


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

    The user is resolved through ``Depends(get_current_user)``. FastAPI caches
    a dependency within one request, so a route that also takes
    ``CurrentUser`` does not run the auth lookup twice.

    Args:
        scope: namespace used in the Redis key (e.g. ``"ai-help"``); keep short.
        limit: maximum requests allowed within the window.
        window_seconds: rolling window length in seconds.
    """
    from app.api.deps.auth import get_current_user  # local import to avoid cycle

    async def _dep(current_user: User = Depends(get_current_user)) -> None:
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


def rate_limit_by_network_and_global(
    *,
    scope: str,
    subnet_limit: int,
    global_limit: int,
    window_seconds: int,
) -> Callable:
    """用同一 Redis 滑動視窗同時限制來源網段與全站容量。"""

    async def _dep(request: Request) -> None:
        redis = await get_redis()
        buckets = (
            (f"network:{scope}:{_client_network(_client_ip(request))}", subnet_limit),
            (f"global:{scope}", global_limit),
        )
        for key, limit in buckets:
            allowed, info = await check_rate_limit_by_key(
                redis,
                key=key,
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


async def enforce_account_rate_limit(
    *,
    scope: str,
    account: str,
    limit: int,
    window_seconds: int,
) -> None:
    """依「嘗試登入的帳號」計次，超過就回 429（登入前還沒有 user id 可用）。

    暴力破解針對的是單一帳號，這條才是主要防線：共用 NAT 出口的整班學生
    各用各的帳號，不會互相吃掉額度。帳號名稱正規化（去空白、小寫）後雜湊，
    Redis key 與超限時的 log 都不會留下明文帳號。
    """
    normalized = account.strip().lower()
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:32]
    redis = await get_redis()
    allowed, info = await check_rate_limit_by_key(
        redis,
        key=f"account:{scope}:{digest}",
        limit=limit,
        window_seconds=window_seconds,
        scope=scope,
    )
    if not allowed:
        retry_after = info.get("window_seconds", window_seconds)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=t("rate_limit.account_too_many_attempts", retry_after=retry_after),
            headers={"Retry-After": str(retry_after)},
        )


__all__ = [
    "enforce_account_rate_limit",
    "rate_limit_by_ip",
    "rate_limit_by_network_and_global",
    "rate_limit_by_user",
]
