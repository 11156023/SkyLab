import uuid
from datetime import date, datetime, timezone

from sqlalchemy import ColumnElement, and_, or_
from sqlalchemy.orm import selectinload
from sqlmodel import Session, col, func, select

from app.models import Resource, SpecChangeRequest, SpecChangeRequestStatus


def create_spec_change_request(
    *,
    session: Session,
    user_id: uuid.UUID,
    vmid: int,
    change_type: str,
    reason: str,
    current_cpu: int | None = None,
    current_memory: int | None = None,
    current_disk: int | None = None,
    requested_cpu: int | None = None,
    requested_memory: int | None = None,
    requested_disk: int | None = None,
    current_expiry_date: date | None = None,
    requested_expiry_date: date | None = None,
    commit: bool = True,
) -> SpecChangeRequest:
    db_request = SpecChangeRequest(
        vmid=vmid,
        resource_vmid=vmid if session.get(Resource, vmid) is not None else None,
        user_id=user_id,
        change_type=change_type,
        reason=reason,
        current_cpu=current_cpu,
        current_memory=current_memory,
        current_disk=current_disk,
        requested_cpu=requested_cpu,
        requested_memory=requested_memory,
        requested_disk=requested_disk,
        current_expiry_date=current_expiry_date,
        requested_expiry_date=requested_expiry_date,
        status=SpecChangeRequestStatus.pending,
        created_at=datetime.now(timezone.utc),
    )
    session.add(db_request)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(db_request)
    return db_request


def get_spec_change_request_by_id(
    *, session: Session, request_id: uuid.UUID, for_update: bool = False
) -> SpecChangeRequest | None:
    statement = (
        select(SpecChangeRequest)
        .options(selectinload(SpecChangeRequest.user))
        .options(selectinload(SpecChangeRequest.reviewer))
        .where(SpecChangeRequest.id == request_id)
    )
    if for_update:
        statement = statement.with_for_update()
    return session.exec(statement).first()


# 「處理中」＝待審核，或已核准但申請人尚未套用完成。同一台機器同時只能有一張，
# 否則兩張以快照計算的磁碟增量會疊加（A 100→200 核准後，B 100→150 會再 +50）。
def _open_request_filter() -> ColumnElement[bool]:
    return or_(
        col(SpecChangeRequest.status) == SpecChangeRequestStatus.pending,
        and_(
            col(SpecChangeRequest.status) == SpecChangeRequestStatus.approved,
            col(SpecChangeRequest.applied_at).is_(None),
        ),
    )


def get_open_spec_change_request_by_vmid(
    *, session: Session, vmid: int
) -> SpecChangeRequest | None:
    statement = (
        select(SpecChangeRequest)
        .options(selectinload(SpecChangeRequest.user))
        .where(SpecChangeRequest.vmid == vmid)
        .where(_open_request_filter())
        .order_by(SpecChangeRequest.created_at.desc())
    )
    return session.exec(statement).first()


def cancel_open_spec_change_requests_for_vmid(
    *, session: Session, vmid: int, comment: str, commit: bool = True
) -> int:
    """機器刪除時把該 vmid 的處理中申請全部標成 cancelled，避免日後 VMID 被
    新機器回收後，舊申請被核准／套用到別人的機器上。回傳筆數。"""
    statement = (
        select(SpecChangeRequest)
        .where(SpecChangeRequest.vmid == vmid)
        .where(_open_request_filter())
    )
    rows = list(session.exec(statement).all())
    now = datetime.now(timezone.utc)
    for row in rows:
        row.status = SpecChangeRequestStatus.cancelled
        row.review_comment = comment
        row.reviewed_at = now
        session.add(row)
    if rows:
        if commit:
            session.commit()
        else:
            session.flush()
    return len(rows)


