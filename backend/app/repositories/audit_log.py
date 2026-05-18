import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import selectinload
from sqlmodel import Session, func, select

from app.models import AuditAction, AuditLog, Resource


def create_audit_log(
    *,
    session: Session,
    user_id: uuid.UUID | None,
    vmid: int | None,
    action: AuditAction | str,
    details: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    commit: bool = True,
) -> AuditLog:
    if isinstance(action, str):
        action = AuditAction(action)
    resource_vmid = vmid if vmid is not None and session.get(Resource, vmid) is not None else None
    db_log = AuditLog(
        user_id=user_id,
        vmid=vmid,
        resource_vmid=resource_vmid,
        action=action,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent,
        created_at=datetime.now(timezone.utc),
    )
    session.add(db_log)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(db_log)
    return db_log


def _build_filters(
    *,
    vmid: int | None = None,
    user_id: uuid.UUID | None = None,
    action: AuditAction | str | None = None,
    actions: list[AuditAction] | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    ip_address: str | None = None,
    search: str | None = None,
):
    filters = []
    if vmid is not None:
        filters.append(AuditLog.vmid == vmid)
    if user_id is not None:
        filters.append(AuditLog.user_id == user_id)
    if action is not None:
        if isinstance(action, str):
            action = AuditAction(action)
        filters.append(AuditLog.action == action)
    if actions:
        filters.append(AuditLog.action.in_(actions))
    if start_time is not None:
        filters.append(AuditLog.created_at >= start_time)
    if end_time is not None:
        filters.append(AuditLog.created_at <= end_time)
    if ip_address:
        filters.append(AuditLog.ip_address.ilike(f"%{ip_address}%"))
    if search:
        like = f"%{search}%"
        filters.append(AuditLog.details.ilike(like))
    return filters


def get_audit_logs(
    *,
    session: Session,
    skip: int = 0,
    limit: int = 100,
    vmid: int | None = None,
    user_id: uuid.UUID | None = None,
    action: AuditAction | str | None = None,
    actions: list[AuditAction] | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    ip_address: str | None = None,
    search: str | None = None,
) -> tuple[list[AuditLog], int]:
    filters = _build_filters(
        vmid=vmid,
        user_id=user_id,
        action=action,
        actions=actions,
        start_time=start_time,
        end_time=end_time,
        ip_address=ip_address,
        search=search,
    )

    count_statement = select(func.count()).select_from(AuditLog)
    for f in filters:
        count_statement = count_statement.where(f)
    count = session.exec(count_statement).one()

    statement = (
        select(AuditLog)
        .options(selectinload(AuditLog.user))
        .order_by(AuditLog.created_at.desc())
    )
    for f in filters:
        statement = statement.where(f)
    statement = statement.offset(skip).limit(limit)
    return list(session.exec(statement).all()), count


def iter_audit_logs_for_export(
    *,
    session: Session,
    vmid: int | None = None,
    user_id: uuid.UUID | None = None,
    action: AuditAction | str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    ip_address: str | None = None,
    search: str | None = None,
    max_rows: int = 50000,
) -> list[AuditLog]:
    filters = _build_filters(
        vmid=vmid,
        user_id=user_id,
        action=action,
        start_time=start_time,
        end_time=end_time,
        ip_address=ip_address,
        search=search,
    )
    statement = (
        select(AuditLog)
        .options(selectinload(AuditLog.user))
        .order_by(AuditLog.created_at.desc())
    )
    for f in filters:
        statement = statement.where(f)
    statement = statement.limit(max_rows)
    return list(session.exec(statement).all())


def get_audit_stats(
    *,
    session: Session,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict:
    """Aggregate stats for the admin dashboard within a time window."""
    base = select(func.count()).select_from(AuditLog)
    if start_time is not None:
        base = base.where(AuditLog.created_at >= start_time)
    if end_time is not None:
        base = base.where(AuditLog.created_at <= end_time)

    total = session.exec(base).one()

    danger_actions = [
        AuditAction.resource_delete,
        AuditAction.resource_reset,
        AuditAction.snapshot_delete,
        AuditAction.user_delete,
        AuditAction.group_delete,
    ]
    danger_stmt = select(func.count()).select_from(AuditLog).where(
        AuditLog.action.in_(danger_actions)
    )
    login_failed_stmt = select(func.count()).select_from(AuditLog).where(
        AuditLog.action.in_([AuditAction.login_failed, AuditAction.login_google_failed])
    )
    active_users_stmt = (
        select(func.count(func.distinct(AuditLog.user_id)))
        .select_from(AuditLog)
        .where(AuditLog.user_id.is_not(None))
    )
    if start_time is not None:
        danger_stmt = danger_stmt.where(AuditLog.created_at >= start_time)
        login_failed_stmt = login_failed_stmt.where(AuditLog.created_at >= start_time)
        active_users_stmt = active_users_stmt.where(AuditLog.created_at >= start_time)
    if end_time is not None:
        danger_stmt = danger_stmt.where(AuditLog.created_at <= end_time)
        login_failed_stmt = login_failed_stmt.where(AuditLog.created_at <= end_time)
        active_users_stmt = active_users_stmt.where(AuditLog.created_at <= end_time)

    return {
        "total": total,
        "danger": session.exec(danger_stmt).one(),
        "login_failed": session.exec(login_failed_stmt).one(),
        "active_users": session.exec(active_users_stmt).one(),
    }


def get_audit_logs_by_user(
    *, session: Session, user_id: uuid.UUID, skip: int = 0, limit: int = 100
) -> tuple[list[AuditLog], int]:
    return get_audit_logs(session=session, user_id=user_id, skip=skip, limit=limit)


def get_audit_logs_by_vmid(
    *, session: Session, vmid: int, skip: int = 0, limit: int = 100
) -> tuple[list[AuditLog], int]:
    return get_audit_logs(session=session, vmid=vmid, skip=skip, limit=limit)


def delete_audit_logs_by_vmid(*, session: Session, vmid: int) -> int:
    """刪除指定 vmid 的所有操作紀錄，返回刪除筆數。"""
    logs = list(session.exec(select(AuditLog).where(AuditLog.vmid == vmid)).all())
    for log in logs:
        session.delete(log)
    session.commit()
    return len(logs)
