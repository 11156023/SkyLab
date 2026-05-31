import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.orm import selectinload
from sqlmodel import Session, func, select

from app.models import VMMigrationStatus, VMRequest, VMRequestStatus
from app.repositories import resource as resource_repo
from app.schemas import VMRequestCreate
from app.utils.hostname import to_punycode_hostname


def create_vm_request(
    *,
    session: Session,
    vm_request_in: VMRequestCreate,
    user_id: uuid.UUID,
    encrypted_password: str,
    commit: bool = True,
) -> VMRequest:
    """Create VM request. Password should be pre-encrypted by the service layer.

    Hostname is normalised to Punycode on creation so all downstream
    comparisons use a single canonical form.
    """
    db_request = VMRequest(
        user_id=user_id,
        reason=vm_request_in.reason,
        resource_type=vm_request_in.resource_type,
        request_kind=(
            "quick_template"
            if getattr(vm_request_in, "mode", "scheduled") == "quick_template"
            else "research"
        ),
        hostname=to_punycode_hostname(vm_request_in.hostname),
        cores=vm_request_in.cores,
        memory=vm_request_in.memory,
        password=encrypted_password,
        storage=vm_request_in.storage,
        environment_type=vm_request_in.environment_type,
        os_info=vm_request_in.os_info,
        expiry_date=vm_request_in.expiry_date,
        start_at=vm_request_in.start_at,
        end_at=vm_request_in.end_at,
        ostemplate=vm_request_in.ostemplate,
        rootfs_size=vm_request_in.rootfs_size,
        template_id=vm_request_in.template_id,
        disk_size=vm_request_in.disk_size,
        username=vm_request_in.username,
        gpu_mapping_id=vm_request_in.gpu_mapping_id,
        service_template_slug=vm_request_in.service_template_slug,
        service_template_script_path=vm_request_in.service_template_script_path,
        status=VMRequestStatus.pending,
        migration_status=VMMigrationStatus.idle,
        created_at=datetime.now(timezone.utc),
    )
    session.add(db_request)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(db_request)
    return db_request


def get_vm_request_by_id(
    *,
    session: Session,
    request_id: uuid.UUID,
    for_update: bool = False,
    skip_locked: bool = False,
) -> VMRequest | None:
    statement = (
        select(VMRequest)
        .where(VMRequest.id == request_id)
        .options(selectinload(VMRequest.user))  # type: ignore[arg-type]
    )
    if for_update:
        statement = statement.with_for_update(skip_locked=skip_locked)
        statement = statement.execution_options(populate_existing=True)
    return session.exec(statement).first()


def get_vm_requests_by_user(
    *, session: Session, user_id: uuid.UUID, skip: int = 0, limit: int = 100
) -> tuple[list[VMRequest], int]:
    count = session.exec(
        select(func.count()).select_from(VMRequest).where(VMRequest.user_id == user_id)
    ).one()
    statement = (
        select(VMRequest)
        .where(VMRequest.user_id == user_id)
        .options(selectinload(VMRequest.user))  # type: ignore[arg-type]
        .order_by(VMRequest.created_at.desc())  # type: ignore[union-attr]
        .offset(skip)
        .limit(limit)
    )
    return list(session.exec(statement).all()), count


def get_all_vm_requests(
    *,
    session: Session,
    status: VMRequestStatus | None = None,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[VMRequest], int]:
    base = select(func.count()).select_from(VMRequest)
    if status:
        base = base.where(VMRequest.status == status)
    count = session.exec(base).one()

    statement = (
        select(VMRequest)
        .options(selectinload(VMRequest.user))  # type: ignore[arg-type]
        .order_by(VMRequest.created_at.desc())  # type: ignore[union-attr]
    )
    if status:
        statement = statement.where(VMRequest.status == status)
    return list(session.exec(statement.offset(skip).limit(limit)).all()), count


