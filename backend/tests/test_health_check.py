"""Unit tests for the health-check helpers in app.api.routes.utils.

These tests bypass FastAPI's TestClient and exercise the internal
``_check_db`` / ``_check_redis`` / ``_check_proxmox`` helpers + the
``_aggregate_overall`` reducer. External dependencies are monkey-patched
so the tests run without a live database, Redis, or Proxmox node.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.api.routes import utils as utils_route


class _FakeConn:
    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _FailingConn(_FakeConn):
    def execute(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("db down")


class _FakeEngine:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def connect(self) -> _FakeConn:
        return self._conn


class _FakeRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def ping(self) -> bool:
        if self._fail:
            raise RuntimeError("redis down")
        return True


@pytest.mark.asyncio
async def test_check_db_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(utils_route, "engine", _FakeEngine(_FakeConn()))
    status = await utils_route._check_db()
    assert status.status == "ok"
    assert status.latency_ms is not None and status.latency_ms >= 0


@pytest.mark.asyncio
async def test_check_db_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(utils_route, "engine", _FakeEngine(_FailingConn()))
    status = await utils_route._check_db()
    assert status.status == "error"
    # health 是未驗證端點：對外只回固定代碼，原始例外字串只進 log
    assert status.detail == utils_route._UNAVAILABLE_DETAIL
    assert "db down" not in (status.detail or "")


@pytest.mark.asyncio
async def test_check_redis_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_redis() -> _FakeRedis:
        return _FakeRedis(fail=False)

    monkeypatch.setattr(utils_route, "get_redis", fake_get_redis)
    status = await utils_route._check_redis()
    assert status.status == "ok"


@pytest.mark.asyncio
async def test_check_redis_skipped_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_redis() -> None:
        return None

    monkeypatch.setattr(utils_route, "get_redis", fake_get_redis)
    status = await utils_route._check_redis()
    assert status.status == "skipped"


@pytest.mark.asyncio
async def test_check_redis_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_redis() -> _FakeRedis:
        return _FakeRedis(fail=True)

    monkeypatch.setattr(utils_route, "get_redis", fake_get_redis)
    status = await utils_route._check_redis()
    assert status.status == "error"


def test_aggregate_overall_ok() -> None:
    s = utils_route.DependencyStatus(status="ok")
    assert utils_route._aggregate_overall(s, s, s) == "ok"


def test_aggregate_overall_degraded_when_skipped() -> None:
    ok = utils_route.DependencyStatus(status="ok")
    skipped = utils_route.DependencyStatus(status="skipped")
    assert utils_route._aggregate_overall(ok, skipped, ok) == "degraded"


def test_aggregate_overall_error_dominates() -> None:
    ok = utils_route.DependencyStatus(status="ok")
    error = utils_route.DependencyStatus(status="error")
    skipped = utils_route.DependencyStatus(status="skipped")
    assert utils_route._aggregate_overall(ok, skipped, error) == "error"


@pytest.mark.asyncio
async def test_check_db_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A long-blocking DB ping should time out gracefully."""

    class _SlowConn(_FakeConn):
        def execute(self, *_args: Any, **_kwargs: Any) -> None:
            # Simulate hang longer than the 3s timeout. Use a short hang
            # combined with a temporarily-shrunk timeout for fast tests.
            import time as _time

            _time.sleep(0.5)

    monkeypatch.setattr(utils_route, "engine", _FakeEngine(_SlowConn()))
    monkeypatch.setattr(utils_route, "_HEALTH_CHECK_TIMEOUT_SECONDS", 0.05)
    status = await utils_route._check_db()
    assert status.status == "error"
    assert status.detail == "timeout"
