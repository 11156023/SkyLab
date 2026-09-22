"""舊班級機器（供裝時沒有 os_info）要能關聯回老師拓撲的顯示名（regression）。

commit 87bd0880 之後新供裝的機器會把老師在拓撲取的機器名寫進
``Resource.os_info``；之前供裝的機器 DB 只有 os_info=None，清單只能
退回 cls-xxx-2-1 這種主機名。這裡驗證：

1. ``_teaching_display_names`` 沿 batch_job_id / job.node_key /
   範本外鍵把名字查回來，查不到就留空。
2. ``list_by_user`` 的組裝會把補名套到 os_info 上。
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import (
    BatchProvisionJob,
    TeachingClassMachineNode,
    VMTemplate,
)
from app.services.resource import resource_service


class _Result:
    def __init__(self, value):
        self.value = value

    def all(self):
        return self.value


class _Session:
    """按 select 目標的 model entity 分發假查詢。"""

    def __init__(self, *, nodes=None, jobs=None, templates=None):
        self._rows = {
            TeachingClassMachineNode: nodes or [],
            BatchProvisionJob: jobs or [],
            VMTemplate: templates or [],
        }

    def exec(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        return _Result(self._rows.get(entity, []))


def _resource(vmid, class_id, *, job_id=None, template_id=None, os_info=None):
    return SimpleNamespace(
        vmid=vmid,
        os_info=os_info,
        teaching_class_id=class_id,
        batch_job_id=job_id,
        template_id=template_id,
    )


def _node(class_id, name, *, job_id=None, node_key="n1", template_id=None):
    return SimpleNamespace(
        class_id=class_id,
        node_key=node_key,
        batch_job_id=job_id,
        name=name,
        source_template_id=template_id,
    )


def test_direct_batch_job_join_returns_teacher_node_name():
    class_id = uuid.uuid4()
    job_id = uuid.uuid4()

    display = resource_service._teaching_display_names(
        session=_Session(
            nodes=[_node(class_id, "n8n", job_id=job_id, node_key="node-a")],
        ),
        db_resources=[_resource(701, class_id, job_id=job_id)],
    )

    assert display == {701: "n8n"}


def test_retry_job_falls_back_to_node_key_from_job_params():
    """節點重新佈建後 batch_job_id 指向新 job；舊 job 用 node_key 反查。"""
    class_id = uuid.uuid4()
    old_job_id = uuid.uuid4()
    new_job_id = uuid.uuid4()
    node_key = "node-b"

    display = resource_service._teaching_display_names(
        session=_Session(
            nodes=[_node(class_id, "kali", job_id=new_job_id, node_key=node_key)],
            jobs=[
                SimpleNamespace(
                    id=old_job_id,
                    template_params=json.dumps(
                        {
                            "ip_reservation_prefix": f"{class_id}:{node_key}",
                        }
                    ),
                )
            ],
        ),
        db_resources=[_resource(702, class_id, job_id=old_job_id)],
    )

    assert display == {702: "kali"}


def test_template_name_fallback_for_qemu_clone():
    class_id = uuid.uuid4()

    display = resource_service._teaching_display_names(
        session=_Session(
            templates=[
                SimpleNamespace(
                    id=uuid.uuid4(), name="Ubuntu 24.04 範本", pve_vmid=105
                )
            ],
        ),
        db_resources=[_resource(703, class_id, template_id=105)],
    )

    assert display == {703: "Ubuntu 24.04 範本"}


def test_node_without_name_uses_source_template_name():
    class_id = uuid.uuid4()
    job_id = uuid.uuid4()
    template_id = uuid.uuid4()

    display = resource_service._teaching_display_names(
        session=_Session(
            nodes=[
                _node(class_id, "", job_id=job_id, template_id=template_id)
            ],
            templates=[
                SimpleNamespace(id=template_id, name="n8n 範本", pve_vmid=110)
            ],
        ),
        db_resources=[_resource(704, class_id, job_id=job_id)],
    )

    assert display == {704: "n8n 範本"}


def test_new_machine_with_os_info_is_untouched():
    class_id = uuid.uuid4()
    job_id = uuid.uuid4()

    display = resource_service._teaching_display_names(
        session=_Session(
            nodes=[_node(class_id, "n8n", job_id=job_id)],
        ),
        db_resources=[_resource(705, class_id, job_id=job_id, os_info="n8n")],
    )

    assert display == {}


def test_unresolvable_machine_stays_empty():
    class_id = uuid.uuid4()

    display = resource_service._teaching_display_names(
        session=_Session(),
        db_resources=[_resource(706, class_id)],
    )

    assert display == {}


def test_personal_machine_without_class_is_ignored():
    display = resource_service._teaching_display_names(
        session=_Session(),
        db_resources=[_resource(707, None, job_id=uuid.uuid4())],
    )

    assert display == {}


def _fake_session() -> SimpleNamespace:
    return SimpleNamespace(
        exec=lambda stmt: SimpleNamespace(all=lambda: [], first=lambda: None),
        get=lambda model, key: None,
        rollback=lambda: None,
    )


def _patch_list_by_user_common(monkeypatch: pytest.MonkeyPatch, *, vmid: int) -> None:
    class_id = uuid.uuid4()
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
        teaching_class_id=class_id,
        allocation_scope="teaching_class",
        control_policy="class_member",
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
                "name": "cls-704664cd-2-1",
                "status": "running",
            }
        ],
    )
    monkeypatch.setattr(
        resource_service.proxmox_service,
        "get_ip_address",
        lambda node, vmid, vm_type: None,
    )
    return class_id


def test_list_by_user_wires_display_name_into_os_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vmid = 708
    class_id = _patch_list_by_user_common(monkeypatch, vmid=vmid)
    resolved: dict[str, Any] = {}

    def fake_display_names(*, session: Any, db_resources: Any) -> dict[int, str]:
        rows = list(db_resources)
        resolved["classes"] = [r.teaching_class_id for r in rows]
        return {vmid: "n8n"}

    monkeypatch.setattr(
        resource_service, "_teaching_display_names", fake_display_names
    )

    result = resource_service.list_by_user(
        session=_fake_session(), user_id=uuid.uuid4()
    )

    assert resolved["classes"] == [class_id]
    assert result[0].os_info == "n8n"
    assert result[0].name == "cls-704664cd-2-1"


def test_list_by_user_offline_fallback_keeps_display_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vmid = 709
    _patch_list_by_user_common(monkeypatch, vmid=vmid)

    def raise_proxmox() -> list[dict]:
        raise RuntimeError("proxmox down")

    monkeypatch.setattr(
        resource_service.proxmox_service, "list_all_resources", raise_proxmox
    )
    monkeypatch.setattr(
        resource_service,
        "_teaching_display_names",
        lambda *, session, db_resources: {vmid: "n8n"},
    )

    result = resource_service.list_by_user(
        session=_fake_session(), user_id=uuid.uuid4()
    )

    assert result[0].os_info == "n8n"
    assert result[0].status == "unknown"
