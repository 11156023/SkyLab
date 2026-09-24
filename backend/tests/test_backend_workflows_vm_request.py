"""Split from tests/test_backend_workflows.py: VM request lifecycle (create/review/admin/legacy templates)."""

import random
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.security import encrypt_value
from app.domain.placement.schemas import NodeCapacity, PlacementRequest
from app.exceptions import (
    BadRequestError,
    ConflictError,
    PermissionDeniedError,
    ProvisioningError,
    ProxmoxError,
)
from app.infrastructure.proxmox import operations as proxmox_service
from app.models import (
    ProxmoxConfig,
    ProxmoxNode,
    ProxmoxStorage,
    Resource,
    SpecChangeRequest,
    SpecChangeRequestStatus,
    SpecChangeType,
    SubnetConfig,
    User,
    UserRole,
    VMRequest,
    VMRequestStatus,
    VMTemplate,
    VMTemplateStatus,
    VMTemplateVisibility,
)
from app.repositories import spec_change_request as spec_change_request_repo
from app.repositories import user as user_repo
from app.schemas import (
    SpecChangeRequestCreate,
    SpecChangeRequestReview,
    UserCreate,
    VMCreateRequest,
    VMRequestCreate,
    VMRequestReview,
)
from app.services.proxmox import gpu_service, provisioning_service
from app.services.user import user_service
from app.services.vm import (
    spec_change_service,
    vm_request_placement_service,
    vm_request_service,
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


def _seed_lxc_template(
    session: Session,
    *,
    pve_vmid: int = 9100,
    status: VMTemplateStatus = VMTemplateStatus.ready,
) -> VMTemplate:
    template = VMTemplate(
        pve_vmid=pve_vmid,
        name=f"lab-template-{pve_vmid}",
        node="pve-a",
        resource_type="lxc",
        status=status,
        visibility=VMTemplateVisibility.global_,
    )
    session.add(template)
    session.commit()
    return template


def test_vm_request_create_preserves_environment_type(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        "app.services.vm.vm_request_service.vm_request_availability_service.validate_request_window",
        lambda **kwargs: None,
    )
    request_in = VMRequestCreate(
        reason="Need a custom environment for backend testing",
        resource_type="vm",
        hostname="env-check",
        cores=2,
        memory=2048,
        password="strongpass123",
        storage="fast-ssd",
        environment_type="ML Lab",
        template_id=9000,
        disk_size=32,
        username="student",
        start_at=now + timedelta(hours=1),
        end_at=now + timedelta(hours=3),
    )

    result = vm_request_service.create(session=db, request_in=request_in, user=user)

    db.expire_all()
    saved = db.exec(select(VMRequest).where(VMRequest.id == result.id)).first()
    assert saved is not None
    assert result.environment_type == "ML Lab"
    assert saved.environment_type == "ML Lab"
    assert saved.storage == "fast-ssd"


def test_admin_scheduled_request_stays_pending(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = _create_user(db, role=UserRole.admin)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        "app.services.vm.vm_request_service.vm_request_availability_service.validate_request_window",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.vm.vm_request_service._approve_and_place",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("should not auto approve")
        ),
    )

    request_in = VMRequestCreate(
        reason="Need a scheduled VM for a reviewed admin request",
        resource_type="vm",
        hostname="admin-scheduled-review",
        cores=2,
        memory=2048,
        password="strongpass123",
        storage="fast-ssd",
        template_id=9000,
        disk_size=32,
        username="admin",
        mode="scheduled",
        start_at=now + timedelta(hours=1),
        end_at=now + timedelta(hours=3),
    )

    result = vm_request_service.create(session=db, request_in=request_in, user=admin)

    db.expire_all()
    saved = db.exec(select(VMRequest).where(VMRequest.id == result.id)).first()
    assert saved is not None
    assert saved.status == VMRequestStatus.pending
    assert saved.reviewer_id is None
    assert saved.assigned_node is None


