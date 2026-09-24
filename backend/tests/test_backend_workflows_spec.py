"""Split from tests/test_backend_workflows.py: spec-change requests & resource deletion.

Shared fixtures live in tests.ai.teacher_judge.helpers.
"""

import random
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine, select

from app.exceptions import (
    BadRequestError,
    ConflictError,
    PermissionDeniedError,
    ProxmoxError,
)
from app.models import (
    Resource,
    SpecChangeRequest,
    SpecChangeRequestStatus,
    SpecChangeType,
    SubnetConfig,
    User,
    UserRole,
)
from app.repositories import spec_change_request as spec_change_request_repo
from app.repositories import user as user_repo
from app.schemas import (
    SpecChangeRequestCreate,
    SpecChangeRequestReview,
    UserCreate,
    VMCreateRequest,
)
from app.services.proxmox import provisioning_service
from app.services.vm import (
    spec_change_service,
)


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _create_user(
    session: Session,
    *,
    is_superuser: bool = False,
    role: UserRole | None = None,
) -> User:
    user = user_repo.create_user(
        session=session,
        user_create=UserCreate(
            email=f"{'admin' if is_superuser else 'user'}-{datetime.now(timezone.utc).timestamp()}@example.com",
            password="strongpass123",
            role=role or (UserRole.admin if is_superuser else UserRole.student),
            is_superuser=is_superuser,
        ),
    )
    session.commit()
    session.refresh(user)
    return user


def _seed_subnet_config(session: Session) -> None:
    """Seed the singleton SubnetConfig so provisioning_service.create_vm can
    run its IP-management steps in tests that don't exercise IP allocation."""
    if session.get(SubnetConfig, 1) is not None:
        return
    session.add(
        SubnetConfig(
            id=1,
            cidr="10.0.0.0/24",
            gateway="10.0.0.1",
            bridge_name="vmbr0",
            gateway_vm_ip="10.0.0.2",
        )
    )
    session.commit()


def _fresh_vmid() -> int:
    """共用測試 DB 不清資料；同一 vmid 只能有一張處理中的規格申請，所以每次換號。"""
    return random.randint(700_000, 999_999)


def _owned_resource(db: Session, *, user: User, vmid: int) -> Resource:
    resource = Resource(
        vmid=vmid,
        user_id=user.id,
        environment_type="Owned VM",
        created_at=datetime.now(timezone.utc),
    )
    db.add(resource)
    db.commit()
    return resource


def _spec_request(
    db: Session,
    *,
    user: User,
    vmid: int,
    status: SpecChangeRequestStatus = SpecChangeRequestStatus.pending,
    **overrides,
) -> SpecChangeRequest:
    fields = {
        "vmid": vmid,
        "resource_vmid": vmid if db.get(Resource, vmid) is not None else None,
        "user_id": user.id,
        "change_type": SpecChangeType.cpu,
        "reason": "Need more CPU for workload spikes",
        "current_cpu": 2,
        "requested_cpu": 4,
        "status": status,
        "created_at": datetime.now(timezone.utc),
    }
    fields.update(overrides)
    request = SpecChangeRequest(**fields)
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def _fake_spec_proxmox(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: str = "stopped",
    specs: dict | None = None,
    fail_update: bool = False,
) -> SimpleNamespace:
    calls: list[str] = []

    def update_config(*_args, **_kwargs):
        calls.append("update_config")
        if fail_update:
            raise ProxmoxError("apply failed")

    fake = SimpleNamespace(
        calls=calls,
        find_resource=lambda vmid: {
            "node": "node-a",
            "type": "qemu",
            "vmid": vmid,
            "name": "vm-a",
        },
        get_current_specs=lambda *_a, **_k: (
            specs or {"cpu": 2, "memory": 2048, "disk": 20}
        ),
        get_status=lambda *_a, **_k: {"status": status},
        control=lambda *_a, **_k: calls.append("control"),
        update_config=update_config,
        resize_disk=lambda *_a, **_k: calls.append("resize_disk"),
        list_all_resources=lambda: [],
    )
    monkeypatch.setattr(spec_change_service, "proxmox_service", fake)
    return fake