def count_quick_template_requests_for_user(
    *,
    session: Session,
    user_id: uuid.UUID,
    since: datetime | None = None,
    active_at: datetime | None = None,
) -> int:
    statement = select(func.count()).select_from(VMRequest).where(
        VMRequest.user_id == user_id,
        VMRequest.request_kind == "quick_template",
    )
    if since is not None:
        statement = statement.where(VMRequest.created_at >= since)
    if active_at is not None:
        statement = statement.where(
            VMRequest.status.in_(
                (
                    VMRequestStatus.pending,
                    VMRequestStatus.approved,
                )
            ),
            VMRequest.migration_status != VMMigrationStatus.failed,
            VMRequest.start_at.is_not(None),
            VMRequest.start_at <= active_at,
            sa.or_(VMRequest.end_at.is_(None), VMRequest.end_at > active_at),
        )
    return int(session.exec(statement).one())


_ACTIVE_STATUSES = (
    VMRequestStatus.approved,
)


def get_approved_vm_requests_overlapping_window(
    *,
    session: Session,
    window_start: datetime,
    window_end: datetime,
) -> list[VMRequest]:
    statement = (
        select(VMRequest)
        .where(
            VMRequest.status.in_(_ACTIVE_STATUSES),
            VMRequest.migration_status != VMMigrationStatus.failed,
            VMRequest.start_at.is_not(None),
            VMRequest.desired_node.is_not(None),
            VMRequest.start_at < window_end,
            sa.or_(VMRequest.end_at.is_(None), VMRequest.end_at > window_start),
        )
        .options(selectinload(VMRequest.user))  # type: ignore[arg-type]
        .order_by(VMRequest.start_at.asc(), VMRequest.reviewed_at.asc())  # type: ignore[union-attr]
    )
    return list(session.exec(statement).all())


def lock_overlapping_vm_requests_for_window(
    *,
    session: Session,
    window_start: datetime,
    window_end: datetime,
    statuses: tuple[VMRequestStatus, ...] = (
        VMRequestStatus.pending,
        VMRequestStatus.approved,
    ),
) -> list[VMRequest]:
    statement = (
        select(VMRequest)
        .where(
            VMRequest.status.in_(statuses),
            VMRequest.migration_status != VMMigrationStatus.failed,
            VMRequest.start_at.is_not(None),
            VMRequest.start_at < window_end,
            sa.or_(VMRequest.end_at.is_(None), VMRequest.end_at > window_start),
        )
        .options(selectinload(VMRequest.user))  # type: ignore[arg-type]
        .order_by(
            VMRequest.start_at.asc(),  # type: ignore[union-attr]
            VMRequest.created_at.asc(),  # type: ignore[union-attr]
            VMRequest.id.asc(),
        )
        .with_for_update()
    )
    return list(session.exec(statement).all())


def update_vm_request_status(
    *,
    session: Session,
    db_request: VMRequest,
    status: VMRequestStatus,
    reviewer_id: uuid.UUID,
    review_comment: str | None = None,
    vmid: int | None = None,
    assigned_node: str | None = None,
    desired_node: str | None = None,
    actual_node: str | None = None,
    placement_strategy_used: str | None = None,
    migration_status: VMMigrationStatus | None = None,
    migration_error: str | None = None,
    rebalance_epoch: int | None = None,
    last_rebalanced_at: datetime | None = None,
    last_migrated_at: datetime | None = None,
    commit: bool = True,
) -> VMRequest:
    db_request.status = status
    db_request.reviewer_id = reviewer_id
    db_request.review_comment = review_comment
    db_request.reviewed_at = datetime.now(timezone.utc)
    if vmid is not None:
        db_request.vmid = vmid
    if assigned_node is not None:
        db_request.assigned_node = assigned_node
    if desired_node is not None:
        db_request.desired_node = desired_node
    if actual_node is not None:
        db_request.actual_node = actual_node
    if placement_strategy_used is not None:
        db_request.placement_strategy_used = placement_strategy_used
    if migration_status is not None:
        db_request.migration_status = migration_status
    db_request.migration_error = migration_error
    if rebalance_epoch is not None:
        db_request.rebalance_epoch = rebalance_epoch
    if last_rebalanced_at is not None:
        db_request.last_rebalanced_at = last_rebalanced_at
    if last_migrated_at is not None:
        db_request.last_migrated_at = last_migrated_at
    session.add(db_request)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(db_request)
    return db_request


