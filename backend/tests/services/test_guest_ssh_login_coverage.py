"""Coverage tests for app.services.resource.guest_ssh_login (agent wait / thread paths)."""

from __future__ import annotations

import importlib.util
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from app.services.resource import guest_ssh_login as svc


def _fresh_module() -> ModuleType:
    """Load an independent copy of the module.

    tests/conftest.py replaces ``_run_qemu`` on the imported module with a no-op;
    a fresh copy (same source file, so coverage still counts it) keeps the real
    implementation without undoing the global fixture.
    """
    spec = importlib.util.find_spec("app.services.resource.guest_ssh_login")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_wait_for_agent_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(svc, "time", clock)
    monkeypatch.setattr(svc.guest, "ping_qemu_agent", lambda _n, _v: False)
    assert svc._wait_for_agent("pve1", 400, 12.0) is False
    assert len(clock.sleeps) == 3


def test_enable_skips_when_agent_never_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        svc.proxmox_ops, "get_config", lambda *_a: {"agent": "enabled=1"}
    )
    monkeypatch.setattr(svc, "_wait_for_agent", lambda _n, _v, _t: False)
    executed: list[Any] = []
    monkeypatch.setattr(svc.guest, "exec_qemu", lambda *a, **k: executed.append(a))
    assert svc.enable_qemu_ssh_login("pve1", 400) is False
    assert executed == []


def test_enable_reports_non_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc.proxmox_ops, "get_config", lambda *_a: {"agent": "1"})
    monkeypatch.setattr(svc, "_wait_for_agent", lambda _n, _v, _t: True)
    monkeypatch.setattr(svc.guest, "get_osinfo_qemu", lambda _n, _v: None)
    monkeypatch.setattr(
        svc.guest,
        "exec_qemu",
        lambda *_a, **_k: (1, "", "guest-exec is disabled\n"),
    )
    assert svc.enable_qemu_ssh_login("pve1", 400) is False


def test_run_qemu_success_clears_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _fresh_module()
    seen: list[tuple[str, int]] = []
    monkeypatch.setattr(
        mod, "enable_qemu_ssh_login", lambda n, v: seen.append((n, v)) or True
    )
    mod._pending.add(("pve1", 402))
    mod._run_qemu("pve1", 402)
    assert seen == [("pve1", 402)]
    assert mod._pending == set()


def test_schedule_thread_start_failure_releases_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BrokenThread:
        def __init__(self, **_kw: Any) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(svc, "threading", SimpleNamespace(Thread=_BrokenThread))
    svc._pending.clear()
    svc.schedule_after_start("pve1", 404, "qemu")
    assert svc._pending == set()
