"""資源清單頁（/resources/my、管理員 /resources）不能逐台串行查 PVE 與 DB。

回歸背景：每台機器依序做 guest agent 查 IP（關機的也查）、get_resource_by_vmid、
IP 快取讀寫、最新核准申請單、來源申請單、班級，50 台約 350 次 DB 查詢加 50 次
串行 PVE 呼叫；頁面還每 30 秒（開機中每 5 秒）重抓一次。
"""

from __future__ import annotations

import threading
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.resource import deletion_service, resource_service


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


def _session() -> SimpleNamespace:
    return SimpleNamespace(
        exec=lambda stmt: SimpleNamespace(all=lambda: [], first=lambda: None),
        get=lambda model, key: None,
        rollback=lambda: None,
    )


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, list[Any]] = {"ip": [], "by_vmids": [], "windows": []}

    def install(pve: list[dict[str, Any]]) -> dict[str, list[Any]]:
        rows = {entry["vmid"]: _db_resource(entry["vmid"]) for entry in pve}
        monkeypatch.setattr(
            resource_service.proxmox_service, "list_all_resources", lambda: pve
        )

        def get_ip(node: str, vmid: int, vm_type: str) -> str:
            calls["ip"].append(vmid)
            return f"10.0.2.{vmid % 250}"

        monkeypatch.setattr(resource_service.proxmox_service, "get_ip_address", get_ip)

        def by_vmids(*, session: Any, vmids: Any) -> dict[int, Any]:
            calls["by_vmids"].append(sorted(vmids))
            return {vmid: rows[vmid] for vmid in vmids if vmid in rows}

        monkeypatch.setattr(
            resource_service.resource_repo, "get_resources_by_vmids", by_vmids
        )

        def get_one(*, session: Any, vmid: int) -> Any:
            raise AssertionError("清單頁不應逐台 get_resource_by_vmid")

        monkeypatch.setattr(
            resource_service.resource_repo, "get_resource_by_vmid", get_one
        )
        monkeypatch.setattr(
            resource_service.resource_repo,
            "sync_ip_cache_many",
            lambda *, session, live_ips: {
                vmid: ip or f"cached-{vmid}" for vmid, ip in live_ips.items()
            },
        )

        def windows(*, session: Any, vmids: Any) -> dict[int, Any]:
            calls["windows"].append(sorted(vmids))
            return {}

        monkeypatch.setattr(
            resource_service.vm_request_repo,
            "get_latest_approved_vm_requests_by_vmids",
            windows,
        )

        def one_window(*, session: Any, vmid: int) -> Any:
            raise AssertionError("清單頁不應逐台查最新核准申請單")

        monkeypatch.setattr(
            resource_service.vm_request_repo,
            "get_latest_approved_vm_request_by_vmid",
            one_window,
        )
        monkeypatch.setattr(
            resource_service.rp_repo, "list_rules_by_vmids", lambda session, vmids: []
        )
        monkeypatch.setattr(
            resource_service.resource_kind, "practice_request_ids", lambda session: set()
        )
        monkeypatch.setattr(
            resource_service.resource_kind,
            "user_display_names",
            lambda session, ids: {},
        )
        monkeypatch.setattr(
            deletion_service, "list_active_for_vmids", lambda *, session, vmids: {}
        )
        return calls

    return install


def _vm(vmid: int, status: str = "running") -> dict[str, Any]:
    return {"vmid": vmid, "type": "qemu", "node": "pve1", "name": f"vm{vmid}", "status": status}


def test_admin_list_batches_db_lookups(env) -> None:
    vmids = list(range(100, 150))
    calls = env([_vm(v) for v in vmids])

    result = resource_service.list_all(session=_session())  # type: ignore[arg-type]

    assert [r.vmid for r in result] == vmids
    assert calls["by_vmids"] == [vmids]
    assert calls["windows"] == [vmids]
    assert sorted(calls["ip"]) == vmids


def test_stopped_vms_skip_the_live_ip_query(env) -> None:
    calls = env([_vm(100), _vm(101, status="stopped")])

    result = resource_service.list_all(session=_session())  # type: ignore[arg-type]

    assert calls["ip"] == [100]
    ips = {r.vmid: r.ip_address for r in result}
    assert ips == {100: "10.0.2.100", 101: "cached-101"}


def test_live_ip_queries_run_in_parallel(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env([_vm(100), _vm(101)])
    barrier = threading.Barrier(2, timeout=5)

    def get_ip(node: str, vmid: int, vm_type: str) -> str:
        barrier.wait()  # 串行執行時第一台會卡在這裡逾時
        return f"10.0.2.{vmid}"

    monkeypatch.setattr(resource_service.proxmox_service, "get_ip_address", get_ip)

    result = resource_service.list_all(session=_session())  # type: ignore[arg-type]

    assert {r.ip_address for r in result} == {"10.0.2.100", "10.0.2.101"}
