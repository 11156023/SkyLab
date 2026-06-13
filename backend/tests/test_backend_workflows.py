import threading
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.security import encrypt_value
from app.domain.placement.schemas import NodeCapacity, PlacementRequest
from app.exceptions import BadRequestError, ProvisioningError, ProxmoxError
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
    VMMigrationJob,
    VMMigrationJobStatus,
    VMMigrationStatus,
    VMRequest,
    VMRequestStatus,
)
from app.repositories import user as user_repo
from app.schemas import (
    SpecChangeRequestReview,
    UserCreate,
    VMCreateRequest,
    VMRequestCreate,
    VMRequestReview,
)
from app.services.proxmox import gpu_service, provisioning_service
from app.services.scheduling import support as scheduling_support
from app.services.scheduling import vm_request_schedule_service
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


def _seed_managed_storage(
    session: Session,
    *,
    node_name: str,
    storage: str,
    speed_tier: str,
    user_priority: int,
    can_vm: bool = True,
    can_lxc: bool = True,
    avail_gb: float = 200.0,
    total_gb: float = 400.0,
) -> None:
    session.add(
        ProxmoxStorage(
            node_name=node_name,
            storage=storage,
            storage_type="dir",
            total_gb=total_gb,
            used_gb=max(total_gb - avail_gb, 0.0),
            avail_gb=avail_gb,
            can_vm=can_vm,
            can_lxc=can_lxc,
            can_iso=False,
            can_backup=False,
            is_shared=False,
            active=True,
            enabled=True,
            speed_tier=speed_tier,
            user_priority=user_priority,
        )
    )


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


def test_gpu_node_counts_are_loaded_from_proxmox_mappings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_mappings = [
        {
            "id": "gpu-a",
            "map": [
                "node=pve-a,path=0000:01:00.0",
                "node=pve-a,path=0000:02:00.0",
            ],
        },
        {
            "id": "gpu-b",
            "map": "node=pve-b,path=0000:03:00.0",
        },
    ]

    class FakePciMappings:
        def get(self):
            return raw_mappings

        def __call__(self, mapping_id: str):
            return SimpleNamespace(
                get=lambda: next(item for item in raw_mappings if item["id"] == mapping_id)
            )

    proxmox = SimpleNamespace(
        cluster=SimpleNamespace(mapping=SimpleNamespace(pci=FakePciMappings()))
    )
    monkeypatch.setattr(gpu_service, "get_proxmox_api", lambda: proxmox)

    assert gpu_service.get_gpu_node_counts() == {"pve-a": 2, "pve-b": 1}
    assert gpu_service.get_gpu_node_counts(mapping_id="gpu-b") == {"pve-b": 1}


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
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not auto approve")),
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

    def fake_approve_and_place(*, session: Session, db_request: VMRequest, reviewer_id: uuid.UUID):
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
        "app.services.vm.vm_request_service.submit_sync",
        lambda _fn, request_id, **_kwargs: calls.append(request_id),
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


def test_student_quick_template_is_limited_and_auto_approved(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db, role=UserRole.student)
    calls: list[uuid.UUID] = []

    monkeypatch.setattr(
        "app.services.vm.vm_request_service.vm_request_availability_service.validate_request_window",
        lambda **kwargs: None,
    )

    def fake_approve_and_place(*, session: Session, db_request: VMRequest, reviewer_id: uuid.UUID):
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
        "app.services.vm.vm_request_service.submit_sync",
        lambda _fn, request_id, **_kwargs: calls.append(request_id),
    )

    request_in = VMRequestCreate(
        reason="Need a short PostgreSQL lab environment",
        resource_type="lxc",
        hostname="quick-pg",
        cores=2,
        memory=2048,
        password="strongpass123",
        ostemplate="local:vztmpl/ubuntu-24.04.tar.zst",
        rootfs_size=16,
        service_template_slug="postgresql",
        mode="quick_template",
    )

    result = vm_request_service.create(session=db, request_in=request_in, user=user)

    db.expire_all()
    saved = db.exec(select(VMRequest).where(VMRequest.id == result.id)).first()
    assert saved is not None
    assert result.request_kind == "quick_template"
    assert saved.request_kind == "quick_template"
    assert saved.status == VMRequestStatus.approved
    assert saved.start_at is not None
    assert saved.end_at is not None
    assert saved.end_at - saved.start_at == timedelta(hours=3)
    assert calls == [saved.id]


def test_student_quick_template_allows_n8n(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db, role=UserRole.student)

    monkeypatch.setattr(
        "app.services.vm.vm_request_service.vm_request_availability_service.validate_request_window",
        lambda **kwargs: None,
    )

    def fake_approve_and_place(*, session: Session, db_request: VMRequest, reviewer_id: uuid.UUID):
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
        "app.services.vm.vm_request_service.submit_sync",
        lambda *_args, **_kwargs: None,
    )

    request_in = VMRequestCreate(
        reason="Need a short n8n automation lab",
        resource_type="lxc",
        hostname="quick-n8n",
        cores=2,
        memory=2048,
        password="strongpass123",
        ostemplate="local:vztmpl/debian-13.tar.zst",
        rootfs_size=10,
        service_template_slug="n8n",
        service_template_script_path="ct/n8n.sh",
        mode="quick_template",
    )

    result = vm_request_service.create(session=db, request_in=request_in, user=user)

    db.expire_all()
    saved = db.exec(select(VMRequest).where(VMRequest.id == result.id)).first()
    assert saved is not None
    assert saved.status == VMRequestStatus.approved
    assert saved.service_template_slug == "n8n"
    assert saved.service_template_script_path == "ct/n8n.sh"
    assert result.service_template_slug == "n8n"


def test_student_quick_template_rejects_unlisted_template(db: Session) -> None:
    user = _create_user(db, role=UserRole.student)
    request_in = VMRequestCreate(
        reason="Need a short unlisted service template",
        resource_type="lxc",
        hostname="quick-bad",
        cores=2,
        memory=2048,
        password="strongpass123",
        ostemplate="local:vztmpl/ubuntu-24.04.tar.zst",
        rootfs_size=16,
        service_template_slug="openwebui",
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
                plan=SimpleNamespace(feasible=True, summary="approved summary", warnings=[]),
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


def test_rebuild_reserved_assignments_uses_updated_prior_reservations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    request_a = VMRequest(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000011"),
        reason="A",
        resource_type="lxc",
        hostname="req-a",
        cores=2,
        memory=2048,
        password="encrypted",
        storage="local-lvm",
        environment_type="Test",
        status=VMRequestStatus.approved,
        start_at=now + timedelta(hours=1),
        end_at=now + timedelta(hours=2),
        created_at=now,
        assigned_node="old-a",
    )
    request_b = VMRequest(
        id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000012"),
        reason="B",
        resource_type="lxc",
        hostname="req-b",
        cores=2,
        memory=2048,
        password="encrypted",
        storage="local-lvm",
        environment_type="Test",
        status=VMRequestStatus.approved,
        start_at=now + timedelta(hours=1),
        end_at=now + timedelta(hours=2),
        created_at=now + timedelta(minutes=1),
        assigned_node="old-b",
    )

    seen_reserved_nodes: list[list[str]] = []

    def _fake_select_reserved_target_node(*, db_request, reserved_requests, **kwargs):
        seen_reserved_nodes.append(
            [str(item.assigned_node) for item in reserved_requests]
        )
        node = "new-a" if db_request.hostname == "req-a" else "new-b"
        return SimpleNamespace(
            node=node,
            strategy="priority_dominant_share",
            plan=SimpleNamespace(feasible=True),
        )

    monkeypatch.setattr(
        "app.services.vm.placement_service.select_reserved_target_node",
        _fake_select_reserved_target_node,
    )

    selections = vm_request_placement_service.rebuild_reserved_assignments(
        session=None,
        requests=[request_b, request_a],
    )

    assert seen_reserved_nodes == [[], ["new-a"]]
    assert selections[request_a.id].node == "new-a"
    assert selections[request_b.id].node == "new-b"


def test_select_request_placement_falls_back_when_reserved_node_is_unavailable(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        resource_type="lxc",
        password=encrypt_value("strongpass123"),
        start_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        end_at=datetime.now(timezone.utc) + timedelta(hours=1),
        cores=2,
        memory=2048,
        storage="local-lvm",
        hostname="fallback-lxc",
        ostemplate="local:vztmpl/debian-12-standard.tar.zst",
        rootfs_size=8,
        environment_type="Fallback Runtime",
        os_info=None,
        expiry_date=None,
        unprivileged=True,
        assigned_node="pve-a",
        placement_strategy_used="priority_dominant_share",
    )
    placement_request = SimpleNamespace()

    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.placement_advisor._load_cluster_state",
        lambda: ([], []),
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.placement_advisor._build_node_capacities",
        lambda **kwargs: [SimpleNamespace(node="pve-a")],
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.placement_advisor._decide_resource_type",
        lambda request: ("lxc", "Prefer LXC for this request."),
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.vm_request_placement_service.build_plan",
        lambda **kwargs: SimpleNamespace(feasible=False),
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.vm_request_repo.get_approved_vm_requests_overlapping_window",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.vm_request_placement_service.select_reserved_target_node",
        lambda **kwargs: SimpleNamespace(
            node="pve-b",
            strategy="priority_dominant_share",
            plan=SimpleNamespace(feasible=True),
        ),
    )

    placement = provisioning_service._select_request_placement(
        session=db,
        db_request=request,
        placement_request=placement_request,
        placement_strategy="priority_dominant_share",
    )

    assert placement.node == "pve-b"
    assert placement.strategy == "priority_dominant_share"
    assert placement.plan.feasible is True


