"""app.services.monitoring.monitoring_service：資料解析邊界、本機／Redis overview 快取退化路徑。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.schemas.monitoring import MonitoringThresholds
from app.services.monitoring import monitoring_service as ms
from app.services.proxmox import proxmox_service

THRESHOLDS = MonitoringThresholds()


@pytest.fixture(autouse=True)
def _clear_local_cache():
    def clear() -> None:
        with ms._local_overview_condition:
            ms._local_overview_entry = None
            ms._local_overview_refreshing = False
            ms._local_overview_condition.notify_all()

    clear()
    yield
    clear()


def _overview():
    return ms.build_overview(
        [
            {
                "node": "pve1",
                "status": "online",
                "cpu": 0.1,
                "maxcpu": 4,
                "mem": 1,
                "maxmem": 4,
                "disk": 1,
                "maxdisk": 4,
                "uptime": 60,
            }
        ],
        [],
    )


def _store(overview: Any, stored_at: float) -> None:
    with ms._local_overview_condition:
        ms._local_overview_entry = ms._LocalOverviewEntry(stored_at, overview)


class _Clock:
    """取代模組內 ``time``：依序回傳給定的 monotonic 值，用完後停在最後一個。"""

    def __init__(self, values: list[float]) -> None:
        self.values = list(values)

    def monotonic(self) -> float:
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]


# ─── 純函式邊界 ─────────────────────────────────────────────────────────────


def test_percent_and_cpu_handle_bad_values() -> None:
    assert ms._percent("abc", 10) is None
    assert ms._percent(object(), 10) is None
    assert ms._cpu_percent({"cpu": "abc"}, require_maxcpu=False) is None


def test_node_signals_empty_when_not_online() -> None:
    assert (
        ms._node_signals({"status": "offline", "cpu": 1.0, "maxcpu": 4}, THRESHOLDS)
        == []
    )
    assert ms._node_signals({"cpu": 1.0, "maxcpu": 4}, THRESHOLDS) == []


def test_issue_target_fallbacks() -> None:
    assert ms._issue_target({"vmid": "abc", "name": ""}) == ("未知資源", None)
    assert ms._issue_target({"vmid": "101"}) == ("VMID 101", 101)
    assert ms._issue_target({"vmid": 102, "name": " web "}) == ("web", 102)


def test_vm_counts_skip_templates_and_non_guests() -> None:
    counts = ms._vm_counts_by_node(
        [
            {"type": "qemu", "node": "a"},
            {"type": "lxc", "node": "a", "template": 1},
            {"type": "storage", "node": "a"},
            {"type": "LXC", "node": "b"},
        ]
    )
    assert counts == {"a": 1, "b": 1}


def test_connection_names_falls_back_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ms.proxmox_node_repo, "get_node_connection_names", lambda _s: {"a": "lab"}
    )
    assert ms._connection_names(object()) == {"a": "lab"}  # type: ignore[arg-type]

    def broken(_s: Any) -> dict[str, str]:
        raise RuntimeError("db gone")

    monkeypatch.setattr(ms.proxmox_node_repo, "get_node_connection_names", broken)
    assert ms._connection_names(object()) == {}  # type: ignore[arg-type]


def test_get_node_rrd_validates_and_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        proxmox_service,
        "get_node_rrd_data",
        lambda node, tf: [{"node": node, "tf": tf}],
    )
    assert ms.get_node_rrd("pve1", "day") == [{"node": "pve1", "tf": "day"}]


# ─── 本機快取 ───────────────────────────────────────────────────────────────


def test_local_collect_returns_fresh_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _store(_overview(), 1000.0)
    monkeypatch.setattr(ms, "time", _Clock([1001.0]))
    monkeypatch.setattr(
        ms, "_collect_overview_with_worker_session", lambda: pytest.fail("cached")
    )
    assert ms._get_or_collect_local_overview().data_status == "fresh"


def test_local_collect_returns_stale_while_other_refreshes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store(_overview(), 1000.0)
    ms._local_overview_refreshing = True
    monkeypatch.setattr(ms, "time", _Clock([1030.0]))
    monkeypatch.setattr(
        ms,
        "_collect_overview_with_worker_session",
        lambda: pytest.fail("someone else refreshes"),
    )
    result = ms._get_or_collect_local_overview()
    assert result.data_status == "stale"
    assert ms._local_overview_refreshing is True


def test_local_collect_waits_then_gives_up_and_collects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ms._local_overview_refreshing = True
    # 1 讀快取、2 截止時間（0+20）、3 迴圈內讀快取、4 剩 0.001 秒 → wait、
    # 5 再讀快取、6 超過截止 → break、其後固定 30
    monkeypatch.setattr(ms, "time", _Clock([0.0, 0.0, 0.0, 19.999, 0.0, 25.0, 30.0]))
    expected = _overview()
    monkeypatch.setattr(ms, "_collect_overview_with_worker_session", lambda: expected)
    result = ms._get_or_collect_local_overview()
    assert result.collected_at == expected.collected_at
    assert result.data_status == "fresh"
    assert ms._local_overview_refreshing is False
    assert ms._local_overview_entry is not None
    assert ms._local_overview_entry.overview is expected


def test_local_collect_rechecks_fresh_cache_under_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store(_overview(), 100.0)
    # 第一次讀到過期，進鎖後再讀一次已是新鮮（別的執行緒剛寫入的情境）
    monkeypatch.setattr(ms, "time", _Clock([200.0, 200.0, 105.0]))
    monkeypatch.setattr(
        ms,
        "_collect_overview_with_worker_session",
        lambda: pytest.fail("fresh after lock"),
    )
    assert ms._get_or_collect_local_overview().data_status == "fresh"


def test_local_collect_failure_returns_stale_or_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> Any:
        raise RuntimeError("PVE unavailable")

    monkeypatch.setattr(ms, "_collect_overview_with_worker_session", broken)
    _store(_overview(), 1000.0)
    monkeypatch.setattr(ms, "time", _Clock([1030.0]))
    assert ms._get_or_collect_local_overview().data_status == "stale"
    assert ms._local_overview_refreshing is False

    with ms._local_overview_condition:
        ms._local_overview_entry = None
    with pytest.raises(RuntimeError, match="PVE unavailable"):
        ms._get_or_collect_local_overview()
    assert ms._local_overview_refreshing is False


# ─── Redis 快取 ─────────────────────────────────────────────────────────────


class FakeRedis:
    def __init__(self, *, fail_write: bool = False, fail_eval: bool = False) -> None:
        self.data: dict[str, str] = {}
        self.fail_write = fail_write
        self.fail_eval = fail_eval

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(
        self, key: str, value: str, *, ex: int | None = None, nx: bool = False
    ) -> bool:
        if nx:
            if key in self.data:
                return False
        elif self.fail_write:
            raise ConnectionError("read-only replica")
        self.data[key] = value
        return True

    async def eval(self, _script: str, _numkeys: int, key: str, token: str) -> int:
        if self.fail_eval:
            raise ConnectionError("eval failed")
        if self.data.get(key) == token:
            del self.data[key]
            return 1
        return 0


async def test_cached_returns_local_fresh_without_redis() -> None:
    overview = _overview()
    ms._store_local_overview(overview)
    result = await ms.get_overview_cached(redis=SimpleNamespace())
    assert result.collected_at == overview.collected_at


async def test_malformed_redis_entry_is_ignored() -> None:
    redis = FakeRedis()
    redis.data["k"] = "not json"
    assert await ms._read_redis_overview(redis, "k", stale=False) is None


async def test_write_and_release_failures_are_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis(fail_write=True, fail_eval=True)
    expected = _overview()
    monkeypatch.setattr(ms, "_collect_overview_with_worker_session", lambda: expected)
    result = await ms.get_overview_cached(redis=redis)
    assert result.collected_at == expected.collected_at
    assert result.data_status == "fresh"
    assert ms._local_overview_refreshing is False
    # 鎖沒釋放成功（eval 失敗），快取也沒寫進去
    assert ms._OVERVIEW_LOCK_KEY in redis.data
    assert ms._OVERVIEW_FRESH_KEY not in redis.data


async def test_lock_held_elsewhere_falls_back_to_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    redis.data[ms._OVERVIEW_LOCK_KEY] = "other"
    redis.data[ms._OVERVIEW_STALE_KEY] = _overview().model_dump_json()
    monkeypatch.setattr(ms, "OVERVIEW_REFRESH_WAIT_SECONDS", 0)
    monkeypatch.setattr(
        ms,
        "_collect_overview_with_worker_session",
        lambda: pytest.fail("lock held elsewhere"),
    )
    result = await ms.get_overview_cached(redis=redis)
    assert result.data_status == "stale"
    # stale 結果不寫進本機快取
    assert ms._local_overview_entry is None


async def test_redis_errors_fall_back_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenRedis:
        async def get(self, key: str) -> str | None:
            raise ConnectionError("redis down")

    expected = _overview()
    monkeypatch.setattr(ms, "_get_or_collect_local_overview", lambda: expected)
    assert await ms.get_overview_cached(redis=BrokenRedis()) is expected
