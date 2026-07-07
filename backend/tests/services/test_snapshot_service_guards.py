"""快照守門測試：保留名、上限、init 保護。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError, ConflictError, PermissionDeniedError
from app.services.network import snapshot_service

INFO = {"node": "pve1", "type": "qemu"}
STUDENT = SimpleNamespace(id=uuid.uuid4(), email="s@campus.edu")


class _FakeSession:
    def add(self, obj) -> None: ...
    def commit(self) -> None: ...


@pytest.fixture()
def pve(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {"snapshots": [], "created": [], "deleted": []}
    monkeypatch.setattr(
        snapshot_service.proxmox_service,
        "list_snapshots",
        lambda node, vmid, rtype: calls["snapshots"],
    )
    monkeypatch.setattr(
        snapshot_service.proxmox_service,
        "create_snapshot",
        lambda node, vmid, rtype, **p: calls["created"].append(p.get("snapname"))
        or "UPID:x",
    )
    monkeypatch.setattr(
        snapshot_service.proxmox_service,
        "delete_snapshot",
        lambda node, vmid, rtype, snapname: calls["deleted"].append(snapname)
        or "UPID:x",
    )
    monkeypatch.setattr(
        snapshot_service.audit_service, "log_action", lambda **kwargs: None
    )
    monkeypatch.setattr(snapshot_service, "_is_admin", lambda user: False)
    monkeypatch.setattr(
        snapshot_service,
        "_snapshot_max_count",
        lambda session: 3,
    )
    return calls


def test_create_reserved_name_rejected(pve: dict) -> None:
    with pytest.raises(BadRequestError):
        snapshot_service.create_snapshot(
            session=_FakeSession(), vmid=101, snapname="skylab-init",
            description=None, vmstate=False, resource_info=INFO,
            user_id=STUDENT.id, user=STUDENT,
        )


def test_create_over_limit_conflicts(pve: dict) -> None:
    pve["snapshots"] = [
        {"name": "a"}, {"name": "b"}, {"name": "c"},
        {"name": "skylab-init"}, {"name": "current"},
    ]
    with pytest.raises(ConflictError):
        snapshot_service.create_snapshot(
            session=_FakeSession(), vmid=101, snapname="d",
            description=None, vmstate=False, resource_info=INFO,
            user_id=STUDENT.id, user=STUDENT,
        )


def test_create_within_limit_ok(pve: dict) -> None:
    pve["snapshots"] = [{"name": "a"}, {"name": "skylab-init"}]
    result = snapshot_service.create_snapshot(
        session=_FakeSession(), vmid=101, snapname="b",
        description=None, vmstate=False, resource_info=INFO,
        user_id=STUDENT.id, user=STUDENT,
    )
    assert pve["created"] == ["b"]
    assert "task_id" in result


def test_delete_init_snapshot_forbidden(pve: dict) -> None:
    with pytest.raises(PermissionDeniedError):
        snapshot_service.delete_snapshot(
            session=_FakeSession(), vmid=101, snapname="skylab-init",
            resource_info=INFO, user_id=STUDENT.id, user=STUDENT,
        )


def test_admin_can_delete_init_snapshot(
    pve: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(snapshot_service, "_is_admin", lambda user: True)
    snapshot_service.delete_snapshot(
        session=_FakeSession(), vmid=101, snapname="skylab-init",
        resource_info=INFO, user_id=STUDENT.id, user=STUDENT,
    )
    assert pve["deleted"] == ["skylab-init"]
