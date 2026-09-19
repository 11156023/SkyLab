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
from app.models import VMTemplate, VMTemplateStatus, VMTemplateVisibility
from app.repositories import vm_template as template_repo
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
        "get_proxmox_settings_for_node",
        lambda node: SimpleNamespace(pool_name="testpool"),
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
    monkeypatch.setattr(template_service, "_pve_template_raw", lambda: None)
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


def test_private_template_is_visible_only_to_owner() -> None:
    owner = make_user("teacher")
    template = make_template(owner_id=owner.id)
    template.visibility = VMTemplateVisibility.private

    assert template_repo.is_template_visible_to_user(
        template=template, user_id=owner.id
    )
    assert not template_repo.is_template_visible_to_user(
        template=template,
        user_id=uuid.uuid4(),
    )


def test_global_template_is_visible_to_other_users() -> None:
    template = make_template(owner_id=uuid.uuid4())
    template.visibility = VMTemplateVisibility.global_

    assert template_repo.is_template_visible_to_user(
        template=template,
        user_id=uuid.uuid4(),
    )


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
    # 配額執法另有專屬測試；其餘案例預設放行，避免碰到真的 DB。
    monkeypatch.setattr(
        template_service, "resolve_effective_spec", lambda template: (2, 2048, 20)
    )
    monkeypatch.setattr(
        clone_service.quota_service,
        "check_quota",
        lambda session, user_id, **deltas: None,
    )
    return template


@pytest.mark.parametrize("count", [1, 2])
async def test_request_clone_denied_for_student(
    visible_template: VMTemplate, count: int
) -> None:
    """學生完全不能克隆（單台也不行）；取得機器一律走申請審核流程。"""
    from app.schemas.template import TemplateCloneRequest

    with pytest.raises(PermissionDeniedError):
        await clone_service.request_clone(
            session=None,  # type: ignore[arg-type]
            user=make_user("student"),
            template_id=visible_template.id,
            data=TemplateCloneRequest(count=count),
        )


async def test_request_clone_enforces_quota_with_template_spec(
    visible_template: VMTemplate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未指定 cores/memory 時，配額要以範本規格 x 台數計算。"""
    from app.schemas.template import TemplateCloneRequest

    monkeypatch.setattr(
        template_service,
        "resolve_effective_spec",
        lambda template: (4, 8192, 32),
    )
    seen: dict[str, Any] = {}

    def fake_check_quota(session: Any, user_id: Any, **deltas: int) -> None:
        seen.update(deltas)
        raise ConflictError("配額不足")

    monkeypatch.setattr(clone_service.quota_service, "check_quota", fake_check_quota)

    with pytest.raises(ConflictError):
        await clone_service.request_clone(
            session=None,  # type: ignore[arg-type]
            user=make_user("teacher"),
            template_id=visible_template.id,
            data=TemplateCloneRequest(count=2),
        )

    assert seen == {
        "delta_cores": 8,
        "delta_memory_mb": 16384,
        "delta_disk_gb": 64,
        "delta_instances": 2,
    }


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

    with pytest.raises(ConflictError, match="尚未就緒"):
        await clone_service.request_clone(
            session=None,  # type: ignore[arg-type]
            user=make_user("teacher"),
            template_id=template.id,
            data=TemplateCloneRequest(count=1),
        )


# ---------------------------------------------------------------------------
# convert 前清快照（PVE 拒絕帶快照的 CT 轉範本；qemu 也一併清）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("resource_type", ["lxc", "qemu"])
def test_remove_snapshots_for_convert_deletes_snapshots_newest_first(
    monkeypatch: pytest.MonkeyPatch, resource_type: str
) -> None:
    deleted: list[str] = []
    monkeypatch.setattr(
        template_service.proxmox_ops,
        "list_snapshots",
        lambda node, vmid, rt: [
            {"name": "old", "snaptime": 100},
            {"name": "current"},  # PVE 的偽快照，不可刪
            {"name": "new", "snaptime": 200},
        ],
    )
    monkeypatch.setattr(
        template_service.proxmox_ops,
        "delete_snapshot",
        lambda node, vmid, rt, snapname: deleted.append(snapname),
    )

    template_service._remove_snapshots_for_convert("pve1", 9001, resource_type)

    assert deleted == ["new", "old"]
# ---------------------------------------------------------------------------
# _strip_hostpci_for_convert：轉範本前剝離 GPU 直通
# ---------------------------------------------------------------------------


def test_strip_hostpci_deletes_all_present_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        template_service.proxmox_ops,
        "get_config",
        lambda node, vmid, rt: {
            "hostpci0": "mapping=h200-gpu,mdev=nvidia-1028",
            "hostpci2": "0000:81:00.0,pcie=1",
            "net0": "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr1",
        },
    )
    captured: dict[str, Any] = {}

    def fake_update_config(node: str, vmid: int, rt: str, **params: Any) -> None:
        captured.update(params)

    monkeypatch.setattr(
        template_service.proxmox_ops, "update_config", fake_update_config
    )

    template_service._strip_hostpci_for_convert("pve1", 9001, "qemu")

    # 稀疏槽位（0 與 2）都要刪到，非 hostpci 鍵不受影響
    assert captured["delete"] == "hostpci0,hostpci2"


def test_strip_hostpci_skips_lxc(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **kw: Any) -> None:
        raise AssertionError("lxc must not touch PVE config")

    monkeypatch.setattr(template_service.proxmox_ops, "get_config", boom)

    template_service._strip_hostpci_for_convert("pve1", 9001, "lxc")


def test_strip_hostpci_noop_without_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        template_service.proxmox_ops,
        "get_config",
        lambda node, vmid, rt: {"net0": "virtio=AA:BB:CC:DD:EE:FF"},
    )

    def boom(*a: Any, **kw: Any) -> None:
        raise AssertionError("update_config must not be called without hostpci")

    monkeypatch.setattr(template_service.proxmox_ops, "update_config", boom)

    template_service._strip_hostpci_for_convert("pve1", 9001, "qemu")


def test_strip_hostpci_tolerates_pve_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a: Any, **kw: Any) -> None:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(template_service.proxmox_ops, "get_config", boom)

    # best-effort：PVE 連不上只記 warning，不得拋出
    template_service._strip_hostpci_for_convert("pve1", 9001, "qemu")