def test_reserved_target_node_prefers_admin_storage_profile(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    now = datetime.now(timezone.utc)
    db.add(
        ProxmoxNode(
            name="pve-a",
            host="10.0.0.1",
            port=8006,
            is_primary=True,
            is_online=True,
            priority=5,
        )
    )
    db.add(
        ProxmoxNode(
            name="pve-b",
            host="10.0.0.2",
            port=8006,
            is_primary=False,
            is_online=True,
            priority=5,
        )
    )
    db.add(
        ProxmoxConfig(
            id=1,
            host="pve.local",
            user="root@pam",
            encrypted_password="encrypted",
            verify_ssl=False,
            iso_storage="local",
            data_storage="local-lvm",
            pool_name="SkyLab",
            placement_strategy="priority_dominant_share",
            cpu_overcommit_ratio=2.0,
            disk_overcommit_ratio=1.0,
        )
    )
    _seed_managed_storage(
        db,
        node_name="pve-a",
        storage="data-hdd",
        speed_tier="hdd",
        user_priority=8,
    )
    _seed_managed_storage(
        db,
        node_name="pve-b",
        storage="data-nvme",
        speed_tier="nvme",
        user_priority=1,
    )
    db.commit()

    request = VMRequest(
        user_id=user.id,
        reason="Need a VM with managed storage placement.",
        resource_type="vm",
        hostname="storage-aware",
        cores=2,
        memory=2048,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Storage Aware",
        template_id=100,
        disk_size=40,
        username="student",
        status=VMRequestStatus.approved,
        start_at=now + timedelta(hours=1),
        end_at=now + timedelta(hours=2),
        created_at=now,
    )

    monkeypatch.setattr(
        "app.services.vm.placement_service.placement_advisor._load_cluster_state",
        lambda: ([], []),
    )
    monkeypatch.setattr(
        "app.services.vm.placement_service.placement_advisor._build_node_capacities",
        lambda **kwargs: [
            NodeCapacity(
                node="pve-a",
                status="online",
                total_cpu_cores=16,
                allocatable_cpu_cores=16,
                cpu_ratio=0.0,
                total_memory_bytes=64 * 1024**3,
                allocatable_memory_bytes=64 * 1024**3,
                memory_ratio=0.0,
                total_disk_bytes=400 * 1024**3,
                allocatable_disk_bytes=400 * 1024**3,
                disk_ratio=0.0,
                gpu_count=0,
                running_resources=0,
                guest_soft_limit=32,
                guest_pressure_ratio=0.0,
                candidate=True,
            ),
            NodeCapacity(
                node="pve-b",
                status="online",
                total_cpu_cores=16,
                allocatable_cpu_cores=16,
                cpu_ratio=0.0,
                total_memory_bytes=64 * 1024**3,
                allocatable_memory_bytes=64 * 1024**3,
                memory_ratio=0.0,
                total_disk_bytes=400 * 1024**3,
                allocatable_disk_bytes=400 * 1024**3,
                disk_ratio=0.0,
                gpu_count=0,
                running_resources=0,
                guest_soft_limit=32,
                guest_pressure_ratio=0.0,
                candidate=True,
            ),
        ],
    )

    selection = vm_request_placement_service.select_reserved_target_node(
        session=db,
        db_request=request,
        reserved_requests=[],
    )

    assert selection.node == "pve-b"
    assert selection.plan.feasible is True


def test_reserved_target_node_uses_managed_storage_instead_of_node_root_disk(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)
    db.add(
        ProxmoxConfig(
            id=1,
            host="pve.local",
            user="root@pam",
            encrypted_password="encrypted",
            verify_ssl=False,
            iso_storage="local",
            data_storage="local-lvm",
            pool_name="SkyLab",
            placement_strategy="priority_dominant_share",
        )
    )
    _seed_managed_storage(
        db,
        node_name="pve-a",
        storage="local-lvm",
        speed_tier="ssd",
        user_priority=1,
        avail_gb=200,
        total_gb=400,
    )
    db.commit()

    monkeypatch.setattr(
        "app.services.vm.placement_service.placement_advisor._load_cluster_state",
        lambda: ([], []),
    )
    monkeypatch.setattr(
        "app.services.vm.placement_service.placement_advisor._build_node_capacities",
        lambda **kwargs: [
            NodeCapacity(
                node="pve-a",
                status="online",
                total_cpu_cores=16,
                allocatable_cpu_cores=16,
                total_memory_bytes=64 * 1024**3,
                allocatable_memory_bytes=64 * 1024**3,
                total_disk_bytes=10 * 1024**3,
                allocatable_disk_bytes=1 * 1024**3,
                gpu_count=0,
                running_resources=0,
                guest_soft_limit=32,
                candidate=False,
            )
        ],
    )

    selection = vm_request_placement_service.select_reserved_target_node_for_request(
        session=db,
        request=PlacementRequest(
            resource_type="vm",
            cpu_cores=2,
            memory_mb=2048,
            disk_gb=40,
            instance_count=1,
        ),
        start_at=now + timedelta(hours=1),
        end_at=now + timedelta(hours=2),
        reserved_requests=[],
        allow_cohort_rebalance=False,
    )

    assert selection.node == "pve-a"
    assert selection.plan.feasible is True


def test_placement_request_with_gpu_mapping_uses_mapping_nodes(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.vm.placement_support.gpu_service.get_gpu_node_counts",
        lambda mapping_id=None: {"pve-b": 1} if mapping_id == "gpu-b" else {"pve-a": 1, "pve-b": 1},
    )

    plan = vm_request_placement_service.build_plan(
        session=db,
        request=PlacementRequest(
            resource_type="vm",
            cpu_cores=2,
            memory_mb=2048,
            disk_gb=40,
            instance_count=1,
            gpu_required=1,
            gpu_mapping_id="gpu-b",
        ),
        node_capacities=[
            NodeCapacity(
                node="pve-a",
                status="online",
                total_cpu_cores=32,
                allocatable_cpu_cores=32,
                total_memory_bytes=128 * 1024**3,
                allocatable_memory_bytes=128 * 1024**3,
                total_disk_bytes=1000 * 1024**3,
                allocatable_disk_bytes=1000 * 1024**3,
                gpu_count=1,
                running_resources=0,
                guest_soft_limit=64,
                candidate=True,
            ),
            NodeCapacity(
                node="pve-b",
                status="online",
                total_cpu_cores=8,
                allocatable_cpu_cores=8,
                total_memory_bytes=32 * 1024**3,
                allocatable_memory_bytes=32 * 1024**3,
                total_disk_bytes=500 * 1024**3,
                allocatable_disk_bytes=500 * 1024**3,
                gpu_count=1,
                running_resources=0,
                guest_soft_limit=16,
                candidate=True,
            ),
        ],
        effective_resource_type="vm",
        resource_type_reason="VM request uses VM placement.",
        placement_strategy="priority_dominant_share",
    )

    assert plan.feasible is True
    assert plan.recommended_node == "pve-b"


def test_create_vm_prefers_admin_selected_storage(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    _seed_subnet_config(db)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.next_vmid",
        lambda: 902,
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.find_vm_template",
        lambda template_id: {"vmid": template_id, "node": "node-d"},
    )
    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.vm_request_placement_service.select_best_storage_name",
        lambda **kwargs: "data-nvme",
    )

    def _resolve_target_storage(node, requested_storage, required_content):
        captured["resolved"] = (node, requested_storage, required_content)
        return requested_storage

    monkeypatch.setattr(
        "app.services.proxmox.provisioning_service.proxmox_service.resolve_target_storage",
        _resolve_target_storage,
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
        "app.services.proxmox.provisioning_service.get_proxmox_settings",
        lambda: SimpleNamespace(pool_name="SkyLab"),
    )

    provisioning_service.create_vm(
        session=db,
        user_id=user.id,
        vm_data=VMCreateRequest(
            hostname="admin-storage-choice",
            template_id=779,
            username="student",
            password="strongpass123",
            cores=2,
            memory=2048,
            disk_size=20,
            storage="user-picked-storage",
            environment_type="Managed Storage",
            start=True,
        ),
    )

    assert captured["resolved"] == ("node-d", "data-nvme", "images")
    assert captured["clone"] == (
        "node-d",
        779,
        {
            "newid": 902,
            "name": "admin-storage-choice",
            "full": 1,
            "storage": "data-nvme",
            "pool": "SkyLab",
        },
    )


