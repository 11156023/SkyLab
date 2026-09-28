"""刪機流程必須先把連結的申請單標成已消耗並 commit，之後才碰 Proxmox。

背景（2026-08-28 test-123 復活事件）：舊流程是 Proxmox 端先銷毀、最後才把
申請單標成 failed。排程器在中間那個 tick 撈到仍是 active 的申請單、發現
機器不見，走 stale-VMID 回復路徑把同名機器重新 clone 出來。

- 標記（add + commit）必須發生在任何 Proxmox 呼叫之前
- Proxmox 端刪除失敗時，標記必須還原，申請單回到可排程狀態
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import ProxmoxError
from app.models import VMProvisioningStatus, VMRequest, VMRequestStatus
from app.services.resource import resource_service


class RecordingSession:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def add(self, obj: Any) -> None:
        self.events.append(("add", obj))

    def commit(self) -> None:
        self.events.append(("commit", None))

    def rollback(self) -> None:
        self.events.append(("rollback", None))


class RecordingProxmox:
    def __init__(
        self,
        *,
        events: list[tuple[str, Any]],
        status: str = "stopped",
        fail_delete: bool = False,
    ) -> None:
        self.events = events
        self.status = status
        self.fail_delete = fail_delete

    def get_status(self, node: str, vmid: int, resource_type: str) -> dict:
        self.events.append(("pve.get_status", vmid))
        return {"status": self.status}

    def control(self, node: str, vmid: int, resource_type: str, action: str) -> None:
        self.events.append((f"pve.{action}", vmid))
        self.status = "stopped"

    def delete_resource(
        self, node: str, vmid: int, resource_type: str, **params: Any
    ) -> None:
        self.events.append(("pve.delete", vmid))
        if self.fail_delete:
            raise ProxmoxError("destroy failed")


def _linked_request() -> VMRequest:
    return VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        hostname="test-123",
        resource_type="vm",
        status=VMRequestStatus.approved,
        vmid=480,
        cores=4,
        memory=4096,
        provisioning_status=VMProvisioningStatus.completed,
        provisioning_error=None,
        resource_warning=None,
        review_comment="ok by reviewer",
    )


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    session = RecordingSession()
    request = _linked_request()

    monkeypatch.setattr(
        resource_service,
        "resource_repo",
        SimpleNamespace(
            get_resource_by_vmid=lambda **kw: None,
            delete_resource=lambda **kw: None,
        ),
    )
    monkeypatch.setattr(
        resource_service,
        "vm_request_repo",
        SimpleNamespace(get_latest_approved_vm_request_by_vmid=lambda **kw: request),
    )
    monkeypatch.setattr(
        resource_service,
        "batch_provision_repo",
        SimpleNamespace(clear_task_vmid_references=lambda **kw: 0),
    )
    monkeypatch.setattr(
        resource_service,
        "audit_service",
        SimpleNamespace(log_action=lambda **kw: session.events.append(("audit", None))),
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
    return {"session": session, "request": request}


def _delete(env: dict[str, Any], pve: RecordingProxmox, **kwargs: Any) -> dict:
    return resource_service.delete(
        session=env["session"],
        vmid=480,
        resource_info={
            "vmid": 480,
            "node": "pve205",
            "type": "qemu",
            "name": "test-123",
            "status": pve.status,
        },
        user_id=uuid.uuid4(),
        **kwargs,
    )


def _first_index(events: list[tuple[str, Any]], prefix: str) -> int:
    return next(i for i, (name, _) in enumerate(events) if name.startswith(prefix))


def test_request_is_marked_consumed_and_committed_before_any_proxmox_call(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, Any]
) -> None:
    session: RecordingSession = env["session"]
    pve = RecordingProxmox(events=session.events, status="running")
    monkeypatch.setattr(resource_service, "proxmox_service", pve)
    monkeypatch.setattr(
        resource_service,
        "_ensure_stopped_before_delete",
        lambda node, vmid, rtype, *, force: session.events.append(("pve.shutdown", vmid)),
    )

    _delete(env, pve)

    request: VMRequest = env["request"]
    assert request.provisioning_status == VMProvisioningStatus.failed
    assert request.provisioning_error == resource_service.RESOURCE_DELETED_BY_USER_MARKER
    assert request.resource_warning == resource_service.RESOURCE_DELETED_BY_USER_MARKER
    assert request.review_comment == resource_service.RESOURCE_DELETED_BY_USER_MARKER

    first_pve = _first_index(session.events, "pve.")
    first_commit = _first_index(session.events, "commit")
    marker_add = next(
        i for i, (name, obj) in enumerate(session.events)
        if name == "add" and obj is request
    )
    # 先 add 申請單、commit，之後才有任何 Proxmox 呼叫（含關機與銷毀）
    assert marker_add < first_commit < first_pve
    assert ("pve.delete", 480) in session.events


def test_marker_is_restored_when_proxmox_destroy_fails(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, Any]
) -> None:
    session: RecordingSession = env["session"]
    pve = RecordingProxmox(events=session.events, status="stopped", fail_delete=True)
    monkeypatch.setattr(resource_service, "proxmox_service", pve)

    with pytest.raises(ProxmoxError):
        _delete(env, pve)

    request: VMRequest = env["request"]
    # 機器還活著：申請單回到刪除前的狀態，排程器與列表都照常對待它
    assert request.provisioning_status == VMProvisioningStatus.completed
    assert request.provisioning_error is None
    assert request.resource_warning is None
    assert request.review_comment == "ok by reviewer"
    # 還原後有 commit 落地
    delete_idx = _first_index(session.events, "pve.delete")
    assert any(
        name == "commit" for name, _ in session.events[delete_idx + 1 :]
    )
    assert ("audit", None) not in session.events


def test_marker_is_restored_when_shutdown_aborts_deletion(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, Any]
) -> None:
    session: RecordingSession = env["session"]
    pve = RecordingProxmox(events=session.events, status="running")
    monkeypatch.setattr(resource_service, "proxmox_service", pve)

    def _never_stops(node: str, vmid: int, rtype: str, *, force: bool) -> None:
        raise ProxmoxError("still running")

    monkeypatch.setattr(
        resource_service, "_ensure_stopped_before_delete", _never_stops
    )

    with pytest.raises(ProxmoxError):
        _delete(env, pve)

    request: VMRequest = env["request"]
    assert request.provisioning_status == VMProvisioningStatus.completed
    assert request.review_comment == "ok by reviewer"
    assert ("pve.delete", 480) not in session.events


def test_delete_without_linked_request_still_succeeds(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, Any]
) -> None:
    session: RecordingSession = env["session"]
    monkeypatch.setattr(
        resource_service,
        "vm_request_repo",
        SimpleNamespace(get_latest_approved_vm_request_by_vmid=lambda **kw: None),
    )
    pve = RecordingProxmox(events=session.events, status="stopped")
    monkeypatch.setattr(resource_service, "proxmox_service", pve)

    result = _delete(env, pve)

    assert "deleted successfully" in result["message"]
    assert ("pve.delete", 480) in session.events
