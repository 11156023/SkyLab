"""app/services/template/template_service.py 剩餘分支的單元測試（in-memory SQLite，mock PVE／隊列）。"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.infrastructure.proxmox import guest
from app.models import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    Resource,
    TaskRecord,
    TemplateAttachment,
    VMTemplate,
    VMTemplateStatus,
    VMTemplateVisibility,
)
from app.schemas.template import VMTemplateCreate
from app.services.network import ip_management_service, nat_service
from app.services.proxmox import provisioning_service
from app.services.resource import resource_service
from app.services.template import template_files
from app.services.template import template_service as ts

PVE_VMID = 900
TEMP_VMID = 950
GIB = 1024**3


# ---------------------------------------------------------------------------
# fixtures／helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def eng(monkeypatch: pytest.MonkeyPatch) -> Engine:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(ts, "engine", engine)
    monkeypatch.setattr(ts, "report_progress", lambda *a: None)
    return engine


@pytest.fixture()
def sess(eng: Engine) -> Session:
    with Session(eng) as session:
        yield session


@pytest.fixture()
def files_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "template_files"
    monkeypatch.setattr(template_files, "ATTACHMENT_DIR", directory)
    return directory


def _user(role: str = "teacher") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_superuser=False)


def _template(session: Session, **overrides: Any) -> VMTemplate:
    values: dict[str, Any] = {
        "pve_vmid": PVE_VMID,
        "name": "ubuntu-lab",
        "owner_id": None,
        "node": "pve1",
        "resource_type": "qemu",
        "status": VMTemplateStatus.ready,
        "visibility": VMTemplateVisibility.private,
    }
    values.update(overrides)
    template = VMTemplate(**values)
    session.add(template)
    session.commit()
    session.refresh(template)
    return template


def _resource(
    session: Session,
    *,
    vmid: int,
    user_id: uuid.UUID,
    environment_type: str = "Custom",
    template_id: int | None = None,
) -> Resource:
    resource = Resource(
        vmid=vmid,
        user_id=user_id,
        environment_type=environment_type,
        template_id=template_id,
        created_at=datetime.now(UTC),
    )
    session.add(resource)
    session.commit()
    return resource


def _attachment(session: Session, template: VMTemplate, name: str = "a.pdf"):
    attachment = TemplateAttachment(
        template_id=template.id, filename=name, size_bytes=3
    )
    session.add(attachment)
    session.commit()
    session.refresh(attachment)
    return attachment


def _find(monkeypatch: pytest.MonkeyPatch, machines: dict[int, dict[str, Any]]):
    def find_resource(vmid: int) -> dict[str, Any]:
        if vmid not in machines:
            raise NotFoundError(f"Resource {vmid} not found")
        return {"vmid": vmid, "node": "pve1", "type": "qemu", **machines[vmid]}

    monkeypatch.setattr(ts.proxmox_ops, "find_resource", find_resource)


def _raise(exc: Exception):
    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise exc

    return _boom


# ---------------------------------------------------------------------------
# 查詢：_to_public／附件數／PVE 原始紀錄／有效規格
# ---------------------------------------------------------------------------


def test_to_public_backfills_unset_spec_from_pve(sess: Session) -> None:
    template = _template(sess)
    raw = {PVE_VMID: {"maxcpu": 2, "maxmem": 2048 * 1024 * 1024, "maxdisk": 10 * GIB}}
    public = ts._to_public(template, pve_raw=raw, attachment_count=4)
    assert public.pve_exists is True
    assert (public.default_cores, public.default_memory, public.default_disk) == (
        2,
        2048,
        10,
    )
    assert public.attachment_count == 4


def test_attachment_counts_failure_returns_empty() -> None:
    broken = SimpleNamespace(exec=_raise(RuntimeError("db down")))
    assert ts._attachment_counts(broken, [uuid.uuid4()]) == {}


def test_pve_template_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ts.proxmox_ops, "get_vm_templates", lambda **_: [{"vmid": "900", "maxcpu": 1}]
    )
    assert ts._pve_template_raw() == {900: {"vmid": "900", "maxcpu": 1}}
    monkeypatch.setattr(ts.proxmox_ops, "get_vm_templates", _raise(RuntimeError()))
    assert ts._pve_template_raw() is None


def test_resolve_effective_spec_prefers_db_values(sess: Session) -> None:
    template = _template(sess, default_cores=2, default_memory=1024, default_disk=8)
    assert ts.resolve_effective_spec(template) == (2, 1024, 8)


def test_resolve_effective_spec_fills_from_pve(
    sess: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _template(sess, default_cores=3)
    monkeypatch.setattr(
        ts,
        "_pve_template_raw",
        lambda *_: {PVE_VMID: {"maxcpu": 8, "maxmem": 512 * 1024 * 1024, "maxdisk": GIB}},
    )
    assert ts.resolve_effective_spec(template) == (3, 512, 1)
    # PVE 連不上：缺的欄位維持 None
    monkeypatch.setattr(ts, "_pve_template_raw", lambda *_: None)
    assert ts.resolve_effective_spec(template) == (3, None, None)


def test_list_student_catalog_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ts.template_repo, "list_student_catalog", lambda session: [])
    assert ts.list_student_catalog(session=SimpleNamespace()) == []


def test_reconcile_skips_when_no_failed_task(
    sess: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _template(sess, status=VMTemplateStatus.creating)
    monkeypatch.setattr(
        ts.task_record_repo, "get_latest_template_task", lambda **kw: None
    )
    ts._reconcile_failed_template_tasks(sess, [template])
    assert template.status == VMTemplateStatus.creating


def test_get_or_404_missing_and_deleted(sess: Session) -> None:
    with pytest.raises(NotFoundError):
        ts.get_or_404(sess, uuid.uuid4())
    deleted = _template(sess, status=VMTemplateStatus.deleted)
    with pytest.raises(NotFoundError):
        ts.get_or_404(sess, deleted.id)


def test_require_view_admin_and_hidden(sess: Session) -> None:
    template = _template(sess, owner_id=uuid.uuid4())
    ts.require_view(sess, _user("admin"), template)
    with pytest.raises(NotFoundError):
        ts.require_view(sess, _user("student"), template)


# ---------------------------------------------------------------------------
# 建立／重試轉換
# ---------------------------------------------------------------------------


def test_prepare_create_source_vm_not_found(
    sess: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _find(monkeypatch, {})
    data = VMTemplateCreate(source_vmid=PVE_VMID, name="x")
    with pytest.raises(NotFoundError):
        ts._prepare_create(sess, _user(), data)


def test_prepare_create_rejects_existing_pve_template(
    sess: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _find(monkeypatch, {PVE_VMID: {"template": 1}})
    data = VMTemplateCreate(source_vmid=PVE_VMID, name="x")
    with pytest.raises(BadRequestError):
        ts._prepare_create(sess, _user(), data)


def test_prepare_create_rejects_other_users_vm(
    sess: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _find(monkeypatch, {PVE_VMID: {}})
    _resource(sess, vmid=PVE_VMID, user_id=uuid.uuid4())
    data = VMTemplateCreate(source_vmid=PVE_VMID, name="x")
    with pytest.raises(PermissionDeniedError):
        ts._prepare_create(sess, _user(), data)


async def test_enqueue_convert_failure_marks_template_failed(
    sess: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _template(sess, status=VMTemplateStatus.creating)

    async def boom(**kwargs: Any) -> Any:
        raise RuntimeError("redis down")

    monkeypatch.setattr(ts, "enqueue_task", boom)
    with pytest.raises(RuntimeError):
        await ts._enqueue_convert(session=sess, user=_user(), template=template)
    sess.refresh(template)
    assert template.status == VMTemplateStatus.failed
    assert "redis down" in (template.error_message or "")


def test_prepare_retry_requires_failed_status(sess: Session) -> None:
    owner = _user()
    template = _template(sess, owner_id=owner.id)
    with pytest.raises(ConflictError):
        ts._prepare_retry(sess, owner, template.id)


def test_prepare_retry_source_gone(
    sess: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _user()
    template = _template(sess, owner_id=owner.id, status=VMTemplateStatus.failed)
    _find(monkeypatch, {})
    with pytest.raises(NotFoundError):
        ts._prepare_retry(sess, owner, template.id)


def test_prepare_retry_already_converted_records_password_settable(
    sess: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _user()
    template = _template(
        sess,
        owner_id=owner.id,
        status=VMTemplateStatus.failed,
        password_settable=False,
    )
    _find(monkeypatch, {PVE_VMID: {"template": 1}})
    monkeypatch.setattr(ts, "_detect_password_settable", lambda *a: True)
    result, record = ts._prepare_retry(sess, owner, template.id)
    assert result.status == VMTemplateStatus.ready
    assert result.password_settable is True
    assert isinstance(record, TaskRecord)


# ---------------------------------------------------------------------------
# 附件
# ---------------------------------------------------------------------------


def test_get_manual_for_cloned_resource_without_registered_template(
    sess: Session,
) -> None:
    _resource(sess, vmid=300, user_id=uuid.uuid4(), template_id=12345)
    assert ts.get_manual_for_cloned_resource(session=sess, vmid=300) == (None, [])


def test_get_manual_attachment_for_cloned_resource(
    sess: Session, files_dir: Path
) -> None:
    # 沒有來源範本
    with pytest.raises(NotFoundError):
        ts.get_manual_attachment_for_cloned_resource(
            session=sess, vmid=301, attachment_id=uuid.uuid4()
        )
    template = _template(sess)
    _resource(sess, vmid=302, user_id=uuid.uuid4(), template_id=PVE_VMID)
    attachment = _attachment(sess, template)
    # 範本有但附件 id 不符
    with pytest.raises(NotFoundError):
        ts.get_manual_attachment_for_cloned_resource(
            session=sess, vmid=302, attachment_id=uuid.uuid4()
        )
    # 紀錄在但實體檔不見
    with pytest.raises(NotFoundError):
        ts.get_manual_attachment_for_cloned_resource(
            session=sess, vmid=302, attachment_id=attachment.id
        )
    template_files.save_attachment(template.id, attachment.id, b"abc")
    path, found = ts.get_manual_attachment_for_cloned_resource(
        session=sess, vmid=302, attachment_id=attachment.id
    )
    assert path.read_bytes() == b"abc"
    assert found.id == attachment.id


def test_add_attachment_rejects_blank_filename(sess: Session) -> None:
    owner = _user()
    template = _template(sess, owner_id=owner.id)
    with pytest.raises(BadRequestError):
        ts.add_attachment(
            session=sess,
            user=owner,
            template_id=template.id,
            filename="\r\n",
            content_type=None,
            data=b"x",
        )


def test_add_attachment_saves_file_and_row(sess: Session, files_dir: Path) -> None:
    owner = _user()
    template = _template(sess, owner_id=owner.id)
    attachment = ts.add_attachment(
        session=sess,
        user=owner,
        template_id=template.id,
        filename="../manual.PDF",
        content_type="",
        data=b"hello",
    )
    assert attachment.filename == "manual.PDF"
    assert attachment.content_type is None
    assert attachment.size_bytes == 5
    assert (files_dir / str(template.id) / str(attachment.id)).read_bytes() == b"hello"


def test_get_attachment_for_download(sess: Session, files_dir: Path) -> None:
    owner = _user()
    template = _template(sess, owner_id=owner.id)
    other = _template(sess, pve_vmid=901, owner_id=owner.id)
    foreign = _attachment(sess, other)
    attachment = _attachment(sess, template)
    with pytest.raises(NotFoundError):
        ts.get_attachment_for_download(
            session=sess,
            user=owner,
            template_id=template.id,
            attachment_id=foreign.id,
        )
    with pytest.raises(NotFoundError):
        ts.get_attachment_for_download(
            session=sess,
            user=owner,
            template_id=template.id,
            attachment_id=attachment.id,
        )
    template_files.save_attachment(template.id, attachment.id, b"data")
    path, found = ts.get_attachment_for_download(
        session=sess, user=owner, template_id=template.id, attachment_id=attachment.id
    )
    assert path.read_bytes() == b"data"
    assert found.id == attachment.id


def test_remove_attachment(sess: Session, files_dir: Path) -> None:
    owner = _user()
    template = _template(sess, owner_id=owner.id)
    with pytest.raises(NotFoundError):
        ts.remove_attachment(
            session=sess,
            user=owner,
            template_id=template.id,
            attachment_id=uuid.uuid4(),
        )
    attachment = _attachment(sess, template)
    template_files.save_attachment(template.id, attachment.id, b"x")
    attachment_id = attachment.id
    ts.remove_attachment(
        session=sess, user=owner, template_id=template.id, attachment_id=attachment_id
    )
    assert sess.get(TemplateAttachment, attachment_id) is None
    assert template_files.attachment_path(template.id, attachment_id) is None


# ---------------------------------------------------------------------------
# 刪除前檢查
# ---------------------------------------------------------------------------


def test_prepare_delete_blocks_during_update_cycle(sess: Session) -> None:
    owner = _user()
    template = _template(sess, owner_id=owner.id, status=VMTemplateStatus.updating)
    with pytest.raises(ConflictError):
        ts._prepare_delete(sess, owner, template.id)


def test_prepare_delete_blocks_on_open_batch_jobs(sess: Session) -> None:
    owner = _user()
    template = _template(sess, owner_id=owner.id)
    for params, status in (
        ({"vm_template_id": str(template.id)}, BatchProvisionJobStatus.running),
        ({"vm_template_id": str(uuid.uuid4())}, BatchProvisionJobStatus.pending),
        ({}, BatchProvisionJobStatus.approved),
    ):
        sess.add(
            BatchProvisionJob(
                teaching_class_id=uuid.uuid4(),
                resource_type="qemu",
                hostname_prefix="lab",
                template_params=params,
                status=status,
                created_at=datetime.now(UTC),
            )
        )
    sess.commit()
    assert ts._open_batch_job_count(sess, template.id) == 1
    with pytest.raises(ConflictError):
        ts._prepare_delete(sess, owner, template.id)


# ---------------------------------------------------------------------------
# 更新循環的請求端校驗
# ---------------------------------------------------------------------------


def test_is_update_temp_vm_edge_cases(
    sess: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        ts._is_update_temp_vm(
            sess, owner_id=None, pve_vmid=PVE_VMID, temp_vmid=PVE_VMID
        )
        is False
    )
    _find(monkeypatch, {})
    assert (
        ts._is_update_temp_vm(
            sess, owner_id=None, pve_vmid=PVE_VMID, temp_vmid=TEMP_VMID
        )
        is False
    )
    _find(monkeypatch, {TEMP_VMID: {"template": 1}})
    assert (
        ts._is_update_temp_vm(
            sess, owner_id=None, pve_vmid=PVE_VMID, temp_vmid=TEMP_VMID
        )
        is False
    )
    # 範本沒有擁有者，但這個 VMID 已有資源紀錄（被別人接走）
    _resource(sess, vmid=TEMP_VMID, user_id=uuid.uuid4())
    assert (
        ts._is_update_temp_vm(
            sess, owner_id=None, pve_vmid=PVE_VMID, temp_vmid=TEMP_VMID
        )
        is False
    )


def test_prepare_update_start_requires_ready(sess: Session) -> None:
    owner = _user()
    template = _template(sess, owner_id=owner.id, status=VMTemplateStatus.failed)
    with pytest.raises(ConflictError):
        ts._prepare_update_start(sess, owner, template.id)


def test_prepare_update_finish_guards(sess: Session) -> None:
    owner = _user()
    ready = _template(sess, owner_id=owner.id)
    with pytest.raises(ConflictError):
        ts._prepare_update_finish(sess, owner, ready.id)
    no_clone = _template(
        sess,
        pve_vmid=901,
        owner_id=owner.id,
        status=VMTemplateStatus.updating,
        source_vmid=None,
    )
    with pytest.raises(ConflictError):
        ts._prepare_update_finish(sess, owner, no_clone.id)


def test_prepare_update_cancel_requires_updating(sess: Session) -> None:
    owner = _user()
    template = _template(sess, owner_id=owner.id)
    with pytest.raises(ConflictError):
        ts._prepare_update_cancel(sess, owner, template.id)


# ---------------------------------------------------------------------------
# 停機、cloud-init 重設與偵測
# ---------------------------------------------------------------------------


def _record_control(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    actions: list[str] = []
    monkeypatch.setattr(ts.proxmox_ops, "get_status", lambda *a: {"status": "running"})
    monkeypatch.setattr(
        ts.proxmox_ops,
        "control",
        lambda node, vmid, rtype, action: actions.append(action),
    )
    return actions


def test_ensure_stopped_graceful_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    actions = _record_control(monkeypatch)
    monkeypatch.setattr(ts, "_wait_until_stopped", lambda *a: True)
    ts._ensure_stopped("pve1", PVE_VMID, "qemu")
    assert actions == ["shutdown"]


def test_ensure_stopped_force_stop_then_gives_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = _record_control(monkeypatch)
    monkeypatch.setattr(ts, "_wait_until_stopped", lambda *a: False)
    with pytest.raises(RuntimeError):
        ts._ensure_stopped("pve1", PVE_VMID, "qemu")
    assert actions == ["shutdown", "stop"]


def test_reset_cloud_init_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ts.proxmox_ops, "get_status", lambda *a: {"status": "running"})
    monkeypatch.setattr(guest, "get_osinfo_qemu", lambda node, vmid: {"id": "ubuntu"})
    monkeypatch.setattr(guest, "exec_qemu", lambda node, vmid, cmd: (1, "", " boom "))
    assert ts._reset_cloud_init_state("pve1", PVE_VMID, "qemu") is False


def test_probe_guest_cloud_init(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ts._probe_guest_cloud_init("pve1", PVE_VMID, "lxc") is None

    monkeypatch.setattr(guest, "ping_qemu_agent", lambda node, vmid: False)
    assert ts._probe_guest_cloud_init("pve1", PVE_VMID, "qemu") is None

    commands: list[list[str]] = []
    monkeypatch.setattr(guest, "ping_qemu_agent", lambda node, vmid: True)
    monkeypatch.setattr(
        guest, "get_osinfo_qemu", lambda node, vmid: {"id": "MSWindows"}
    )

    def exec_ok(node: str, vmid: int, cmd: list[str]) -> tuple[int, str, str]:
        commands.append(cmd)
        return 0, "", ""

    monkeypatch.setattr(guest, "exec_qemu", exec_ok)
    assert ts._probe_guest_cloud_init("pve1", PVE_VMID, "qemu") is True
    assert commands[-1][0] == "powershell.exe"

    monkeypatch.setattr(guest, "get_osinfo_qemu", lambda node, vmid: None)
    monkeypatch.setattr(guest, "exec_qemu", lambda node, vmid, cmd: (1, "", ""))
    assert ts._probe_guest_cloud_init("pve1", PVE_VMID, "qemu") is False

    monkeypatch.setattr(guest, "exec_qemu", _raise(RuntimeError("agent gone")))
    assert ts._probe_guest_cloud_init("pve1", PVE_VMID, "qemu") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("local:vm-1-disk-0", None),
        ("local:vm-1-disk-0,size=512M", 1),
        ("local:vm-1-disk-0,size=2049M", 3),
        ("local:vm-1-disk-0,size=2T", 2048),
        ("local:vm-1-disk-0,size=32G", 32),
        ("local:vm-1-disk-0,size=10", 10),
    ],
)
def test_parse_disk_size_gb(raw: str, expected: int | None) -> None:
    assert ts._parse_disk_size_gb(raw) == expected


def test_detect_template_disk_gb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ts.proxmox_ops, "get_config", lambda *a: {"rootfs": "local:subvol,size=8G"}
    )
    assert ts._detect_template_disk_gb("pve1", 1, "lxc") == 8
    monkeypatch.setattr(
        ts.proxmox_ops, "get_config", lambda *a: {"scsi0": "local:vm,size=20G"}
    )
    assert ts._detect_template_disk_gb("pve1", 1, "qemu") == 20
    monkeypatch.setattr(ts.proxmox_ops, "get_config", lambda *a: {})
    assert ts._detect_template_disk_gb("pve1", 1, "qemu") is None
    monkeypatch.setattr(ts.proxmox_ops, "get_config", _raise(RuntimeError("down")))
    assert ts._detect_template_disk_gb("pve1", 1, "qemu") is None


def test_set_template_error(sess: Session) -> None:
    ts._set_template_error(uuid.uuid4(), "ignored")  # 範本不存在直接略過
    template = _template(sess, status=VMTemplateStatus.creating)
    ts._set_template_error(template.id, "x" * 2000, status=VMTemplateStatus.failed)
    sess.refresh(template)
    assert template.status == VMTemplateStatus.failed
    assert len(template.error_message or "") == 1000


# ---------------------------------------------------------------------------
# worker：轉範本
# ---------------------------------------------------------------------------


def _convert_payload(template: VMTemplate) -> dict[str, Any]:
    return {
        "template_id": str(template.id),
        "pve_vmid": template.pve_vmid,
        "resource_type": template.resource_type,
        "node": template.node,
    }


def test_run_convert_task_failure_marks_failed(
    sess: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _template(sess, status=VMTemplateStatus.creating)
    monkeypatch.setattr(
        ts, "_reset_cloud_init_state", _raise(RuntimeError("pve exploded"))
    )
    with pytest.raises(RuntimeError):
        ts.run_convert_task(uuid.uuid4(), _convert_payload(template))
    sess.refresh(template)
    assert template.status == VMTemplateStatus.failed
    assert template.error_message == "pve exploded"


def test_run_convert_task_success_records_detected_values(
    sess: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _template(sess, status=VMTemplateStatus.creating)
    _resource(sess, vmid=PVE_VMID, user_id=uuid.uuid4())
    monkeypatch.setattr(ts, "_reset_cloud_init_state", lambda *a: True)
    monkeypatch.setattr(ts, "_probe_guest_cloud_init", lambda *a: True)
    monkeypatch.setattr(ts, "_ensure_stopped", lambda *a: None)
    monkeypatch.setattr(ts, "_strip_hostpci_for_convert", lambda *a: None)
    protected: list[tuple] = []
    monkeypatch.setattr(
        ts.proxmox_ops, "protect_template_guest", lambda *a: protected.append(a)
    )
    monkeypatch.setattr(ts, "_detect_template_disk_gb", lambda *a: 40)
    monkeypatch.setattr(ts, "_detect_password_settable", lambda *a: True)
    monkeypatch.setattr(ip_management_service, "release_ip", lambda s, v: None)
    monkeypatch.setattr(nat_service, "remove_nat_rules_for_vmid", lambda s, v: None)
    monkeypatch.setattr(
        resource_service, "mark_linked_request_consumed", lambda **kw: None
    )

    result = ts.run_convert_task(uuid.uuid4(), _convert_payload(template))

    assert result == {"vmid": PVE_VMID, "cloud_init_reset": True}
    # 平台範本：PVE 端不轉換，只把母機鎖定
    assert protected == [("pve1", PVE_VMID, "qemu")]
    sess.expire_all()
    stored = sess.get(VMTemplate, template.id)
    assert stored.status == VMTemplateStatus.ready
    assert stored.default_disk == 40
    assert stored.password_settable is True
    assert sess.get(Resource, PVE_VMID) is None


# ---------------------------------------------------------------------------
# worker：刪除範本
# ---------------------------------------------------------------------------


def _delete_payload(template: VMTemplate) -> dict[str, Any]:
    return {
        "template_id": str(template.id),
        "pve_vmid": template.pve_vmid,
        "resource_type": "lxc",
        "node": "pve1",
    }


def test_run_delete_task_removes_pve_template_and_files(
    sess: Session, files_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _template(sess)
    attachment = _attachment(sess, template)
    template_files.save_attachment(template.id, attachment.id, b"x")
    calls: list[tuple] = []
    monkeypatch.setattr(ts.proxmox_ops, "find_vm_template", lambda vmid: {"vmid": vmid})
    monkeypatch.setattr(
        ts.proxmox_ops,
        "unprotect_template_guest",
        lambda *a: calls.append(("unprotect", *a)),
    )
    monkeypatch.setattr(
        ts.proxmox_ops, "delete_resource", lambda *a: calls.append(("delete", *a))
    )

    assert ts.run_delete_task(uuid.uuid4(), _delete_payload(template)) == {
        "vmid": PVE_VMID
    }

    # 平台範本的母機開著 protection，要先解除才刪得掉
    assert calls == [
        ("unprotect", "pve1", PVE_VMID, "lxc"),
        ("delete", "pve1", PVE_VMID, "lxc"),
    ]
    sess.expire_all()
    assert sess.get(VMTemplate, template.id).status == VMTemplateStatus.deleted
    assert sess.exec(select(TemplateAttachment)).all() == []
    assert not (files_dir / str(template.id)).exists()


def test_run_delete_task_skips_pve_when_template_already_gone(
    sess: Session, files_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _template(sess)
    monkeypatch.setattr(
        ts.proxmox_ops, "find_vm_template", _raise(NotFoundError("gone"))
    )
    monkeypatch.setattr(
        ts.proxmox_ops, "delete_resource", _raise(AssertionError("must not delete"))
    )
    ts.run_delete_task(uuid.uuid4(), _delete_payload(template))
    sess.expire_all()
    assert sess.get(VMTemplate, template.id).status == VMTemplateStatus.deleted


def test_run_delete_task_failure_keeps_status(
    sess: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _template(sess)
    monkeypatch.setattr(ts.proxmox_ops, "find_vm_template", lambda vmid: {})
    monkeypatch.setattr(ts.proxmox_ops, "unprotect_template_guest", lambda *a: None)
    monkeypatch.setattr(
        ts.proxmox_ops, "delete_resource", _raise(RuntimeError("locked"))
    )
    with pytest.raises(RuntimeError):
        ts.run_delete_task(uuid.uuid4(), _delete_payload(template))
    sess.expire_all()
    stored = sess.get(VMTemplate, template.id)
    assert stored.status == VMTemplateStatus.ready
    assert stored.error_message == "locked"


# ---------------------------------------------------------------------------
# worker：更新循環 clone／convert
# ---------------------------------------------------------------------------


@contextmanager
def _no_lock():
    yield


def _stub_update_clone(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    clones: list[tuple] = []
    monkeypatch.setattr(ts.proxmox_ops, "vmid_allocation_lock", _no_lock)
    monkeypatch.setattr(
        provisioning_service, "allocate_free_vmid", lambda session: TEMP_VMID
    )
    monkeypatch.setattr(
        ts,
        "get_proxmox_settings_for_node",
        lambda node: SimpleNamespace(pool_name="pool"),
    )
    monkeypatch.setattr(ts, "get_connection_id_for_node", lambda node: 7)
    monkeypatch.setattr(
        ts.proxmox_ops,
        "clone_vm",
        lambda node, vmid, **kw: clones.append(("qemu", node, vmid, kw)),
    )
    monkeypatch.setattr(
        ts.proxmox_ops,
        "clone_lxc",
        lambda node, vmid, **kw: clones.append(("lxc", node, vmid, kw)),
    )
    return clones


def _update_clone_payload(template: VMTemplate, resource_type: str) -> dict[str, Any]:
    return {
        "template_id": str(template.id),
        "pve_vmid": template.pve_vmid,
        "resource_type": resource_type,
        "node": "pve1",
    }


def test_run_update_clone_task_qemu_registers_owner_resource(
    sess: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id = uuid.uuid4()
    template = _template(
        sess, owner_id=owner_id, status=VMTemplateStatus.updating, error_message="old"
    )
    clones = _stub_update_clone(monkeypatch)

    result = ts.run_update_clone_task(
        uuid.uuid4(), _update_clone_payload(template, "qemu")
    )

    assert result == {"vmid": TEMP_VMID}
    assert clones == [
        (
            "qemu",
            "pve1",
            PVE_VMID,
            {
                "newid": TEMP_VMID,
                "name": f"tpl-{PVE_VMID}-edit",
                "full": 1,
                "pool": "pool",
            },
        )
    ]
    sess.expire_all()
    stored = sess.get(VMTemplate, template.id)
    assert stored.source_vmid == TEMP_VMID
    assert stored.error_message is None
    temp = sess.get(Resource, TEMP_VMID)
    assert temp.user_id == owner_id
    assert temp.connection_id == 7
    assert temp.environment_type == ts.UPDATE_TEMP_ENVIRONMENT_TYPE


def test_run_update_convert_task_tolerates_old_delete_failure(
    sess: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id = uuid.uuid4()
    template = _template(
        sess,
        owner_id=owner_id,
        status=VMTemplateStatus.updating,
        source_vmid=TEMP_VMID,
        password_settable=True,
    )
    _resource(
        sess,
        vmid=TEMP_VMID,
        user_id=owner_id,
        environment_type=ts.UPDATE_TEMP_ENVIRONMENT_TYPE,
    )
    monkeypatch.setattr(ts, "_is_update_temp_vm", lambda *a, **kw: True)
    monkeypatch.setattr(ts, "_reset_cloud_init_state", lambda *a: False)
    monkeypatch.setattr(ts, "_probe_guest_cloud_init", lambda *a: False)
    monkeypatch.setattr(ts, "_ensure_stopped", lambda *a: None)
    monkeypatch.setattr(ts, "_strip_hostpci_for_convert", lambda *a: None)
    monkeypatch.setattr(ts.proxmox_ops, "protect_template_guest", lambda *a: None)
    monkeypatch.setattr(ts.proxmox_ops, "unprotect_template_guest", lambda *a: None)
    monkeypatch.setattr(
        ts.proxmox_ops, "delete_resource", _raise(RuntimeError("has linked clones"))
    )
    monkeypatch.setattr(ts, "_detect_template_disk_gb", lambda *a: 50)
    monkeypatch.setattr(ts, "_detect_password_settable", lambda *a: False)

    result = ts.run_update_convert_task(
        uuid.uuid4(),
        {
            "template_id": str(template.id),
            "old_pve_vmid": PVE_VMID,
            "temp_vmid": TEMP_VMID,
            "resource_type": "qemu",
            "node": "pve1",
        },
    )

    assert result["vmid"] == TEMP_VMID
    assert "has linked clones" in result["warning"]
    sess.expire_all()
    stored = sess.get(VMTemplate, template.id)
    assert stored.pve_vmid == TEMP_VMID
    assert stored.version == 2
    assert stored.default_disk == 50
    assert stored.password_settable is False
    assert "has linked clones" in (stored.error_message or "")
    assert sess.get(Resource, TEMP_VMID) is None
