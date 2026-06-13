import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func as sa_func
from sqlmodel import Session, select

from app.models import Resource, VMMigrationJob, VMMigrationJobStatus

_OPEN_JOB_STATUSES = (
    VMMigrationJobStatus.pending,
    VMMigrationJobStatus.running,
)


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def get_open_job_for_request(
    *,
    session: Session,
    request_id: uuid.UUID,
) -> VMMigrationJob | None:
    statement = (
        select(VMMigrationJob)
        .where(
            VMMigrationJob.request_id == request_id,
            VMMigrationJob.status.in_(_OPEN_JOB_STATUSES),
        )
        .order_by(VMMigrationJob.requested_at.desc())
    )
    return session.exec(statement).first()


def get_latest_job_for_request(
    *,
    session: Session,
    request_id: uuid.UUID,
) -> VMMigrationJob | None:
    statement = (
        select(VMMigrationJob)
        .where(VMMigrationJob.request_id == request_id)
        .order_by(VMMigrationJob.requested_at.desc(), VMMigrationJob.updated_at.desc())
    )
    return session.exec(statement).first()


def create_or_update_pending_job(
    *,
    session: Session,
    request_id: uuid.UUID,
    vmid: int | None,
    source_node: str | None,
    target_node: str,
    rebalance_epoch: int,
    last_error: str | None = None,
    requested_at: datetime | None = None,
    available_at: datetime | None = None,
    commit: bool = True,
) -> VMMigrationJob:
    now = requested_at or datetime.now(timezone.utc)
    existing = get_open_job_for_request(session=session, request_id=request_id)
    resource_vmid = vmid if vmid is not None and session.get(Resource, vmid) is not None else None
    if existing is None or existing.status == VMMigrationJobStatus.running:
        job = VMMigrationJob(
            request_id=request_id,
            vmid=vmid,
            resource_vmid=resource_vmid,
            source_node=source_node,
            target_node=target_node,
            status=VMMigrationJobStatus.pending,
            rebalance_epoch=rebalance_epoch,
            attempt_count=0,
            last_error=last_error,
            requested_at=now,
            available_at=available_at or now,
            updated_at=now,
        )
        session.add(job)
    else:
        job = existing
        job.vmid = vmid
        job.resource_vmid = resource_vmid
        job.source_node = source_node
        job.target_node = target_node
        job.status = VMMigrationJobStatus.pending
        job.rebalance_epoch = rebalance_epoch
        job.last_error = last_error
        job.available_at = available_at or now
        job.claimed_by = None
        job.claimed_at = None
        job.claim_expires_at = None
        job.finished_at = None
        job.updated_at = now
        session.add(job)
    if commit:
        session.commit()
        session.refresh(job)
    else:
        session.flush()
    return job


def cancel_pending_jobs_for_request(
    *,
    session: Session,
    request_id: uuid.UUID,
    reason: str | None = None,
    commit: bool = True,
) -> int:
    jobs = list(
        session.exec(
            select(VMMigrationJob).where(
                VMMigrationJob.request_id == request_id,
                VMMigrationJob.status == VMMigrationJobStatus.pending,
            )
        ).all()
    )
    now = datetime.now(timezone.utc)
    for job in jobs:
        job.status = VMMigrationJobStatus.cancelled
        job.last_error = reason
        job.claimed_by = None
        job.claimed_at = None
        job.claim_expires_at = None
        job.finished_at = now
        job.updated_at = now
        session.add(job)
    if commit:
        session.commit()
    elif jobs:
        session.flush()
    return len(jobs)


def delete_jobs_for_request(
    *,
    session: Session,
    request_id: uuid.UUID,
    commit: bool = True,
) -> int:
    jobs = list(
        session.exec(
            select(VMMigrationJob).where(VMMigrationJob.request_id == request_id)
        ).all()
    )
    for job in jobs:
        session.delete(job)
    if commit:
        session.commit()
    elif jobs:
        session.flush()
    return len(jobs)


def list_pending_jobs_for_requests(
    *,
    session: Session,
    request_ids: list[uuid.UUID],
) -> list[VMMigrationJob]:
    if not request_ids:
        return []
    statement = (
        select(VMMigrationJob)
        .where(
            VMMigrationJob.request_id.in_(request_ids),
            VMMigrationJob.status == VMMigrationJobStatus.pending,
        )
        .order_by(
            VMMigrationJob.rebalance_epoch.asc(),
            VMMigrationJob.requested_at.asc(),
        )
        .with_for_update()
    )
    return list(session.exec(statement).all())


def claim_jobs_for_requests(
    *,
    session: Session,
    request_ids: list[uuid.UUID],
    worker_id: str,
    now: datetime,
    limit: int,
    claim_timeout_seconds: int,
) -> list[VMMigrationJob]:
    if not request_ids or limit <= 0:
        return []
    statement = (
        select(VMMigrationJob)
        .where(
            VMMigrationJob.request_id.in_(request_ids),
            VMMigrationJob.status.in_(_OPEN_JOB_STATUSES),
        )
        .order_by(
            VMMigrationJob.available_at.asc().nullsfirst(),
            VMMigrationJob.rebalance_epoch.asc(),
            VMMigrationJob.requested_at.asc(),
        )
        .with_for_update()
    )
    candidates = list(session.exec(statement).all())
    claim_deadline = _normalize_datetime(now) or now
    claimed: list[VMMigrationJob] = []
    for job in candidates:
        available_at = _normalize_datetime(job.available_at)
        if available_at is not None and available_at > claim_deadline:
            continue
        claim_expires_at = _normalize_datetime(job.claim_expires_at)
        if claim_expires_at is not None and claim_expires_at > claim_deadline:
            continue
        # 先前的 continue 已保證 claim_expires_at <= claim_deadline
        if (
            job.status == VMMigrationJobStatus.running
            and claim_expires_at is not None
        ):
            job.status = VMMigrationJobStatus.pending
            job.last_error = (
                job.last_error
                or "Migration worker claim expired and the job was re-queued."
            )
        job.claimed_by = worker_id
        job.claimed_at = now
        job.claim_expires_at = now + timedelta(seconds=max(claim_timeout_seconds, 1))
        job.updated_at = now
        session.add(job)
        claimed.append(job)
        if len(claimed) >= limit:
            break
    if claimed:
        session.flush()
    return claimed


