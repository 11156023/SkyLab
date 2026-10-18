"""app.services.monitoring.heartbeat_service：Redis 寫入／讀取路徑與失敗退回記憶體。"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.monitoring import heartbeat_service


class _AsyncPipe:
    def __init__(self, owner: _AsyncRedis) -> None:
        self.owner = owner

    def __getattr__(self, name: str):
        def record(*args: Any, **kwargs: Any) -> None:
            self.owner.calls.append((name, args, kwargs))

        return record

    async def execute(self) -> list[Any]:
        if self.owner.fail:
            raise ConnectionError("redis down")
        return []


class _AsyncRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def pipeline(self, transaction: bool = False) -> _AsyncPipe:
        assert transaction is False
        return _AsyncPipe(self)


@pytest.fixture(autouse=True)
def _clean_memory(monkeypatch: pytest.MonkeyPatch):
    heartbeat_service.reset_memory()
    monkeypatch.setattr(heartbeat_service._memory, "last_redis_warning", 0.0)
    yield
    heartbeat_service.reset_memory()


def _use_redis(monkeypatch: pytest.MonkeyPatch, redis: _AsyncRedis) -> None:
    async def fake_get_redis():
        return redis

    monkeypatch.setattr(heartbeat_service, "get_redis", fake_get_redis)


def test_warn_redis_failure_is_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(
        heartbeat_service.logger, "warning", lambda msg, *a, **k: warnings.append(msg)
    )
    monkeypatch.setattr(heartbeat_service.time, "monotonic", lambda: 1000.0)
    heartbeat_service._warn_redis_failure()
    heartbeat_service._warn_redis_failure()
    assert len(warnings) == 1
    assert heartbeat_service._memory.last_redis_warning == 1000.0


async def test_on_start_redis_failure_keeps_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warned: list[bool] = []
    monkeypatch.setattr(
        heartbeat_service, "_warn_redis_failure", lambda: warned.append(True)
    )
    _use_redis(monkeypatch, _AsyncRedis(fail=True))
    observer = heartbeat_service.HeartbeatObserver("scheduler", interval_seconds=60)
    await observer.on_start(["a"])
    assert warned == [True]
    assert ("scheduler", "a") in heartbeat_service._memory.tasks


async def test_on_tick_writes_redis_and_handles_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _AsyncRedis()
    _use_redis(monkeypatch, redis)
    observer = heartbeat_service.HeartbeatObserver("scheduler", interval_seconds=60)
    await observer.on_tick(is_leader=True)
    assert [c[0] for c in redis.calls] == ["hset", "expire", "sadd"]
    mapping = redis.calls[0][2]["mapping"]
    assert "leader_last_tick_at" in mapping

    warned: list[bool] = []
    monkeypatch.setattr(
        heartbeat_service, "_warn_redis_failure", lambda: warned.append(True)
    )
    redis.fail = True
    await observer.on_tick(is_leader=False)
    assert warned == [True]


async def test_on_task_writes_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _AsyncRedis()
    _use_redis(monkeypatch, redis)
    observer = heartbeat_service.HeartbeatObserver("scheduler", interval_seconds=60)

    await observer.on_task("a", ok=True, duration_seconds=0.5, error=None)
    names = [c[0] for c in redis.calls]
    assert names == ["hset", "hincrby", "expire", "sadd"]
    assert redis.calls[0][2]["mapping"]["consecutive_failures"] == 0

    redis.calls.clear()
    await observer.on_task("a", ok=False, duration_seconds=0.1, error=None)
    names = [c[0] for c in redis.calls]
    assert names == ["hset", "hincrby", "hincrby", "hincrby", "expire", "sadd"]
    assert redis.calls[0][2]["mapping"]["last_error"] == ""

    redis.calls.clear()
    await observer.on_task(
        "a", ok=False, duration_seconds=0.1, error=RuntimeError("boom")
    )
    assert redis.calls[0][2]["mapping"]["last_error"] == "RuntimeError: boom"

    warned: list[bool] = []
    monkeypatch.setattr(
        heartbeat_service, "_warn_redis_failure", lambda: warned.append(True)
    )
    redis.fail = True
    await observer.on_task("a", ok=True, duration_seconds=0.1, error=None)
    assert warned == [True]


def test_coerce_handles_bad_values() -> None:
    out = heartbeat_service._coerce(
        {
            "last_run_at": "not-a-number",
            "interval_seconds": "60",
            "consecutive_failures": "x",
            "total_runs": "3",
            "last_error": "",
            "last_success_at": "",
        }
    )
    assert out["last_run_at"] is None
    assert out["interval_seconds"] == 60.0
    assert out["consecutive_failures"] == 0
    assert out["total_runs"] == 3
    assert out["last_error"] is None
    assert out["last_success_at"] == ""


class _SyncPipe:
    def __init__(self, store: dict[str, dict[str, Any] | None], fail: bool) -> None:
        self.store = store
        self.fail = fail
        self.keys: list[str] = []

    def hgetall(self, key: str) -> None:
        self.keys.append(key)

    def execute(self) -> list[Any]:
        if self.fail:
            raise ConnectionError("redis down")
        return [self.store.get(key) for key in self.keys]


class _SyncRedis:
    def __init__(
        self,
        loops: set[str] | None,
        tasks: set[str] | None,
        store: dict[str, dict[str, Any] | None],
        *,
        fail: bool = False,
    ) -> None:
        self.sets = {
            heartbeat_service.LOOPS_SET: loops,
            heartbeat_service.TASKS_SET: tasks,
        }
        self.store = store
        self.fail = fail

    def smembers(self, key: str):
        return self.sets[key]

    def pipeline(self, transaction: bool = False) -> _SyncPipe:
        return _SyncPipe(self.store, self.fail)


def test_snapshot_reads_redis_and_merges_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with heartbeat_service._memory.lock:
        heartbeat_service._memory.loops["scheduler"] = {
            "loop": "scheduler",
            "interval_seconds": 1,
        }
        heartbeat_service._memory.loops["web_push"] = {
            "loop": "web_push",
            "interval_seconds": 5,
        }
        heartbeat_service._memory.tasks[("scheduler", "a")] = {
            "loop": "scheduler",
            "task": "a",
        }
        heartbeat_service._memory.tasks[("scheduler", "new")] = {
            "loop": "scheduler",
            "task": "new",
        }

    client = _SyncRedis(
        {"scheduler"},
        {"scheduler:a", "scheduler:b"},
        {
            "skylab:hb:loop:scheduler": {
                "interval_seconds": "60",
                "last_tick_at": "10",
            },
            "skylab:hb:task:scheduler:a": {
                "consecutive_failures": "2",
                "last_error": "",
            },
            "skylab:hb:task:scheduler:b": None,
        },
    )
    monkeypatch.setattr(heartbeat_service, "get_sync_redis", lambda: client)
    snap = heartbeat_service.snapshot()
    assert snap.source == "redis"
    assert [loop["loop"] for loop in snap.loops] == ["scheduler", "web_push"]
    assert snap.loops[0]["interval_seconds"] == 60.0
    assert [(t["loop"], t["task"]) for t in snap.tasks] == [
        ("scheduler", "a"),
        ("scheduler", "b"),
        ("scheduler", "new"),
    ]
    assert snap.tasks[0]["consecutive_failures"] == 2
    assert snap.tasks[0]["last_error"] is None


def test_snapshot_falls_back_to_memory_on_redis_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with heartbeat_service._memory.lock:
        heartbeat_service._memory.loops["scheduler"] = {"loop": "scheduler"}
    client = _SyncRedis({"scheduler"}, set(), {}, fail=True)
    monkeypatch.setattr(heartbeat_service, "get_sync_redis", lambda: client)
    snap = heartbeat_service.snapshot()
    assert snap.source == "memory"
    assert snap.loops == [{"loop": "scheduler"}]
