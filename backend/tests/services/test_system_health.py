"""平台健康：判定規則、心跳 observer、runner 掛勾、readiness 與系統告警。

全部不需要真的 DB／Redis／PVE：探測函式與 Redis client 以 monkeypatch 替換。
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.domain.scheduling import runner
from app.domain.scheduling.models import ScheduledTask
from app.services.monitoring import (
    health_policy,
    heartbeat_service,
    system_health_service,
)

NOW = 1_800_000_000.0


# ─── health_policy：任務／迴圈狀態 ─────────────────────────────────────────


def test_task_status_pending_until_first_run() -> None:
    assert health_policy.task_status({}, interval_seconds=60, now=NOW) == "pending"


def test_task_status_ok_warning_failing() -> None:
    base = {"last_run_at": NOW - 30}
    assert health_policy.task_status({**base, "consecutive_failures": 0}, interval_seconds=60, now=NOW) == "ok"
    assert health_policy.task_status({**base, "consecutive_failures": 1}, interval_seconds=60, now=NOW) == "warning"
    assert (
        health_policy.task_status(
            {**base, "consecutive_failures": health_policy.FAILING_THRESHOLD},
            interval_seconds=60,
            now=NOW,
        )
        == "failing"
    )


def test_task_status_stale_uses_minimum_window() -> None:
    # 60 秒間隔 × 5 = 300 秒，但下限 600 秒：一輪 tick 本來就可能跑好幾分鐘
    entry = {"last_run_at": NOW - 500, "consecutive_failures": 0}
    assert health_policy.task_status(entry, interval_seconds=60, now=NOW) == "ok"
    entry = {"last_run_at": NOW - 601, "consecutive_failures": 0}
    assert health_policy.task_status(entry, interval_seconds=60, now=NOW) == "stale"


def test_failing_takes_priority_over_stale() -> None:
    entry = {"last_run_at": NOW - 10_000, "consecutive_failures": 5}
    assert health_policy.task_status(entry, interval_seconds=60, now=NOW) == "failing"


def test_loop_status_tracks_leader_tick() -> None:
    assert health_policy.loop_status({"interval_seconds": 60}, now=NOW) == "pending"
    fresh = {"interval_seconds": 60, "leader_last_tick_at": NOW - 90}
    assert health_policy.loop_status(fresh, now=NOW) == "ok"
    # 非 leader 行程一直有 tick，但沒有人拿到 leader → 仍算停擺
    stale = {"interval_seconds": 60, "last_tick_at": NOW, "leader_last_tick_at": NOW - 3600}
    assert health_policy.loop_status(stale, now=NOW) == "stale"


def test_overall_status_down_only_for_core_dependencies() -> None:
    ok = [{"name": "database", "status": "ok"}, {"name": "redis", "status": "ok"}]
    assert health_policy.overall_status(ok, [], []) == "ok"
    worker_down = [*ok, {"name": "worker", "status": "down"}]
    assert health_policy.overall_status(worker_down, [], []) == "degraded"
    db_down = [{"name": "database", "status": "down"}, {"name": "redis", "status": "ok"}]
    assert health_policy.overall_status(db_down, [], []) == "down"


def test_overall_status_ignores_single_task_warning() -> None:
    comps = [{"name": "database", "status": "ok"}]
    assert health_policy.overall_status(comps, [], [{"status": "warning"}]) == "ok"
    assert health_policy.overall_status(comps, [], [{"status": "failing"}]) == "degraded"
    assert health_policy.overall_status(comps, [{"status": "stale"}], []) == "degraded"


# ─── health_policy：系統告警 ───────────────────────────────────────────────


def test_build_findings_covers_tasks_loops_and_components() -> None:
    findings = health_policy.build_findings(
        components=[
            {"name": "database", "status": "down"},  # DB 掛了寫不進告警，交給外部探測
            {"name": "worker", "label": "Worker", "status": "down", "detail": "no heartbeat"},
            {"name": "pve:1", "label": "Proxmox VE · lab", "status": "ok"},
            {"name": "redis", "status": "unknown"},
        ],
        loops=[{"loop": "web_push", "status": "stale"}, {"loop": "scheduler", "status": "ok"}],
        tasks=[
            {"loop": "scheduler", "task": "a", "status": "failing", "consecutive_failures": 4, "last_error": "boom"},
            {"loop": "scheduler", "task": "b", "status": "warning", "consecutive_failures": 1},
            {"loop": "scheduler", "task": "c", "status": "stale", "interval_seconds": 60},
        ],
    )
    by_target = {f.target: f for f in findings}
    assert set(by_target) == {
        "task:scheduler/a",
        "task:scheduler/c",
        "loop:web_push",
        "component:worker",
    }
    assert by_target["task:scheduler/a"].value == 4
    assert "boom" in by_target["task:scheduler/a"].message
    assert "Worker" in by_target["component:worker"].message


def test_evaluate_system_alerts_opens_resolves_and_respects_cooldown() -> None:
    findings = [
        health_policy.SystemFinding("component:worker", 0, 1, "worker down"),
        health_policy.SystemFinding("task:scheduler/a", 3, 3, "failing"),
        health_policy.SystemFinding("loop:web_push", 0, 1, "stale"),
    ]
    decision = health_policy.evaluate_system_alerts(
        findings,
        open_targets=["component:worker", "component:redis"],
        last_created={"loop:web_push": NOW - 60},
        cooldown_seconds=1800,
        now=NOW,
    )
    # worker 已經開著不重複開；web_push 在冷卻期內；redis 恢復了要收掉
    assert [f.target for f in decision.new_findings] == ["task:scheduler/a"]
    assert decision.resolved_targets == ["component:redis"]


# ─── heartbeat_service：observer（Redis 停用 → 只用記憶體） ────────────────


@pytest.fixture
def memory_only_heartbeat(monkeypatch: pytest.MonkeyPatch):
    async def no_redis():
        return None

    monkeypatch.setattr(heartbeat_service, "get_redis", no_redis)
    monkeypatch.setattr(heartbeat_service, "get_sync_redis", lambda: None)
    heartbeat_service.reset_memory()
    yield
    heartbeat_service.reset_memory()


async def test_observer_tracks_consecutive_failures(memory_only_heartbeat) -> None:
    observer = heartbeat_service.HeartbeatObserver("pytest_loop", interval_seconds=30)
    await observer.on_start(["job", "never_ran"])
    await observer.on_tick(is_leader=True)
    await observer.on_task("job", ok=False, duration_seconds=0.1, error=RuntimeError("x" * 500))
    await observer.on_task("job", ok=False, duration_seconds=0.1, error=RuntimeError("again"))

    snap = heartbeat_service.snapshot()
    assert snap.source == "memory"
    tasks = {t["task"]: t for t in snap.tasks}
    assert tasks["job"]["consecutive_failures"] == 2
    assert tasks["job"]["total_failures"] == 2
    assert tasks["job"]["last_error"] == "RuntimeError: again"
    assert "last_run_at" not in tasks["never_ran"]

    await observer.on_task("job", ok=True, duration_seconds=0.2, error=None)
    job = next(t for t in heartbeat_service.snapshot().tasks if t["task"] == "job")
    assert job["consecutive_failures"] == 0
    assert job["total_runs"] == 3
    assert job["last_success_at"] >= job["last_failure_at"]

    loop = next(item for item in heartbeat_service.snapshot().loops if item["loop"] == "pytest_loop")
    assert loop["interval_seconds"] == 30
    assert loop["leader_last_tick_at"] is not None


async def test_observer_error_message_is_truncated(memory_only_heartbeat) -> None:
    observer = heartbeat_service.HeartbeatObserver("pytest_loop", interval_seconds=30)
    await observer.on_task("job", ok=False, duration_seconds=0.1, error=RuntimeError("x" * 500))
    job = heartbeat_service.snapshot().tasks[0]
    assert len(job["last_error"]) == heartbeat_service.MAX_ERROR_LENGTH


async def test_non_leader_tick_does_not_refresh_leader_timestamp(memory_only_heartbeat) -> None:
    observer = heartbeat_service.HeartbeatObserver("pytest_loop", interval_seconds=30)
    await observer.on_tick(is_leader=False)
    loop = heartbeat_service.snapshot().loops[0]
    assert loop.get("last_tick_at") is not None
    assert loop.get("leader_last_tick_at") is None


# ─── runner：observer 掛勾 ────────────────────────────────────────────────


class _RecordingObserver:
    def __init__(self, stop: asyncio.Event) -> None:
        self.stop = stop
        self.events: list[tuple[Any, ...]] = []

    async def on_start(self, task_names: list[str]) -> None:
        self.events.append(("start", tuple(task_names)))

    async def on_tick(self, *, is_leader: bool) -> None:
        self.events.append(("tick", is_leader))

    async def on_task(self, task_name, *, ok, duration_seconds, error) -> None:
        self.events.append(("task", task_name, ok, type(error).__name__ if error else None))
        if task_name == "last":
            self.stop.set()


async def test_runner_reports_success_and_failure_to_observer() -> None:
    stop = asyncio.Event()
    observer = _RecordingObserver(stop)

    def boom() -> None:
        raise ValueError("nope")

    await asyncio.wait_for(
        runner.run_polling_scheduler(
            stop_event=stop,
            interval_seconds=1,
            tasks=[
                ScheduledTask(name="good", handler=lambda: 1),
                ScheduledTask(name="bad", handler=boom),
                ScheduledTask(name="last", handler=lambda: None),
            ],
            observer=observer,
        ),
        timeout=5,
    )

    assert observer.events == [
        ("start", ("good", "bad", "last")),
        ("tick", True),
        ("task", "good", True, None),
        ("task", "bad", False, "ValueError"),
        ("task", "last", True, None),
    ]


async def test_runner_marks_database_outage_as_failure_and_stops_tick() -> None:
    stop = asyncio.Event()
    observer = _RecordingObserver(stop)
    calls: list[str] = []

    def db_down() -> None:
        calls.append("db_down")
        stop.set()
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    await asyncio.wait_for(
        runner.run_polling_scheduler(
            stop_event=stop,
            interval_seconds=1,
            tasks=[
                ScheduledTask(name="db_down", handler=db_down),
                ScheduledTask(name="skipped", handler=lambda: calls.append("skipped")),
            ],
            observer=observer,
        ),
        timeout=5,
    )

    assert calls == ["db_down"]
    assert ("task", "db_down", False, "OperationalError") in observer.events


async def test_runner_survives_broken_observer() -> None:
    stop = asyncio.Event()
    ran: list[str] = []

    class Broken:
        async def on_start(self, task_names):
            raise RuntimeError("observer start broke")

        async def on_tick(self, *, is_leader):
            raise RuntimeError("observer tick broke")

        async def on_task(self, *args, **kwargs):
            raise RuntimeError("observer task broke")

    def task() -> None:
        ran.append("task")
        stop.set()

    await asyncio.wait_for(
        runner.run_polling_scheduler(
            stop_event=stop,
            interval_seconds=1,
            tasks=[ScheduledTask(name="t", handler=task)],
            observer=Broken(),
        ),
        timeout=5,
    )
    assert ran == ["task"]


async def test_runner_non_leader_ticks_without_running_tasks() -> None:
    stop = asyncio.Event()
    observer = _RecordingObserver(stop)
    ran: list[str] = []

    @contextmanager
    def not_leader():
        stop.set()
        yield False

    await asyncio.wait_for(
        runner.run_polling_scheduler(
            stop_event=stop,
            interval_seconds=1,
            tasks=[ScheduledTask(name="t", handler=lambda: ran.append("t"))],
            leader_gate=not_leader,
            observer=observer,
        ),
        timeout=5,
    )
    assert ran == []
    assert ("tick", False) in observer.events


# ─── system_health_service：readiness 與彙總 ───────────────────────────────


def _comp(name: str, status: str) -> dict[str, Any]:
    return {"name": name, "label": name, "status": status, "latency_ms": None, "detail": None}


def test_readiness_requires_database_and_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_health_service, "check_database", lambda: _comp("database", "ok"))
    monkeypatch.setattr(system_health_service, "check_redis", lambda: _comp("redis", "ok"))
    assert system_health_service.readiness() == {
        "status": "ok",
        "checks": {"database": True, "redis": True},
    }

    monkeypatch.setattr(system_health_service, "check_redis", lambda: _comp("redis", "down"))
    assert system_health_service.readiness()["status"] == "fail"

    # Redis 停用（REDIS_ENABLED=false）不算失敗
    monkeypatch.setattr(system_health_service, "check_redis", lambda: _comp("redis", "disabled"))
    assert system_health_service.readiness() == {
        "status": "ok",
        "checks": {"database": True, "redis": None},
    }

    monkeypatch.setattr(system_health_service, "check_database", lambda: _comp("database", "down"))
    assert system_health_service.readiness()["status"] == "fail"


def test_check_worker_reads_arq_health_key(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePipe:
        def __init__(self, health: str | None) -> None:
            self.health = health

        def get(self, key):
            assert key == "skylab:tasks:health-check"

        def zcard(self, key):
            assert key == "skylab:tasks"

        def execute(self):
            return [self.health, 7]

    class FakeRedis:
        def __init__(self, health: str | None) -> None:
            self.health = health

        def pipeline(self, transaction=False):
            return FakePipe(self.health)

    monkeypatch.setattr(system_health_service, "get_sync_redis", lambda: FakeRedis("j_complete=3"))
    assert system_health_service.check_worker(redis_ok=True)["status"] == "ok"

    monkeypatch.setattr(system_health_service, "get_sync_redis", lambda: FakeRedis(None))
    worker = system_health_service.check_worker(redis_ok=True)
    assert worker["status"] == "down"
    assert "queued=7" in worker["detail"]

    assert system_health_service.check_worker(redis_ok=False)["status"] == "unknown"

    monkeypatch.setattr(system_health_service, "get_sync_redis", lambda: None)
    assert system_health_service.check_worker(redis_ok=False)["status"] == "disabled"


def test_collect_system_health_merges_components_and_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        system_health_service,
        "collect_components",
        lambda **_: [_comp("database", "ok"), _comp("redis", "ok"), _comp("worker", "down")],
    )
    now = datetime.now(timezone.utc).timestamp()
    monkeypatch.setattr(
        heartbeat_service,
        "snapshot",
        lambda: heartbeat_service.HeartbeatSnapshot(
            loops=[{"loop": "scheduler", "interval_seconds": 60.0, "leader_last_tick_at": now}],
            tasks=[
                {"loop": "scheduler", "task": "b", "last_run_at": now, "consecutive_failures": 0},
                {"loop": "scheduler", "task": "a", "last_run_at": now, "consecutive_failures": 3},
            ],
            source="redis",
        ),
    )
    health = system_health_service.collect_system_health()
    assert health["status"] == "degraded"
    assert [t["task"] for t in health["tasks"]] == ["a", "b"]
    assert health["tasks"][0]["status"] == "failing"
    assert health["tasks"][0]["interval_seconds"] == 60.0
    assert health["loops"][0]["status"] == "ok"


async def test_readiness_route_returns_503_when_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.routes import utils as utils_routes

    monkeypatch.setattr(
        system_health_service,
        "readiness",
        lambda: {"status": "fail", "checks": {"database": False, "redis": True}},
    )
    response = await utils_routes.readiness_check()
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"

    monkeypatch.setattr(
        system_health_service,
        "readiness",
        lambda: {"status": "ok", "checks": {"database": True, "redis": None}},
    )
    assert (await utils_routes.readiness_check()).status_code == 200


# ─── system_health_service：告警要連續出現兩輪才開 ─────────────────────────


def test_findings_need_two_consecutive_rounds() -> None:
    system_health_service.reset_alert_state()
    worker = health_policy.SystemFinding("component:worker", 0, 1, "worker down")
    pve = health_policy.SystemFinding("component:pve:1", 0, 1, "pve down")

    assert system_health_service._confirmed([worker]) == []
    assert system_health_service._confirmed([worker, pve]) == [worker]
    # worker 恢復一輪又壞掉 → 重新計數
    assert system_health_service._confirmed([pve]) == [pve]
    assert system_health_service._confirmed([worker, pve]) == [pve]
    system_health_service.reset_alert_state()
