"""範本系統 2.0 單元測試（mock PVE operations，無 DB / Redis）。

涵蓋 progress 計畫指定的三塊：
- linked clone 失敗自動退 full clone
- 刪除範本時擋 linked clone 子機
- 列表 RBAC 過濾（admin 全部 / teacher 可見 / student 僅 ready）
外加 request_clone 的學生配額與批量權限。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import ConflictError, PermissionDeniedError
from app.models import VMTemplate, VMTemplateStatus
from app.services.proxmox import provisioning_service
from app.services.template import clone_service, template_service


def make_user(role: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_superuser=False)


def make_template(
    *,
    owner_id: uuid.UUID | None = None,
    status: VMTemplateStatus = VMTemplateStatus.ready,
    resource_type: str = "qemu",
) -> VMTemplate:
    return VMTemplate(
        id=uuid.uuid4(),
        pve_vmid=9001,
        name="lab-vm",
        owner_id=owner_id,
        node="pve1",
        resource_type=resource_type,
        status=status,
    )


@pytest.fixture(autouse=True)
def fake_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        clone_service,
        "get_proxmox_settings",
        lambda: SimpleNamespace(pool_name="testpool"),
    )


# ---------------------------------------------------------------------------
# linked → full fallback
# ---------------------------------------------------------------------------


def test_clone_with_fallback_prefers_linked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_clone_vm(node: str, template_vmid: int, **config: Any) -> str:
        calls.append({"node": node, "template": template_vmid, **config})
        return "UPID:ok"

    monkeypatch.setattr(clone_service.proxmox_ops, "clone_vm", fake_clone_vm)

    mode = clone_service.clone_with_fallback(
        node="pve1",
        template_vmid=9001,
        new_vmid=101,
        hostname="stu-01",
        resource_type="qemu",
    )

    assert mode == "linked"
    assert len(calls) == 1
    assert calls[0]["full"] == 0
    assert calls[0]["newid"] == 101
    assert calls[0]["name"] == "stu-01"
    assert calls[0]["pool"] == "testpool"


def test_clone_with_fallback_falls_back_to_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    cleaned: list[int] = []

    def fake_clone_vm(node: str, template_vmid: int, **config: Any) -> str:
        calls.append(dict(config))
        if config["full"] == 0:
            raise RuntimeError("linked clone feature is not available")
        return "UPID:ok"

    monkeypatch.setattr(clone_service.proxmox_ops, "clone_vm", fake_clone_vm)
    monkeypatch.setattr(
        provisioning_service,
        "cleanup_provisioned_resource",
        lambda vmid: cleaned.append(vmid),
    )

    mode = clone_service.clone_with_fallback(
        node="pve1",
        template_vmid=9001,
        new_vmid=102,
        hostname="stu-02",
        resource_type="qemu",
        full_kwargs={"storage": "fast-lvm"},
    )

    assert mode == "full"
    assert [c["full"] for c in calls] == [0, 1]
    # full clone 才帶 storage；linked 不可帶
    assert "storage" not in calls[0]
    assert calls[1]["storage"] == "fast-lvm"
    # 退 full 前先清掉 linked 殘骸
    assert cleaned == [102]


def test_clone_with_fallback_lxc_uses_hostname_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_clone_lxc(node: str, template_vmid: int, **config: Any) -> str:
        calls.append(dict(config))
        return "UPID:ok"

    monkeypatch.setattr(clone_service.proxmox_ops, "clone_lxc", fake_clone_lxc)

    mode = clone_service.clone_with_fallback(
        node="pve1",
        template_vmid=9002,
        new_vmid=103,
        hostname="ctn-01",
        resource_type="lxc",
    )

    assert mode == "linked"
    assert calls[0]["hostname"] == "ctn-01"
    assert "name" not in calls[0]


# ---------------------------------------------------------------------------
# 刪除擋子機
# ---------------------------------------------------------------------------


class FakeSession:
    """最小 Session 假件：get 回範本、exec(...).all() 回子機 vmid 清單。"""

    def __init__(self, template: VMTemplate | None, children: list[int]) -> None:
        self.template = template
        self.children = children

    def get(self, model: type, key: Any) -> Any:
        return self.template

    def exec(self, stmt: Any) -> Any:
        return SimpleNamespace(all=lambda: list(self.children))


async def test_delete_template_blocked_by_clone_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = make_user("admin")
    template = make_template(owner_id=user.id)
    session = FakeSession(template, children=[201, 202])

    async def fail_enqueue(**kwargs: Any) -> None:
        raise AssertionError("enqueue_task should not be called")

    monkeypatch.setattr(template_service, "enqueue_task", fail_enqueue)

    with pytest.raises(ConflictError, match="201, 202"):
        await template_service.delete_template(
            session=session,  # type: ignore[arg-type]
            user=user,
            template_id=template.id,
        )


async def test_delete_template_enqueues_without_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = make_user("teacher")
    template = make_template(owner_id=user.id)
    session = FakeSession(template, children=[])
    captured: dict[str, Any] = {}

    async def fake_enqueue(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "task-record"

    monkeypatch.setattr(template_service, "enqueue_task", fake_enqueue)

    record = await template_service.delete_template(
        session=session,  # type: ignore[arg-type]
        user=user,
        template_id=template.id,
    )

    assert record == "task-record"
    assert captured["task_type"] == template_service.TASK_DELETE
    assert captured["payload"]["pve_vmid"] == template.pve_vmid


async def test_delete_template_denies_non_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = make_user("teacher")
    other_teacher = make_user("teacher")
    template = make_template(owner_id=owner.id)
    session = FakeSession(template, children=[])

    with pytest.raises(PermissionDeniedError):
        await template_service.delete_template(
            session=session,  # type: ignore[arg-type]
            user=other_teacher,
            template_id=template.id,
        )


# ---------------------------------------------------------------------------
# RBAC 過濾
# ---------------------------------------------------------------------------


@pytest.fixture
def rbac_repo(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    def fake_list_all(*, session: Any) -> list[VMTemplate]:
        calls["fn"] = "list_all"
        return []

    def fake_list_visible(
        *, session: Any, user_id: uuid.UUID, only_ready: bool = False
    ) -> list[VMTemplate]:
        calls["fn"] = "list_visible"
        calls["only_ready"] = only_ready
        return []

    monkeypatch.setattr(
        template_service.template_repo, "list_all_templates", fake_list_all
    )
    monkeypatch.setattr(
        template_service.template_repo, "list_visible_templates", fake_list_visible
    )
    monkeypatch.setattr(template_service, "_pve_template_vmids", lambda: None)
    return calls


def test_list_templates_admin_sees_all(rbac_repo: dict[str, Any]) -> None:
    template_service.list_templates(session=None, user=make_user("admin"))  # type: ignore[arg-type]
    assert rbac_repo["fn"] == "list_all"


def test_list_templates_teacher_sees_any_status(
    rbac_repo: dict[str, Any],
) -> None:
    template_service.list_templates(session=None, user=make_user("teacher"))  # type: ignore[arg-type]
    assert rbac_repo["fn"] == "list_visible"
    assert rbac_repo["only_ready"] is False


def test_list_templates_student_sees_only_ready(
    rbac_repo: dict[str, Any],
) -> None:
    template_service.list_templates(session=None, user=make_user("student"))  # type: ignore[arg-type]
    assert rbac_repo["fn"] == "list_visible"
    assert rbac_repo["only_ready"] is True


# ---------------------------------------------------------------------------
# request_clone：學生配額與批量權限
# ---------------------------------------------------------------------------


@pytest.fixture
def visible_template(monkeypatch: pytest.MonkeyPatch) -> VMTemplate:
    template = make_template()
    monkeypatch.setattr(
        template_service, "_get_or_404", lambda session, template_id: template
    )
    monkeypatch.setattr(
        template_service, "_require_view", lambda session, user, template: None
    )
    return template


async def test_request_clone_student_batch_denied(
    visible_template: VMTemplate,
) -> None:
    from app.schemas.template import TemplateCloneRequest

    with pytest.raises(PermissionDeniedError, match="batch"):
        await clone_service.request_clone(
            session=None,  # type: ignore[arg-type]
            user=make_user("student"),
            template_id=visible_template.id,
            data=TemplateCloneRequest(count=2),
        )


async def test_request_clone_student_quota_exceeded(
    visible_template: VMTemplate, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings
    from app.schemas.template import TemplateCloneRequest

    monkeypatch.setattr(
        clone_service.resource_repo,
        "get_resources_by_user",
        lambda *, session, user_id: [
            SimpleNamespace(vmid=i)
            for i in range(settings.TEMPLATE_CLONE_STUDENT_MAX_INSTANCES)
        ],
    )

    with pytest.raises(ConflictError, match="quota"):
        await clone_service.request_clone(
            session=None,  # type: ignore[arg-type]
            user=make_user("student"),
            template_id=visible_template.id,
            data=TemplateCloneRequest(count=1),
        )


async def test_request_clone_teacher_batch_enqueues_numbered_hostnames(
    visible_template: VMTemplate, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.schemas.template import TemplateCloneRequest

    payloads: list[dict[str, Any]] = []

    async def fake_enqueue(**kwargs: Any) -> Any:
        payloads.append(kwargs["payload"])
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(clone_service, "enqueue_task", fake_enqueue)

    records = await clone_service.request_clone(
        session=None,  # type: ignore[arg-type]
        user=make_user("teacher"),
        template_id=visible_template.id,
        data=TemplateCloneRequest(hostname="lab-vm", count=3),
    )

    assert len(records) == 3
    assert [p["hostname"] for p in payloads] == [
        "lab-vm-01",
        "lab-vm-02",
        "lab-vm-03",
    ]


async def test_request_clone_rejects_not_ready_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.schemas.template import TemplateCloneRequest

    template = make_template(status=VMTemplateStatus.updating)
    monkeypatch.setattr(
        template_service, "_get_or_404", lambda session, template_id: template
    )
    monkeypatch.setattr(
        template_service, "_require_view", lambda session, user, template: None
    )

    with pytest.raises(ConflictError, match="not ready"):
        await clone_service.request_clone(
            session=None,  # type: ignore[arg-type]
            user=make_user("teacher"),
            template_id=template.id,
            data=TemplateCloneRequest(count=1),
        )
