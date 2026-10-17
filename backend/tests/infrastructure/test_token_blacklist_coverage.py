"""app.infrastructure.redis.token_blacklist 在 fail-open／fail-closed 兩種環境的分支覆蓋（假 Redis）。"""

from __future__ import annotations

import time

import pytest

from app.infrastructure.redis import token_blacklist as tb


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


async def test_revoke_redis_error_returns_false(caplog) -> None:
    assert await tb.revoke_jti(_FakeRedis(fail=True), "j", _future()) is False
    assert "Failed to revoke" in caplog.text


@pytest.mark.parametrize("fatal", [True, False])
async def test_is_revoked_redis_error(monkeypatch, caplog, fatal: bool) -> None:
    _fatal(monkeypatch, fatal)
    assert await tb.is_jti_revoked(_FakeRedis(fail=True), "j") is fatal
    assert ("rejecting" if fatal else "allowing") in caplog.text


async def test_mark_refresh_expired_token_rejected() -> None:
    assert (
        await tb.mark_refresh_token_used(_FakeRedis(), "r", int(time.time()) - 5)
        is False
    )


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
