"""批量建立資源 repository"""

import uuid
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.models.batch_provision import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    BatchProvisionTask,
    BatchProvisionTaskStatus,
)


def create_job(
    *,
    session: Session,
    group_id: uuid.UUID,
    initiated_by: uuid.UUID,
    resource_type: str,
    hostname_prefix: str,
    template_params: str,
    member_user_ids: list[uuid.UUID],
    initial_status: BatchProvisionJobStatus = BatchProvisionJobStatus.pending_review,
    recurrence_rule: str | None = None,
    recurrence_duration_minutes: int | None = None,
    schedule_timezone: str | None = None,
) -> BatchProvisionJob:
    now = datetime.now(UTC)
    job = BatchProvisionJob(
        group_id=group_id,
        initiated_by=initiated_by,
        resource_type=resource_type,
        hostname_prefix=hostname_prefix,
        template_params=template_params,
        status=initial_status,
        total=len(member_user_ids),
        done=0,
        failed_count=0,
        created_at=now,
        recurrence_rule=recurrence_rule,
        recurrence_duration_minutes=recurrence_duration_minutes,
        schedule_timezone=schedule_timezone,
    )
    session.add(job)
    session.flush()  # 取得 job.id

    for idx, user_id in enumerate(member_user_ids, start=1):
        task = BatchProvisionTask(
            job_id=job.id,
            user_id=user_id,
            member_index=idx,
            status=BatchProvisionTaskStatus.pending,
        )
        session.add(task)

    session.commit()
    session.refresh(job)
    return job


def mark_reviewed(
    *,
    session: Session,
    job_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    decision: BatchProvisionJobStatus,
    review_comment: str | None = None,
) -> BatchProvisionJob | None:
    """Record an admin's approve/reject decision on a pending batch."""
    job = session.get(BatchProvisionJob, job_id)
    if job is None:
        return None
    job.status = decision
    job.reviewer_id = reviewer_id
    job.reviewed_at = datetime.now(UTC)
    job.review_comment = (review_comment or None)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def list_pending_review_jobs(*, session: Session) -> list[BatchProvisionJob]:
    stmt = (
        select(BatchProvisionJob)
        .where(BatchProvisionJob.status == BatchProvisionJobStatus.pending_review)
        .order_by(BatchProvisionJob.created_at)
    )
    return list(session.exec(stmt).all())


def list_jobs_with_recurrence(*, session: Session) -> list[BatchProvisionJob]:
    """Jobs that the scheduler must inspect for window-based start/stop."""
    stmt = select(BatchProvisionJob).where(
        BatchProvisionJob.recurrence_rule.isnot(None),  # type: ignore[union-attr]
        BatchProvisionJob.status == BatchProvisionJobStatus.completed,
    )
    return list(session.exec(stmt).all())


def get_job(*, session: Session, job_id: uuid.UUID) -> BatchProvisionJob | None:
    return session.get(BatchProvisionJob, job_id)


def get_job_tasks(*, session: Session, job_id: uuid.UUID) -> list[BatchProvisionTask]:
    stmt = (
        select(BatchProvisionTask)
        .where(BatchProvisionTask.job_id == job_id)
        .order_by(BatchProvisionTask.member_index)
    )
    return list(session.exec(stmt).all())


def get_pending_tasks(*, session: Session, job_id: uuid.UUID) -> list[BatchProvisionTask]:
    stmt = (
        select(BatchProvisionTask)
        .where(
            BatchProvisionTask.job_id == job_id,
            BatchProvisionTask.status == BatchProvisionTaskStatus.pending,
        )
        .order_by(BatchProvisionTask.member_index)
    )
    return list(session.exec(stmt).all())


def update_task_running(*, session: Session, task_id: uuid.UUID) -> None:
    task = session.get(BatchProvisionTask, task_id)
    if task:
        task.status = BatchProvisionTaskStatus.running
        task.started_at = datetime.now(UTC)
        session.add(task)
        session.commit()


def update_task_done(
    *, session: Session, task_id: uuid.UUID, vmid: int
) -> None:
    task = session.get(BatchProvisionTask, task_id)
    if task:
        task.status = BatchProvisionTaskStatus.completed
        task.vmid = vmid
        task.finished_at = datetime.now(UTC)
        session.add(task)
        session.commit()


def update_task_failed(
    *, session: Session, task_id: uuid.UUID, error: str
) -> None:
    task = session.get(BatchProvisionTask, task_id)
    if task:
        task.status = BatchProvisionTaskStatus.failed
        task.error = error[:500]
        task.finished_at = datetime.now(UTC)
        session.add(task)
        session.commit()


def increment_job_done(*, session: Session, job_id: uuid.UUID) -> None:
    job = session.get(BatchProvisionJob, job_id)
    if job:
        job.done += 1
        session.add(job)
        session.commit()


def increment_job_failed(*, session: Session, job_id: uuid.UUID) -> None:
    job = session.get(BatchProvisionJob, job_id)
    if job:
        job.failed_count += 1
        session.add(job)
        session.commit()


def update_job_status(
    *,
    session: Session,
    job_id: uuid.UUID,
    status: BatchProvisionJobStatus,
) -> None:
    job = session.get(BatchProvisionJob, job_id)
    if job:
        job.status = status
        if status in (BatchProvisionJobStatus.completed, BatchProvisionJobStatus.failed):
            job.finished_at = datetime.now(UTC)
        session.add(job)
        session.commit()


def list_jobs_by_group(
    *, session: Session, group_id: uuid.UUID
) -> list[BatchProvisionJob]:
    stmt = (
        select(BatchProvisionJob)
        .where(BatchProvisionJob.group_id == group_id)
        .order_by(BatchProvisionJob.created_at.desc())
    )
    return list(session.exec(stmt).all())


def clear_task_vmid_references(
    *, session: Session, vmid: int, commit: bool = True
) -> int:
    """Clear VMID references from batch tasks when the underlying resource is deleted."""
    tasks = list(
        session.exec(
            select(BatchProvisionTask).where(BatchProvisionTask.vmid == vmid)
        ).all()
    )
    if not tasks:
        return 0

    for task in tasks:
        task.vmid = None
        session.add(task)

    if commit:
        session.commit()
    else:
        session.flush()

    return len(tasks)