def get_spec_change_requests_by_user(
    *, session: Session, user_id: uuid.UUID, skip: int = 0, limit: int = 100
) -> tuple[list[SpecChangeRequest], int]:
    count = session.exec(
        select(func.count())
        .select_from(SpecChangeRequest)
        .where(SpecChangeRequest.user_id == user_id)
    ).one()
    statement = (
        select(SpecChangeRequest)
        .options(selectinload(SpecChangeRequest.user))
        .options(selectinload(SpecChangeRequest.reviewer))
        .where(SpecChangeRequest.user_id == user_id)
        .order_by(SpecChangeRequest.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(session.exec(statement).all()), count


def get_all_spec_change_requests(
    *,
    session: Session,
    skip: int = 0,
    limit: int = 100,
    status: SpecChangeRequestStatus | str | None = None,
    vmid: int | None = None,
) -> tuple[list[SpecChangeRequest], int]:
    filters = []
    if status is not None:
        if isinstance(status, str):
            status = SpecChangeRequestStatus(status)
        filters.append(SpecChangeRequest.status == status)
    if vmid is not None:
        filters.append(SpecChangeRequest.vmid == vmid)

    count_statement = select(func.count()).select_from(SpecChangeRequest)
    for f in filters:
        count_statement = count_statement.where(f)
    count = session.exec(count_statement).one()

    statement = (
        select(SpecChangeRequest)
        .options(selectinload(SpecChangeRequest.user))
        .options(selectinload(SpecChangeRequest.reviewer))
        .order_by(SpecChangeRequest.created_at.desc())
    )
    for f in filters:
        statement = statement.where(f)
    statement = statement.offset(skip).limit(limit)
    return list(session.exec(statement).all()), count


def update_spec_change_request_status(
    *,
    session: Session,
    request_id: uuid.UUID,
    status: SpecChangeRequestStatus | str,
    reviewer_id: uuid.UUID,
    review_comment: str | None = None,
    commit: bool = True,
) -> SpecChangeRequest:
    if isinstance(status, str):
        status = SpecChangeRequestStatus(status)
    db_request = get_spec_change_request_by_id(
        session=session, request_id=request_id, for_update=True
    )
    if not db_request:
        raise ValueError(f"Spec change request {request_id} not found")
    db_request.status = status
    db_request.reviewer_id = reviewer_id
    db_request.review_comment = review_comment
    db_request.reviewed_at = datetime.now(timezone.utc)
    session.add(db_request)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(db_request)
    return db_request


def update_spec_change_current_specs(
    *,
    session: Session,
    request_id: uuid.UUID,
    current_cpu: int | None,
    current_memory: int | None,
    current_disk: int | None,
    commit: bool = True,
) -> SpecChangeRequest:
    """用 Proxmox 上實際生效的值覆寫建立時的「目前規格」快照。"""
    db_request = get_spec_change_request_by_id(
        session=session, request_id=request_id, for_update=True
    )
    if not db_request:
        raise ValueError(f"Spec change request {request_id} not found")
    db_request.current_cpu = current_cpu
    db_request.current_memory = current_memory
    db_request.current_disk = current_disk
    session.add(db_request)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(db_request)
    return db_request


def mark_spec_change_apply_started(
    *, session: Session, request_id: uuid.UUID, commit: bool = True
) -> SpecChangeRequest:
    db_request = get_spec_change_request_by_id(
        session=session, request_id=request_id, for_update=True
    )
    if not db_request:
        raise ValueError(f"Spec change request {request_id} not found")
    db_request.apply_started_at = datetime.now(timezone.utc)
    db_request.apply_error = None
    session.add(db_request)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(db_request)
    return db_request


def mark_spec_change_applied(
    *,
    session: Session,
    request_id: uuid.UUID,
    warning: str | None = None,
    commit: bool = True,
) -> SpecChangeRequest:
    """規格已寫進 Proxmox。``warning`` 用於「已套用但自動開機失敗」這類
    不影響套用結果、但使用者必須知道的情況。"""
    db_request = get_spec_change_request_by_id(
        session=session, request_id=request_id, for_update=True
    )
    if not db_request:
        raise ValueError(f"Spec change request {request_id} not found")
    db_request.applied_at = datetime.now(timezone.utc)
    db_request.apply_error = warning
    session.add(db_request)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(db_request)
    return db_request


def mark_spec_change_apply_failed(
    *, session: Session, request_id: uuid.UUID, error: str, commit: bool = True
) -> SpecChangeRequest:
    db_request = get_spec_change_request_by_id(
        session=session, request_id=request_id, for_update=True
    )
    if not db_request:
        raise ValueError(f"Spec change request {request_id} not found")
    db_request.apply_error = error[:2000]
    session.add(db_request)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(db_request)
    return db_request
