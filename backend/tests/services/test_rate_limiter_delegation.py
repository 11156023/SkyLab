"""限流兩個入口共用同一套滑動視窗實作（不需要真的 Redis）。"""

from __future__ import annotations

import pytest

from app.exceptions import AppError
from app.infrastructure.redis import rate_limiter


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


class _FakeRedis:
    """只模擬滑動視窗 Lua 腳本會用到的行為：每個 key 一個計數。"""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.eval_calls: list[tuple[str, int, int]] = []

    async def eval(self, _script: str, _numkeys: int, key: str, *argv):
        limit = int(argv[2])
        self.eval_calls.append((key, limit, int(argv[3])))
        current = self.counts.get(key, 0)
        if current >= limit:
            return [0, current]
        self.counts[key] = current + 1
        return [1, current + 1]

    async def zremrangebyscore(self, key: str, *_args) -> int:  # noqa: ARG002
        return 0

    async def zcard(self, key: str) -> int:
        return self.counts.get(key, 0)

    async def delete(self, key: str) -> int:
        return 1 if self.counts.pop(key, None) is not None else 0


async def test_ai_proxy_limit_delegates_to_generic_key_limiter() -> None:
    redis = _FakeRedis()

    allowed, info = await rate_limiter.check_rate_limit_sliding_window(
        redis, "user-1", limit=2, window_seconds=60
    )

    assert allowed is True
    assert info["current"] == 1
    assert info["remaining"] == 1
    # key 命名與舊實作相容：狀態端點與 clear_user_rate_limit 都靠這個 key
    assert redis.eval_calls == [("rate_limit:user:user-1", 2, 120)]


async def test_ai_proxy_limit_blocks_and_peek_does_not_consume() -> None:
    redis = _FakeRedis()
    for _ in range(2):
        await rate_limiter.check_rate_limit_sliding_window(
            redis, "user-2", limit=2, window_seconds=60
        )

    allowed, info = await rate_limiter.check_rate_limit_sliding_window(
        redis, "user-2", limit=2, window_seconds=60
    )
    assert allowed is False
    assert info["remaining"] == 0

    peeked = await rate_limiter.peek_rate_limit_by_key(
        redis, key=rate_limiter.ai_proxy_rate_limit_key("user-2"), window_seconds=60
    )
    assert peeked == 2
    assert redis.counts["rate_limit:user:user-2"] == 2


async def test_peek_returns_none_without_redis() -> None:
    assert (
        await rate_limiter.peek_rate_limit_by_key(None, key="user:x", window_seconds=60)
        is None
    )


async def test_ai_proxy_limit_fails_closed_without_redis_outside_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rate_limiter, "redis_failures_are_fatal", lambda: True)

    with pytest.raises(AppError) as excinfo:
        await rate_limiter.check_rate_limit_sliding_window(None, "user-3")
    assert excinfo.value.status_code == 503


async def test_ai_proxy_limit_fails_open_without_redis_in_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rate_limiter, "redis_failures_are_fatal", lambda: False)

    allowed, info = await rate_limiter.check_rate_limit_sliding_window(None, "user-4")

    assert allowed is True
    assert info["disabled"] is True


async def test_clear_user_rate_limit_targets_same_key() -> None:
    redis = _FakeRedis()
    await rate_limiter.check_rate_limit_sliding_window(redis, "user-5")

    assert await rate_limiter.clear_user_rate_limit(redis, "user-5") is True
    assert "rate_limit:user:user-5" not in redis.counts