def test_process_due_request_starts_rebalances_active_window_and_migrates(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    now = datetime.now(timezone.utc)
    existing = VMRequest(
        user_id=user.id,
        reason="Already running request",
        resource_type="vm",
        hostname="active-a",
        cores=2,
        memory=2048,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Rebalance Test",
        template_id=101,
        disk_size=20,
        username="student",
        status=VMRequestStatus.approved,
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
        vmid=701,
        assigned_node="pve-a",
        desired_node="pve-a",
        actual_node="pve-a",
        migration_status=VMMigrationStatus.idle,
        rebalance_epoch=1,
        last_rebalanced_at=now - timedelta(hours=1),
        created_at=now - timedelta(hours=1),
    )
    new_start = VMRequest(
        user_id=user.id,
        reason="New request entering the active slot",
        resource_type="vm",
        hostname="active-b",
        cores=2,
        memory=2048,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Rebalance Test",
        template_id=102,
        disk_size=20,
        username="student",
        status=VMRequestStatus.approved,
        start_at=now - timedelta(minutes=1),
        end_at=now + timedelta(hours=1),
        vmid=702,
        assigned_node="pve-b",
        desired_node="pve-b",
        actual_node="pve-b",
        migration_status=VMMigrationStatus.idle,
        rebalance_epoch=0,
        last_rebalanced_at=None,
        created_at=now - timedelta(minutes=5),
    )
    db.add(existing)
    db.add(new_start)
    db.commit()
    db.refresh(existing)
    db.refresh(new_start)

    resources = {
        701: {"vmid": 701, "node": "pve-a", "name": "active-a", "type": "qemu"},
        702: {"vmid": 702, "node": "pve-b", "name": "active-b", "type": "qemu"},
    }
    migrations: list[tuple[str, str, int, str, bool]] = []

    monkeypatch.setattr(
        "app.services.scheduling.coordinator.engine",
        db.get_bind(),
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator._utc_now",
        lambda: now,
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.vm_request_placement_service.rebalance_active_assignments",
        lambda **kwargs: {
            existing.id: SimpleNamespace(
                node="pve-c",
                strategy="priority_dominant_share",
                plan=SimpleNamespace(feasible=True),
            ),
            new_start.id: SimpleNamespace(
                node="pve-b",
                strategy="priority_dominant_share",
                plan=SimpleNamespace(feasible=True),
            ),
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.find_resource",
        lambda vmid: resources[vmid],
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_status",
        lambda node, vmid, resource_type: {"status": "running"},
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_config",
        lambda node, vmid, resource_type: {
            "scsi0": f"local-lvm:vm-{vmid}-disk-0,size=20G",
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.list_node_storages",
        lambda node: [{"storage": "local-lvm", "shared": 1}],
    )

    def _migrate(source_node, target_node, vmid, resource_type, online=True, **kwargs):
        migrations.append((source_node, target_node, vmid, resource_type, online))
        resources[vmid]["node"] = target_node
        return "UPID:migrate"

    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.migrate_resource",
        _migrate,
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.audit_service.log_action",
        lambda *args, **kwargs: None,
    )

    started_count = vm_request_schedule_service.process_due_request_starts()

    db.expire_all()
    refreshed_existing = db.exec(
        select(VMRequest).where(VMRequest.id == existing.id)
    ).first()
    refreshed_new = db.exec(
        select(VMRequest).where(VMRequest.id == new_start.id)
    ).first()
    assert started_count == 0
    assert migrations == [("pve-a", "pve-c", 701, "qemu", True)]
    assert refreshed_existing is not None
    assert refreshed_existing.assigned_node == "pve-c"
    assert refreshed_existing.desired_node == "pve-c"
    assert refreshed_existing.actual_node == "pve-c"
    assert refreshed_existing.migration_status == VMMigrationStatus.completed
    assert refreshed_existing.rebalance_epoch == 2
    assert refreshed_new is not None
    assert refreshed_new.assigned_node == "pve-b"
    assert refreshed_new.desired_node == "pve-b"
    assert refreshed_new.actual_node == "pve-b"
    assert refreshed_new.rebalance_epoch == 2


def test_process_due_request_starts_provisions_new_active_request_on_rebalanced_node(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    now = datetime.now(timezone.utc)
    request = VMRequest(
        user_id=user.id,
        reason="Start and place on the rebalanced node",
        resource_type="lxc",
        hostname="active-new-lxc",
        cores=2,
        memory=2048,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Provision Test",
        ostemplate="local:vztmpl/debian-12-standard.tar.zst",
        rootfs_size=8,
        status=VMRequestStatus.approved,
        start_at=now - timedelta(minutes=1),
        end_at=now + timedelta(hours=1),
        created_at=now - timedelta(minutes=2),
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    monkeypatch.setattr(
        "app.services.scheduling.coordinator.engine",
        db.get_bind(),
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator._utc_now",
        lambda: now,
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.vm_request_placement_service.rebalance_active_assignments",
        lambda **kwargs: {
            request.id: SimpleNamespace(
                node="pve-b",
                strategy="priority_dominant_share",
                plan=SimpleNamespace(feasible=True),
            )
        },
    )

    def _fake_adopt_or_provision_due_request(**kwargs):
        db_request = kwargs["request"]
        session = kwargs["session"]
        db_request.vmid = 990
        db_request.assigned_node = "pve-b"
        db_request.desired_node = "pve-b"
        db_request.actual_node = "pve-b"
        db_request.placement_strategy_used = "priority_dominant_share"
        session.add(db_request)
        session.flush()
        return 990, "pve-b", "priority_dominant_share", True

    monkeypatch.setattr(
        "app.services.scheduling.coordinator._adopt_or_provision_due_request",
        _fake_adopt_or_provision_due_request,
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_config",
        lambda node, vmid, resource_type: {
            "rootfs": f"local-lvm:subvol-{vmid}-disk-0,size=8G",
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.list_node_storages",
        lambda node: [{"storage": "local-lvm", "shared": 1}],
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.audit_service.log_action",
        lambda *args, **kwargs: None,
    )

    started_count = vm_request_schedule_service.process_due_request_starts()

    db.expire_all()
    refreshed = db.exec(select(VMRequest).where(VMRequest.id == request.id)).first()
    assert started_count == 1
    assert refreshed is not None
    assert refreshed.vmid == 990
    assert refreshed.assigned_node == "pve-b"
    assert refreshed.desired_node == "pve-b"
    assert refreshed.actual_node == "pve-b"
    assert refreshed.migration_status == VMMigrationStatus.completed
    assert refreshed.rebalance_epoch == 1
    assert refreshed.last_rebalanced_at == now.replace(tzinfo=None)


def test_process_due_request_starts_defers_when_migration_budget_is_exhausted(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    now = datetime.now(timezone.utc)
    db.add(
        ProxmoxConfig(
            id=1,
            host="pve.local",
            user="root@pam",
            encrypted_password="encrypted",
            verify_ssl=False,
            iso_storage="local",
            data_storage="local-lvm",
            pool_name="SkyLab",
            placement_strategy="priority_dominant_share",
            cpu_overcommit_ratio=2.0,
            disk_overcommit_ratio=1.0,
            migration_enabled=True,
            migration_max_per_rebalance=0,
            migration_min_interval_minutes=60,
        )
    )
    request = VMRequest(
        user_id=user.id,
        reason="Budget-limited migration should defer.",
        resource_type="vm",
        hostname="budget-vm",
        cores=2,
        memory=2048,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Migration Budget",
        template_id=401,
        disk_size=20,
        username="student",
        status=VMRequestStatus.approved,
        start_at=now - timedelta(minutes=1),
        end_at=now + timedelta(hours=1),
        vmid=1401,
        assigned_node="pve-b",
        desired_node="pve-a",
        actual_node="pve-b",
        migration_status=VMMigrationStatus.pending,
        created_at=now - timedelta(hours=1),
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    monkeypatch.setattr("app.services.scheduling.coordinator.engine", db.get_bind())
    monkeypatch.setattr("app.services.scheduling.coordinator._utc_now", lambda: now)
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.vm_request_placement_service.rebalance_active_assignments",
        lambda **kwargs: {
            request.id: SimpleNamespace(
                node="pve-a",
                strategy="priority_dominant_share",
                plan=SimpleNamespace(feasible=True),
            )
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.find_resource",
        lambda vmid: {"vmid": vmid, "node": "pve-b", "name": "budget-vm", "type": "qemu"},
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_status",
        lambda node, vmid, resource_type: {"status": "running"},
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_config",
        lambda node, vmid, resource_type: {
            "scsi0": "local-lvm:vm-1401-disk-0,size=20G",
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.list_node_storages",
        lambda node: [{"storage": "local-lvm"}],
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.migrate_resource",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("migrate_resource should not be called when migration budget is 0")
        ),
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.audit_service.log_action",
        lambda *args, **kwargs: None,
    )

    started_count = vm_request_schedule_service.process_due_request_starts()

    db.refresh(request)
    assert started_count == 0
    assert request.actual_node == "pve-b"
    assert request.desired_node == "pve-a"
    assert request.migration_status == VMMigrationStatus.pending
    assert request.migration_error is not None
    assert "migration budget" in request.migration_error


def test_process_due_request_starts_defers_when_recently_migrated(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    now = datetime.now(timezone.utc)
    db.add(
        ProxmoxConfig(
            id=1,
            host="pve.local",
            user="root@pam",
            encrypted_password="encrypted",
            verify_ssl=False,
            iso_storage="local",
            data_storage="local-lvm",
            pool_name="SkyLab",
            placement_strategy="priority_dominant_share",
            cpu_overcommit_ratio=2.0,
            disk_overcommit_ratio=1.0,
            migration_enabled=True,
            migration_max_per_rebalance=2,
            migration_min_interval_minutes=120,
        )
    )
    request = VMRequest(
        user_id=user.id,
        reason="Recently migrated VM should not flap.",
        resource_type="vm",
        hostname="recent-vm",
        cores=2,
        memory=2048,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Migration Interval",
        template_id=402,
        disk_size=20,
        username="student",
        status=VMRequestStatus.approved,
        start_at=now - timedelta(minutes=1),
        end_at=now + timedelta(hours=1),
        vmid=1402,
        assigned_node="pve-b",
        desired_node="pve-a",
        actual_node="pve-b",
        migration_status=VMMigrationStatus.pending,
        last_migrated_at=now - timedelta(minutes=30),
        created_at=now - timedelta(hours=2),
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    monkeypatch.setattr("app.services.scheduling.coordinator.engine", db.get_bind())
    monkeypatch.setattr("app.services.scheduling.coordinator._utc_now", lambda: now)
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.vm_request_placement_service.rebalance_active_assignments",
        lambda **kwargs: {
            request.id: SimpleNamespace(
                node="pve-a",
                strategy="priority_dominant_share",
                plan=SimpleNamespace(feasible=True),
            )
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.find_resource",
        lambda vmid: {"vmid": vmid, "node": "pve-b", "name": "recent-vm", "type": "qemu"},
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_status",
        lambda node, vmid, resource_type: {"status": "running"},
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_config",
        lambda node, vmid, resource_type: {
            "scsi0": "local-lvm:vm-1402-disk-0,size=20G",
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.list_node_storages",
        lambda node: [{"storage": "local-lvm"}],
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.migrate_resource",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("migrate_resource should not be called when min interval has not elapsed")
        ),
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.audit_service.log_action",
        lambda *args, **kwargs: None,
    )

    started_count = vm_request_schedule_service.process_due_request_starts()

    db.refresh(request)
    assert started_count == 0
    assert request.actual_node == "pve-b"
    assert request.desired_node == "pve-a"
    assert request.migration_status == VMMigrationStatus.pending
    assert request.migration_error is not None
    assert "too recently" in request.migration_error


def test_rebalance_active_window_enqueues_pending_migration_job(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    now = datetime.now(timezone.utc)
    request = VMRequest(
        user_id=user.id,
        reason="Queue a migration job when active rebalance changes node.",
        resource_type="vm",
        hostname="queue-vm",
        cores=2,
        memory=2048,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Queue Test",
        template_id=510,
        disk_size=20,
        username="student",
        status=VMRequestStatus.approved,
        start_at=now - timedelta(minutes=10),
        end_at=now + timedelta(hours=1),
        vmid=1510,
        assigned_node="pve-a",
        desired_node="pve-a",
        actual_node="pve-a",
        migration_status=VMMigrationStatus.idle,
        created_at=now - timedelta(hours=1),
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    monkeypatch.setattr(
        "app.services.scheduling.coordinator.engine",
        db.get_bind(),
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.vm_request_placement_service.rebalance_active_assignments",
        lambda **kwargs: {
            request.id: SimpleNamespace(
                node="pve-b",
                strategy="priority_dominant_share",
                plan=SimpleNamespace(feasible=True),
            ),
        },
    )

    processed = vm_request_schedule_service._rebalance_active_window(now)

    db.refresh(request)
    job = db.exec(select(VMMigrationJob)).first()
    assert processed == 1
    assert request.desired_node == "pve-b"
    assert request.migration_status == VMMigrationStatus.pending
    assert job is not None
    assert job.request_id == request.id
    assert job.status == VMMigrationJobStatus.pending
    assert job.source_node == "pve-a"
    assert job.target_node == "pve-b"
    assert job.rebalance_epoch == 1


def test_rebalance_active_window_requeues_after_interval_elapsed(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    now = datetime.now(timezone.utc)
    request = VMRequest(
        user_id=user.id,
        reason="Rebalance again after the active interval elapses.",
        resource_type="vm",
        hostname="requeue-active-vm",
        cores=2,
        memory=2048,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Queue Test",
        template_id=610,
        disk_size=20,
        username="student",
        status=VMRequestStatus.approved,
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
        vmid=1610,
        assigned_node="pve-a",
        desired_node="pve-a",
        actual_node="pve-a",
        migration_status=VMMigrationStatus.idle,
        rebalance_epoch=3,
        last_rebalanced_at=now - timedelta(minutes=20),
        created_at=now - timedelta(hours=2),
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    monkeypatch.setattr(
        "app.services.scheduling.coordinator.engine",
        db.get_bind(),
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.vm_request_placement_service.rebalance_active_assignments",
        lambda **kwargs: {
            request.id: SimpleNamespace(
                node="pve-a",
                strategy="priority_dominant_share",
                plan=SimpleNamespace(feasible=True),
            ),
        },
    )

    processed = vm_request_schedule_service._rebalance_active_window(now)

    db.refresh(request)
    assert processed == 1
    assert request.rebalance_epoch == 4
    assert request.last_rebalanced_at == now.replace(tzinfo=None)
    assert request.desired_node == "pve-a"


def test_process_due_request_starts_retries_pending_migration_job_until_limit(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    now = datetime.now(timezone.utc)
    db.add(
        ProxmoxConfig(
            id=1,
            host="pve.local",
            user="root@pam",
            encrypted_password="encrypted",
            verify_ssl=False,
            iso_storage="local",
            data_storage="local-lvm",
            pool_name="SkyLab",
            placement_strategy="priority_dominant_share",
            cpu_overcommit_ratio=2.0,
            disk_overcommit_ratio=1.0,
            migration_enabled=True,
            migration_max_per_rebalance=2,
            migration_min_interval_minutes=0,
            migration_retry_limit=2,
        )
    )
    request = VMRequest(
        user_id=user.id,
        reason="Retry queue should preserve pending state before limit is reached.",
        resource_type="vm",
        hostname="retry-vm",
        cores=2,
        memory=2048,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Retry Test",
        template_id=511,
        disk_size=20,
        username="student",
        status=VMRequestStatus.approved,
        start_at=now - timedelta(minutes=1),
        end_at=now + timedelta(hours=1),
        vmid=1511,
        assigned_node="pve-b",
        desired_node="pve-a",
        actual_node="pve-b",
        migration_status=VMMigrationStatus.pending,
        created_at=now - timedelta(hours=1),
    )
    job = VMMigrationJob(
        request_id=request.id,
        vmid=1511,
        source_node="pve-b",
        target_node="pve-a",
        status=VMMigrationJobStatus.pending,
        rebalance_epoch=1,
        attempt_count=0,
        requested_at=now - timedelta(minutes=1),
        updated_at=now - timedelta(minutes=1),
    )
    db.add(request)
    db.add(job)
    db.commit()

    monkeypatch.setattr("app.services.scheduling.coordinator.engine", db.get_bind())
    monkeypatch.setattr("app.services.scheduling.coordinator._utc_now", lambda: now)
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.vm_request_placement_service.rebalance_active_assignments",
        lambda **kwargs: {
            request.id: SimpleNamespace(
                node="pve-a",
                strategy="priority_dominant_share",
                plan=SimpleNamespace(feasible=True),
            ),
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.find_resource",
        lambda vmid: {"vmid": vmid, "node": "pve-b", "name": "retry-vm", "type": "qemu"},
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_status",
        lambda node, vmid, resource_type: {"status": "running"},
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_config",
        lambda node, vmid, resource_type: {
            "scsi0": "local-lvm:vm-1511-disk-0,size=20G",
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.list_node_storages",
        lambda node: [{"storage": "local-lvm", "shared": 1}],
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.migrate_resource",
        lambda *args, **kwargs: (_ for _ in ()).throw(ProxmoxError("link down")),
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.audit_service.log_action",
        lambda *args, **kwargs: None,
    )

    vm_request_schedule_service.process_due_request_starts()
    db.refresh(request)
    db.refresh(job)
    assert job.status == VMMigrationJobStatus.pending
    assert job.attempt_count == 1
    assert request.migration_status == VMMigrationStatus.pending
    assert request.migration_error is not None
    assert "link down" in request.migration_error

    vm_request_schedule_service.process_due_request_starts()
    db.refresh(request)
    db.refresh(job)
    assert job.status == VMMigrationJobStatus.pending
    assert job.attempt_count == 1

    monkeypatch.setattr(
        "app.services.scheduling.coordinator._utc_now",
        lambda: now + timedelta(seconds=121),
    )
    vm_request_schedule_service.process_due_request_starts()
    db.refresh(request)
    db.refresh(job)
    assert job.status == VMMigrationJobStatus.failed
    assert job.attempt_count == 2
    assert request.migration_status == VMMigrationStatus.failed


def test_build_plan_prefers_current_node_when_migration_cost_is_applied(
    db: Session,
) -> None:
    db.add(
        ProxmoxConfig(
            id=1,
            host="pve.local",
            user="root@pam",
            encrypted_password="encrypted",
            verify_ssl=False,
            iso_storage="local",
            data_storage="local-lvm",
            pool_name="SkyLab",
            placement_strategy="priority_dominant_share",
            rebalance_migration_cost=0.5,
        )
    )
    db.commit()

    plan = vm_request_placement_service.build_plan(
        session=db,
        request=PlacementRequest(
            resource_type="vm",
            cpu_cores=2,
            memory_mb=2048,
            disk_gb=20,
            instance_count=1,
        ),
        node_capacities=[
                NodeCapacity(
                    node="pve-a",
                    status="online",
                    candidate=True,
                    guest_soft_limit=100,
                    total_cpu_cores=16,
                    allocatable_cpu_cores=16,
                    total_memory_bytes=64 * 1024**3,
                allocatable_memory_bytes=64 * 1024**3,
                total_disk_bytes=500 * 1024**3,
                allocatable_disk_bytes=500 * 1024**3,
            ),
                NodeCapacity(
                    node="pve-b",
                    status="online",
                    candidate=True,
                    guest_soft_limit=100,
                    total_cpu_cores=16,
                    allocatable_cpu_cores=16,
                    total_memory_bytes=64 * 1024**3,
                allocatable_memory_bytes=64 * 1024**3,
                total_disk_bytes=500 * 1024**3,
                allocatable_disk_bytes=500 * 1024**3,
            ),
        ],
        effective_resource_type="vm",
        resource_type_reason="vm",
        current_node="pve-b",
    )

    assert plan.recommended_node == "pve-b"


def test_build_plan_avoids_high_loadavg_and_peak_risk_node(
    db: Session,
) -> None:
    db.add(
        ProxmoxConfig(
            id=1,
            host="pve.local",
            user="root@pam",
            encrypted_password="encrypted",
            verify_ssl=False,
            iso_storage="local",
            data_storage="local-lvm",
            pool_name="SkyLab",
            placement_strategy="priority_dominant_share",
            rebalance_peak_cpu_margin=2.0,
            rebalance_peak_memory_margin=1.05,
            rebalance_loadavg_warn_per_core=0.5,
            rebalance_loadavg_max_per_core=1.0,
            rebalance_loadavg_penalty_weight=1.5,
        )
    )
    db.commit()

    plan = vm_request_placement_service.build_plan(
        session=db,
        request=PlacementRequest(
            resource_type="vm",
            cpu_cores=1,
            memory_mb=1024,
            disk_gb=10,
            instance_count=1,
        ),
        node_capacities=[
                NodeCapacity(
                    node="pve-a",
                    status="online",
                    candidate=True,
                    guest_soft_limit=100,
                    total_cpu_cores=10,
                    allocatable_cpu_cores=4.2,
                    total_memory_bytes=100 * 1024**3,
                allocatable_memory_bytes=90 * 1024**3,
                total_disk_bytes=500 * 1024**3,
                allocatable_disk_bytes=450 * 1024**3,
                current_loadavg_1=9.0,
            ),
                NodeCapacity(
                    node="pve-b",
                    status="online",
                    candidate=True,
                    guest_soft_limit=100,
                    total_cpu_cores=10,
                    allocatable_cpu_cores=10.0,
                    total_memory_bytes=100 * 1024**3,
                allocatable_memory_bytes=31 * 1024**3,
                total_disk_bytes=500 * 1024**3,
                allocatable_disk_bytes=450 * 1024**3,
                current_loadavg_1=1.0,
            ),
        ],
        effective_resource_type="vm",
        resource_type_reason="vm",
    )

    assert plan.recommended_node == "pve-b"


def test_build_plan_prefers_balance_before_node_priority(
    db: Session,
) -> None:
    db.add(
        ProxmoxConfig(
            id=1,
            host="pve.local",
            user="root@pam",
            encrypted_password="encrypted",
            verify_ssl=False,
            iso_storage="local",
            data_storage="local-lvm",
            pool_name="SkyLab",
            placement_strategy="priority_dominant_share",
        )
    )
    db.commit()

    plan = vm_request_placement_service.build_plan(
        session=db,
        request=PlacementRequest(
            resource_type="vm",
            cpu_cores=1,
            memory_mb=1024,
            disk_gb=10,
            instance_count=1,
        ),
        node_capacities=[
            NodeCapacity(
                node="pve-a",
                status="online",
                candidate=True,
                guest_soft_limit=100,
                total_cpu_cores=10,
                allocatable_cpu_cores=3,
                total_memory_bytes=64 * 1024**3,
                allocatable_memory_bytes=56 * 1024**3,
                total_disk_bytes=500 * 1024**3,
                allocatable_disk_bytes=480 * 1024**3,
            ),
            NodeCapacity(
                node="pve-b",
                status="online",
                candidate=True,
                guest_soft_limit=100,
                total_cpu_cores=10,
                allocatable_cpu_cores=10,
                total_memory_bytes=64 * 1024**3,
                allocatable_memory_bytes=64 * 1024**3,
                total_disk_bytes=500 * 1024**3,
                allocatable_disk_bytes=500 * 1024**3,
            ),
        ],
        effective_resource_type="vm",
        resource_type_reason="vm",
        node_priorities={"pve-a": 1, "pve-b": 5},
    )

    assert plan.recommended_node == "pve-b"


def test_unprovisioned_request_reassignment_has_no_migration_cost(
    db: Session,
) -> None:
    now = datetime.now(timezone.utc)
    db.add(
        ProxmoxConfig(
            id=1,
            host="pve.local",
            user="root@pam",
            encrypted_password="encrypted",
            verify_ssl=False,
            iso_storage="local",
            data_storage="local-lvm",
            pool_name="SkyLab",
            placement_strategy="priority_dominant_share",
            rebalance_migration_cost=1.0,
        )
    )
    db.commit()

    request = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="preview",
        resource_type="vm",
        hostname="preview-vm",
        cores=2,
        memory=2048,
        password="encrypted",
        storage="local-lvm",
        environment_type="Test",
        status=VMRequestStatus.approved,
        start_at=now,
        end_at=now + timedelta(hours=1),
        assigned_node="pve-a",
        created_at=now,
    )

    baseline_nodes = [
        NodeCapacity(
            node="pve-a",
            status="online",
            candidate=True,
            guest_soft_limit=100,
            total_cpu_cores=12,
            allocatable_cpu_cores=12,
            total_memory_bytes=64 * 1024**3,
            allocatable_memory_bytes=64 * 1024**3,
            total_disk_bytes=500 * 1024**3,
            allocatable_disk_bytes=500 * 1024**3,
        ),
        NodeCapacity(
            node="pve-b",
            status="online",
            candidate=True,
            guest_soft_limit=100,
            total_cpu_cores=12,
            allocatable_cpu_cores=12,
            total_memory_bytes=64 * 1024**3,
            allocatable_memory_bytes=64 * 1024**3,
            total_disk_bytes=500 * 1024**3,
            allocatable_disk_bytes=500 * 1024**3,
        ),
    ]
    priorities = {"pve-a": 5, "pve-b": 5}
    tuning = vm_request_placement_service._get_placement_tuning(session=db)

    evaluation_with_reserved_node = vm_request_placement_service._evaluate_active_assignment_map(
        session=db,
        ordered_requests=[request],
        baseline_nodes=baseline_nodes,
        assignments={request.id: "pve-b"},
        priorities=priorities,
        tuning=tuning,
    )
    request.assigned_node = None
    evaluation_without_reserved_node = vm_request_placement_service._evaluate_active_assignment_map(
        session=db,
        ordered_requests=[request],
        baseline_nodes=baseline_nodes,
        assignments={request.id: "pve-b"},
        priorities=priorities,
        tuning=tuning,
    )

    assert evaluation_with_reserved_node.objective == evaluation_without_reserved_node.objective


def test_migrate_request_to_desired_node_blocks_vm_with_passthrough(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    request = VMRequest(
        user_id=user.id,
        reason="Passthrough VM should not auto-migrate",
        resource_type="vm",
        hostname="passthrough-vm",
        cores=2,
        memory=2048,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Migration Guard",
        template_id=301,
        disk_size=20,
        username="student",
        status=VMRequestStatus.approved,
        vmid=1201,
        assigned_node="pve-b",
        desired_node="pve-b",
        actual_node="pve-a",
        migration_status=VMMigrationStatus.pending,
        created_at=datetime.now(timezone.utc),
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_status",
        lambda node, vmid, resource_type: {"status": "running"},
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_config",
        lambda node, vmid, resource_type: {
            "hostpci0": "0000:65:00",
            "scsi0": "local-lvm:vm-1201-disk-0,size=20G",
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.list_node_storages",
        lambda node: [{"storage": "local-lvm"}],
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.migrate_resource",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("migrate_resource should not be called for passthrough VM")
        ),
    )

    result_node, migrated = vm_request_schedule_service._migrate_request_to_desired_node(
        session=db,
        request=request,
        current_node="pve-a",
        now=datetime.now(timezone.utc),
        policy=vm_request_schedule_service._MigrationPolicy(
            enabled=True,
            max_per_rebalance=2,
            min_interval_minutes=60,
        ),
        migrations_used=0,
    )

    db.refresh(request)
    assert result_node == "pve-a"
    assert migrated is False
    assert request.actual_node == "pve-a"
    assert request.desired_node == "pve-b"
    assert request.migration_status == VMMigrationStatus.blocked
    assert request.migration_error is not None
    assert "passthrough devices" in request.migration_error


def test_migrate_request_to_desired_node_blocks_gpu_vm(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    request = VMRequest(
        user_id=user.id,
        reason="GPU VM should stay on its current node",
        resource_type="vm",
        hostname="gpu-vm",
        cores=4,
        memory=8192,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="GPU Lab",
        template_id=302,
        disk_size=40,
        username="student",
        gpu_mapping_id="lab-gpu-01",
        status=VMRequestStatus.approved,
        vmid=1202,
        assigned_node="pve-b",
        desired_node="pve-b",
        actual_node="pve-a",
        migration_status=VMMigrationStatus.pending,
        created_at=datetime.now(timezone.utc),
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.migrate_resource",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("migrate_resource should not be called for GPU VM")
        ),
    )

    result_node, migrated = vm_request_schedule_service._migrate_request_to_desired_node(
        session=db,
        request=request,
        current_node="pve-a",
        now=datetime.now(timezone.utc),
        policy=vm_request_schedule_service._MigrationPolicy(
            enabled=True,
            max_per_rebalance=2,
            min_interval_minutes=60,
        ),
        migrations_used=0,
    )

    db.refresh(request)
    assert result_node == "pve-a"
    assert migrated is False
    assert request.actual_node == "pve-a"
    assert request.desired_node == "pve-b"
    assert request.migration_status == VMMigrationStatus.blocked
    assert request.migration_error is not None
    assert "GPU mapping" in request.migration_error


def test_migrate_request_to_desired_node_blocks_non_shared_storage_vm(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    request = VMRequest(
        user_id=user.id,
        reason="VM on node-local storage should not auto-migrate",
        resource_type="vm",
        hostname="local-storage-vm",
        cores=2,
        memory=4096,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Migration Guard",
        template_id=303,
        disk_size=32,
        username="student",
        status=VMRequestStatus.approved,
        vmid=1203,
        assigned_node="pve-b",
        desired_node="pve-b",
        actual_node="pve-a",
        migration_status=VMMigrationStatus.pending,
        created_at=datetime.now(timezone.utc),
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_status",
        lambda node, vmid, resource_type: {"status": "running"},
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_config",
        lambda node, vmid, resource_type: {
            "scsi0": "local-lvm:vm-1203-disk-0,size=32G",
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.list_node_storages",
        lambda node: [{"storage": "local-lvm", "shared": 0}],
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.migrate_resource",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("migrate_resource should not be called for non-shared storage VM")
        ),
    )

    result_node, migrated = vm_request_schedule_service._migrate_request_to_desired_node(
        session=db,
        request=request,
        current_node="pve-a",
        now=datetime.now(timezone.utc),
        policy=vm_request_schedule_service._MigrationPolicy(
            enabled=True,
            max_per_rebalance=2,
            min_interval_minutes=60,
        ),
        migrations_used=0,
    )

    db.refresh(request)
    assert result_node == "pve-a"
    assert migrated is False
    assert request.actual_node == "pve-a"
    assert request.desired_node == "pve-b"
    assert request.migration_status == VMMigrationStatus.blocked
    assert request.migration_error is not None
    assert "explicitly shared storage 'local-lvm'" in request.migration_error


def test_migrate_request_to_desired_node_blocks_lxc_bind_mount(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    request = VMRequest(
        user_id=user.id,
        reason="Bind mount container should not auto-migrate",
        resource_type="lxc",
        hostname="bind-lxc",
        cores=2,
        memory=2048,
        password=encrypt_value("strongpass123"),
        storage="local-lvm",
        environment_type="Migration Guard",
        ostemplate="local:vztmpl/debian-12-standard.tar.zst",
        rootfs_size=8,
        status=VMRequestStatus.approved,
        vmid=1301,
        assigned_node="pve-b",
        desired_node="pve-b",
        actual_node="pve-a",
        migration_status=VMMigrationStatus.pending,
        created_at=datetime.now(timezone.utc),
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_status",
        lambda node, vmid, resource_type: {"status": "running"},
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_config",
        lambda node, vmid, resource_type: {
            "rootfs": "local-lvm:subvol-1301-disk-0,size=8G",
            "mp0": "/srv/shared,mp=/data",
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.list_node_storages",
        lambda node: [{"storage": "local-lvm"}],
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.migrate_resource",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("migrate_resource should not be called for bind-mount LXC")
        ),
    )

    result_node, migrated = vm_request_schedule_service._migrate_request_to_desired_node(
        session=db,
        request=request,
        current_node="pve-a",
        now=datetime.now(timezone.utc),
        policy=vm_request_schedule_service._MigrationPolicy(
            enabled=True,
            max_per_rebalance=2,
            min_interval_minutes=60,
        ),
        migrations_used=0,
    )

    db.refresh(request)
    assert result_node == "pve-a"
    assert migrated is False
    assert request.actual_node == "pve-a"
    assert request.desired_node == "pve-b"
    assert request.migration_status == VMMigrationStatus.blocked
    assert request.migration_error is not None
    assert "LXC containers stay on their current node" in request.migration_error


def test_rebalance_active_assignments_keeps_passthrough_vm_on_current_node(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)
    request = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="Pinned passthrough VM should stay put during active rebalance.",
        resource_type="vm",
        hostname="pinned-active-vm",
        cores=4,
        memory=4096,
        password="encrypted",
        storage="local-lvm",
        environment_type="Pinned",
        status=VMRequestStatus.approved,
        start_at=now - timedelta(minutes=10),
        end_at=now + timedelta(hours=1),
        vmid=1801,
        assigned_node="pve-a",
        actual_node="pve-a",
        created_at=now - timedelta(hours=1),
    )

    monkeypatch.setattr(
        "app.services.vm.placement_service._build_rebalance_baseline_nodes",
        lambda **kwargs: [
            NodeCapacity(
                node="pve-a",
                status="online",
                candidate=True,
                guest_soft_limit=100,
                total_cpu_cores=8,
                allocatable_cpu_cores=5,
                total_memory_bytes=32 * 1024**3,
                allocatable_memory_bytes=16 * 1024**3,
                total_disk_bytes=500 * 1024**3,
                allocatable_disk_bytes=300 * 1024**3,
            ),
            NodeCapacity(
                node="pve-b",
                status="online",
                candidate=True,
                guest_soft_limit=100,
                total_cpu_cores=8,
                allocatable_cpu_cores=8,
                total_memory_bytes=32 * 1024**3,
                allocatable_memory_bytes=32 * 1024**3,
                total_disk_bytes=500 * 1024**3,
                allocatable_disk_bytes=500 * 1024**3,
            ),
        ],
    )
    monkeypatch.setattr(
        "app.services.scheduling.support.proxmox_service.get_config",
        lambda node, vmid, resource_type: {
            "hostpci0": "0000:65:00",
            "scsi0": "local-lvm:vm-1801-disk-0,size=20G",
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.support.proxmox_service.list_node_storages",
        lambda node: [{"storage": "local-lvm"}],
    )
    monkeypatch.setattr(
        "app.services.scheduling.support.proxmox_service.get_status",
        lambda node, vmid, resource_type: {"status": "running"},
    )

    selections = vm_request_placement_service.rebalance_active_assignments(
        session=db,
        requests=[request],
    )

    selection = selections[request.id]
    assert selection.node == "pve-a"


def test_rebalance_active_assignments_keeps_lxc_on_current_node(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)
    request = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="LXC should stay on its current node during active rebalance.",
        resource_type="lxc",
        hostname="stable-active-lxc",
        cores=2,
        memory=2048,
        password="encrypted",
        storage="local-lvm",
        environment_type="Container",
        ostemplate="local:vztmpl/debian-12-standard.tar.zst",
        rootfs_size=8,
        status=VMRequestStatus.approved,
        start_at=now - timedelta(minutes=10),
        end_at=now + timedelta(hours=1),
        vmid=1802,
        assigned_node="pve-a",
        actual_node="pve-a",
        created_at=now - timedelta(hours=1),
    )

    monkeypatch.setattr(
        "app.services.vm.placement_service._build_rebalance_baseline_nodes",
        lambda **kwargs: [
            NodeCapacity(
                node="pve-a",
                status="online",
                candidate=True,
                guest_soft_limit=100,
                total_cpu_cores=8,
                allocatable_cpu_cores=5,
                total_memory_bytes=32 * 1024**3,
                allocatable_memory_bytes=16 * 1024**3,
                total_disk_bytes=500 * 1024**3,
                allocatable_disk_bytes=300 * 1024**3,
            ),
            NodeCapacity(
                node="pve-b",
                status="online",
                candidate=True,
                guest_soft_limit=100,
                total_cpu_cores=8,
                allocatable_cpu_cores=8,
                total_memory_bytes=32 * 1024**3,
                allocatable_memory_bytes=32 * 1024**3,
                total_disk_bytes=500 * 1024**3,
                allocatable_disk_bytes=500 * 1024**3,
            ),
        ],
    )
    monkeypatch.setattr(
        "app.services.scheduling.support.proxmox_service.get_config",
        lambda node, vmid, resource_type: {
            "rootfs": "local-lvm:subvol-1802-disk-0,size=8G",
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.support.proxmox_service.list_node_storages",
        lambda node: [{"storage": "local-lvm"}],
    )
    monkeypatch.setattr(
        "app.services.scheduling.support.proxmox_service.get_status",
        lambda node, vmid, resource_type: {"status": "running"},
    )

    selections = vm_request_placement_service.rebalance_active_assignments(
        session=db,
        requests=[request],
    )

    selection = selections[request.id]
    assert selection.node == "pve-a"


def test_rebalance_active_assignments_keeps_non_shared_storage_vm_on_current_node(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)
    request = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="VM on non-shared storage should stay on its current node.",
        resource_type="vm",
        hostname="non-shared-active-vm",
        cores=2,
        memory=4096,
        password="encrypted",
        storage="local-lvm",
        environment_type="Stable",
        status=VMRequestStatus.approved,
        start_at=now - timedelta(minutes=10),
        end_at=now + timedelta(hours=1),
        vmid=1803,
        assigned_node="pve-a",
        actual_node="pve-a",
        created_at=now - timedelta(hours=1),
    )

    monkeypatch.setattr(
        "app.services.vm.placement_service._build_rebalance_baseline_nodes",
        lambda **kwargs: [
            NodeCapacity(
                node="pve-a",
                status="online",
                candidate=True,
                guest_soft_limit=100,
                total_cpu_cores=8,
                allocatable_cpu_cores=5,
                total_memory_bytes=32 * 1024**3,
                allocatable_memory_bytes=16 * 1024**3,
                total_disk_bytes=500 * 1024**3,
                allocatable_disk_bytes=300 * 1024**3,
            ),
            NodeCapacity(
                node="pve-b",
                status="online",
                candidate=True,
                guest_soft_limit=100,
                total_cpu_cores=8,
                allocatable_cpu_cores=8,
                total_memory_bytes=32 * 1024**3,
                allocatable_memory_bytes=32 * 1024**3,
                total_disk_bytes=500 * 1024**3,
                allocatable_disk_bytes=500 * 1024**3,
            ),
        ],
    )
    monkeypatch.setattr(
        "app.services.scheduling.support.proxmox_service.get_config",
        lambda node, vmid, resource_type: {
            "scsi0": "local-lvm:vm-1803-disk-0,size=32G",
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.support.proxmox_service.list_node_storages",
        lambda node: [{"storage": "local-lvm", "shared": 0}],
    )
    monkeypatch.setattr(
        "app.services.scheduling.support.proxmox_service.get_status",
        lambda node, vmid, resource_type: {"status": "running"},
    )

    selections = vm_request_placement_service.rebalance_active_assignments(
        session=db,
        requests=[request],
    )

    selection = selections[request.id]
    assert selection.node == "pve-a"


def test_spec_change_review_stays_pending_when_apply_fails(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _create_user(db)
    reviewer = _create_user(db, is_superuser=True)
    request = SpecChangeRequest(
        vmid=456,
        user_id=user.id,
        change_type=SpecChangeType.cpu,
        reason="Need more CPU for workload spikes",
        current_cpu=2,
        requested_cpu=4,
        status=SpecChangeRequestStatus.pending,
        created_at=datetime.now(timezone.utc),
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    monkeypatch.setattr(
        "app.services.vm.spec_change_service.proxmox_service.find_resource",
        lambda vmid: {"node": "node-a", "type": "qemu"},
    )
    monkeypatch.setattr(
        "app.services.vm.spec_change_service.proxmox_service.update_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(ProxmoxError("apply failed")),
    )

    with pytest.raises(ProxmoxError):
        spec_change_service.review(
            session=db,
            request_id=request.id,
            review_data=SpecChangeRequestReview(
                status=SpecChangeRequestStatus.approved
            ),
            reviewer=reviewer,
        )

    db.expire_all()
    refreshed = db.exec(
        select(SpecChangeRequest).where(SpecChangeRequest.id == request.id)
    ).first()
    assert refreshed is not None
    assert refreshed.status == SpecChangeRequestStatus.pending
    assert refreshed.applied_at is None
    assert refreshed.reviewer_id is None


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
        "app.services.proxmox.provisioning_service.get_proxmox_settings",
        lambda: SimpleNamespace(pool_name="SkyLab"),
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
        "app.services.proxmox.provisioning_service.get_proxmox_settings",
        lambda: SimpleNamespace(pool_name="SkyLab"),
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


def test_user_role_teacher_is_treated_as_regular_user(db: Session) -> None:
    teacher = _create_user(db, role=UserRole.teacher)

    assert teacher.role == UserRole.teacher
    assert teacher.is_superuser is False
    assert teacher.is_instructor is False


def test_delete_user_rejects_owned_resources(db: Session) -> None:
    owner = _create_user(db)
    admin = _create_user(db, is_superuser=True)
    db.add(
        Resource(
            vmid=999,
            user_id=owner.id,
            environment_type="Owned VM",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    with pytest.raises(BadRequestError):
        user_service.delete_user(session=db, user_id=owner.id, current_user=admin)

    assert db.get(User, owner.id) is not None


def test_delete_user_removes_owned_migration_jobs(db: Session) -> None:
    owner = _create_user(db)
    admin = _create_user(db, is_superuser=True)
    now = datetime.now(timezone.utc)
    request = VMRequest(
        id=uuid.uuid4(),
        user_id=owner.id,
        reason="Cleanup request",
        resource_type="vm",
        hostname="cleanup-vm",
        cores=2,
        memory=2048,
        password="encrypted",
        storage="local-lvm",
        environment_type="Cleanup",
        status=VMRequestStatus.pending,
        created_at=now,
    )
    db.add(request)
    db.flush()
    job = VMMigrationJob(
        request_id=request.id,
        vmid=166,
        source_node="pve",
        target_node="pve2",
        status=VMMigrationJobStatus.pending,
        rebalance_epoch=9,
        requested_at=now,
        updated_at=now,
    )
    db.add(job)
    db.commit()

    user_service.delete_user(session=db, user_id=owner.id, current_user=admin)

    assert db.get(User, owner.id) is None
    assert db.get(VMRequest, request.id) is None
    assert db.get(VMMigrationJob, job.id) is None


@pytest.mark.parametrize(
    ("vmid", "desired_node", "actual_node", "expected_reason"),
    [
        (
            None,
            "pve2",
            "pve",
            "Migration queue cleared because the request no longer has a VMID.",
        ),
        (
            166,
            None,
            "pve",
            "Migration queue cleared because the request no longer has a desired target node.",
        ),
        (
            166,
            "pve2",
            "pve2",
            "Migration queue cleared because the request is already on the target node.",
        ),
    ],
)
def test_sync_request_migration_job_uses_specific_clear_reason(
    db: Session,
    vmid: int | None,
    desired_node: str | None,
    actual_node: str | None,
    expected_reason: str,
) -> None:
    now = datetime.now(timezone.utc)
    request = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="Status detail test",
        resource_type="vm",
        hostname="status-detail-vm",
        cores=2,
        memory=2048,
        password="encrypted",
        storage="local-lvm",
        environment_type="Cleanup",
        status=VMRequestStatus.approved,
        vmid=vmid,
        desired_node=desired_node,
        assigned_node=desired_node,
        actual_node=actual_node,
        migration_status=VMMigrationStatus.pending,
        created_at=now,
    )
    job = VMMigrationJob(
        request_id=request.id,
        vmid=166,
        source_node="pve",
        target_node="pve2",
        status=VMMigrationJobStatus.pending,
        rebalance_epoch=1,
        requested_at=now,
        updated_at=now,
    )
    db.add(request)
    db.add(job)
    db.commit()

    scheduling_support.sync_request_migration_job(
        session=db,
        request=request,
        source_node=actual_node,
        now=now,
    )
    db.commit()
    db.refresh(job)

    assert job.status == VMMigrationJobStatus.cancelled
    assert job.last_error == expected_reason


def test_vm_templates_are_filtered_by_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.infrastructure.proxmox.operations.get_proxmox_settings",
        type("Cfg", (), {"pool_name": "SkyLab"}),
    )
    monkeypatch.setattr(
        "app.infrastructure.proxmox.operations._raw_vms",
        lambda: [
            {"vmid": 100, "name": "allowed", "node": "node-a", "template": 1, "pool": "SkyLab"},
            {"vmid": 101, "name": "blocked", "node": "node-b", "template": 1, "pool": "OtherPool"},
            {"vmid": 102, "name": "not-template", "node": "node-c", "template": 0, "pool": "SkyLab"},
        ],
    )

    templates = proxmox_service.get_vm_templates()

    assert templates == [
        {"vmid": 100, "name": "allowed", "node": "node-a", "template": 1, "pool": "SkyLab"}
    ]


def test_local_rebalance_search_improves_unbalanced_initial_assignment(
    db: Session,
) -> None:
    now = datetime.now(timezone.utc)
    db.add(
        ProxmoxConfig(
            id=1,
            host="pve.local",
            user="root@pam",
            encrypted_password="encrypted",
            verify_ssl=False,
            iso_storage="local",
            data_storage="local-lvm",
            pool_name="SkyLab",
            placement_strategy="priority_dominant_share",
            rebalance_migration_cost=0.1,
            rebalance_search_max_relocations=2,
            rebalance_search_depth=3,
        )
    )
    db.commit()

    request_a = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="A",
        resource_type="vm",
        hostname="local-search-a",
        cores=4,
        memory=2048,
        password="encrypted",
        storage="local-lvm",
        environment_type="Test",
        status=VMRequestStatus.approved,
        start_at=now,
        end_at=now + timedelta(hours=1),
        actual_node="pve-a",
        assigned_node="pve-a",
        created_at=now,
    )
    request_b = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="B",
        resource_type="vm",
        hostname="local-search-b",
        cores=4,
        memory=2048,
        password="encrypted",
        storage="local-lvm",
        environment_type="Test",
        status=VMRequestStatus.approved,
        start_at=now,
        end_at=now + timedelta(hours=1),
        actual_node="pve-a",
        assigned_node="pve-a",
        created_at=now + timedelta(seconds=1),
    )

    improved = vm_request_placement_service._run_local_rebalance_search(
        session=db,
        ordered_requests=[request_a, request_b],
        baseline_nodes=[
                NodeCapacity(
                    node="pve-a",
                    status="online",
                    candidate=True,
                    guest_soft_limit=100,
                    total_cpu_cores=12,
                    allocatable_cpu_cores=12,
                    total_memory_bytes=64 * 1024**3,
                    allocatable_memory_bytes=64 * 1024**3,
                    total_disk_bytes=500 * 1024**3,
                    allocatable_disk_bytes=500 * 1024**3,
                ),
                NodeCapacity(
                    node="pve-b",
                    status="online",
                    candidate=True,
                    guest_soft_limit=100,
                    total_cpu_cores=12,
                    allocatable_cpu_cores=12,
                    total_memory_bytes=64 * 1024**3,
                    allocatable_memory_bytes=64 * 1024**3,
                    total_disk_bytes=500 * 1024**3,
                    allocatable_disk_bytes=500 * 1024**3,
            ),
        ],
        initial_assignments={
            request_a.id: "pve-a",
            request_b.id: "pve-a",
        },
        priorities={"pve-a": 5, "pve-b": 5},
        tuning=vm_request_placement_service._get_placement_tuning(session=db),
    )

    assert improved[request_a.id] != improved[request_b.id]


def test_reserved_target_node_preview_matches_active_rebalance_objective(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    db.add(
        ProxmoxConfig(
            id=1,
            host="pve.local",
            user="root@pam",
            encrypted_password="encrypted",
            verify_ssl=False,
            iso_storage="local",
            data_storage="local-lvm",
            pool_name="SkyLab",
            placement_strategy="priority_dominant_share",
            rebalance_migration_cost=0.5,
            rebalance_search_max_relocations=2,
            rebalance_search_depth=3,
        )
    )
    db.commit()

    monkeypatch.setattr(
        "app.services.vm.placement_service.placement_advisor._load_cluster_state",
        lambda: ([], []),
    )
    monkeypatch.setattr(
        "app.services.vm.placement_service.placement_advisor._build_node_capacities",
        lambda **kwargs: [
            NodeCapacity(
                node="pve-a",
                status="online",
                candidate=True,
                guest_soft_limit=100,
                total_cpu_cores=12,
                allocatable_cpu_cores=8,
                total_memory_bytes=64 * 1024**3,
                allocatable_memory_bytes=60 * 1024**3,
                total_disk_bytes=500 * 1024**3,
                allocatable_disk_bytes=480 * 1024**3,
            ),
            NodeCapacity(
                node="pve-b",
                status="online",
                candidate=True,
                guest_soft_limit=100,
                total_cpu_cores=12,
                allocatable_cpu_cores=12,
                total_memory_bytes=64 * 1024**3,
                allocatable_memory_bytes=64 * 1024**3,
                total_disk_bytes=500 * 1024**3,
                allocatable_disk_bytes=500 * 1024**3,
            ),
        ],
    )

    existing = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="existing",
        resource_type="vm",
        hostname="existing-vm",
        cores=4,
        memory=2048,
        password="encrypted",
        storage="local-lvm",
        environment_type="Test",
        status=VMRequestStatus.approved,
        start_at=now,
        end_at=now + timedelta(hours=2),
        vmid=101,
        assigned_node="pve-a",
        actual_node="pve-a",
        created_at=now,
    )

    selection = vm_request_placement_service.select_reserved_target_node_for_request(
        session=db,
        request=PlacementRequest(
            resource_type="vm",
            cpu_cores=4,
            memory_mb=2048,
            disk_gb=20,
            instance_count=1,
        ),
        start_at=now,
        end_at=now + timedelta(hours=2),
        reserved_requests=[existing],
    )

    assert selection.node == "pve-b"


def test_quick_template_reserved_target_skips_cohort_rebalance_preview(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    db.add(
        ProxmoxConfig(
            id=1,
            host="pve.local",
            user="root@pam",
            encrypted_password="encrypted",
            verify_ssl=False,
            iso_storage="local",
            data_storage="local-lvm",
            pool_name="SkyLab",
            placement_strategy="priority_dominant_share",
        )
    )
    db.commit()

    quick = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="quick",
        request_kind="quick_template",
        resource_type="lxc",
        hostname="quick-template",
        cores=1,
        memory=1024,
        password="encrypted",
        storage="local-lvm",
        environment_type="Quick",
        rootfs_size=8,
        status=VMRequestStatus.approved,
        start_at=now,
        end_at=now + timedelta(hours=3),
        created_at=now,
    )
    existing = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="existing",
        resource_type="lxc",
        hostname="existing-research",
        cores=2,
        memory=2048,
        password="encrypted",
        storage="local-lvm",
        environment_type="Research",
        rootfs_size=16,
        status=VMRequestStatus.approved,
        start_at=now,
        end_at=now + timedelta(hours=4),
        assigned_node="pve-a",
        created_at=now - timedelta(hours=1),
    )

    monkeypatch.setattr(
        "app.services.vm.placement_service.placement_advisor._load_cluster_state",
        lambda: ([], []),
    )
    monkeypatch.setattr(
        "app.services.vm.placement_service.placement_advisor._build_node_capacities",
        lambda **kwargs: [
            NodeCapacity(
                node="pve-a",
                status="online",
                candidate=True,
                guest_soft_limit=100,
                total_cpu_cores=12,
                allocatable_cpu_cores=12,
                total_memory_bytes=64 * 1024**3,
                allocatable_memory_bytes=64 * 1024**3,
                total_disk_bytes=500 * 1024**3,
                allocatable_disk_bytes=500 * 1024**3,
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.vm.placement_service._solve_rebalance_assignments",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("quick templates should not preview cohort rebalance")
        ),
    )

    selection = vm_request_placement_service.select_reserved_target_node(
        session=db,
        db_request=quick,
        reserved_requests=[existing],
    )

    assert selection.node == "pve-a"
    assert "without previewing cohort rebalance" in selection.plan.summary


def test_storage_selection_penalizes_high_contention_even_with_better_priority() -> None:
    tuning = vm_request_placement_service._PlacementTuning(
        migration_cost=0.15,
        peak_cpu_margin=1.1,
        peak_memory_margin=1.05,
        loadavg_warn_per_core=0.8,
        loadavg_max_per_core=1.5,
        loadavg_penalty_weight=0.9,
        disk_contention_warn_share=0.7,
        disk_contention_high_share=0.9,
        disk_penalty_weight=0.75,
        search_max_relocations=2,
        search_depth=3,
    )

    chosen = vm_request_placement_service._select_best_storage_for_request(
        storage_pools=[
            vm_request_placement_service._WorkingStoragePool(
                storage="priority-fast-but-hot",
                total_gb=100.0,
                avail_gb=25.0,
                active=True,
                enabled=True,
                can_vm=True,
                can_lxc=True,
                is_shared=False,
                speed_tier="nvme",
                user_priority=1,
            ),
            vm_request_placement_service._WorkingStoragePool(
                storage="slightly-lower-priority-but-cooler",
                total_gb=500.0,
                avail_gb=300.0,
                active=True,
                enabled=True,
                can_vm=True,
                can_lxc=True,
                is_shared=False,
                speed_tier="nvme",
                user_priority=5,
            ),
        ],
        resource_type="vm",
        disk_gb=20,
        disk_overcommit_ratio=1.0,
        tuning=tuning,
    )

    assert chosen is not None
    assert chosen.pool.storage == "slightly-lower-priority-but-cooler"


def test_process_pending_migration_jobs_skips_job_until_backoff_expires(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)
    request = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="Backoff test",
        resource_type="vm",
        hostname="backoff-vm",
        cores=2,
        memory=2048,
        password="encrypted",
        storage="local-lvm",
        environment_type="Backoff",
        status=VMRequestStatus.approved,
        start_at=now - timedelta(minutes=1),
        end_at=now + timedelta(hours=1),
        vmid=991,
        assigned_node="pve-b",
        desired_node="pve-a",
        actual_node="pve-b",
        migration_status=VMMigrationStatus.pending,
        created_at=now,
    )
    job = VMMigrationJob(
        request_id=request.id,
        vmid=991,
        source_node="pve-b",
        target_node="pve-a",
        status=VMMigrationJobStatus.pending,
        rebalance_epoch=1,
        requested_at=now - timedelta(minutes=1),
        available_at=now + timedelta(minutes=5),
        updated_at=now - timedelta(minutes=1),
    )
    db.add(request)
    db.add(job)
    db.commit()

    policy = vm_request_schedule_service._MigrationPolicy(
        enabled=True,
        max_per_rebalance=2,
        min_interval_minutes=0,
        retry_limit=3,
        worker_concurrency=2,
        claim_timeout_seconds=300,
        retry_backoff_seconds=120,
    )

    migrations_used = vm_request_schedule_service._process_pending_migration_jobs(
        session=db,
        now=now,
        policy=policy,
        active_requests=[request],
    )

    db.refresh(job)
    assert migrations_used == 0
    assert job.status == VMMigrationJobStatus.pending
    assert job.attempt_count == 0


def test_process_pending_migration_jobs_reclaims_expired_running_claim(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)
    request = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="Claim reclaim test",
        resource_type="vm",
        hostname="claim-recover-vm",
        cores=2,
        memory=2048,
        password="encrypted",
        storage="local-lvm",
        environment_type="Claim",
        status=VMRequestStatus.approved,
        start_at=now - timedelta(minutes=1),
        end_at=now + timedelta(hours=1),
        vmid=992,
        assigned_node="pve-a",
        desired_node="pve-a",
        actual_node="pve-b",
        migration_status=VMMigrationStatus.pending,
        created_at=now,
    )
    job = VMMigrationJob(
        request_id=request.id,
        vmid=992,
        source_node="pve-b",
        target_node="pve-a",
        status=VMMigrationJobStatus.running,
        rebalance_epoch=1,
        requested_at=now - timedelta(minutes=10),
        claimed_by="old-worker",
        claimed_at=now - timedelta(minutes=10),
        claim_expires_at=now - timedelta(minutes=5),
        updated_at=now - timedelta(minutes=10),
    )
    db.add(request)
    db.add(job)
    db.commit()

    resources = {992: {"vmid": 992, "node": "pve-b", "name": "claim-recover-vm", "type": "qemu"}}

    monkeypatch.setattr(
        "app.services.scheduling.coordinator._refresh_actual_node",
        lambda **kwargs: ("pve-b", {"node": "pve-b"}),
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_status",
        lambda node, vmid, resource_type: {"status": "running"},
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_config",
        lambda node, vmid, resource_type: {
            "scsi0": "local-lvm:vm-992-disk-0,size=20G",
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.list_node_storages",
        lambda node: [{"storage": "local-lvm", "shared": 1}],
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.migrate_resource",
        lambda *args, **kwargs: resources[992].update({"node": "pve-a"}) or "UPID:migrate",
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.find_resource",
        lambda vmid: resources[vmid],
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.audit_service.log_action",
        lambda *args, **kwargs: None,
    )

    policy = vm_request_schedule_service._MigrationPolicy(
        enabled=True,
        max_per_rebalance=2,
        min_interval_minutes=0,
        retry_limit=3,
        worker_concurrency=1,
        claim_timeout_seconds=300,
        retry_backoff_seconds=120,
    )

    migrations_used = vm_request_schedule_service._process_pending_migration_jobs(
        session=db,
        now=now,
        policy=policy,
        active_requests=[request],
    )

    db.refresh(request)
    db.refresh(job)
    assert migrations_used == 1
    assert job.status == VMMigrationJobStatus.completed
    assert job.claimed_by is None
    assert request.actual_node == "pve-a"
    assert request.migration_status == VMMigrationStatus.completed


def test_process_pending_migration_jobs_runs_claimed_jobs_in_parallel(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)
    requests: list[VMRequest] = []
    for index in range(2):
        request = VMRequest(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            reason=f"Parallel migration {index}",
            resource_type="vm",
            hostname=f"parallel-{index}",
            cores=2,
            memory=2048,
            password="encrypted",
            storage="local-lvm",
            environment_type="Parallel",
            status=VMRequestStatus.approved,
            start_at=now - timedelta(minutes=1),
            end_at=now + timedelta(hours=1),
            vmid=3000 + index,
            assigned_node="pve-a",
            desired_node="pve-b",
            actual_node="pve-a",
            migration_status=VMMigrationStatus.pending,
            created_at=now,
        )
        job = VMMigrationJob(
            request_id=request.id,
            vmid=request.vmid,
            source_node="pve-a",
            target_node="pve-b",
            status=VMMigrationJobStatus.pending,
            rebalance_epoch=1,
            requested_at=now,
            updated_at=now,
        )
        db.add(request)
        db.add(job)
        requests.append(request)
    db.commit()

    thread_ids: list[int] = []
    barrier = threading.Barrier(2)

    def _fake_process_claimed_migration_job(**kwargs):
        del kwargs
        thread_ids.append(threading.get_ident())
        barrier.wait(timeout=2)
        return True

    monkeypatch.setattr(
        "app.services.scheduling.coordinator._process_claimed_migration_job",
        _fake_process_claimed_migration_job,
    )

    migrations_used = vm_request_schedule_service._process_pending_migration_jobs(
        session=db,
        now=now,
        policy=vm_request_schedule_service._MigrationPolicy(
            enabled=True,
            max_per_rebalance=2,
            min_interval_minutes=0,
            retry_limit=3,
            worker_concurrency=2,
            claim_timeout_seconds=300,
            retry_backoff_seconds=120,
        ),
        active_requests=requests,
    )

    assert migrations_used == 2
    assert len(set(thread_ids)) == 2


def test_process_pending_migration_jobs_deletes_orphaned_jobs(
    db: Session,
) -> None:
    now = datetime.now(timezone.utc)
    request = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="Orphan cleanup test",
        resource_type="vm",
        hostname="orphan-cleanup-vm",
        cores=2,
        memory=2048,
        password="encrypted",
        storage="local-lvm",
        environment_type="Cleanup",
        status=VMRequestStatus.approved,
        start_at=now - timedelta(minutes=1),
        end_at=now + timedelta(hours=1),
        vmid=994,
        assigned_node="pve",
        desired_node="pve2",
        actual_node="pve",
        migration_status=VMMigrationStatus.pending,
        created_at=now,
    )
    job = VMMigrationJob(
        request_id=request.id,
        vmid=994,
        source_node="pve",
        target_node="pve2",
        status=VMMigrationJobStatus.pending,
        rebalance_epoch=9,
        requested_at=now,
        updated_at=now,
    )
    db.add(request)
    db.add(job)
    db.commit()
    job_id = job.id

    stale_request = request
    db.delete(request)
    db.commit()

    migrations_used = vm_request_schedule_service._process_pending_migration_jobs(
        session=db,
        now=now,
        policy=vm_request_schedule_service._MigrationPolicy(
            enabled=True,
            max_per_rebalance=2,
            min_interval_minutes=0,
            retry_limit=3,
            worker_concurrency=1,
            claim_timeout_seconds=300,
            retry_backoff_seconds=120,
        ),
        active_requests=[stale_request],
    )

    assert migrations_used == 0
    assert db.get(VMMigrationJob, job_id) is None


def test_migrate_request_to_desired_node_refreshes_job_claim_while_waiting(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)
    request = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="Heartbeat test",
        resource_type="vm",
        hostname="heartbeat-vm",
        cores=2,
        memory=2048,
        password="encrypted",
        storage="local-lvm",
        environment_type="Heartbeat",
        status=VMRequestStatus.approved,
        start_at=now - timedelta(minutes=1),
        end_at=now + timedelta(hours=1),
        vmid=993,
        assigned_node="pve-a",
        desired_node="pve-b",
        actual_node="pve-a",
        migration_status=VMMigrationStatus.pending,
        created_at=now,
    )
    job = VMMigrationJob(
        request_id=request.id,
        vmid=993,
        source_node="pve-a",
        target_node="pve-b",
        status=VMMigrationJobStatus.pending,
        rebalance_epoch=1,
        requested_at=now - timedelta(minutes=1),
        updated_at=now - timedelta(minutes=1),
    )
    db.add(request)
    db.add(job)
    db.commit()

    refresh_calls: list[datetime] = []
    monotonic_values = iter([0.0, 16.0, 16.0, 32.0])
    utc_values = iter([now, now + timedelta(seconds=16), now + timedelta(seconds=35)])

    monkeypatch.setattr(
        "app.services.scheduling.coordinator.time.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator._utc_now",
        lambda: next(utc_values),
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_status",
        lambda node, vmid, resource_type: {"status": "running"},
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.get_config",
        lambda node, vmid, resource_type: {
            "scsi0": "local-lvm:vm-993-disk-0,size=20G",
        },
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.list_node_storages",
        lambda node: [{"storage": "local-lvm", "shared": 1}],
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.find_resource",
        lambda vmid: {"vmid": vmid, "node": "pve-b", "name": "heartbeat-vm", "type": "qemu"},
    )

    original_extend = vm_request_schedule_service.vm_migration_job_repo.extend_job_claim

    def _extend_job_claim(**kwargs):
        refresh_calls.append(kwargs["now"])
        return original_extend(**kwargs)

    monkeypatch.setattr(
        "app.services.scheduling.coordinator.vm_migration_job_repo.extend_job_claim",
        _extend_job_claim,
    )

    def _migrate(source_node, target_node, vmid, resource_type, online=True, progress_callback=None, **kwargs):
        assert progress_callback is not None
        progress_callback({"status": "running"})
        return "UPID:migrate"

    monkeypatch.setattr(
        "app.services.scheduling.coordinator.proxmox_service.migrate_resource",
        _migrate,
    )
    monkeypatch.setattr(
        "app.services.scheduling.coordinator.audit_service.log_action",
        lambda *args, **kwargs: None,
    )

    result_node, migrated = vm_request_schedule_service._migrate_request_to_desired_node(
        session=db,
        request=request,
        current_node="pve-a",
        now=now,
        policy=vm_request_schedule_service._MigrationPolicy(
            enabled=True,
            max_per_rebalance=2,
            min_interval_minutes=0,
            retry_limit=3,
            worker_concurrency=1,
            claim_timeout_seconds=30,
            retry_backoff_seconds=120,
        ),
        migrations_used=0,
        job=job,
    )

    db.refresh(job)
    assert migrated is True
    assert result_node == "pve-b"
    assert len(refresh_calls) == 1
    assert refresh_calls[0] == now + timedelta(seconds=16)
    assert job.claimed_by is None
