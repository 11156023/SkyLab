import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import selectinload
from sqlmodel import Session, func, select

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


def mark_spec_change_applied(
    *, session: Session, request_id: uuid.UUID, commit: bool = True
) -> SpecChangeRequest:
    db_request = get_spec_change_request_by_id(
        session=session, request_id=request_id, for_update=True
    )
    if not db_request:
        raise ValueError(f"Spec change request {request_id} not found")
    db_request.applied_at = datetime.now(timezone.utc)
    session.add(db_request)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(db_request)
    return db_request
