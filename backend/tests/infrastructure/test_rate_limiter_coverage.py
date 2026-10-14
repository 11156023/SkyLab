"""app.infrastructure.redis.rate_limiter 的放行／拒絕與 Redis 故障分支覆蓋（假 Redis）。"""

from __future__ import annotations

import pytest

from app.exceptions import AppError
from app.infrastructure.redis import rate_limiter as rl


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


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


def test_ai_proxy_rate_limit_key() -> None:
    assert rl.ai_proxy_rate_limit_key("abc") == "ai-request:abc"


async def test_no_redis_local_allows(monkeypatch) -> None:
    _fatal(monkeypatch, False)
    allowed, info = await rl.check_rate_limit_by_key(
        None, key="k", limit=5, window_seconds=60, scope="login"
    )
    assert allowed is True
    assert info["disabled"] is True
    assert info["remaining"] == 5


async def test_no_redis_fail_closed_scope_raises(monkeypatch) -> None:
    _fatal(monkeypatch, True)
    with pytest.raises(AppError) as exc:
        await rl.check_rate_limit_by_key(
            None, key="k", limit=5, window_seconds=60, scope="login"
        )
    assert exc.value.status_code == 503


async def test_no_redis_fail_open_scope_allows(monkeypatch, caplog) -> None:
    _fatal(monkeypatch, True)
    allowed, _ = await rl.check_rate_limit_by_key(
        None, key="k", limit=5, window_seconds=60, scope="vm-action"
    )
    assert allowed is True
    assert "fail-open scope=vm-action" in caplog.text


async def test_allowed_and_denied(caplog) -> None:
    redis = _EvalRedis([1, 3])
    allowed, info = await rl.check_rate_limit_by_key(
        redis, key="k", limit=5, window_seconds=60, legacy_keys=("old",)
    )
    assert allowed is True
    assert info["current"] == 3
    assert info["remaining"] == 2
    numkeys, args = redis.calls[0]
    assert numkeys == 2
    assert args[:2] == ("rate_limit:k", "rate_limit:old")

    redis = _EvalRedis([0, 5])
    allowed, info = await rl.check_rate_limit_by_key(
        redis, key="k", limit=5, window_seconds=60
    )
    assert allowed is False
    assert info["remaining"] == 0
    assert "Rate limit exceeded" in caplog.text


async def test_redis_error_fail_open(monkeypatch, caplog) -> None:
    _fatal(monkeypatch, True)
    allowed, info = await rl.check_rate_limit_by_key(
        _EvalRedis(fail=True), key="k", limit=5, window_seconds=60, scope="other"
    )
    assert allowed is True
    assert info["error"] == "redis down"
    assert "Redis rate limit check failed" in caplog.text


async def test_redis_error_fail_closed(monkeypatch) -> None:
    _fatal(monkeypatch, True)
    with pytest.raises(AppError):
        await rl.check_rate_limit_by_key(
            _EvalRedis(fail=True), key="k", limit=5, window_seconds=60, scope="signup"
        )


async def test_sliding_window_uses_ai_proxy_key_and_legacy() -> None:
    redis = _EvalRedis([1, 1])
    allowed, _ = await rl.check_rate_limit_sliding_window(
        redis, "req1", limit=3, window_seconds=30, legacy_credential_ids=("c1",)
    )
    assert allowed is True
    _, args = redis.calls[0]
    assert args[:2] == ("rate_limit:ai-request:req1", "rate_limit:credential:c1")


async def test_peek_rate_limit() -> None:
    assert await rl.peek_rate_limit_by_key(None, key="k", window_seconds=60) is None
    redis = _EvalRedis("4")
    assert (
        await rl.peek_rate_limit_by_key(
            redis, key="k", window_seconds=60, legacy_keys=("a", "b")
        )
        == 4
    )
    assert redis.calls[0][0] == 3


async def test_peek_rate_limit_error_returns_none(caplog) -> None:
    assert (
        await rl.peek_rate_limit_by_key(
            _EvalRedis(fail=True), key="k", window_seconds=60
        )
        is None
    )
    assert "peek failed" in caplog.text
