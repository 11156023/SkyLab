"""一鍵重置編排測試（mock PVE）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError, ConflictError
from app.services.resource import reset_service

USER = SimpleNamespace(id=uuid.uuid4(), email="t@campus.edu")
INFO = {"node": "pve1", "type": "qemu"}


class _FakeSession:
    def add(self, obj) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


@pytest.fixture(autouse=True)
def no_audit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(reset_service, "_audit_reset", lambda *a, **k: None)


@pytest.fixture()
def pve(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {"snapshots": [], "control": [], "rollback": [], "status": "running"}
    monkeypatch.setattr(
        reset_service.proxmox_service,
        "list_snapshots",
        lambda node, vmid, rtype: calls["snapshots"],
    )
    monkeypatch.setattr(
        reset_service.proxmox_service,
        "create_snapshot",
        lambda node, vmid, rtype, wait_timeout_seconds=None, **p: calls.setdefault(
            "created", []
        ).append(p.get("snapname")),
    )
    monkeypatch.setattr(
        reset_service.proxmox_service,
        "get_status",
        lambda node, vmid, rtype: {"status": calls["status"]},
    )
    monkeypatch.setattr(
        reset_service.proxmox_service,
        "control",
        lambda node, vmid, rtype, action: calls["control"].append(action),
    )
    monkeypatch.setattr(
        reset_service.proxmox_service,
        "rollback_snapshot",
        lambda node, vmid, rtype, snapname: calls["rollback"].append(snapname),
    )
    monkeypatch.setattr(
        reset_service.proxmox_service,
        "find_resource",
        lambda vmid: {"vmid": vmid, "node": "pve1", "type": "qemu"},
    )
    monkeypatch.setattr(
        reset_service.audit_service, "log_action", lambda **kwargs: None
    )
    return calls


def test_start_reset_requires_init_snapshot(pve: dict) -> None:
    pve["snapshots"] = [{"name": "current"}]
    with pytest.raises(BadRequestError):
        reset_service.start_reset(
            _FakeSession(), vmid=101, resource_info=INFO, user=USER
        )


def test_run_reset_stops_rolls_back_and_restarts(pve: dict) -> None:
    pve["status"] = "running"
    reset_service._run_reset(101, "pve1", "qemu", USER.id)
    assert pve["control"] == ["stop", "start"]
    assert pve["rollback"] == [reset_service.INIT_SNAPSHOT_NAME]


def test_run_reset_stopped_vm_stays_stopped(pve: dict) -> None:
    pve["status"] = "stopped"
    reset_service._run_reset(101, "pve1", "qemu", USER.id)
    assert pve["control"] == []
    assert pve["rollback"] == [reset_service.INIT_SNAPSHOT_NAME]


def test_create_init_snapshot_conflicts_when_exists(pve: dict) -> None:
    pve["snapshots"] = [{"name": reset_service.INIT_SNAPSHOT_NAME}]
    with pytest.raises(ConflictError):
        reset_service.create_init_snapshot(
            _FakeSession(), vmid=101, resource_info=INFO, user=USER
        )


def test_ensure_init_snapshot_swallow_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(vmid):
        raise RuntimeError("PVE down")

    monkeypatch.setattr(reset_service.proxmox_service, "find_resource", _boom)
    assert reset_service.ensure_init_snapshot(101) is False
