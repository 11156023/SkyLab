"""範本克隆的 VMID 配發與「範本更新母機」刪除防護。

- 範本克隆（run_clone_task）與範本更新循環的暫存母機克隆（run_update_clone_task）
  都要跳過 DB 已預留、但 PVE nextid 還看不到的 VMID。
- 更新循環進行中的暫存母機不能從資源頁刪除，要從範本頁取消更新。
"""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import ConflictError
from app.services.proxmox import provisioning_service
from app.services.resource import deletion_service
from app.services.template import clone_service, template_service


class _Stop(Exception):
    """在 clone 呼叫時中斷任務，後續步驟與本測試無關。"""


class _FakeSession:
    def __init__(self, template: Any = None) -> None:
        self._template = template

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get(self, _model: Any, _key: Any) -> Any:
        return self._template

    def commit(self) -> None:
        return None


@pytest.fixture
def pve_nextid_reserved_in_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """PVE nextid 回 500，但 500 已被排程在 DB 預留（IP 配發紀錄）。"""
    monkeypatch.setattr(provisioning_service.proxmox_service, "next_vmid", lambda: 500)
    monkeypatch.setattr(
        provisioning_service.resource_repo,
        "get_allocated_ip_address",
        lambda *, session, vmid: "10.0.0.5" if vmid == 500 else None,
    )
    monkeypatch.setattr(
        provisioning_service.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: None,
    )
    monkeypatch.setattr(
        clone_service.proxmox_ops, "vmid_allocation_lock", lambda **_: nullcontext()
    )


def test_template_clone_skips_vmid_reserved_in_db(
    monkeypatch: pytest.MonkeyPatch, pve_nextid_reserved_in_db: None
) -> None:
    template = SimpleNamespace(
        status=clone_service.VMTemplateStatus.ready,
        pve_vmid=9000,
        name="ubuntu",
        node="pve1",
        resource_type="lxc",
        default_cores=1,
        default_memory=512,
        default_disk=8,
    )
    monkeypatch.setattr(clone_service, "Session", lambda _engine: _FakeSession(template))
    monkeypatch.setattr(clone_service, "report_progress", lambda *_a, **_k: None)
    allocated: list[int] = []
    monkeypatch.setattr(
        clone_service.ip_management_service,
        "get_network_config_for_vm",
        lambda session: {},
    )
    monkeypatch.setattr(
        clone_service.ip_management_service,
        "allocate_ip",
        lambda session, vmid, purpose, **_: allocated.append(vmid) or "10.0.0.9",
    )
    monkeypatch.setattr(
        clone_service.ip_management_service, "release_ip", lambda *_a, **_k: None
    )
    cloned: list[int] = []

    def fake_clone(**kwargs: Any) -> str:
        cloned.append(kwargs["new_vmid"])
        raise _Stop

    monkeypatch.setattr(clone_service, "clone_with_fallback", fake_clone)

    with pytest.raises(_Stop):
        clone_service.run_clone_task(
            uuid.uuid4(),
            {
                "template_id": str(uuid.uuid4()),
                "user_id": str(uuid.uuid4()),
                "hostname": "student-1",
            },
        )

    assert allocated == [501]
    assert cloned == [501]


def test_template_update_clone_skips_vmid_reserved_in_db(
    monkeypatch: pytest.MonkeyPatch, pve_nextid_reserved_in_db: None
) -> None:
    monkeypatch.setattr(template_service, "Session", lambda _engine: _FakeSession())
    monkeypatch.setattr(template_service, "report_progress", lambda *_a, **_k: None)
    monkeypatch.setattr(
        template_service,
        "get_proxmox_settings_for_node",
        lambda node: SimpleNamespace(pool_name="pool"),
    )
    monkeypatch.setattr(template_service, "_set_template_error", lambda *_a, **_k: None)
    cloned: list[int] = []

    def fake_clone_lxc(node: str, vmid: int, *, newid: int, **_: Any) -> None:
        cloned.append(newid)
        raise _Stop

    monkeypatch.setattr(template_service.proxmox_ops, "clone_lxc", fake_clone_lxc)

    with pytest.raises(_Stop):
        template_service.run_update_clone_task(
            uuid.uuid4(),
            {
                "template_id": str(uuid.uuid4()),
                "pve_vmid": 9000,
                "resource_type": "lxc",
                "node": "pve1",
            },
        )

    assert cloned == [501]


# ---------------------------------------------------------------------------
# 範本更新母機的刪除防護
# ---------------------------------------------------------------------------


def _admin() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), email="admin@example.com")


def _patch_delete_deps(
    monkeypatch: pytest.MonkeyPatch, *, resource: Any, template: Any
) -> list[int]:
    monkeypatch.setattr(
        deletion_service.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: resource,
    )
    monkeypatch.setattr(
        deletion_service, "can_bypass_resource_ownership", lambda user: True
    )
    monkeypatch.setattr(
        deletion_service.template_repo,
        "get_updating_template_by_source_vmid",
        lambda *, session, source_vmid: template,
    )
    looked_up: list[int] = []

    def fake_find_resource(vmid: int) -> dict[str, Any]:
        looked_up.append(vmid)
        raise _Stop

    monkeypatch.setattr(
        deletion_service.proxmox_service, "find_resource", fake_find_resource
    )
    return looked_up


def test_delete_refuses_temp_vm_of_running_template_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = SimpleNamespace(
        vmid=501, environment_type=template_service.UPDATE_TEMP_ENVIRONMENT_TYPE
    )
    looked_up = _patch_delete_deps(
        monkeypatch, resource=resource, template=SimpleNamespace(name="ubuntu")
    )

    with pytest.raises(ConflictError):
        deletion_service.request_deletion(session=None, user=_admin(), vmid=501)  # type: ignore[arg-type]

    assert looked_up == []


def test_delete_allows_temp_vm_once_update_cycle_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = SimpleNamespace(
        vmid=501, environment_type=template_service.UPDATE_TEMP_ENVIRONMENT_TYPE
    )
    looked_up = _patch_delete_deps(monkeypatch, resource=resource, template=None)

    with pytest.raises(_Stop):
        deletion_service.request_deletion(session=None, user=_admin(), vmid=501)  # type: ignore[arg-type]

    assert looked_up == [501]


def test_delete_ignores_template_lookup_for_regular_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = SimpleNamespace(vmid=501, environment_type="課程")
    looked_up = _patch_delete_deps(
        monkeypatch, resource=resource, template=SimpleNamespace(name="ubuntu")
    )

    with pytest.raises(_Stop):
        deletion_service.request_deletion(session=None, user=_admin(), vmid=501)  # type: ignore[arg-type]

    assert looked_up == [501]
