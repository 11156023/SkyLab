"""Tests for the generic key-based rate limiter (used by IP-scoped limits)."""

from __future__ import annotations

import pytest

redis_asyncio = pytest.importorskip("redis.asyncio")
Redis = redis_asyncio.Redis

from app.exceptions import AppError
from app.infrastructure.redis import rate_limiter
from app.infrastructure.redis.rate_limiter import (
    FAIL_CLOSED_SCOPES,
    check_rate_limit_by_key,
)


@pytest.fixture
async def redis_client():
    from app.features.ai.config import settings

    client = Redis.from_url(settings.redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.mark.asyncio
async def test_allows_under_limit(redis_client: Redis) -> None:
    for i in range(4):
        allowed, info = await check_rate_limit_by_key(
            redis_client, key="ip:test:1.2.3.4", limit=5, window_seconds=60
        )
        assert allowed is True
        assert info["current"] == i + 1


@pytest.mark.asyncio
async def test_blocks_over_limit(redis_client: Redis) -> None:
    for _ in range(5):
        await check_rate_limit_by_key(
            redis_client, key="ip:block:1.1.1.1", limit=5, window_seconds=60
        )
    allowed, info = await check_rate_limit_by_key(
        redis_client, key="ip:block:1.1.1.1", limit=5, window_seconds=60
    )
    assert allowed is False
    assert info["remaining"] == 0


@pytest.mark.asyncio
async def test_distinct_keys_have_independent_quotas(redis_client: Redis) -> None:
    for _ in range(3):
        await check_rate_limit_by_key(
            redis_client, key="ip:a:1", limit=3, window_seconds=60
        )
    # key "a" exhausted; key "b" should still have full quota.
    allowed_a, _ = await check_rate_limit_by_key(
        redis_client, key="ip:a:1", limit=3, window_seconds=60
    )
    allowed_b, info_b = await check_rate_limit_by_key(
        redis_client, key="ip:b:1", limit=3, window_seconds=60
    )
    assert allowed_a is False
    assert allowed_b is True
    assert info_b["current"] == 1


@pytest.mark.asyncio
async def test_redis_disabled_fails_open() -> None:
    allowed, info = await check_rate_limit_by_key(
        None, key="anything", limit=1, window_seconds=10
    )
    assert allowed is True
    assert info.get("disabled") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["signup", "ai-api-request", "ai-api-rotate"])
async def test_control_plane_scopes_fail_closed_without_redis(
    monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    monkeypatch.setattr(rate_limiter, "redis_failures_are_fatal", lambda: True)
    assert scope in FAIL_CLOSED_SCOPES
    with pytest.raises(AppError) as exc_info:
        await check_rate_limit_by_key(
            None,
            key=f"test:{scope}",
            limit=1,
            window_seconds=60,
            scope=scope,
        )
    assert exc_info.value.status_code == 503