def update_job_status(
    *,
    session: Session,
    job: VMMigrationJob,
    status: VMMigrationJobStatus,
    last_error: str | None = None,
    attempt_delta: int = 0,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    available_at: datetime | None = None,
    claimed_by: str | None = None,
    claimed_at: datetime | None = None,
    claim_expires_at: datetime | None = None,
    source_node: str | None = None,
    target_node: str | None = None,
    vmid: int | None = None,
    commit: bool = True,
) -> VMMigrationJob:
    now = datetime.now(timezone.utc)
    job.status = status
    job.last_error = last_error
    job.attempt_count = max(int(job.attempt_count or 0) + int(attempt_delta), 0)
    job.started_at = started_at if started_at is not None else job.started_at
    job.finished_at = finished_at
    if available_at is not None:
        job.available_at = available_at
    job.source_node = source_node if source_node is not None else job.source_node
    job.target_node = target_node if target_node is not None else job.target_node
    if vmid is not None:
        job.vmid = vmid
    if claimed_by is not None:
        job.claimed_by = claimed_by
    if claimed_at is not None:
        job.claimed_at = claimed_at
    if claim_expires_at is not None:
        job.claim_expires_at = claim_expires_at
    if status in {
        VMMigrationJobStatus.pending,
        VMMigrationJobStatus.completed,
        VMMigrationJobStatus.failed,
        VMMigrationJobStatus.blocked,
        VMMigrationJobStatus.cancelled,
    }:
        job.claimed_by = None
        job.claimed_at = None
        job.claim_expires_at = None
    job.updated_at = now
    session.add(job)
    if commit:
        session.commit()
        session.refresh(job)
    else:
        session.flush()
    return job


def extend_job_claim(
    *,
    session: Session,
    job: VMMigrationJob,
    now: datetime,
    claim_timeout_seconds: int,
    commit: bool = True,
) -> VMMigrationJob:
    normalized_now = _normalize_datetime(now) or now
    job.claimed_at = normalized_now
    job.claim_expires_at = normalized_now + timedelta(
        seconds=max(int(claim_timeout_seconds or 0), 1)
    )
    job.updated_at = normalized_now
    session.add(job)
    if commit:
        session.commit()
        session.refresh(job)
    else:
        session.flush()
    return job


def list_all_jobs(
    *,
    session: Session,
    status: VMMigrationJobStatus | None = None,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[VMMigrationJob], int]:
    count_stmt = select(sa_func.count()).select_from(VMMigrationJob)
    query_stmt = select(VMMigrationJob)

    if status is not None:
        count_stmt = count_stmt.where(VMMigrationJob.status == status)
        query_stmt = query_stmt.where(VMMigrationJob.status == status)

    total = session.exec(count_stmt).one()
    jobs = list(
        session.exec(
            query_stmt.order_by(
                VMMigrationJob.requested_at.desc(),
                VMMigrationJob.updated_at.desc(),
            )
            .offset(skip)
            .limit(limit)
        ).all()
    )
    return jobs, total


def get_job_by_id(
    *,
    session: Session,
    job_id: uuid.UUID,
) -> VMMigrationJob | None:
    return session.get(VMMigrationJob, job_id)


def get_migration_stats(
    *,
    session: Session,
) -> dict:
    from sqlalchemy import extract

    total = session.exec(
        select(sa_func.count()).select_from(VMMigrationJob)
    ).one()

    by_status = {}
    for s in list(VMMigrationJobStatus):
        count = session.exec(
            select(sa_func.count())
            .select_from(VMMigrationJob)
            .where(VMMigrationJob.status == s)
        ).one()
        by_status[s.value] = count

    avg_duration = session.exec(
        select(
            sa_func.avg(
                extract(
                    "epoch",
                    VMMigrationJob.finished_at - VMMigrationJob.started_at,
                )
            )
        )
        .select_from(VMMigrationJob)
        .where(
            VMMigrationJob.status == VMMigrationJobStatus.completed,
            VMMigrationJob.started_at.is_not(None),
            VMMigrationJob.finished_at.is_not(None),
        )
    ).first()

    return {
        "total_jobs": total,
        "by_status": by_status,
        "avg_duration_seconds": round(float(avg_duration or 0), 2),
        "success_rate": (
            round(by_status.get("completed", 0) / max(total, 1) * 100, 1)
        ),
    }


__all__ = [
    "cancel_pending_jobs_for_request",
    "claim_jobs_for_requests",
    "create_or_update_pending_job",
    "delete_jobs_for_request",
    "extend_job_claim",
    "get_job_by_id",
    "get_latest_job_for_request",
    "get_migration_stats",
    "get_open_job_for_request",
    "list_all_jobs",
    "list_pending_jobs_for_requests",
    "update_job_status",
]