def test_admin_immediate_request_is_auto_approved(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = _create_user(db, role=UserRole.admin)
    calls: list[uuid.UUID] = []

    def fake_approve_and_place(
        *, session: Session, db_request: VMRequest, reviewer_id: uuid.UUID
    ):
        db_request.status = VMRequestStatus.approved
        db_request.reviewer_id = reviewer_id
        db_request.assigned_node = "pve-a"
        db_request.desired_node = "pve-a"
        session.add(db_request)
        session.flush()
        return None

    monkeypatch.setattr(
        "app.services.vm.vm_request_service._approve_and_place",
        fake_approve_and_place,
    )
    monkeypatch.setattr(
        "app.services.scheduling.provision_pool.submit_provision",
        lambda _session, *, request_id, **_kwargs: calls.append(request_id),
    )


    request_in = VMRequestCreate(
        reason="Need an immediate VM for admin maintenance",
        resource_type="vm",
        hostname="admin-immediate",
        cores=2,
        memory=2048,
        password="strongpass123",
        storage="fast-ssd",
        template_id=9000,
        disk_size=32,
        username="admin",
        mode="immediate",
    )

    result = vm_request_service.create(session=db, request_in=request_in, user=admin)

    db.expire_all()
    saved = db.exec(select(VMRequest).where(VMRequest.id == result.id)).first()
    assert saved is not None
    assert saved.status == VMRequestStatus.approved
    assert saved.reviewer_id == admin.id
    assert saved.start_at is not None
    assert calls == [saved.id]


def test_vm_request_create_rejects_unavailable_window(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    now = datetime.now(timezone.utc)
    request_in = VMRequestCreate(
        reason="Need a custom environment for backend testing",
        resource_type="vm",
        hostname="env-check-blocked",
        cores=2,
        memory=2048,
        password="strongpass123",
        storage="fast-ssd",
        environment_type="ML Lab",
        template_id=9000,
        disk_size=32,
        username="student",
        start_at=now + timedelta(hours=1),
        end_at=now + timedelta(hours=3),
    )

    monkeypatch.setattr(
        "app.services.vm.vm_request_service.vm_request_availability_service.validate_request_window",
        lambda **kwargs: (_ for _ in ()).throw(
            BadRequestError("No node is available for the requested time window.")
        ),
    )

    with pytest.raises(BadRequestError):
        vm_request_service.create(session=db, request_in=request_in, user=user)


def test_legacy_quick_template_mode_is_rejected(db: Session) -> None:
    """舊的自助模式會自動核准，已停用；學生只能走快速練習或一般申請。"""
    user = _create_user(db, role=UserRole.student)
    _seed_lxc_template(db, pve_vmid=9100)
    request_in = VMRequestCreate(
        reason="Need a short PostgreSQL lab environment",
        resource_type="lxc",
        hostname="quick-pg",
        cores=2,
        memory=2048,
        password="strongpass123",
        template_id=9100,
        rootfs_size=16,
        mode="quick_template",
    )

    with pytest.raises(BadRequestError, match="已停用"):
        vm_request_service.create(session=db, request_in=request_in, user=user)


def test_legacy_quick_template_mode_is_rejected_without_a_template(
    db: Session,
) -> None:
    """停用後不論帶不帶範本都不接受，錯誤訊息一致。"""
    user = _create_user(db, role=UserRole.student)
    request_in = VMRequestCreate(
        reason="Need a short lab without a template",
        resource_type="lxc",
        hostname="quick-bad",
        cores=2,
        memory=2048,
        password="strongpass123",
        ostemplate="local:vztmpl/ubuntu-24.04.tar.zst",
        rootfs_size=16,
        mode="quick_template",
    )

    with pytest.raises(BadRequestError):
        vm_request_service.create(session=db, request_in=request_in, user=user)


def test_legacy_quick_template_mode_is_rejected_for_unready_templates(
    db: Session,
) -> None:
    user = _create_user(db, role=UserRole.student)
    _seed_lxc_template(db, pve_vmid=9200, status=VMTemplateStatus.creating)
    request_in = VMRequestCreate(
        reason="Need a short lab from an unfinished template",
        resource_type="lxc",
        hostname="quick-bad",
        cores=2,
        memory=2048,
        password="strongpass123",
        template_id=9200,
        rootfs_size=16,
        mode="quick_template",
    )

    with pytest.raises(BadRequestError):
        vm_request_service.create(session=db, request_in=request_in, user=user)


def test_quick_template_approval_does_not_rebuild_existing_reservations(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    reviewer_id = uuid.uuid4()
    existing = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="research reservation should stay assigned",
        request_kind="research",
        resource_type="lxc",
        hostname="research-reservation",
        cores=4,
        memory=8192,
        password="encrypted",
        storage="local-lvm",
        environment_type="Research",
        status=VMRequestStatus.approved,
        start_at=now - timedelta(minutes=10),
        end_at=now + timedelta(hours=4),
        assigned_node="research-node",
        desired_node="research-node",
        created_at=now - timedelta(hours=1),
    )
    quick = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="quick template",
        request_kind="quick_template",
        resource_type="lxc",
        hostname="quick-template",
        cores=1,
        memory=1024,
        password="encrypted",
        storage="local-lvm",
        environment_type="Quick",
        rootfs_size=8,
        status=VMRequestStatus.pending,
        start_at=now,
        end_at=now + timedelta(hours=3),
        created_at=now,
    )
    db.add(existing)
    db.add(quick)
    db.commit()
    db.refresh(existing)
    db.refresh(quick)

    def _fail_rebuild(**kwargs):
        raise AssertionError("quick templates must not rebuild existing reservations")

    def _fake_select_reserved_target_node(*, db_request, reserved_requests, **kwargs):
        assert db_request.id == quick.id
        assert [item.assigned_node for item in reserved_requests] == ["research-node"]
        return SimpleNamespace(
            node="quick-node",
            strategy="priority_dominant_share",
            plan=SimpleNamespace(feasible=True),
        )

    monkeypatch.setattr(
        "app.services.vm.vm_request_service.vm_request_placement_service.rebuild_reserved_assignments",
        _fail_rebuild,
    )
    monkeypatch.setattr(
        "app.services.vm.vm_request_service.vm_request_placement_service.select_reserved_target_node",
        _fake_select_reserved_target_node,
    )

    selection = vm_request_service._approve_and_place(
        session=db,
        db_request=quick,
        reviewer_id=reviewer_id,
    )

    assert selection.node == "quick-node"
    assert quick.status == VMRequestStatus.approved
    assert quick.assigned_node == "quick-node"
    assert existing.assigned_node == "research-node"


def test_vm_request_review_rolls_back_and_cleans_up_on_failure(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    reviewer = _create_user(db, is_superuser=True)
    now = datetime.now(timezone.utc)
    request = VMRequest(
        user_id=user.id,
        reason="Need a VM for rollback coverage",
        resource_type="vm",
        hostname="rollback-vm",
        cores=2,
        memory=2048,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Rollback Test",
        template_id=123,
        disk_size=20,
        username="student",
        status=VMRequestStatus.pending,
        start_at=now + timedelta(hours=2),
        end_at=now + timedelta(hours=4),
        created_at=now,
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    monkeypatch.setattr(
        "app.services.vm.vm_request_service.vm_request_placement_service.rebuild_reserved_assignments",
        lambda **kwargs: {
            request.id: SimpleNamespace(
                node="pve-a",
                strategy="priority_dominant_share",
                plan=SimpleNamespace(feasible=True),
            )
        },
    )

    def _raise_audit(*args, **kwargs):
        raise RuntimeError("audit failure")

    monkeypatch.setattr(
        "app.services.vm.vm_request_service.audit_service.log_action",
        _raise_audit,
    )

    with pytest.raises(ProvisioningError):
        vm_request_service.review(
            session=db,
            request_id=request.id,
            review_data=VMRequestReview(status=VMRequestStatus.approved),
            reviewer=reviewer,
        )

    db.expire_all()
    refreshed = db.exec(select(VMRequest).where(VMRequest.id == request.id)).first()
    assert refreshed is not None
    assert refreshed.status == VMRequestStatus.pending
    assert refreshed.vmid is None
    assert refreshed.reviewer_id is None
    assert refreshed.assigned_node is None
    assert refreshed.placement_strategy_used is None


def test_vm_request_review_locks_overlapping_requests(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    reviewer = _create_user(db, is_superuser=True)
    now = datetime.now(timezone.utc)
    request = VMRequest(
        user_id=user.id,
        reason="Need a VM for scheduled class usage",
        resource_type="vm",
        hostname="lock-window-vm",
        cores=2,
        memory=2048,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Lock Window Test",
        template_id=123,
        disk_size=20,
        username="student",
        status=VMRequestStatus.pending,
        start_at=now + timedelta(hours=2),
        end_at=now + timedelta(hours=4),
        created_at=now,
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    captured: dict[str, datetime] = {}

    def _lock_window(**kwargs):
        captured["start_at"] = kwargs["window_start"]
        captured["end_at"] = kwargs["window_end"]
        return [request]

    monkeypatch.setattr(
        "app.services.vm.vm_request_service.vm_request_repo.lock_overlapping_vm_requests_for_window",
        _lock_window,
    )
    monkeypatch.setattr(
        "app.services.vm.vm_request_service.audit_service.log_action",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.vm.vm_request_service.vm_request_placement_service.rebuild_reserved_assignments",
        lambda **kwargs: {
            request.id: SimpleNamespace(
                node="pve-a",
                strategy="priority_dominant_share",
                plan=SimpleNamespace(feasible=True),
            )
        },
    )

    vm_request_service.review(
        session=db,
        request_id=request.id,
        review_data=VMRequestReview(status=VMRequestStatus.approved),
        reviewer=reviewer,
    )

    assert captured["start_at"] == request.start_at.replace(tzinfo=timezone.utc)
    assert captured["end_at"] == request.end_at.replace(tzinfo=timezone.utc)


def test_vm_request_review_assigns_reserved_node(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    reviewer = _create_user(db, is_superuser=True)
    now = datetime.now(timezone.utc)
    request = VMRequest(
        user_id=user.id,
        reason="Need a VM for scheduled class usage",
        resource_type="vm",
        hostname="reserved-node-vm",
        cores=2,
        memory=2048,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Reserved Node Test",
        template_id=123,
        disk_size=20,
        username="student",
        status=VMRequestStatus.pending,
        start_at=now + timedelta(hours=2),
        end_at=now + timedelta(hours=4),
        created_at=now,
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    monkeypatch.setattr(
        "app.services.vm.vm_request_service.audit_service.log_action",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.vm.vm_request_service.vm_request_placement_service.rebuild_reserved_assignments",
        lambda **kwargs: {
            request.id: SimpleNamespace(
                node="pve-a",
                strategy="priority_dominant_share",
                plan=SimpleNamespace(feasible=True),
            )
        },
    )

    result = vm_request_service.review(
        session=db,
        request_id=request.id,
        review_data=VMRequestReview(status=VMRequestStatus.approved),
        reviewer=reviewer,
    )

    db.expire_all()
    refreshed = db.exec(select(VMRequest).where(VMRequest.id == request.id)).first()
    assert refreshed is not None
    assert result.status == VMRequestStatus.approved
    assert result.assigned_node == "pve-a"
    assert result.placement_strategy_used == "priority_dominant_share"
    assert refreshed.status == VMRequestStatus.approved
    assert refreshed.assigned_node == "pve-a"
    assert refreshed.placement_strategy_used == "priority_dominant_share"


def test_vm_request_review_context_includes_runtime_and_projection(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    reviewer = _create_user(db, is_superuser=True)
    now = datetime.now(timezone.utc)
    approved = VMRequest(
        user_id=user.id,
        reason="Approved overlap request",
        resource_type="vm",
        hostname="approved-overlap",
        cores=2,
        memory=2048,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Review Context",
        template_id=200,
        disk_size=20,
        username="student",
        status=VMRequestStatus.approved,
        start_at=now + timedelta(hours=1),
        end_at=now + timedelta(hours=3),
        vmid=801,
        assigned_node="pve-a",
        desired_node="pve-a",
        actual_node="pve-a",
        created_at=now - timedelta(minutes=5),
    )
    pending = VMRequest(
        user_id=user.id,
        reason="Pending request for review context",
        resource_type="vm",
        hostname="pending-review",
        cores=2,
        memory=2048,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Review Context",
        template_id=201,
        disk_size=20,
        username="student",
        status=VMRequestStatus.pending,
        start_at=now + timedelta(hours=1),
        end_at=now + timedelta(hours=3),
        created_at=now,
    )
    db.add(approved)
    db.add(pending)
    db.commit()
    db.refresh(approved)
    db.refresh(pending)

    monkeypatch.setattr(
        "app.services.vm.vm_request_service.proxmox_service.list_nodes",
        lambda: [
            {"node": "pve-a"},
            {"node": "pve-b"},
            {"node": "pve-c"},
            {"node": "pve-d"},
        ],
    )
    monkeypatch.setattr(
        "app.services.vm.vm_request_service.proxmox_service.list_all_resources",
        lambda: [
            {
                "vmid": 801,
                "name": "approved-overlap",
                "node": "pve-a",
                "type": "qemu",
                "status": "running",
                "pool": "SkyLab",
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.vm.vm_request_service.vm_request_repo.list_active_approved_vm_requests",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.vm.vm_request_service.vm_request_placement_service.rebuild_reserved_assignments",
        lambda **kwargs: {
            approved.id: SimpleNamespace(
                node="pve-a",
                strategy="priority_dominant_share",
                plan=SimpleNamespace(
                    feasible=True, summary="approved summary", warnings=[]
                ),
            ),
            pending.id: SimpleNamespace(
                node="pve-b",
                strategy="priority_dominant_share",
                plan=SimpleNamespace(
                    feasible=True,
                    summary="pending summary",
                    rationale=["因為可降低 pve-a 的整體負載尖峰風險。"],
                    warnings=["rebalance warning"],
                ),
            ),
        },
    )

    context = vm_request_service.get_review_context(
        session=db,
        request_id=pending.id,
        current_user=reviewer,
    )

    assert context.projected_node == "pve-b"
    assert context.placement_strategy == "priority_dominant_share"
    assert context.summary == "pending summary"
    assert context.reasons == ["因為可降低 pve-a 的整體負載尖峰風險。"]
    assert context.warnings == ["rebalance warning"]
    assert context.cluster_nodes == ["pve-a", "pve-b", "pve-c", "pve-d"]
    assert len(context.current_running_resources) == 1
    assert context.current_running_resources[0].vmid == 801
    assert context.current_running_resources[0].linked_request_id is None
    assert context.overlapping_approved_requests[0].is_current_request is True
    assert context.overlapping_approved_requests[0].projected_node == "pve-b"
    assert context.overlapping_approved_requests[1].hostname == "approved-overlap"
    assert {item.node for item in context.projected_nodes} == {"pve-a", "pve-b"}
