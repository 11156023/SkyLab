"""監控 overview 快取與併發去重測試。"""

import asyncio
import time

import pytest

from app.services.monitoring import monitoring_service


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.get_calls: list[str] = []

    async def get(self, key: str) -> str | None:
        self.get_calls.append(key)
        return self.data.get(key)

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def eval(self, _script: str, _numkeys: int, key: str, token: str) -> int:
        if self.data.get(key) == token:
            del self.data[key]
            return 1
        return 0


@pytest.fixture(autouse=True)
def clear_local_cache() -> None:
    with monitoring_service._local_overview_condition:
        monitoring_service._local_overview_entry = None
        monitoring_service._local_overview_refreshing = False
        monitoring_service._local_overview_condition.notify_all()


def _overview():
    return monitoring_service.build_overview(
        [{
            "node": "pve1",
            "status": "online",
            "cpu": 0.1,
            "maxcpu": 4,
            "mem": 1,
            "maxmem": 4,
            "disk": 1,
            "maxdisk": 4,
            "uptime": 60,
        }],
        [],
    )


async def test_fresh_redis_overview_avoids_pve_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    redis.data[monitoring_service._OVERVIEW_FRESH_KEY] = _overview().model_dump_json()

    def should_not_collect(*, session: object):
        raise AssertionError("PVE collection should not run on a fresh cache hit")

    monkeypatch.setattr(monitoring_service, "get_overview", should_not_collect)

    result = await monitoring_service.get_overview_cached(redis=redis)

    assert result.data_status == "fresh"
    assert monitoring_service._OVERVIEW_FRESH_KEY in redis.get_calls


async def test_concurrent_redis_misses_share_one_pve_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    calls = 0
    expected = _overview()

    def collect(*, session: object):
        nonlocal calls
        calls += 1
        time.sleep(0.15)
        return expected

    monkeypatch.setattr(monitoring_service, "get_overview", collect)

    first, second = await asyncio.gather(
        monitoring_service.get_overview_cached(redis=redis),
        monitoring_service.get_overview_cached(redis=redis),
    )

    assert calls == 1
    assert first.collected_at == second.collected_at
    assert first.data_status == "fresh"
    assert second.data_status == "fresh"


async def test_pve_failure_returns_stale_redis_overview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    redis.data[monitoring_service._OVERVIEW_STALE_KEY] = _overview().model_dump_json()

    def unavailable(*, session: object):
        raise RuntimeError("PVE unavailable")

    monkeypatch.setattr(monitoring_service, "get_overview", unavailable)

    result = await monitoring_service.get_overview_cached(redis=redis)

    assert result.data_status == "stale"
    assert result.overall_status == "warning"
    assert result.cache_age_seconds >= 0


async def test_pve_failure_without_stale_is_not_retried_in_same_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    calls = 0

    def unavailable(*, session: object):
        nonlocal calls
        calls += 1
        raise RuntimeError("PVE unavailable")

    monkeypatch.setattr(monitoring_service, "get_overview", unavailable)

    with pytest.raises(RuntimeError, match="PVE unavailable"):
        await monitoring_service.get_overview_cached(redis=redis)

    assert calls == 1
