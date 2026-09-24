"""ExpiringKV：Redis 為主、記憶體備援的短期暫存。"""

from __future__ import annotations

import json

import pytest

from app.core.config import settings as core_settings
from app.infrastructure.redis import sync_kv


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


class _FakeRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.fail = fail

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("redis down")

    def get(self, key: str) -> str | None:
        self._check()
        return self.data.get(key)

    def set(self, key: str, value: str, *, ex: int) -> None:
        self._check()
        self.data[key] = value
        self.ttls[key] = ex

    def delete(self, key: str) -> int:
        self._check()
        return 1 if self.data.pop(key, None) is not None else 0

    def scan_iter(self, *, match: str, count: int):  # noqa: ARG002
        self._check()
        prefix = match[:-1]
        yield from [k for k in self.data if k.startswith(prefix)]


def test_memory_backend_when_redis_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", False)
    kv = sync_kv.ExpiringKV("t", ttl_seconds=60)

    kv.set("a", {"x": 1})
    assert kv.get("a") == {"x": 1}
    assert kv.count() == 1
    kv.delete("a")
    assert kv.get("a") is None
    assert kv.count() == 0


def test_memory_backend_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", False)
    clock = [1000.0]
    monkeypatch.setattr(sync_kv.time, "monotonic", lambda: clock[0])
    kv = sync_kv.ExpiringKV("t", ttl_seconds=10)

    kv.set("a", {"x": 1})
    clock[0] += 5
    assert kv.get("a") == {"x": 1}
    clock[0] += 6
    assert kv.get("a") is None


def test_redis_backend_namespaces_keys_and_sets_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", True)
    fake = _FakeRedis()
    kv = sync_kv.ExpiringKV("device", ttl_seconds=300)
    monkeypatch.setattr(kv, "_redis", lambda: fake)

    kv.set("code1", {"token": None}, ttl_seconds=42)

    assert json.loads(fake.data["kv:device:code1"]) == {"token": None}
    assert fake.ttls["kv:device:code1"] == 42
    assert kv.get("code1") == {"token": None}
    assert kv.count() == 1
    kv.delete("code1")
    assert fake.data == {}


def test_redis_failure_falls_back_to_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", True)
    fake = _FakeRedis(fail=True)
    kv = sync_kv.ExpiringKV("device", ttl_seconds=300)
    monkeypatch.setattr(kv, "_redis", lambda: fake)

    kv.set("code1", {"token": None})

    assert kv.get("code1") == {"token": None}
    assert kv.count() == 1


def test_redis_miss_falls_through_to_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis 短暫故障時寫進記憶體的 key，Redis 恢復後仍要讀得到。"""
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", True)
    fake = _FakeRedis(fail=True)
    kv = sync_kv.ExpiringKV("device", ttl_seconds=300)
    monkeypatch.setattr(kv, "_redis", lambda: fake)
    kv.set("code1", {"token": None})  # 落到記憶體

    fake.fail = False  # Redis 恢復，但那個 key 不在 Redis 裡
    assert kv.get("code1") == {"token": None}
