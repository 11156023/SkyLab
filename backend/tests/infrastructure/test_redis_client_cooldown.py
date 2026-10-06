"""Redis 斷線後的請求路徑重連冷卻：不能每個請求都重建連線池再逾時。"""

from __future__ import annotations

import logging
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


def test_redis_connection_label_removes_userinfo_and_query_secrets() -> None:
    label = client.redis_connection_label(
        "rediss://alice:super-secret@redis.internal:6380/?db=4&password=query-secret"
    )

    assert label == "rediss://redis.internal:6380/4"
    assert "alice" not in label
    assert "super-secret" not in label
    assert "query-secret" not in label


async def test_successful_redis_log_uses_redacted_connection_label(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    dsn = "redis://alice:super-secret@redis.internal:6380/4?token=query-secret"

    class _Pool:
        @staticmethod
        def from_url(*_args: object, **_kwargs: object) -> object:
            return object()

    class _Redis:
        def __init__(self, *, connection_pool: object) -> None:
            self.connection_pool = connection_pool

        async def ping(self) -> None:
            return None

    monkeypatch.setattr(client.core_settings, "REDIS_URL", dsn)
    monkeypatch.setattr(client, "ConnectionPool", _Pool)
    monkeypatch.setattr(client, "Redis", _Redis)

    with caplog.at_level(logging.INFO, logger=client.logger.name):
        await client.init_redis()

    assert "redis://redis.internal:6380/4" in caplog.text
    assert "alice" not in caplog.text
    assert "super-secret" not in caplog.text
    assert "query-secret" not in caplog.text


async def test_fatal_redis_error_does_not_expose_dsn_or_driver_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn = "redis://alice:super-secret@redis.internal:6380/4?token=query-secret"

    class _BoomPool:
        @staticmethod
        def from_url(*_args: object, **_kwargs: object) -> None:
            raise ConnectionError(f"driver echoed {dsn}")

    monkeypatch.setattr(client.core_settings, "REDIS_URL", dsn)
    monkeypatch.setattr(client, "ConnectionPool", _BoomPool)
    monkeypatch.setattr(client, "redis_failures_are_fatal", lambda: True)

    with pytest.raises(RuntimeError) as exc_info:
        await client.init_redis()

    message = str(exc_info.value)
    assert "redis://redis.internal:6380/4" in message
    assert "ConnectionError" in message
    assert "alice" not in message
    assert "super-secret" not in message
    assert "query-secret" not in message


async def test_nonfatal_redis_log_does_not_expose_driver_secrets(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    dsn = "redis://alice:super-secret@redis.internal:6380/4?token=query-secret"

    class _BoomPool:
        @staticmethod
        def from_url(*_args: object, **_kwargs: object) -> None:
            raise ConnectionError(f"driver echoed {dsn}")

    monkeypatch.setattr(client.core_settings, "REDIS_URL", dsn)
    monkeypatch.setattr(client, "ConnectionPool", _BoomPool)
    monkeypatch.setattr(client, "redis_failures_are_fatal", lambda: False)

    with caplog.at_level(logging.ERROR, logger=client.logger.name):
        await client.init_redis(raise_on_failure=False)

    assert "redis://redis.internal:6380/4" in caplog.text
    assert "ConnectionError" in caplog.text
    assert "alice" not in caplog.text
    assert "super-secret" not in caplog.text
    assert "query-secret" not in caplog.text
