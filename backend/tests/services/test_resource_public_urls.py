"""「我的資源」清單與單筆資源要帶出反向代理的對外網址（``public_urls``）。

清單頁是一次批次查所有機器的反向代理規則（不逐台查），沒有規則的機器
回空陣列；DB 查詢失敗只讓網址空掉，不能讓整頁掛掉。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.resource import deletion_service, resource_service


def _fake_session() -> SimpleNamespace:
    return SimpleNamespace(
        exec=lambda stmt: SimpleNamespace(all=lambda: [], first=lambda: None),
        get=lambda model, key: None,
        rollback=lambda: None,
    )


def _db_resource(vmid: int) -> SimpleNamespace:
    return SimpleNamespace(
        vmid=vmid,
        request_id=None,
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
        user_id=uuid.uuid4(),
    )


def _rule(vmid: int, domain: str, *, port: int = 80, https: bool = True) -> Any:
    return SimpleNamespace(
        vmid=vmid, domain=domain, internal_port=port, enable_https=https
    )


def _patch_common(monkeypatch: pytest.MonkeyPatch, vmids: list[int]) -> None:
    rows = [_db_resource(vmid) for vmid in vmids]
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resources_by_user",
        lambda *, session, user_id: rows,
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
            {"vmid": vmid, "type": "lxc", "node": "pve1", "name": f"ct-{vmid}",
             "status": "running"}
            for vmid in vmids
        ],
    )
    monkeypatch.setattr(
        resource_service.proxmox_service,
        "get_ip_address",
        lambda node, vmid, vm_type: None,
    )
    monkeypatch.setattr(
        deletion_service, "list_active_for_vmids", lambda *, session, vmids: {}
    )


def test_list_by_user_attaches_public_urls_in_one_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch, [101, 102])
    calls: list[list[int]] = []

    def fake_list_rules_by_vmids(session: Any, vmids: list[int]) -> list[Any]:
        calls.append(list(vmids))
        return [
            _rule(101, "b.example.edu", port=8080, https=False),
            _rule(101, "a.example.edu", port=80),
        ]

    monkeypatch.setattr(
        resource_service.rp_repo, "list_rules_by_vmids", fake_list_rules_by_vmids
    )

    result = resource_service.list_by_user(
        session=_fake_session(), user_id=uuid.uuid4()
    )
    by_vmid = {r.vmid: r for r in result}

    # 兩台機器只查一次 DB，且 vmid 去重排序
    assert calls == [[101, 102]]
    # 同一台多個網址依 port 排序；enable_https 決定 scheme
    assert by_vmid[101].public_urls == [
        "https://a.example.edu",
        "http://b.example.edu",
    ]
    assert by_vmid[102].public_urls == []


def test_public_urls_query_failure_degrades_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch, [101])

    def boom(session: Any, vmids: list[int]) -> list[Any]:
        raise RuntimeError("db down")

    monkeypatch.setattr(resource_service.rp_repo, "list_rules_by_vmids", boom)

    result = resource_service.list_by_user(
        session=_fake_session(), user_id=uuid.uuid4()
    )

    assert len(result) == 1
    assert result[0].public_urls == []


def test_get_by_vmid_queries_this_machine_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: _db_resource(vmid),
    )
    monkeypatch.setattr(
        resource_service.resource_repo,
        "sync_ip_cache",
        lambda *, session, vmid, live_ip: live_ip,
    )
    monkeypatch.setattr(
        resource_service.proxmox_service,
        "get_ip_address",
        lambda node, vmid, vm_type: "10.0.0.5",
    )
    monkeypatch.setattr(
        resource_service.rp_repo,
        "list_rules_by_vmids",
        lambda session, vmids: [_rule(vmid, f"vm{vmid}.example.edu") for vmid in vmids],
    )

    public = resource_service.get_by_vmid(
        session=_fake_session(),
        vmid=105,
        resource_info={"vmid": 105, "type": "qemu", "node": "pve1", "name": "vm"},
    )

    assert public.public_urls == ["https://vm105.example.edu"]
