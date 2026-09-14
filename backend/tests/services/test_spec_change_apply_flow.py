"""Unit tests for the user-driven spec change apply flow (no DB / Proxmox).

Approval no longer writes to Proxmox. ``_run_apply`` is the background task
that decides whether a power cycle is needed (running QEMU + cores/memory),
shuts the machine down, applies, and starts it again. Everything that touches
the DB (``_finish_apply``) is stubbed out here.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import ProxmoxError
from app.models import SpecChangeRequestStatus
from app.services.vm import spec_change_service as scs


class FakeProxmox:
    """Records calls; ``statuses`` is consumed by successive get_status calls."""

    def __init__(self, statuses: list[str], *, fail_update: bool = False,
                 fail_start: bool = False) -> None:
        self._statuses = list(statuses)
        self.fail_update = fail_update
        self.fail_start = fail_start
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def get_status(self, node: str, vmid: int, rtype: str) -> dict:
        self.calls.append(("get_status", (node, vmid, rtype)))
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return {"status": status}

    def control(self, node: str, vmid: int, rtype: str, action: str) -> None:
        self.calls.append(("control", (node, vmid, rtype, action)))
        if action == "start" and self.fail_start:
            raise RuntimeError("start boom")

    def update_config(self, node: str, vmid: int, rtype: str, **params: Any) -> None:
        self.calls.append(("update_config", (node, vmid, rtype, params)))
        if self.fail_update:
            raise RuntimeError("update boom")

    def resize_disk(self, node: str, vmid: int, rtype: str, disk: str, size: str) -> None:
        self.calls.append(("resize_disk", (node, vmid, rtype, disk, size)))

    def find_resource(self, vmid: int) -> dict:
        return {"node": "pve1", "type": "qemu", "vmid": vmid}

    def actions(self) -> list[str]:
        return [
            f"{name}:{args[3]}" if name == "control" else name
            for name, args in self.calls
        ]


def _request(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "vmid": 150,
        "requested_cpu": 4,
        "requested_memory": None,
        "requested_disk": None,
        "current_cpu": 2,
        "current_memory": 2048,
        "current_disk": 20,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture()
def finish_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_finish(request_id: uuid.UUID, user_id: uuid.UUID, vmid: int, **kw: Any) -> None:
        calls.append({"request_id": request_id, "vmid": vmid, **kw})

    monkeypatch.setattr(scs, "_finish_apply", fake_finish)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    return calls


def _run(fake: FakeProxmox, req: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
         rtype: str = "qemu") -> None:
    monkeypatch.setattr(scs, "proxmox_service", fake)
    scs._run_apply(
        req.id, req, {"node": "pve1", "type": rtype, "vmid": req.vmid}, uuid.uuid4()
    )


# ── needs_power_cycle ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("rtype", "running", "cpu", "mem", "disk", "expected"),
    [
        ("qemu", True, 4, None, None, True),
        ("qemu", True, None, 4096, None, True),
        ("qemu", True, None, None, 40, False),  # disk resize is online
        ("qemu", False, 4, None, None, False),  # already off
        ("lxc", True, 4, 4096, None, False),  # cgroup limits apply live
        ("lxc", True, None, None, 40, False),
    ],
)
def test_needs_power_cycle(rtype, running, cpu, mem, disk, expected) -> None:
    req = _request(requested_cpu=cpu, requested_memory=mem, requested_disk=disk)
    assert scs.needs_power_cycle(rtype, running, req) is expected


# ── _run_apply ────────────────────────────────────────────────────────────


def test_running_qemu_is_shut_down_applied_and_started(
    finish_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeProxmox(["running", "stopped"])
    req = _request()

    _run(fake, req, monkeypatch)

    assert fake.actions() == [
        "get_status",  # was_running?
        "control:shutdown",
        "get_status",  # stopped
        "update_config",
        "control:start",
    ]
    assert fake.calls[3][1][3] == {"cores": 4}
    assert finish_calls == [
        {
            "request_id": req.id,
            "vmid": 150,
            "changes": ["CPU: 2 -> 4 cores"],
            "warning": None,
            "power_cycled": True,
        }
    ]


def test_stopped_qemu_is_applied_without_power_actions(
    finish_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeProxmox(["stopped"])

    _run(fake, _request(), monkeypatch)

    assert fake.actions() == ["get_status", "update_config"]
    assert finish_calls[0]["power_cycled"] is False


def test_running_lxc_is_applied_live(
    finish_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeProxmox(["running"])

    _run(fake, _request(requested_memory=4096), monkeypatch, rtype="lxc")

    assert fake.actions() == ["get_status", "update_config"]
    assert fake.calls[1][1][3] == {"cores": 4, "memory": 4096}
    assert finish_calls[0]["power_cycled"] is False


def test_apply_failure_after_shutdown_restarts_machine_and_records_error(
    finish_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeProxmox(["running", "stopped"], fail_update=True)
    req = _request()

    with pytest.raises(ProxmoxError):
        _run(fake, req, monkeypatch)

    assert fake.actions()[-2:] == ["update_config", "control:start"]
    assert len(finish_calls) == 1
    error = finish_calls[0]["error"]
    assert "update boom" in error
    assert "機器已重新開機" in error
    assert "changes" not in finish_calls[0]


def test_start_failure_after_apply_is_reported_as_warning(
    finish_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeProxmox(["running", "stopped"], fail_start=True)

    _run(fake, _request(), monkeypatch)

    assert finish_calls[0]["changes"] == ["CPU: 2 -> 4 cores"]
    assert "自動開機失敗" in finish_calls[0]["warning"]
    assert finish_calls[0]["power_cycled"] is True


def test_graceful_shutdown_timeout_falls_back_to_force_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # never reports stopped during the graceful window, stopped after "stop"
    class StubbornProxmox(FakeProxmox):
        def control(self, node, vmid, rtype, action):
            super().control(node, vmid, rtype, action)
            if action == "stop":
                self._statuses = ["stopped"]

    fake = StubbornProxmox(["running"])
    monkeypatch.setattr(scs, "proxmox_service", fake)
    monkeypatch.setattr(scs, "SHUTDOWN_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(scs, "STOP_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(scs, "_POLL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    scs._ensure_stopped("pve1", 150, "qemu")

    control_actions = [a for a in fake.actions() if a.startswith("control:")]
    assert control_actions == ["control:shutdown", "control:stop"]


def test_unstoppable_machine_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeProxmox(["running"])
    monkeypatch.setattr(scs, "proxmox_service", fake)
    monkeypatch.setattr(scs, "SHUTDOWN_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(scs, "STOP_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(scs, "_POLL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    with pytest.raises(ProxmoxError):
        scs._ensure_stopped("pve1", 150, "qemu")


# ── _apply_status ─────────────────────────────────────────────────────────


def _row(status: SpecChangeRequestStatus, **overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "status": status,
        "applied_at": None,
        "apply_error": None,
        "apply_started_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_apply_status_derivation(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    approved = SpecChangeRequestStatus.approved

    assert scs._apply_status(_row(SpecChangeRequestStatus.pending)) is None
    assert scs._apply_status(_row(approved)) == "ready"
    assert scs._apply_status(_row(approved, applied_at=now)) == "applied"
    assert scs._apply_status(_row(approved, apply_error="boom")) == "failed"
    # applied wins over a leftover warning in apply_error
    assert scs._apply_status(_row(approved, applied_at=now, apply_error="warn")) == "applied"

    monkeypatch.setattr(scs.background_tasks, "is_active", lambda _tid: True)
    assert scs._apply_status(_row(approved, apply_started_at=now)) == "applying"
    monkeypatch.setattr(scs.background_tasks, "is_active", lambda _tid: False)
    assert scs._apply_status(_row(approved, apply_started_at=now)) == "interrupted"
