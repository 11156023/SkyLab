"""
Redis Rate Limiter 測試
"""

import time
from datetime import datetime

import pytest

redis_asyncio = pytest.importorskip("redis.asyncio")
Redis = redis_asyncio.Redis

from app.infrastructure.redis.rate_limiter import check_rate_limit_sliding_window


@pytest.fixture
async def redis_client():
    """創建 Redis 測試客戶端"""
    from app.features.ai.config import settings

    redis = Redis.from_url(settings.redis_url, decode_responses=True)

    # 清理測試數據
    await redis.flushdb()

    yield redis

    # 清理測試數據
    await redis.flushdb()
    await redis.aclose()


@pytest.mark.asyncio
async def test_rate_limit_allows_within_limit(redis_client: Redis):
    """測試：在限制內應該允許"""
    user_id = "test-user-1"

    # 發送 19 個請求（限制 20）
    for i in range(19):
        allowed, info = await check_rate_limit_sliding_window(
            redis_client, user_id, limit=20, window_seconds=60
        )
        assert allowed is True
        assert info["remaining"] == 20 - i - 1
        assert info["limit"] == 20
        assert info["current"] == i + 1


@pytest.mark.asyncio
async def test_rate_limit_blocks_over_limit(redis_client: Redis):
    """測試：超過限制應該拒絕"""
    user_id = "test-user-2"

    # 發送 20 個請求
    for i in range(20):
        allowed, info = await check_rate_limit_sliding_window(
            redis_client, user_id, limit=20, window_seconds=60
        )
        assert allowed is True
        assert info["current"] == i + 1

    # 第 21 個應該被拒絕
    allowed, info = await check_rate_limit_sliding_window(
        redis_client, user_id, limit=20, window_seconds=60
    )
    assert allowed is False
    assert info["remaining"] == 0
    assert info["current"] == 20


@pytest.mark.asyncio
async def test_sliding_window_accuracy(redis_client: Redis):
    """測試：滑動窗口的精準性"""
    user_id = "test-user-3"

    # T0: 發送 20 個請求
    for _ in range(20):
        allowed, _ = await check_rate_limit_sliding_window(
            redis_client, user_id, limit=20, window_seconds=5
        )
        assert allowed is True

    # T0: 應該被拒絕
    allowed, info = await check_rate_limit_sliding_window(
        redis_client, user_id, limit=20, window_seconds=5
    )
    assert allowed is False
    assert info["current"] == 20

    # 等待 5 秒（窗口過期）
    time.sleep(5)

    # T+5s: 應該允許（舊請求已移出窗口）
    allowed, info = await check_rate_limit_sliding_window(
        redis_client, user_id, limit=20, window_seconds=5
    )
    assert allowed is True
    assert info["current"] == 1  # 只有這個新請求


@pytest.mark.asyncio
async def test_rate_limit_different_users(redis_client: Redis):
    """測試：不同用戶的限制是獨立的"""
    user1 = "test-user-4"
    user2 = "test-user-5"

    # 用戶 1 發送 20 個請求
    for _ in range(20):
        allowed, _ = await check_rate_limit_sliding_window(
            redis_client, user1, limit=20, window_seconds=60
        )
        assert allowed is True

    # 用戶 1 應該被拒絕
    allowed, _ = await check_rate_limit_sliding_window(
        redis_client, user1, limit=20, window_seconds=60
    )
    assert allowed is False

    # 用戶 2 應該仍然可以發送
    allowed, info = await check_rate_limit_sliding_window(
        redis_client, user2, limit=20, window_seconds=60
    )
    assert allowed is True
    assert info["current"] == 1


@pytest.mark.asyncio
async def test_rate_limit_info_structure(redis_client: Redis):
    """測試：返回的資訊結構正確"""
    user_id = "test-user-7"

    allowed, info = await check_rate_limit_sliding_window(
        redis_client, user_id, limit=20, window_seconds=60
    )

    assert allowed is True
    assert "limit" in info
    assert "current" in info
    assert "remaining" in info
    assert "reset_at" in info
    assert "window_seconds" in info

    assert info["limit"] == 20
    assert info["current"] == 1
    assert info["remaining"] == 19
    assert info["window_seconds"] == 60
    assert isinstance(info["reset_at"], datetime)


@pytest.mark.asyncio
async def test_rate_limit_partial_window(redis_client: Redis):
    """測試：部分窗口過期後恢復配額"""
    user_id = "test-user-8"

    # T0: 發送 10 個請求
    for _ in range(10):
        await check_rate_limit_sliding_window(
            redis_client, user_id, limit=20, window_seconds=3
        )

    # 等待 1 秒
    time.sleep(1)

    # T+1s: 再發送 10 個請求
    for i in range(10):
        allowed, info = await check_rate_limit_sliding_window(
            redis_client, user_id, limit=20, window_seconds=3
        )
        assert allowed is True
        assert info["current"] == 10 + i + 1

    # T+1s: 應該被拒絕（20 個請求都在窗口內）
    allowed, _ = await check_rate_limit_sliding_window(
        redis_client, user_id, limit=20, window_seconds=3
    )
    assert allowed is False

    # 等待 3 秒（第一批請求移出窗口）
    time.sleep(3)

    # T+4s: 第一批 10 個請求已經移出，應該可以發送
    allowed, info = await check_rate_limit_sliding_window(
        redis_client, user_id, limit=20, window_seconds=3
    )
    assert allowed is True
    # 窗口內只剩下第二批 10 個請求
    assert info["current"] <= 11  # 可能還剩一些第二批的請求
