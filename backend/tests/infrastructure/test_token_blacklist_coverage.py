"""app.infrastructure.redis.token_blacklist 在 fail-open／fail-closed 兩種環境的分支覆蓋（假 Redis）。"""

from __future__ import annotations

import time

import pytest

from app.infrastructure.redis import token_blacklist as tb


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


class _FakeRedis:
    def __init__(self, *, fail: bool = False, as_bytes: bool = False) -> None:
        self.data: dict[str, str] = {}
        self.fail = fail
        self.as_bytes = as_bytes
        self.vanish_on_get = False

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("redis down")

    async def set(self, key, value, ex=None, nx=False):
        self._check()
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def get(self, key):
        self._check()
        if self.vanish_on_get:
            return None
        value = self.data.get(key)
        if value is not None and self.as_bytes:
            return value.encode()
        return value

    async def exists(self, key):
        self._check()
        return 1 if key in self.data else 0


def _fatal(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    monkeypatch.setattr(tb, "redis_failures_are_fatal", lambda: value)


def _future() -> int:
    return int(time.time()) + 600


@pytest.mark.parametrize("fatal", [True, False])
async def test_revoke_without_redis(monkeypatch, caplog, fatal: bool) -> None:
    _fatal(monkeypatch, fatal)
    assert await tb.revoke_jti(None, "j", _future()) is False
    assert ("cannot revoke" in caplog.text) is fatal


async def test_revoke_and_check_roundtrip() -> None:
    redis = _FakeRedis()
    assert await tb.revoke_jti(redis, "j1", _future()) is True
    assert await tb.is_jti_revoked(redis, "j1") is True
    assert await tb.is_jti_revoked(redis, "j2") is False


async def test_revoke_expired_token_is_noop() -> None:
    assert await tb.revoke_jti(_FakeRedis(), "j", int(time.time()) - 1) is False


async def test_revoke_redis_error_returns_false(caplog) -> None:
    assert await tb.revoke_jti(_FakeRedis(fail=True), "j", _future()) is False
    assert "Failed to revoke" in caplog.text


@pytest.mark.parametrize("fatal", [True, False])
async def test_is_revoked_without_redis(monkeypatch, fatal: bool) -> None:
    _fatal(monkeypatch, fatal)
    assert await tb.is_jti_revoked(None, "j") is fatal


@pytest.mark.parametrize("fatal", [True, False])
async def test_is_revoked_redis_error(monkeypatch, caplog, fatal: bool) -> None:
    _fatal(monkeypatch, fatal)
    assert await tb.is_jti_revoked(_FakeRedis(fail=True), "j") is fatal
    assert ("rejecting" if fatal else "allowing") in caplog.text


@pytest.mark.parametrize("fatal", [True, False])
async def test_mark_refresh_without_redis(monkeypatch, fatal: bool) -> None:
    _fatal(monkeypatch, fatal)
    assert await tb.mark_refresh_token_used(None, "r", _future()) is (not fatal)


async def test_mark_refresh_expired_token_rejected() -> None:
    assert (
        await tb.mark_refresh_token_used(_FakeRedis(), "r", int(time.time()) - 5)
        is False
    )


@pytest.mark.parametrize("as_bytes", [False, True])
async def test_mark_refresh_first_use_then_grace_then_stale(
    monkeypatch, as_bytes: bool
) -> None:
    redis = _FakeRedis(as_bytes=as_bytes)
    clock = [1_000_000.0]
    monkeypatch.setattr(tb.time, "time", lambda: clock[0])
    exp = int(clock[0]) + 3600

    assert await tb.mark_refresh_token_used(redis, "r", exp) is True
    clock[0] += 10
    assert await tb.mark_refresh_token_used(redis, "r", exp, grace_seconds=30) is True
    clock[0] += 60
    assert await tb.mark_refresh_token_used(redis, "r", exp, grace_seconds=30) is False


async def test_mark_refresh_key_vanished_between_calls() -> None:
    redis = _FakeRedis()
    exp = _future()
    assert await tb.mark_refresh_token_used(redis, "r", exp) is True
    redis.vanish_on_get = True
    assert await tb.mark_refresh_token_used(redis, "r", exp) is True


@pytest.mark.parametrize("fatal", [True, False])
async def test_mark_refresh_redis_error(monkeypatch, caplog, fatal: bool) -> None:
    _fatal(monkeypatch, fatal)
    assert await tb.mark_refresh_token_used(_FakeRedis(fail=True), "r", _future()) is (
        not fatal
    )
    assert ("rejecting" if fatal else "allowing") in caplog.text
