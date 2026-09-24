"""機器轉範本後，連結的 VMRequest 必須標為已消耗（regression）。

用申請單開出來的機器被轉成範本後，原本只刪 Resource 紀錄、不動
VMRequest：排程器每個 tick 會嘗試啟動已是範本的 VMID（PVE 拒絕、
反覆失敗），資源頁也會把 approved 申請單復活成「建立失敗」placeholder。

新行為：
- ``run_convert_task`` 收尾時呼叫 ``mark_linked_request_consumed``，
  以 ``RESOURCE_CONVERTED_TO_TEMPLATE_MARKER`` 標記申請單；
- 標記後 provisioning_status=failed（排程器不再接管）、
  resource_warning 帶 marker（資源頁不復活 placeholder）。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.domain.resource_markers import RESOURCE_CONVERTED_TO_TEMPLATE_MARKER
from app.models import Resource, VMTemplate, VMTemplateStatus
from app.models.vm_request import VMProvisioningStatus
from app.services.resource import resource_service
from app.services.template import template_service

# ---------------------------------------------------------------------------
# mark_linked_request_consumed
# ---------------------------------------------------------------------------


def test_mark_linked_request_consumed_sets_failed_and_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SimpleNamespace(
        provisioning_status=VMProvisioningStatus.completed,
        provisioning_error=None,
        resource_warning=None,
        review_comment=None,
    )
    added: list[Any] = []
    session = SimpleNamespace(add=added.append)
    monkeypatch.setattr(
        resource_service.vm_request_repo,
        "get_latest_approved_vm_request_by_vmid",
        lambda *, session, vmid: request if vmid == 102 else None,
    )

    resource_service.mark_linked_request_consumed(
        session=session,
        vmid=102,
        marker=RESOURCE_CONVERTED_TO_TEMPLATE_MARKER,
    )

    marker = RESOURCE_CONVERTED_TO_TEMPLATE_MARKER
    assert request.provisioning_status == VMProvisioningStatus.failed
    assert request.provisioning_error == marker
    assert request.resource_warning == marker
    assert request.review_comment == marker
    assert added == [request]


def test_mark_linked_request_consumed_without_linked_request_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    added: list[Any] = []
    session = SimpleNamespace(add=added.append)
    monkeypatch.setattr(
        resource_service.vm_request_repo,
        "get_latest_approved_vm_request_by_vmid",
        lambda *, session, vmid: None,
    )

    resource_service.mark_linked_request_consumed(
        session=session,
        vmid=999,
        marker=resource_service.RESOURCE_DELETED_BY_USER_MARKER,
    )

    assert added == []


def test_converted_marker_suppresses_placeholder_resurrection() -> None:
    # list_by_user 靠這個集合過濾已消耗的 approved 申請單
    assert (
        RESOURCE_CONVERTED_TO_TEMPLATE_MARKER
        in resource_service._RESOURCE_DELETED_MARKERS
    )


# ---------------------------------------------------------------------------
# run_convert_task 收尾
# ---------------------------------------------------------------------------


class FakeSession:
    """收集 get/delete/add 呼叫的假 DB session（context manager）。"""

    def __init__(self, objects: dict[tuple[type, Any], Any]) -> None:
        self.objects = objects
        self.deleted: list[Any] = []
        self.committed = False

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def get(self, model: type, key: Any) -> Any:
        return self.objects.get((model, key))

    def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    def add(self, obj: Any) -> None:
        pass

    def commit(self) -> None:
        self.committed = True


def test_run_convert_task_marks_linked_request_consumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template_id = uuid.uuid4()
    template = SimpleNamespace(
        status=VMTemplateStatus.creating, error_message="old"
    )
    resource = SimpleNamespace(vmid=102)
    fake_session = FakeSession(
        {(VMTemplate, template_id): template, (Resource, 102): resource}
    )
    monkeypatch.setattr(
        template_service, "Session", lambda engine: fake_session
    )
    monkeypatch.setattr(template_service, "report_progress", lambda *a: None)
    monkeypatch.setattr(
        template_service.template_repo, "touch", lambda **kw: None
    )
    monkeypatch.setattr(
        template_service.proxmox_ops,
        "get_status",
        lambda node, vmid, rtype: {"status": "stopped"},
    )
    monkeypatch.setattr(
        template_service.proxmox_ops,
        "list_snapshots",
        lambda node, vmid, rtype: [],
    )
    monkeypatch.setattr(
        template_service.proxmox_ops,
        "convert_to_template",
        lambda node, vmid, rtype: None,
    )

    consumed: list[dict[str, Any]] = []
    monkeypatch.setattr(
        resource_service,
        "mark_linked_request_consumed",
        lambda **kw: consumed.append(kw),
    )

    result = template_service.run_convert_task(
        uuid.uuid4(),
        {
            "template_id": str(template_id),
            "pve_vmid": 102,
            "resource_type": "lxc",
            "node": "pve1",
        },
    )

    assert result == {"vmid": 102, "cloud_init_reset": False}
    assert template.status == VMTemplateStatus.ready
    assert fake_session.deleted == [resource]
    assert fake_session.committed
    assert len(consumed) == 1
    assert consumed[0]["vmid"] == 102
    assert (
        consumed[0]["marker"]
        == RESOURCE_CONVERTED_TO_TEMPLATE_MARKER
    )
