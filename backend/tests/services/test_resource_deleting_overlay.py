"""刪除進行中的資源在「我的資源」必須顯示「刪除中」（regression）。

刪除走 202 + 背景 DeletionRequest（graceful shutdown → 等待 stopped →
destroy），期間 VM 仍存在於 Proxmox 且狀態為 stopped。修正前
``list_by_user`` 直接回傳 live 狀態，卡片會在輪詢後以「已關機」復活，
使用者以為刪除失敗。

新行為：``list_by_user`` 以 ``deletion_service.list_active_for_vmids``
疊加 overlay — 有 pending/running DeletionRequest 的 vmid 一律回
``status="deleting"``、``can_control=False``。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.resource import deletion_service, resource_service


def _fake_session() -> SimpleNamespace:
    """step 2 的 pending_requests 查詢回空集合即可。"""
    return SimpleNamespace(
        exec=lambda stmt: SimpleNamespace(all=lambda: [], first=lambda: None),
        get=lambda model, key: None,
        rollback=lambda: None,
    )


def _patch_common(monkeypatch: pytest.MonkeyPatch, *, vmid: int) -> None:
    db_resource = SimpleNamespace(
        vmid=vmid,
        request_id=None,
        user_id=uuid.uuid4(),
        environment_type=None,
        os_info=None,
        guest_os=None,
        expiry_date=None,
        template_id=None,
        batch_job_id=None,
        ssh_public_key=None,
        login_password_encrypted=None,
        idle_since=None,
        auto_stop_at=None,
        auto_stop_reason=None,
        scheduled_deletion_at=None,
        mining_exempt=False,
        teaching_class_id=None,
        allocation_scope="personal",
        control_policy="owner",
    )
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resources_by_user",
        lambda *, session, user_id: [db_resource],
    )
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_cached_ip_address",
        lambda *, session, vmid: None,
    )
    monkeypatch.setattr(
        resource_service.proxmox_service,
        "list_all_resources",
        lambda: [
            {
                "vmid": vmid,
                "type": "lxc",
                "node": "pve1",
                "name": "test-ct",
                "status": "stopped",
            }
        ],
    )
    monkeypatch.setattr(
        resource_service.proxmox_service,
        "get_ip_address",
        lambda node, vmid, vm_type: None,
    )


def test_active_deletion_overlays_status_as_deleting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch, vmid=102)
    monkeypatch.setattr(
        deletion_service,
        "list_active_for_vmids",
        lambda *, session, vmids: {102: SimpleNamespace(vmid=102)},
    )

    result = resource_service.list_by_user(
        session=_fake_session(), user_id=uuid.uuid4()
    )

    assert len(result) == 1
    assert result[0].status == "deleting"
    assert result[0].can_control is False


def test_no_active_deletion_keeps_live_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch, vmid=102)
    calls: list[list[int]] = []

    def fake_list_active(*, session: Any, vmids: list[int]) -> dict[int, Any]:
        calls.append(vmids)
        return {}

    monkeypatch.setattr(
        deletion_service, "list_active_for_vmids", fake_list_active
    )

    result = resource_service.list_by_user(
        session=_fake_session(), user_id=uuid.uuid4()
    )

    assert len(result) == 1
    assert result[0].status == "stopped"
    assert result[0].can_control is True
    assert calls == [[102]]