def update_vm_request_provisioning(
    *,
    session: Session,
    db_request: VMRequest,
    vmid: int | None,
    assigned_node: str | None = None,
    desired_node: str | None = None,
    actual_node: str | None = None,
    placement_strategy_used: str | None = None,
    migration_status: VMMigrationStatus | None = None,
    migration_error: str | None = None,
    rebalance_epoch: int | None = None,
    last_rebalanced_at: datetime | None = None,
    last_migrated_at: datetime | None = None,
    commit: bool = True,
) -> VMRequest:
    if vmid is not None:
        db_request.vmid = vmid
    db_request.assigned_node = assigned_node
    db_request.desired_node = desired_node if desired_node is not None else assigned_node
    if actual_node is not None:
        db_request.actual_node = actual_node
    db_request.placement_strategy_used = placement_strategy_used
    if migration_status is not None:
        db_request.migration_status = migration_status
    db_request.migration_error = migration_error
    if rebalance_epoch is not None:
        db_request.rebalance_epoch = rebalance_epoch
    if last_rebalanced_at is not None:
        db_request.last_rebalanced_at = last_rebalanced_at
    if last_migrated_at is not None:
        db_request.last_migrated_at = last_migrated_at
    session.add(db_request)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(db_request)
    return db_request


def clear_vm_request_provisioning(
    *,
    session: Session,
    db_request: VMRequest,
    commit: bool = True,
) -> VMRequest:
    stale_vmid = db_request.vmid
    db_request.vmid = None
    db_request.assigned_node = None
    db_request.desired_node = None
    db_request.actual_node = None
    db_request.placement_strategy_used = None
    db_request.migration_status = VMMigrationStatus.idle
    db_request.migration_error = None
    session.add(db_request)
    if stale_vmid is not None:
        resource_repo.delete_resource(
            session=session,
            vmid=stale_vmid,
            commit=False,
        )
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(db_request)
    return db_request


def get_latest_approved_vm_request_by_vmid(
    *, session: Session, vmid: int
) -> VMRequest | None:
    statement = (
        select(VMRequest)
        .where(
            VMRequest.vmid == vmid,
            VMRequest.status.in_(_ACTIVE_STATUSES),
        )
        .options(selectinload(VMRequest.user))  # type: ignore[arg-type]
        .order_by(VMRequest.reviewed_at.desc(), VMRequest.created_at.desc())  # type: ignore[union-attr]
    )
    return session.exec(statement).first()


def list_active_approved_vm_requests(
    *,
    session: Session,
    at_time: datetime,
) -> list[VMRequest]:
    statement = (
        select(VMRequest)
        .where(
            VMRequest.status.in_(_ACTIVE_STATUSES),
            VMRequest.migration_status != VMMigrationStatus.failed,
            VMRequest.start_at.is_not(None),
            VMRequest.start_at <= at_time,
            sa.or_(VMRequest.end_at.is_(None), VMRequest.end_at > at_time),
        )
        .options(selectinload(VMRequest.user))  # type: ignore[arg-type]
        .order_by(
            VMRequest.start_at.asc(),  # type: ignore[union-attr]
            VMRequest.reviewed_at.asc(),  # type: ignore[union-attr]
            VMRequest.created_at.asc(),  # type: ignore[union-attr]
        )
    )
    return list(session.exec(statement).all())


def list_due_for_rebalance_vm_requests(
    *,
    session: Session,
    at_time: datetime,
    interval_minutes: int = 15,
) -> list[VMRequest]:
    rebalance_before = at_time - timedelta(minutes=max(int(interval_minutes or 0), 0))
    statement = (
        select(VMRequest)
        .where(
            VMRequest.status.in_(_ACTIVE_STATUSES),
            VMRequest.migration_status != VMMigrationStatus.failed,
            VMRequest.start_at.is_not(None),
            VMRequest.start_at <= at_time,
            sa.or_(VMRequest.end_at.is_(None), VMRequest.end_at > at_time),
            (
                VMRequest.last_rebalanced_at.is_(None)
                | (VMRequest.last_rebalanced_at < VMRequest.start_at)
                | (VMRequest.last_rebalanced_at <= rebalance_before)
            ),
        )
        .options(selectinload(VMRequest.user))  # type: ignore[arg-type]
        .order_by(
            VMRequest.start_at.asc(),  # type: ignore[union-attr]
            VMRequest.created_at.asc(),  # type: ignore[union-attr]
        )
        .with_for_update()
    )
    return list(session.exec(statement).all())
