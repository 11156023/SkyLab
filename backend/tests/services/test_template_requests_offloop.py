"""範本建立／重試／刪除／更新循環／克隆的同步校驗不可在 event loop 上執行。

這些 async 入口在入列前會同步查 DB 與 PVE（find_resource、GPU mapping、
配額）；PVE 一慢就會凍住整個 worker 的 event loop（VNC、終端機、教室 WS
一起卡住）。這裡記錄同步步驟執行時的 thread，確認不在跑測試的 event loop
thread 上，且入列本身仍在 loop 上。
"""

from __future__ import annotations

import threading
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import VMTemplate, VMTemplateStatus
from app.schemas.template import TemplateCloneRequest, VMTemplateCreate
from app.services.template import clone_service, template_service


def _user(role: str = "admin") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_superuser=role == "admin")


def _template(status: VMTemplateStatus = VMTemplateStatus.ready) -> VMTemplate:
    return VMTemplate(
        id=uuid.uuid4(),
        pve_vmid=9001,
        name="lab-vm",
        owner_id=None,
        node="pve1",
        resource_type="qemu",
        status=status,
    )


@pytest.fixture
def threads(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    log: dict[str, int] = {}

    async def fake_enqueue(**kwargs: Any) -> Any:
        log["enqueue"] = threading.get_ident()
        return SimpleNamespace(id=uuid.uuid4(), payload=kwargs.get("payload"))

    monkeypatch.setattr(template_service, "enqueue_task", fake_enqueue)
    monkeypatch.setattr(clone_service, "enqueue_task", fake_enqueue)
    return log


async def test_create_template_checks_pve_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, threads: dict[str, int]
) -> None:
    loop_thread = threading.get_ident()

    def fake_find_resource(vmid: int) -> dict[str, Any]:
        threads["find_resource"] = threading.get_ident()
        return {"vmid": vmid, "node": "pve1", "type": "qemu", "template": 0}

    monkeypatch.setattr(template_service.proxmox_ops, "find_resource", fake_find_resource)
    monkeypatch.setattr(
        template_service.template_repo,
        "get_template_by_pve_vmid",
        lambda **_kw: None,
    )
    monkeypatch.setattr(
        template_service.template_repo,
        "create_template",
        lambda **kw: _template(VMTemplateStatus.creating),
    )
    session = SimpleNamespace(get=lambda _model, _key: None)

    public, _record = await template_service.create_template(
        session=session,  # type: ignore[arg-type]
        user=_user("admin"),  # type: ignore[arg-type]
        data=VMTemplateCreate(source_vmid=9001, name="lab-vm"),
    )

    assert public.status == VMTemplateStatus.creating
    assert threads["find_resource"] != loop_thread
    assert threads["enqueue"] == loop_thread


async def test_retry_and_delete_run_checks_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, threads: dict[str, int]
) -> None:
    loop_thread = threading.get_ident()
    seen: list[int] = []

    def fake_get_or_404(session: Any, template_id: uuid.UUID) -> VMTemplate:
        seen.append(threading.get_ident())
        return _template(VMTemplateStatus.ready)

    monkeypatch.setattr(template_service, "get_or_404", fake_get_or_404)
    monkeypatch.setattr(template_service, "_require_owner", lambda user, template: None)
    monkeypatch.setattr(template_service, "_clone_children_vmids", lambda s, v: [])
    monkeypatch.setattr(template_service, "_open_request_count", lambda s, v: 0)
    monkeypatch.setattr(template_service, "_open_batch_job_count", lambda s, i: 0)
    monkeypatch.setattr(template_service, "_environments_referencing", lambda s, i: [])
    monkeypatch.setattr(
        template_service.template_repo, "touch", lambda **_kw: None
    )

    await template_service.delete_template(
        session=None,  # type: ignore[arg-type]
        user=_user(),  # type: ignore[arg-type]
        template_id=uuid.uuid4(),
    )
    await template_service.start_update_cycle(
        session=None,  # type: ignore[arg-type]
        user=_user(),  # type: ignore[arg-type]
        template_id=uuid.uuid4(),
    )

    assert len(seen) == 2
    assert loop_thread not in seen
    assert threads["enqueue"] == loop_thread


async def test_clone_request_resolves_gpu_nodes_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, threads: dict[str, int]
) -> None:
    from app.services.proxmox import provisioning_service

    loop_thread = threading.get_ident()
    template = _template()
    monkeypatch.setattr(
        template_service, "get_or_404", lambda session, template_id: template
    )
    monkeypatch.setattr(
        template_service, "require_view", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        template_service, "resolve_effective_spec", lambda template: (2, 2048, 20)
    )
    monkeypatch.setattr(
        clone_service.quota_service, "check_quota", lambda session, user_id, **d: None
    )

    def fake_gpu_nodes(mapping_id: str) -> set[str]:
        threads["gpu_nodes"] = threading.get_ident()
        return {"pve1"}

    monkeypatch.setattr(provisioning_service, "_gpu_mapping_nodes", fake_gpu_nodes)

    records = await clone_service.request_clone(
        session=None,  # type: ignore[arg-type]
        user=_user("teacher"),  # type: ignore[arg-type]
        template_id=template.id,
        data=TemplateCloneRequest(hostname="lab-vm", gpu_mapping_id="h200"),
    )

    assert len(records) == 1
    assert threads["gpu_nodes"] != loop_thread
    assert threads["enqueue"] == loop_thread
