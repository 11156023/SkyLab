"""app.infrastructure.redis.rate_limiter 的放行／拒絕與 Redis 故障分支覆蓋（假 Redis）。"""

from __future__ import annotations

import pytest

from app.infrastructure.redis import rate_limiter as rl


class _EvalRedis:
    def __init__(self, result=None, *, fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.calls: list[tuple] = []

    async def eval(self, script, numkeys, *args):
        self.calls.append((numkeys, args))
        if self.fail:
            raise ConnectionError("redis down")
        return self.result


def _fatal(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    monkeypatch.setattr(rl, "redis_failures_are_fatal", lambda: value)


async def test_redis_error_fail_open(monkeypatch, caplog) -> None:
    _fatal(monkeypatch, True)
    allowed, info = await rl.check_rate_limit_by_key(
        _EvalRedis(fail=True), key="k", limit=5, window_seconds=60, scope="other"
    )
    assert allowed is True
    assert info["error"] == "redis down"
    assert "Redis rate limit check failed" in caplog.text


async def test_peek_rate_limit_error_returns_none(caplog) -> None:
    assert (
        await rl.peek_rate_limit_by_key(
            _EvalRedis(fail=True), key="k", window_seconds=60
        )
        is None
    )
    assert "peek failed" in caplog.text