def _reload_spec_request(db: Session, request_id: uuid.UUID) -> SpecChangeRequest:
    db.expire_all()
    refreshed = db.exec(
        select(SpecChangeRequest).where(SpecChangeRequest.id == request_id)
    ).first()
    assert refreshed is not None
    return refreshed


def test_spec_change_review_approves_without_applying(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """核准只改狀態並用 Proxmox 實際值刷新快照，不寫設定（等申請人套用）。"""
    user = _create_user(db)
    reviewer = _create_user(db, is_superuser=True)
    vmid = _fresh_vmid()
    _owned_resource(db, user=user, vmid=vmid)
    request = _spec_request(db, user=user, vmid=vmid)
    fake = _fake_spec_proxmox(
        monkeypatch, specs={"cpu": 3, "memory": 2048, "disk": 20}, fail_update=True
    )

    result = spec_change_service.review(
        session=db,
        request_id=request.id,
        review_data=SpecChangeRequestReview(status=SpecChangeRequestStatus.approved),
        reviewer=reviewer,
    )

    assert fake.calls == []  # never touched Proxmox config
    assert result.status == SpecChangeRequestStatus.approved
    assert result.apply_status == "ready"
    assert result.applied_at is None
    assert result.current_cpu == 3  # snapshot refreshed from live config
    refreshed = _reload_spec_request(db, request.id)
    assert refreshed.reviewer_id == reviewer.id
    assert refreshed.applied_at is None


def test_spec_change_review_refuses_approval_when_resource_is_gone(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """resource_vmid 已 SET NULL（機器刪掉）就不能核准：VMID 會被新機器回收。"""
    user = _create_user(db)
    reviewer = _create_user(db, is_superuser=True)
    request = _spec_request(db, user=user, vmid=_fresh_vmid())  # no Resource row
    assert request.resource_vmid is None
    _fake_spec_proxmox(monkeypatch)

    with pytest.raises(BadRequestError):
        spec_change_service.review(
            session=db,
            request_id=request.id,
            review_data=SpecChangeRequestReview(
                status=SpecChangeRequestStatus.approved
            ),
            reviewer=reviewer,
        )

    refreshed = _reload_spec_request(db, request.id)
    assert refreshed.status == SpecChangeRequestStatus.pending
    assert refreshed.reviewer_id is None


def test_spec_change_review_schema_only_accepts_a_decision() -> None:
    with pytest.raises(ValidationError):
        SpecChangeRequestReview(status=SpecChangeRequestStatus.pending)
    with pytest.raises(ValidationError):
        SpecChangeRequestReview(status=SpecChangeRequestStatus.cancelled)


def test_spec_change_create_blocks_second_open_request_for_same_vmid(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = _create_user(db, is_superuser=True)
    vmid = _fresh_vmid()
    _fake_spec_proxmox(monkeypatch)
    payload = SpecChangeRequestCreate(
        vmid=vmid,
        change_type=SpecChangeType.combined,
        reason="Need more CPU for workload spikes",
        requested_cpu=4,
    )

    first = spec_change_service.create(session=db, request_in=payload, user=admin)
    assert first.status == SpecChangeRequestStatus.pending

    with pytest.raises(ConflictError):
        spec_change_service.create(session=db, request_in=payload, user=admin)

    # once the first one is cancelled a new request is allowed again
    spec_change_service.cancel(session=db, request_id=first.id, user=admin)
    second = spec_change_service.create(session=db, request_in=payload, user=admin)
    assert second.id != first.id


def test_spec_change_apply_submits_background_task(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    vmid = _fresh_vmid()
    _owned_resource(db, user=user, vmid=vmid)
    request = _spec_request(
        db, user=user, vmid=vmid, status=SpecChangeRequestStatus.approved
    )
    _fake_spec_proxmox(monkeypatch)
    submitted: list[dict] = []

    def fake_submit_sync(func, *args, **kwargs):
        submitted.append({"func": func, "args": args, "task_id": kwargs.get("task_id")})
        return kwargs["task_id"]

    monkeypatch.setattr(
        spec_change_service.background_tasks, "submit_sync", fake_submit_sync
    )
    monkeypatch.setattr(
        spec_change_service.background_tasks, "is_active", lambda _tid: bool(submitted)
    )

    accepted = spec_change_service.apply(session=db, request_id=request.id, user=user)

    assert submitted and submitted[0]["func"] is spec_change_service._run_apply
    assert accepted.task_id == f"spec-apply-{request.id}"
    assert accepted.request.apply_status == "applying"
    refreshed = _reload_spec_request(db, request.id)
    assert refreshed.apply_started_at is not None
    assert refreshed.applied_at is None

    # a second click while the task is active is refused
    with pytest.raises(ConflictError):
        spec_change_service.apply(session=db, request_id=request.id, user=user)


def test_spec_change_apply_refuses_non_owner(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _create_user(db)
    stranger = _create_user(db)
    vmid = _fresh_vmid()
    _owned_resource(db, user=owner, vmid=vmid)
    request = _spec_request(
        db, user=owner, vmid=vmid, status=SpecChangeRequestStatus.approved
    )
    _fake_spec_proxmox(monkeypatch)

    with pytest.raises(PermissionDeniedError):
        spec_change_service.apply(session=db, request_id=request.id, user=stranger)


def test_spec_change_run_apply_records_failure_and_keeps_request_approved(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """套用失敗：applied_at 不能寫、狀態留在 approved、錯誤讓申請人看得到並可重試。"""
    user = _create_user(db)
    vmid = _fresh_vmid()
    _owned_resource(db, user=user, vmid=vmid)
    request = _spec_request(
        db,
        user=user,
        vmid=vmid,
        status=SpecChangeRequestStatus.approved,
        apply_started_at=datetime.now(timezone.utc),
    )
    _fake_spec_proxmox(monkeypatch, fail_update=True)
    # 背景任務用獨立 session 寫回結果；測試把它指到同一個 SQLite engine
    monkeypatch.setattr(
        spec_change_service, "_open_session", lambda: Session(db.get_bind())
    )

    with pytest.raises(ProxmoxError):
        spec_change_service._run_apply(
            request.id,
            spec_change_service._snapshot(request),
            {"node": "node-a", "type": "qemu", "vmid": vmid},
            user.id,
        )

    refreshed = _reload_spec_request(db, request.id)
    assert refreshed.status == SpecChangeRequestStatus.approved
    assert refreshed.applied_at is None
    assert refreshed.apply_error is not None and "apply failed" in refreshed.apply_error
    assert spec_change_service._apply_status(refreshed) == "failed"


def test_spec_change_run_apply_marks_applied(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    vmid = _fresh_vmid()
    _owned_resource(db, user=user, vmid=vmid)
    request = _spec_request(
        db,
        user=user,
        vmid=vmid,
        status=SpecChangeRequestStatus.approved,
        apply_started_at=datetime.now(timezone.utc),
    )
    fake = _fake_spec_proxmox(monkeypatch, status="stopped")
    monkeypatch.setattr(
        spec_change_service, "_open_session", lambda: Session(db.get_bind())
    )

    spec_change_service._run_apply(
        request.id,
        spec_change_service._snapshot(request),
        {"node": "node-a", "type": "qemu", "vmid": vmid},
        user.id,
    )

    assert fake.calls == ["update_config"]  # stopped machine: no power actions
    refreshed = _reload_spec_request(db, request.id)
    assert refreshed.applied_at is not None
    assert refreshed.apply_error is None
    assert spec_change_service._apply_status(refreshed) == "applied"


def test_spec_change_cancel_by_requester(db: Session) -> None:
    user = _create_user(db)
    request = _spec_request(db, user=user, vmid=_fresh_vmid())

    result = spec_change_service.cancel(session=db, request_id=request.id, user=user)

    assert result.status == SpecChangeRequestStatus.cancelled
    assert result.review_comment == "Cancelled by requester"

    applied = _spec_request(
        db,
        user=user,
        vmid=_fresh_vmid(),
        status=SpecChangeRequestStatus.approved,
        applied_at=datetime.now(timezone.utc),
    )
    with pytest.raises(BadRequestError):
        spec_change_service.cancel(session=db, request_id=applied.id, user=user)


def test_resource_deletion_cancels_open_spec_change_requests(db: Session) -> None:
    user = _create_user(db)
    vmid = _fresh_vmid()
    pending = _spec_request(db, user=user, vmid=vmid)
    awaiting_apply = _spec_request(
        db, user=user, vmid=vmid, status=SpecChangeRequestStatus.approved
    )
    already_applied = _spec_request(
        db,
        user=user,
        vmid=vmid,
        status=SpecChangeRequestStatus.approved,
        applied_at=datetime.now(timezone.utc),
    )

    cancelled = spec_change_request_repo.cancel_open_spec_change_requests_for_vmid(
        session=db, vmid=vmid, comment="Resource deleted by user"
    )

    assert cancelled == 2
    assert (
        _reload_spec_request(db, pending.id).status == SpecChangeRequestStatus.cancelled
    )
    assert (
        _reload_spec_request(db, awaiting_apply.id).status
        == SpecChangeRequestStatus.cancelled
    )
    assert (
        _reload_spec_request(db, already_applied.id).status
        == SpecChangeRequestStatus.approved
    )


def test_create_vm_uses_template_node_and_normalizes_disk_size(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    _seed_subnet_config(db)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.next_vmid",
        lambda: 900,
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.find_vm_template",
        lambda template_id: {"vmid": template_id, "node": "node-b"},
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.resolve_target_storage",
        lambda node, requested_storage, required_content: requested_storage,
    )

    def _clone_vm(node, template_id, **clone_config):
        captured["clone"] = (node, template_id, clone_config)
        return "UPID:clone"

    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.clone_vm",
        _clone_vm,
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.update_config",
        lambda node, vmid, resource_type, **config: captured.setdefault(
            "update", (node, vmid, resource_type, config)
        ),
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.resize_disk",
        lambda node, vmid, resource_type, disk, size: captured.setdefault(
            "resize", (node, vmid, resource_type, disk, size)
        ),
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.control",
        lambda node, vmid, resource_type, action: captured.setdefault(
            "control", (node, vmid, resource_type, action)
        ),
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.firewall_service.setup_default_rules",
        lambda node, vmid, resource_type: captured.setdefault(
            "firewall", (node, vmid, resource_type)
        ),
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.audit_service.log_action",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.get_proxmox_settings_for_node",
        lambda _node: SimpleNamespace(pool_name="SkyLab"),
    )

    result = provisioning_service.create_vm(
        session=db,
        user_id=user.id,
        vm_data=VMCreateRequest(
            hostname="template-node-check",
            template_id=777,
            username="student",
            password="strongpass123",
            cores=4,
            memory=4096,
            disk_size=40,
            storage="fast-ssd",
            environment_type="Node Aware",
            start=True,
        ),
    )

    db.expire_all()
    saved = db.exec(select(Resource).where(Resource.vmid == 900)).first()
    assert saved is not None
    assert captured["clone"] == (
        "node-b",
        777,
        {
            "newid": 900,
            "name": "template-node-check",
            "full": 1,
            "storage": "fast-ssd",
            "pool": "SkyLab",
        },
    )
    assert captured["resize"] == ("node-b", 900, "qemu", "scsi0", "40G")
    assert captured["control"] == ("node-b", 900, "qemu", "start")
    assert saved.environment_type == "Node Aware"
    assert result.vmid == 900


def test_create_vm_falls_back_when_requested_storage_is_unavailable(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    _seed_subnet_config(db)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.next_vmid",
        lambda: 901,
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.find_vm_template",
        lambda template_id: {"vmid": template_id, "node": "node-c"},
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.resolve_target_storage",
        lambda node, requested_storage, required_content: "fast-ssd",
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.clone_vm",
        lambda node, template_id, **clone_config: (
            captured.setdefault("clone", (node, template_id, clone_config)),
            "UPID:clone",
        )[1],
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.update_config",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.resize_disk",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.control",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.firewall_service.setup_default_rules",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.audit_service.log_action",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.get_proxmox_settings_for_node",
        lambda _node: SimpleNamespace(pool_name="SkyLab"),
    )

    provisioning_service.create_vm(
        session=db,
        user_id=user.id,
        vm_data=VMCreateRequest(
            hostname="storage-fallback",
            template_id=778,
            username="student",
            password="strongpass123",
            cores=2,
            memory=2048,
            disk_size=20,
            storage="local-lvm",
            environment_type="Fallback Test",
            start=True,
        ),
    )

    assert captured["clone"] == (
        "node-c",
        778,
        {
            "newid": 901,
            "name": "storage-fallback",
            "full": 1,
            "storage": "fast-ssd",
            "pool": "SkyLab",
        },
    )
