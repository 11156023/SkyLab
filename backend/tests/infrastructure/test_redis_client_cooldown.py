"""Redis 斷線後的請求路徑重連冷卻：不能每個請求都重建連線池再逾時。"""

from __future__ import annotations

import time

import pytest

from app.infrastructure.redis import client


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


@pytest.fixture(autouse=True)
def _isolated_client_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(client, "_redis_enabled", True)
    monkeypatch.setattr(client, "_redis_client", None)
    monkeypatch.setattr(client, "_redis_pool", None)
    monkeypatch.setattr(client, "_last_init_failure_at", None)


async def test_get_redis_skips_reinit_inside_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[bool] = []

    async def fake_init(*, raise_on_failure: bool = True) -> None:
        attempts.append(raise_on_failure)

    monkeypatch.setattr(client, "init_redis", fake_init)
    monkeypatch.setattr(client, "_last_init_failure_at", time.monotonic())

    assert await client.get_redis() is None
    assert attempts == []


async def test_get_redis_retries_after_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[bool] = []

    async def fake_init(*, raise_on_failure: bool = True) -> None:
        attempts.append(raise_on_failure)

    monkeypatch.setattr(client, "init_redis", fake_init)
    monkeypatch.setattr(
        client,
        "_last_init_failure_at",
        time.monotonic() - client.REINIT_COOLDOWN_SECONDS - 1,
    )

    assert await client.get_redis() is None
    # 請求路徑上的重試永遠不得讓端點變 500
    assert attempts == [False]


async def test_failed_init_starts_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BoomPool:
        @staticmethod
        def from_url(*_args, **_kwargs):
            raise ConnectionError("redis down")

    monkeypatch.setattr(client, "ConnectionPool", _BoomPool)
    monkeypatch.setattr(client, "redis_failures_are_fatal", lambda: False)

    await client.init_redis(raise_on_failure=False)

    assert client._redis_client is None
    assert client._in_reinit_cooldown() is True


async def test_close_redis_clears_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "_last_init_failure_at", time.monotonic())

    await client.close_redis()

    assert client._in_reinit_cooldown() is False
