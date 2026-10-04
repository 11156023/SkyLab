"""快照守門測試：保留名、上限、init 保護、機器當下不支援快照。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import (
    BadRequestError,
    ConflictError,
    PermissionDeniedError,
    ProxmoxError,
)
from app.services.network import snapshot_service

INFO = {"node": "pve1", "type": "qemu"}
STUDENT = SimpleNamespace(id=uuid.uuid4(), email="s@campus.edu")


class _FakeSession:
    def add(self, obj) -> None:
        """測試替身：不需實作。"""

    def commit(self) -> None:
        """測試替身：不需實作。"""


@pytest.fixture()
def pve(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {
        "snapshots": [],
        "created": [],
        "deleted": [],
        "rolled_back": [],
        "supported": True,
    }

    def _has_feature(node, vmid, rtype):
        if isinstance(calls["supported"], Exception):
            raise calls["supported"]
        return calls["supported"]

    monkeypatch.setattr(
        snapshot_service.proxmox_service, "has_snapshot_feature", _has_feature
    )
    monkeypatch.setattr(
        snapshot_service.proxmox_service,
        "rollback_snapshot",
        lambda node, vmid, rtype, snapname: calls["rolled_back"].append(snapname)
        or "UPID:x",
    )
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
            user=STUDENT,
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
            user=STUDENT,
        )


def test_create_within_limit_ok(pve: dict) -> None:
    pve["snapshots"] = [{"name": "a"}, {"name": "skylab-init"}]
    result = snapshot_service.create_snapshot(
        session=_FakeSession(), vmid=101, snapname="b",
        description=None, vmstate=False, resource_info=INFO,
        user=STUDENT,
    )
    assert pve["created"] == ["b"]
    assert "task_id" in result


def test_delete_init_snapshot_forbidden(pve: dict) -> None:
    with pytest.raises(PermissionDeniedError):
        snapshot_service.delete_snapshot(
            session=_FakeSession(), vmid=101, snapname="skylab-init",
            resource_info=INFO, user=STUDENT,
        )


def test_admin_can_delete_init_snapshot(
    pve: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(snapshot_service, "_is_admin", lambda user: True)
    snapshot_service.delete_snapshot(
        session=_FakeSession(), vmid=101, snapname="skylab-init",
        resource_info=INFO, user=STUDENT,
    )
    assert pve["deleted"] == ["skylab-init"]


# ---------------------------------------------------------------------------
# 快照可用性：機器當下不能用快照時，查詢回不可用、所有寫入操作一律拒絕
# ---------------------------------------------------------------------------


def test_capability_available_when_pve_supports(pve: dict) -> None:
    assert snapshot_service.get_capability(vmid=101, resource_info=INFO) == {
        "available": True,
        "reason": None,
    }


@pytest.mark.parametrize("rtype", ["qemu", "lxc"])
def test_capability_unsupported_when_pve_lacks_feature(pve: dict, rtype: str) -> None:
    pve["supported"] = False
    info = {"node": "pve1", "type": rtype}
    assert snapshot_service.get_capability(vmid=101, resource_info=info) == {
        "available": False,
        "reason": "unsupported",
    }


def test_capability_unknown_when_check_fails(pve: dict) -> None:
    """PVE 查不到時 fail-closed：視為不可用，但查詢本身不拋例外。"""
    pve["supported"] = RuntimeError("PVE down")
    assert snapshot_service.get_capability(vmid=101, resource_info=INFO) == {
        "available": False,
        "reason": "unknown",
    }


def test_create_rejected_when_unsupported(pve: dict) -> None:
    pve["supported"] = False
    with pytest.raises(ConflictError):
        snapshot_service.create_snapshot(
            session=_FakeSession(), vmid=101, snapname="b",
            description=None, vmstate=False, resource_info=INFO,
            user=STUDENT,
        )
    assert pve["created"] == []


def test_delete_rejected_when_unsupported(pve: dict) -> None:
    pve["supported"] = False
    with pytest.raises(ConflictError):
        snapshot_service.delete_snapshot(
            session=_FakeSession(), vmid=101, snapname="a",
            resource_info=INFO, user=STUDENT,
        )
    assert pve["deleted"] == []


def test_rollback_rejected_when_unsupported(pve: dict) -> None:
    pve["supported"] = False
    with pytest.raises(ConflictError):
        snapshot_service.rollback_snapshot(
            session=_FakeSession(), vmid=101, snapname="a",
            resource_info=INFO, user_id=STUDENT.id,
        )
    assert pve["rolled_back"] == []


def test_write_rejected_with_502_when_check_fails(pve: dict) -> None:
    """可用性查不到時不把請求送到 PVE，也不讓 PVE 的原始例外漏出去。"""
    pve["supported"] = RuntimeError("storage 'data-nvme' does not exist")
    with pytest.raises(ProxmoxError) as excinfo:
        snapshot_service.create_snapshot(
            session=_FakeSession(), vmid=101, snapname="b",
            description=None, vmstate=False, resource_info=INFO,
            user=STUDENT,
        )
    assert excinfo.value.status_code == 502
    assert pve["created"] == []


def test_rollback_ok_when_supported(pve: dict) -> None:
    snapshot_service.rollback_snapshot(
        session=_FakeSession(), vmid=101, snapname="a",
        resource_info=INFO, user_id=STUDENT.id,
    )
    assert pve["rolled_back"] == ["a"]
