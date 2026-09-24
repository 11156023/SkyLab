"""lifespan 迴圈的 leader 鎖：推播與 WireGuard reconciler 多副本時只跑一份。"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any

import pytest

from app.services.network import wireguard_service
from app.services.notification import web_push_service
from app.services.scheduling import leader


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


def _fake_lock(acquired: bool, seen: list[int]):
    @contextmanager
    def _lock(key: int = leader.SCHEDULER_LEADER_LOCK_KEY):
        seen.append(key)
        yield acquired

    return _lock


def test_each_loop_has_its_own_lock_key() -> None:
    keys = {
        leader.SCHEDULER_LEADER_LOCK_KEY,
        leader.PUSH_NOTIFIER_LEADER_LOCK_KEY,
        leader.WIREGUARD_RECONCILER_LEADER_LOCK_KEY,
    }
    assert len(keys) == 3


def test_push_notifier_gate_uses_push_lock_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    monkeypatch.setattr(leader, "scheduler_leader_lock", _fake_lock(True, seen))

    with web_push_service.push_notifier_leader_gate() as is_leader:
        assert is_leader is True
    assert seen == [leader.PUSH_NOTIFIER_LEADER_LOCK_KEY]


async def test_run_push_notifier_passes_leader_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_scheduler(**kwargs: Any) -> None:
        captured.update(kwargs)

    from app.domain.scheduling import runner

    monkeypatch.setattr(web_push_service, "is_available", lambda: True)
    monkeypatch.setattr(runner, "run_polling_scheduler", fake_scheduler)

    await web_push_service.run_push_notifier(asyncio.Event())

    assert captured["leader_gate"] is web_push_service.push_notifier_leader_gate
    assert captured["interval_seconds"] == web_push_service.PUSH_POLL_SECONDS


def test_wireguard_tick_skips_reconcile_when_not_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []
    calls: list[str] = []
    monkeypatch.setattr(leader, "scheduler_leader_lock", _fake_lock(False, seen))
    monkeypatch.setattr(
        wireguard_service, "reconcile_once", lambda: calls.append("reconcile")
    )

    assert wireguard_service.reconcile_tick() is False
    assert calls == []
    assert seen == [leader.WIREGUARD_RECONCILER_LEADER_LOCK_KEY]


def test_wireguard_tick_reconciles_when_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(leader, "scheduler_leader_lock", _fake_lock(True, []))
    monkeypatch.setattr(
        wireguard_service, "reconcile_once", lambda: calls.append("reconcile")
    )

    assert wireguard_service.reconcile_tick() is True
    assert calls == ["reconcile"]


async def test_run_reconciler_uses_gated_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = asyncio.Event()
    ticks: list[str] = []

    def fake_tick() -> bool:
        ticks.append("tick")
        stop.set()
        return True

    monkeypatch.setattr(wireguard_service, "reconcile_tick", fake_tick)
    monkeypatch.setattr(
        wireguard_service.settings, "WIREGUARD_RECONCILE_INTERVAL_SECONDS", 10
    )

    await asyncio.wait_for(wireguard_service.run_reconciler(stop), timeout=5)

    assert ticks == ["tick"]


def test_push_gate_resets_baseline_when_not_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    """失去 leader 的行程要清掉去重基準，之後搶回時才不會補推一整段舊事件。"""
    resets: list[str] = []
    monkeypatch.setattr(leader, "scheduler_leader_lock", _fake_lock(False, []))
    monkeypatch.setattr(web_push_service, "reset_tick_state", lambda: resets.append("reset"))

    with web_push_service.push_notifier_leader_gate() as is_leader:
        assert is_leader is False
    assert resets == ["reset"]

    monkeypatch.setattr(leader, "scheduler_leader_lock", _fake_lock(True, []))
    with web_push_service.push_notifier_leader_gate() as is_leader:
        assert is_leader is True
    assert resets == ["reset"]  # leader 那一輪不清


async def test_polling_scheduler_enters_gate_off_the_loop_thread() -> None:
    """leader 鎖是同步 DB I/O：要在執行緒裡進出，不能凍住 event loop。"""
    import threading

    from app.domain.scheduling import runner
    from app.domain.scheduling.models import ScheduledTask

    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}
    stop = asyncio.Event()

    @contextmanager
    def gate():
        seen["enter"] = threading.get_ident()
        try:
            yield True
        finally:
            seen["exit"] = threading.get_ident()

    def task() -> None:
        stop.set()

    await asyncio.wait_for(
        runner.run_polling_scheduler(
            stop_event=stop,
            interval_seconds=1,
            tasks=[ScheduledTask(name="t", handler=task)],
            leader_gate=gate,
        ),
        timeout=5,
    )

    assert seen["enter"] != loop_thread
    assert seen["exit"] != loop_thread
