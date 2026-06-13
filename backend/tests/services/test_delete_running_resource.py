"""Regression tests for deleting a running resource.

Deleting a running VM used to be broken in several ways:
- ``force=False`` rejected the request outright, but the frontend snapshots
  ``isRunning`` at click time while deletion executes later from a queue, so a
  stale snapshot caused spurious failures;
- the stop-wait loop treated a status-poll exception as "stopped" and also
  fell through after the timeout, so Proxmox ``delete`` was attempted on a
  still-running VM and failed.

New behaviour under test: graceful ``shutdown`` first, hard ``stop`` as a
fallback, and deletion is aborted (raises) if the resource never stops.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import ProxmoxError
from app.services.resource import resource_service


class FakeTime:
    """Virtual clock so timeout loops run instantly."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeProxmox:
    """Scriptable stand-in for proxmox_service.

    ``stops_on`` lists the power actions that actually transition the
    resource to ``stopped`` (e.g. a VM with no guest agent ignores
    ``shutdown`` but honours ``stop``).
    """

    def __init__(
        self,
        *,
        status: str = "running",
        stops_on: set[str] | None = None,
        status_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.stops_on = stops_on if stops_on is not None else {"shutdown", "stop"}
        self.status_error = status_error
        self.actions: list[str] = []
        self.deleted: list[int] = []

    def control(self, node: str, vmid: int, resource_type: str, action: str) -> None:
        self.actions.append(action)
        if action in self.stops_on:
            self.status = "stopped"

    def get_status(self, node: str, vmid: int, resource_type: str) -> dict:
        if self.status_error is not None:
            raise self.status_error
        return {"status": self.status}

    def delete_resource(
        self, node: str, vmid: int, resource_type: str, **params: Any
    ) -> None:
        self.deleted.append(vmid)


class FakeSession:
    def add(self, obj: Any) -> None:
        pass

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


@pytest.fixture()
def fake_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub every collaborator of resource_service.delete except proxmox."""
    monkeypatch.setattr(resource_service, "time", FakeTime())
    monkeypatch.setattr(
        resource_service,
        "resource_repo",
        SimpleNamespace(delete_resource=lambda **kw: None),
    )
    monkeypatch.setattr(
        resource_service,
        "audit_log_repo",
        SimpleNamespace(delete_audit_logs_by_vmid=lambda **kw: None),
    )
    monkeypatch.setattr(
        resource_service,
        "vm_request_repo",
        SimpleNamespace(get_latest_approved_vm_request_by_vmid=lambda **kw: None),
    )
    monkeypatch.setattr(
        resource_service,
        "batch_provision_repo",
        SimpleNamespace(clear_task_vmid_references=lambda **kw: 0),
    )
    monkeypatch.setattr(
        resource_service,
        "audit_service",
        SimpleNamespace(log_action=lambda **kw: None),
    )

    from app.services.network import ip_management_service, reverse_proxy_service

    monkeypatch.setattr(
        reverse_proxy_service,
        "remove_reverse_proxy_rules_for_vmid",
        lambda session, vmid: None,
    )
    monkeypatch.setattr(
        ip_management_service, "release_ip", lambda session, vmid: None
    )
    return {"session": FakeSession(), "user_id": uuid.uuid4()}


def _delete(fake_env: dict[str, Any], pve: FakeProxmox, **kwargs: Any) -> dict:
    return resource_service.delete(
        session=fake_env["session"],
        vmid=150,
        resource_info={
            "vmid": 150,
            "node": "pve1",
            "type": "qemu",
            "name": "vm-150",
            "status": kwargs.pop("snapshot_status", pve.status),
        },
        user_id=fake_env["user_id"],
        **kwargs,
    )


def test_running_resource_is_gracefully_shut_down_then_deleted(
    monkeypatch: pytest.MonkeyPatch, fake_env: dict[str, Any]
) -> None:
    pve = FakeProxmox(status="running", stops_on={"shutdown"})
    monkeypatch.setattr(resource_service, "proxmox_service", pve)

    _delete(fake_env, pve)

    assert pve.actions == ["shutdown"]
    assert pve.deleted == [150]


def test_falls_back_to_hard_stop_when_shutdown_hangs(
    monkeypatch: pytest.MonkeyPatch, fake_env: dict[str, Any]
) -> None:
    # e.g. no guest agent: shutdown is ignored, only hard stop works
    pve = FakeProxmox(status="running", stops_on={"stop"})
    monkeypatch.setattr(resource_service, "proxmox_service", pve)

    _delete(fake_env, pve)

    assert pve.actions == ["shutdown", "stop"]
    assert pve.deleted == [150]


def test_aborts_deletion_when_resource_never_stops(
    monkeypatch: pytest.MonkeyPatch, fake_env: dict[str, Any]
) -> None:
    pve = FakeProxmox(status="running", stops_on=set())
    monkeypatch.setattr(resource_service, "proxmox_service", pve)

    with pytest.raises(ProxmoxError, match="still running"):
        _delete(fake_env, pve)

    assert pve.deleted == []  # never delete a running resource


def test_force_skips_graceful_shutdown(
    monkeypatch: pytest.MonkeyPatch, fake_env: dict[str, Any]
) -> None:
    pve = FakeProxmox(status="running", stops_on={"stop"})
    monkeypatch.setattr(resource_service, "proxmox_service", pve)

    _delete(fake_env, pve, force=True)

    assert pve.actions == ["stop"]
    assert pve.deleted == [150]


def test_stopped_resource_gets_no_power_actions(
    monkeypatch: pytest.MonkeyPatch, fake_env: dict[str, Any]
) -> None:
    pve = FakeProxmox(status="stopped")
    monkeypatch.setattr(resource_service, "proxmox_service", pve)

    _delete(fake_env, pve)

    assert pve.actions == []
    assert pve.deleted == [150]


def test_stale_stopped_snapshot_still_shuts_down_running_resource(
    monkeypatch: pytest.MonkeyPatch, fake_env: dict[str, Any]
) -> None:
    # Snapshot taken at request time says "stopped", but the VM was started
    # before the queued deletion executed. Live status must win.
    pve = FakeProxmox(status="running", stops_on={"shutdown"})
    monkeypatch.setattr(resource_service, "proxmox_service", pve)

    _delete(fake_env, pve, snapshot_status="stopped")

    assert pve.actions == ["shutdown"]
    assert pve.deleted == [150]


def test_status_poll_errors_never_count_as_stopped(
    monkeypatch: pytest.MonkeyPatch, fake_env: dict[str, Any]
) -> None:
    # Old code broke out of the wait loop on any status exception and went on
    # to delete a possibly-running VM. Now poll errors keep the resource
    # treated as running and the deletion is aborted instead.
    pve = FakeProxmox(
        status="running",
        stops_on=set(),
        status_error=RuntimeError("pve flapping"),
    )
    monkeypatch.setattr(resource_service, "proxmox_service", pve)

    with pytest.raises(ProxmoxError, match="still running"):
        _delete(fake_env, pve, snapshot_status="running")

    assert pve.deleted == []
