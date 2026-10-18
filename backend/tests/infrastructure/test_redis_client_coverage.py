"""app.infrastructure.redis.client 與 sync_client 的連線、標籤與關閉流程覆蓋。"""

from __future__ import annotations

import pytest

from app.infrastructure.redis import client, sync_client


@pytest.fixture(autouse=True)
def _isolated_client_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(client, "_redis_enabled", True)
    monkeypatch.setattr(client, "_redis_backend_available", True)
    monkeypatch.setattr(client, "_redis_client", None)
    monkeypatch.setattr(client, "_redis_pool", None)
    monkeypatch.setattr(client, "_last_init_failure_at", None)


class _Closable:
    def __init__(self, **_kw: object) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _Pool(_Closable):
    created: list[_Pool] = []

    @classmethod
    def from_url(cls, *_args: object, **_kwargs: object) -> _Pool:
        pool = cls()
        cls.created.append(pool)
        return pool


class _OkRedis(_Closable):
    async def ping(self) -> bool:
        return True


class _PingFailRedis(_Closable):
    async def ping(self) -> bool:
        raise ConnectionError("no pong")


@pytest.mark.parametrize(
    ("dsn", "expected"),
    [
        ("redis://host:notaport/0", "redis://<invalid>"),
        ("unix:///var/run/redis.sock", "unix:///var/run/redis.sock"),
        ("unix://", "unix://<unknown>"),
        ("redis://", "redis://<unknown>"),
        ("//cache:6379", "redis://cache:6379"),
        ("redis://[::1]:6379/2", "redis://[::1]:6379/2"),
        ("redis://cache/?db=x", "redis://cache/"),
        ("redis://cache?db=3", "redis://cache/3"),
    ],
)
def test_redis_connection_label(dsn: str, expected: str) -> None:
    assert client.redis_connection_label(dsn) == expected


async def test_init_without_backend_package_fatal_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client, "_redis_backend_available", False)
    monkeypatch.setattr(client.core_settings, "REDIS_ENABLED", True)
    monkeypatch.setattr(client, "redis_failures_are_fatal", lambda: True)

    with pytest.raises(RuntimeError, match="not installed"):
        await client.init_redis()


async def test_init_without_backend_package_nonfatal_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(client, "_redis_backend_available", False)
    monkeypatch.setattr(client.core_settings, "REDIS_ENABLED", True)
    monkeypatch.setattr(client, "redis_failures_are_fatal", lambda: True)

    await client.init_redis(raise_on_failure=False)

    assert "Functionality will be skipped" in caplog.text


async def test_ping_failure_nonfatal_logs_and_enters_cooldown(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(client, "ConnectionPool", _Pool)
    monkeypatch.setattr(client, "Redis", _PingFailRedis)
    monkeypatch.setattr(client, "redis_failures_are_fatal", lambda: False)

    assert await client.get_redis() is None
    assert "Rate limiting will be disabled" in caplog.text
    # second call inside the cooldown does not retry
    monkeypatch.setattr(client, "Redis", _OkRedis)
    assert await client.get_redis() is None


async def test_close_redis_closes_client_and_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = _OkRedis()
    pool = _Pool()
    monkeypatch.setattr(client, "_redis_client", redis_client)
    monkeypatch.setattr(client, "_redis_pool", pool)

    await client.close_redis()

    assert redis_client.closed is True
    assert pool.closed is True
    assert client._redis_client is None
    assert client._redis_pool is None


def test_get_sync_redis_creates_client_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    class _FakeRedisClass:
        @staticmethod
        def from_url(url: str, **kwargs: object) -> object:
            calls.append((url, kwargs))
            return object()

    class _FakeModule:
        Redis = _FakeRedisClass

    monkeypatch.setattr(sync_client.settings, "REDIS_ENABLED", True)
    monkeypatch.setattr(sync_client, "redis_sync", _FakeModule)
    monkeypatch.setitem(sync_client._state, "client", None)

    first = sync_client.get_sync_redis()
    second = sync_client.get_sync_redis()

    assert first is second
    assert len(calls) == 1
    assert calls[0][1]["socket_timeout"] == sync_client._SOCKET_TIMEOUT_SECONDS
