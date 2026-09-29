"""範本生命週期的回歸測試（in-memory SQLite，mock PVE 與隊列）。

- 完成／取消更新前，暫存母機的 VMID 可能已被別人的新機器接走
- 重試轉換前要重新確認這個 VMID 上的機器仍歸呼叫者
- 更新循環收尾要把未開通的申請單改指新版範本
- 克隆後放大開機磁碟要找對磁碟、不縮小
- PATCH 對 NOT NULL 欄位送 null 要回 400
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.exceptions import BadRequestError, ConflictError, PermissionDeniedError
from app.models import (
    Resource,
    VMRequest,
    VMRequestStatus,
    VMTemplate,
    VMTemplateStatus,
    VMTemplateVisibility,
)
from app.schemas.template import VMTemplateUpdate
from app.services.template import clone_service, template_service

OLD_VMID = 105
TEMP_VMID = 120


@pytest.fixture()
def engine(monkeypatch: pytest.MonkeyPatch) -> Engine:
    # 服務層把同步檢查丟到 run_in_threadpool；StaticPool 讓各執行緒共用同一個
    # in-memory 連線，否則 worker thread 會拿到一個空資料庫
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(eng)
    # worker 端以 Session(engine) 開自己的 session
    monkeypatch.setattr(template_service, "engine", eng)
    monkeypatch.setattr(template_service, "report_progress", lambda *a: None)
    return eng


@pytest.fixture()
def db(engine: Engine) -> Session:
    with Session(engine) as session:
        yield session


@pytest.fixture()
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_enqueue(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4(), payload=kwargs.get("payload"))

    monkeypatch.setattr(template_service, "enqueue_task", fake_enqueue)
    return calls


@pytest.fixture()
def pve_ops(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """記錄所有會改動 PVE 的呼叫。"""
    calls: list[tuple] = []
    monkeypatch.setattr(
        template_service, "_ensure_stopped", lambda *a: calls.append(("stop", *a))
    )
    monkeypatch.setattr(
        template_service, "_reset_cloud_init_state", lambda *a: False
    )
    monkeypatch.setattr(
        template_service,
        "_remove_snapshots_for_convert",
        lambda *a: calls.append(("snapshots", *a)),
    )
    monkeypatch.setattr(
        template_service.proxmox_ops,
        "convert_to_template",
        lambda *a: calls.append(("convert", *a)),
    )
    monkeypatch.setattr(
        template_service.proxmox_ops,
        "delete_resource",
        lambda *a: calls.append(("delete", *a)),
    )
    monkeypatch.setattr(template_service, "_detect_template_disk_gb", lambda *a: None)
    return calls


def _pve(monkeypatch: pytest.MonkeyPatch, machines: dict[int, dict[str, Any]]) -> None:
    def find_resource(vmid: int) -> dict[str, Any]:
        if vmid not in machines:
            raise template_service.NotFoundError(f"Resource {vmid} not found")
        return {"vmid": vmid, "node": "pve1", "type": "qemu", **machines[vmid]}

    monkeypatch.setattr(template_service.proxmox_ops, "find_resource", find_resource)


def _user(role: str = "teacher") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_superuser=False)


def _template(
    session: Session,
    *,
    owner_id: uuid.UUID | None,
    status: VMTemplateStatus,
    source_vmid: int | None,
    pve_vmid: int = OLD_VMID,
) -> VMTemplate:
    template = VMTemplate(
        pve_vmid=pve_vmid,
        name="ubuntu-lab",
        owner_id=owner_id,
        node="pve1",
        resource_type="qemu",
        status=status,
        visibility=VMTemplateVisibility.private,
        source_vmid=source_vmid,
    )
    session.add(template)
    session.commit()
    session.refresh(template)
    return template


def _resource(
    session: Session,
    *,
    vmid: int,
    user_id: uuid.UUID,
    environment_type: str = template_service.UPDATE_TEMP_ENVIRONMENT_TYPE,
) -> None:
    session.add(
        Resource(
            vmid=vmid,
            user_id=user_id,
            environment_type=environment_type,
            created_at=datetime.now(UTC),
        )
    )
    session.commit()


def _request(session: Session, **overrides: Any) -> VMRequest:
    values: dict[str, Any] = {
        "user_id": uuid.uuid4(),
        "reason": "lab",
        "resource_type": "vm",
        "hostname": "stu-01",
        "template_id": OLD_VMID,
        "status": VMRequestStatus.approved,
        "created_at": datetime.now(UTC),
    }
    values.update(overrides)
    request = VMRequest(**values)
    session.add(request)
    session.commit()
    session.refresh(request)
    return request


def _cancel_payload(template: VMTemplate) -> dict[str, Any]:
    return {
        "template_id": str(template.id),
        "temp_vmid": template.source_vmid,
        "pve_vmid": template.pve_vmid,
        "resource_type": "qemu",
        "node": "pve1",
    }


def _convert_payload(template: VMTemplate) -> dict[str, Any]:
    return {
        "template_id": str(template.id),
        "old_pve_vmid": template.pve_vmid,
        "temp_vmid": template.source_vmid,
        "resource_type": "qemu",
        "node": "pve1",
    }


# ---------------------------------------------------------------------------
# finish / cancel 前確認暫存母機仍是這次更新的
# ---------------------------------------------------------------------------


async def test_finish_refuses_when_temp_vmid_now_belongs_to_another_user(
    db: Session, enqueued: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    teacher = _user()
    student_id = uuid.uuid4()
    template = _template(
        db, owner_id=teacher.id, status=VMTemplateStatus.updating, source_vmid=TEMP_VMID
    )
    # 暫存母機被刪後，VMID 120 發給了學生的新機器
    _resource(db, vmid=TEMP_VMID, user_id=student_id, environment_type="Custom")
    _pve(monkeypatch, {TEMP_VMID: {"name": "student-vm"}})

    with pytest.raises(ConflictError):
        await template_service.finish_update_cycle(
            session=db, user=teacher, template_id=template.id
        )
    assert enqueued == []


async def test_finish_refuses_when_pve_name_no_longer_matches(
    db: Session, enqueued: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    teacher = _user()
    template = _template(
        db, owner_id=teacher.id, status=VMTemplateStatus.updating, source_vmid=TEMP_VMID
    )
    _resource(db, vmid=TEMP_VMID, user_id=teacher.id)
    _pve(monkeypatch, {TEMP_VMID: {"name": "something-else"}})

    with pytest.raises(ConflictError):
        await template_service.finish_update_cycle(
            session=db, user=teacher, template_id=template.id
        )
    assert enqueued == []


async def test_finish_enqueues_for_the_real_update_clone(
    db: Session, enqueued: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    teacher = _user()
    template = _template(
        db, owner_id=teacher.id, status=VMTemplateStatus.updating, source_vmid=TEMP_VMID
    )
    _resource(db, vmid=TEMP_VMID, user_id=teacher.id)
    _pve(monkeypatch, {TEMP_VMID: {"name": f"tpl-{OLD_VMID}-edit"}})

    await template_service.finish_update_cycle(
        session=db, user=teacher, template_id=template.id
    )

    assert len(enqueued) == 1
    assert enqueued[0]["payload"]["temp_vmid"] == TEMP_VMID


def test_cancel_task_leaves_reused_vmid_alone(
    db: Session, pve_ops: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    teacher_id = uuid.uuid4()
    student_id = uuid.uuid4()
    template = _template(
        db, owner_id=teacher_id, status=VMTemplateStatus.updating, source_vmid=TEMP_VMID
    )
    _resource(db, vmid=TEMP_VMID, user_id=student_id, environment_type="Custom")
    _pve(monkeypatch, {TEMP_VMID: {"name": "student-vm"}})

    result = template_service.run_update_cancel_task(
        uuid.uuid4(), _cancel_payload(template)
    )

    assert pve_ops == []
    assert result["temp_removed"] is False
    db.expire_all()
    refreshed = db.get(VMTemplate, template.id)
    assert refreshed is not None
    assert refreshed.status == VMTemplateStatus.ready
    assert refreshed.source_vmid is None
    # 學生的 Resource 紀錄不能被當成暫存母機刪掉
    student_resource = db.get(Resource, TEMP_VMID)
    assert student_resource is not None
    assert student_resource.user_id == student_id


def test_cancel_task_removes_the_real_update_clone(
    db: Session, pve_ops: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    teacher_id = uuid.uuid4()
    template = _template(
        db, owner_id=teacher_id, status=VMTemplateStatus.updating, source_vmid=TEMP_VMID
    )
    _resource(db, vmid=TEMP_VMID, user_id=teacher_id)
    _pve(monkeypatch, {TEMP_VMID: {"name": f"tpl-{OLD_VMID}-edit"}})

    result = template_service.run_update_cancel_task(
        uuid.uuid4(), _cancel_payload(template)
    )

    assert result["temp_removed"] is True
    assert [call[0] for call in pve_ops] == ["stop", "delete"]
    db.expire_all()
    assert db.get(Resource, TEMP_VMID) is None


def test_convert_task_refuses_reused_vmid(
    db: Session, pve_ops: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    teacher_id = uuid.uuid4()
    student_id = uuid.uuid4()
    template = _template(
        db, owner_id=teacher_id, status=VMTemplateStatus.updating, source_vmid=TEMP_VMID
    )
    _resource(db, vmid=TEMP_VMID, user_id=student_id, environment_type="Custom")
    _pve(monkeypatch, {TEMP_VMID: {"name": "student-vm"}})

    with pytest.raises(RuntimeError):
        template_service.run_update_convert_task(
            uuid.uuid4(), _convert_payload(template)
        )

    assert pve_ops == []
    db.expire_all()
    refreshed = db.get(VMTemplate, template.id)
    assert refreshed is not None
    assert refreshed.pve_vmid == OLD_VMID
    assert refreshed.status == VMTemplateStatus.updating
    assert refreshed.error_message
    assert db.get(Resource, TEMP_VMID) is not None


# 上面幾個「VMID 被接走」的案例連 PVE 名稱也對不上，光名稱檢查就會擋下；
# 以下讓名稱符合 tpl-<vmid>-edit，只留 Resource 不符，單獨驗證平台紀錄的檢查
# （例如學生把新機器取名成 tpl-105-edit）。
_MISMATCHED_TEMP_RESOURCE = [
    pytest.param(
        False, template_service.UPDATE_TEMP_ENVIRONMENT_TYPE, id="other-user"
    ),
    pytest.param(True, "Custom", id="owner-but-not-update-temp"),
    pytest.param(False, "Custom", id="other-user-custom-vm"),
]


@pytest.mark.parametrize(
    ("owned_by_owner", "environment_type"), _MISMATCHED_TEMP_RESOURCE
)
async def test_finish_refuses_mismatched_resource_even_when_name_matches(
    db: Session,
    enqueued: list,
    monkeypatch: pytest.MonkeyPatch,
    owned_by_owner: bool,
    environment_type: str,
) -> None:
    teacher = _user()
    template = _template(
        db, owner_id=teacher.id, status=VMTemplateStatus.updating, source_vmid=TEMP_VMID
    )
    user_id = teacher.id if owned_by_owner else uuid.uuid4()
    _resource(db, vmid=TEMP_VMID, user_id=user_id, environment_type=environment_type)
    _pve(monkeypatch, {TEMP_VMID: {"name": f"tpl-{OLD_VMID}-edit"}})

    with pytest.raises(ConflictError):
        await template_service.finish_update_cycle(
            session=db, user=teacher, template_id=template.id
        )
    assert enqueued == []


@pytest.mark.parametrize(
    ("owned_by_owner", "environment_type"), _MISMATCHED_TEMP_RESOURCE
)
def test_cancel_task_leaves_mismatched_resource_alone_even_when_name_matches(
    db: Session,
    pve_ops: list,
    monkeypatch: pytest.MonkeyPatch,
    owned_by_owner: bool,
    environment_type: str,
) -> None:
    teacher_id = uuid.uuid4()
    template = _template(
        db, owner_id=teacher_id, status=VMTemplateStatus.updating, source_vmid=TEMP_VMID
    )
    user_id = teacher_id if owned_by_owner else uuid.uuid4()
    _resource(db, vmid=TEMP_VMID, user_id=user_id, environment_type=environment_type)
    _pve(monkeypatch, {TEMP_VMID: {"name": f"tpl-{OLD_VMID}-edit"}})

    result = template_service.run_update_cancel_task(
        uuid.uuid4(), _cancel_payload(template)
    )

    assert pve_ops == []
    assert result["temp_removed"] is False
    db.expire_all()
    remaining = db.get(Resource, TEMP_VMID)
    assert remaining is not None
    assert remaining.user_id == user_id
    assert remaining.environment_type == environment_type


@pytest.mark.parametrize(
    ("owned_by_owner", "environment_type"), _MISMATCHED_TEMP_RESOURCE
)
def test_convert_task_refuses_mismatched_resource_even_when_name_matches(
    db: Session,
    pve_ops: list,
    monkeypatch: pytest.MonkeyPatch,
    owned_by_owner: bool,
    environment_type: str,
) -> None:
    teacher_id = uuid.uuid4()
    template = _template(
        db, owner_id=teacher_id, status=VMTemplateStatus.updating, source_vmid=TEMP_VMID
    )
    user_id = teacher_id if owned_by_owner else uuid.uuid4()
    _resource(db, vmid=TEMP_VMID, user_id=user_id, environment_type=environment_type)
    _pve(monkeypatch, {TEMP_VMID: {"name": f"tpl-{OLD_VMID}-edit"}})

    with pytest.raises(RuntimeError):
        template_service.run_update_convert_task(
            uuid.uuid4(), _convert_payload(template)
        )

    assert pve_ops == []
    db.expire_all()
    refreshed = db.get(VMTemplate, template.id)
    assert refreshed is not None
    assert refreshed.pve_vmid == OLD_VMID
    remaining = db.get(Resource, TEMP_VMID)
    assert remaining is not None
    assert remaining.user_id == user_id


# 範本沒有擁有者時 clone 不會登記 Resource，所以這個 VMID 一有 Resource 就代表被接走
def test_cancel_task_without_owner_leaves_registered_vmid_alone(
    db: Session, pve_ops: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    student_id = uuid.uuid4()
    template = _template(
        db, owner_id=None, status=VMTemplateStatus.updating, source_vmid=TEMP_VMID
    )
    _resource(db, vmid=TEMP_VMID, user_id=student_id, environment_type="Custom")
    _pve(monkeypatch, {TEMP_VMID: {"name": f"tpl-{OLD_VMID}-edit"}})

    result = template_service.run_update_cancel_task(
        uuid.uuid4(), _cancel_payload(template)
    )

    assert pve_ops == []
    assert result["temp_removed"] is False
    db.expire_all()
    remaining = db.get(Resource, TEMP_VMID)
    assert remaining is not None
    assert remaining.user_id == student_id


def test_convert_task_without_owner_refuses_registered_vmid(
    db: Session, pve_ops: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _template(
        db, owner_id=None, status=VMTemplateStatus.updating, source_vmid=TEMP_VMID
    )
    _resource(db, vmid=TEMP_VMID, user_id=uuid.uuid4(), environment_type="Custom")
    _pve(monkeypatch, {TEMP_VMID: {"name": f"tpl-{OLD_VMID}-edit"}})

    with pytest.raises(RuntimeError):
        template_service.run_update_convert_task(
            uuid.uuid4(), _convert_payload(template)
        )

    assert pve_ops == []
    db.expire_all()
    assert db.get(VMTemplate, template.id).pve_vmid == OLD_VMID
    assert db.get(Resource, TEMP_VMID) is not None


def test_cancel_task_without_owner_removes_unregistered_update_clone(
    db: Session, pve_ops: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _template(
        db, owner_id=None, status=VMTemplateStatus.updating, source_vmid=TEMP_VMID
    )
    _pve(monkeypatch, {TEMP_VMID: {"name": f"tpl-{OLD_VMID}-edit"}})

    result = template_service.run_update_cancel_task(
        uuid.uuid4(), _cancel_payload(template)
    )

    assert result["temp_removed"] is True
    assert [call[0] for call in pve_ops] == ["stop", "delete"]


# ---------------------------------------------------------------------------
# 收尾時未開通的申請單改指新版範本
# ---------------------------------------------------------------------------


def test_convert_task_repoints_open_requests_to_new_template(
    db: Session, pve_ops: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    teacher_id = uuid.uuid4()
    template = _template(
        db, owner_id=teacher_id, status=VMTemplateStatus.updating, source_vmid=TEMP_VMID
    )
    _resource(db, vmid=TEMP_VMID, user_id=teacher_id)
    _pve(monkeypatch, {TEMP_VMID: {"name": f"tpl-{OLD_VMID}-edit"}})
    approved = _request(db, status=VMRequestStatus.approved)
    pending = _request(db, status=VMRequestStatus.pending)
    provisioned = _request(db, status=VMRequestStatus.approved, vmid=300)
    rejected = _request(db, status=VMRequestStatus.rejected)
    approved_id, pending_id = approved.id, pending.id
    provisioned_id, rejected_id = provisioned.id, rejected.id

    result = template_service.run_update_convert_task(
        uuid.uuid4(), _convert_payload(template)
    )

    assert result["vmid"] == TEMP_VMID
    assert [call[0] for call in pve_ops] == ["stop", "snapshots", "convert", "delete"]
    db.expire_all()
    template_ids = {
        row.id: row.template_id for row in db.exec(select(VMRequest)).all()
    }
    assert template_ids[approved_id] == TEMP_VMID
    assert template_ids[pending_id] == TEMP_VMID
    # 已開出機器與已結案的申請單不動
    assert template_ids[provisioned_id] == OLD_VMID
    assert template_ids[rejected_id] == OLD_VMID
    refreshed = db.get(VMTemplate, template.id)
    assert refreshed is not None
    assert refreshed.pve_vmid == TEMP_VMID
    assert refreshed.status == VMTemplateStatus.ready
    assert db.get(Resource, TEMP_VMID) is None


# ---------------------------------------------------------------------------
# 重試轉換要重新確認機器歸屬
# ---------------------------------------------------------------------------


async def test_retry_refuses_vmid_now_owned_by_another_user(
    db: Session, enqueued: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    teacher = _user()
    template = _template(
        db, owner_id=teacher.id, status=VMTemplateStatus.failed, source_vmid=OLD_VMID
    )
    _resource(db, vmid=OLD_VMID, user_id=uuid.uuid4(), environment_type="Custom")
    _pve(monkeypatch, {OLD_VMID: {"name": "student-vm", "template": 0}})

    with pytest.raises(PermissionDeniedError):
        await template_service.retry_template_conversion(
            session=db, user=teacher, template_id=template.id
        )
    assert enqueued == []
    db.expire_all()
    assert db.get(VMTemplate, template.id).status == VMTemplateStatus.failed


async def test_retry_refuses_unregistered_vm_for_non_admin(
    db: Session, enqueued: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    teacher = _user()
    template = _template(
        db, owner_id=teacher.id, status=VMTemplateStatus.failed, source_vmid=OLD_VMID
    )
    _pve(monkeypatch, {OLD_VMID: {"name": "orphan", "template": 0}})

    with pytest.raises(PermissionDeniedError):
        await template_service.retry_template_conversion(
            session=db, user=teacher, template_id=template.id
        )
    assert enqueued == []


async def test_retry_refuses_already_template_owned_by_another_user(
    db: Session, enqueued: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    teacher = _user()
    template = _template(
        db, owner_id=teacher.id, status=VMTemplateStatus.failed, source_vmid=OLD_VMID
    )
    _resource(db, vmid=OLD_VMID, user_id=uuid.uuid4(), environment_type="Custom")
    _pve(monkeypatch, {OLD_VMID: {"name": "x", "template": 1}})

    with pytest.raises(PermissionDeniedError):
        await template_service.retry_template_conversion(
            session=db, user=teacher, template_id=template.id
        )
    db.expire_all()
    assert db.get(VMTemplate, template.id).status == VMTemplateStatus.failed


async def test_retry_still_enqueues_for_own_source_vm(
    db: Session, enqueued: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    teacher = _user()
    template = _template(
        db, owner_id=teacher.id, status=VMTemplateStatus.failed, source_vmid=OLD_VMID
    )
    _resource(db, vmid=OLD_VMID, user_id=teacher.id, environment_type="Custom")
    _pve(monkeypatch, {OLD_VMID: {"name": "my-vm", "template": 0}})

    await template_service.retry_template_conversion(
        session=db, user=teacher, template_id=template.id
    )

    assert len(enqueued) == 1
    assert enqueued[0]["task_type"] == template_service.TASK_CONVERT


async def test_retry_shortcut_marks_ready_when_already_converted(
    db: Session, enqueued: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 轉換成功時母機 Resource 已被移除；這個情況仍要能直接標 ready
    teacher = _user()
    template = _template(
        db, owner_id=teacher.id, status=VMTemplateStatus.failed, source_vmid=OLD_VMID
    )
    _pve(monkeypatch, {OLD_VMID: {"name": "ubuntu-lab", "template": 1}})

    public, _record = await template_service.retry_template_conversion(
        session=db, user=teacher, template_id=template.id
    )

    assert public.status == VMTemplateStatus.ready
    assert enqueued == []


# ---------------------------------------------------------------------------
# NOT NULL 欄位送 null
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field", ["name", "visibility", "allow_password_change", "requires_gpu"]
)
def test_update_template_rejects_null_for_required_fields(
    db: Session, field: str
) -> None:
    teacher = _user()
    template = _template(
        db, owner_id=teacher.id, status=VMTemplateStatus.ready, source_vmid=OLD_VMID
    )

    with pytest.raises(BadRequestError):
        template_service.update_template(
            session=db,
            user=teacher,
            template_id=template.id,
            data=VMTemplateUpdate.model_construct(**{field: None}),
        )
    db.expire_all()
    assert db.get(VMTemplate, template.id).name == "ubuntu-lab"


def test_update_template_still_allows_clearing_nullable_fields(db: Session) -> None:
    teacher = _user()
    template = _template(
        db, owner_id=teacher.id, status=VMTemplateStatus.ready, source_vmid=OLD_VMID
    )

    public = template_service.update_template(
        session=db,
        user=teacher,
        template_id=template.id,
        data=VMTemplateUpdate.model_validate(
            {"description": None, "default_cores": None, "name": "renamed"}
        ),
    )

    assert public.name == "renamed"
    assert public.description is None


# ---------------------------------------------------------------------------
# 克隆後放大開機磁碟
# ---------------------------------------------------------------------------


def _grow(
    monkeypatch: pytest.MonkeyPatch, config: dict[str, Any], disk_gb: int
) -> list[tuple]:
    resized: list[tuple] = []
    monkeypatch.setattr(
        clone_service.proxmox_ops, "get_config", lambda node, vmid, rtype: config
    )
    monkeypatch.setattr(
        clone_service.proxmox_ops,
        "resize_disk",
        lambda *a: resized.append(a),
    )
    clone_service._grow_qemu_boot_disk(node="pve1", vmid=200, disk_gb=disk_gb)
    return resized


def test_grow_boot_disk_skips_same_size_virtio(monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"virtio0": "local-lvm:vm-200-disk-0,size=32G"}
    assert _grow(monkeypatch, config, 32) == []


def test_grow_boot_disk_resizes_virtio_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"virtio0": "local-lvm:vm-200-disk-0,size=32G"}
    assert _grow(monkeypatch, config, 40) == [("pve1", 200, "qemu", "virtio0", "40G")]


def test_grow_boot_disk_never_shrinks(monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"scsi0": "local-lvm:vm-200-disk-0,size=40G"}
    assert _grow(monkeypatch, config, 20) == []


def test_grow_boot_disk_follows_bootdisk(monkeypatch: pytest.MonkeyPatch) -> None:
    config = {
        "bootdisk": "sata0",
        "scsi0": "local-lvm:vm-200-disk-1,size=100G",
        "sata0": "local-lvm:vm-200-disk-0,size=16G",
    }
    assert _grow(monkeypatch, config, 20) == [("pve1", 200, "qemu", "sata0", "20G")]
