"""Batch provisioning APIs for formal teaching classes."""

import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlmodel import select

from app.api.deps import AdminUser, InstructorUser, SessionDep
from app.core.authorizers import require_teaching_access
from app.core.i18n import t
from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    BatchProvisionJobStatus,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
    User,
)
from app.models.base import get_datetime_utc
from app.repositories import batch_provision as bp_repo
from app.services.teaching import class_capacity_service
from app.services.vm import batch_provision_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/batch-provision", tags=["batch-provision"])


class BatchProvisionReviewRequest(BaseModel):
    """Payload for admin approve/reject of a pending batch."""

    decision: str = Field(..., pattern="^(approved|rejected)$")
    review_comment: str | None = Field(default=None, max_length=500)


class BatchProvisionTaskPublic(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str | None
    user_name: str | None
    member_index: int
    vmid: int | None
    status: str
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class BatchProvisionJobSpec(BaseModel):
    """Spec parameters that apply to every member's resource. Reflects the
    JSON stored in ``BatchProvisionJob.template_params``."""

    cores: int | None = None
    memory: int | None = None
    disk_size: int | None = None
    rootfs_size: int | None = None
    ostemplate: str | None = None
    template_id: int | None = None
    vm_template_id: str | None = None
    username: str | None = None
    environment_type: str | None = None
    os_info: str | None = None
    expiry_date: str | None = None


class BatchProvisionJobPublic(BaseModel):
    id: uuid.UUID
    teaching_class_id: uuid.UUID
    teaching_class_name: str | None = None
    resource_type: str
    hostname_prefix: str
    status: str
    total: int
    done: int
    failed_count: int
    created_at: datetime
    finished_at: datetime | None
    initiated_by: uuid.UUID | None = None
    initiated_by_email: str | None = None
    initiated_by_name: str | None = None
    reviewer_id: uuid.UUID | None = None
    reviewer_email: str | None = None
    reviewed_at: datetime | None = None
    review_comment: str | None = None
    recurrence_rule: str | None = None
    recurrence_duration_minutes: int | None = None
    schedule_timezone: str | None = None
    next_window_start: datetime | None = None
    next_window_end: datetime | None = None
    spec: BatchProvisionJobSpec
    tasks: list[BatchProvisionTaskPublic]


def _build_job_public(session: SessionDep, job) -> BatchProvisionJobPublic:
    tasks = bp_repo.get_job_tasks(session=session, job_id=job.id)

    # Collect every user we want to display: task owners + initiator + reviewer.
    user_ids: set[uuid.UUID] = {task.user_id for task in tasks}
    if job.initiated_by:
        user_ids.add(job.initiated_by)
    if job.reviewer_id:
        user_ids.add(job.reviewer_id)

    users: dict[uuid.UUID, User] = {}
    if user_ids:
        rows = session.exec(select(User).where(User.id.in_(list(user_ids)))).all()
        users = {user.id: user for user in rows}

    teaching_class = (
        session.get(TeachingClass, job.teaching_class_id)
        if job.teaching_class_id
        else None
    )

    # Parse the JSON-encoded spec snapshot.
    try:
        params = json.loads(job.template_params or "{}")
    except (TypeError, ValueError):
        params = {}
    spec = BatchProvisionJobSpec(
        cores=params.get("cores"),
        memory=params.get("memory"),
        disk_size=params.get("disk_size"),
        rootfs_size=params.get("rootfs_size"),
        ostemplate=params.get("ostemplate"),
        template_id=params.get("template_id"),
        vm_template_id=params.get("vm_template_id"),
        username=params.get("username"),
        environment_type=params.get("environment_type"),
        os_info=params.get("os_info"),
        expiry_date=params.get("expiry_date"),
    )

    task_publics = [
        BatchProvisionTaskPublic(
            id=task.id,
            user_id=task.user_id,
            user_email=users[task.user_id].email if task.user_id in users else None,
            user_name=users[task.user_id].full_name if task.user_id in users else None,
            member_index=task.member_index,
            vmid=task.vmid,
            status=task.status,
            error=task.error,
            started_at=task.started_at,
            finished_at=task.finished_at,
        )
        for task in tasks
    ]

    initiator = users.get(job.initiated_by) if job.initiated_by else None
    reviewer = users.get(job.reviewer_id) if job.reviewer_id else None

    return BatchProvisionJobPublic(
        id=job.id,
        teaching_class_id=job.teaching_class_id,
        teaching_class_name=teaching_class.name if teaching_class else None,
        resource_type=job.resource_type,
        hostname_prefix=job.hostname_prefix,
        status=job.status,
        total=job.total,
        done=job.done,
        failed_count=job.failed_count,
        created_at=job.created_at,
        finished_at=job.finished_at,
        initiated_by=job.initiated_by,
        initiated_by_email=initiator.email if initiator else None,
        initiated_by_name=initiator.full_name if initiator else None,
        reviewer_id=job.reviewer_id,
        reviewer_email=reviewer.email if reviewer else None,
        reviewed_at=job.reviewed_at,
        review_comment=job.review_comment,
        recurrence_rule=job.recurrence_rule,
        recurrence_duration_minutes=job.recurrence_duration_minutes,
        schedule_timezone=job.schedule_timezone,
        next_window_start=job.next_window_start,
        next_window_end=job.next_window_end,
        spec=spec,
        tasks=task_publics,
    )


@router.get("/{job_id}/status", response_model=BatchProvisionJobPublic)
def get_batch_status(
    job_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> BatchProvisionJobPublic:
    job = bp_repo.get_job(session=session, job_id=job_id)
    if not job:
        raise NotFoundError(t("batchProvision.jobNotFound"))
    teaching_class = session.get(TeachingClass, job.teaching_class_id)
    if not teaching_class:
        raise NotFoundError(t("batchProvision.classNotFound"))
    require_teaching_access(current_user, teaching_class.owner_id)
    return _build_job_public(session, job)


# ─── Admin review endpoints ───────────────────────────────────────────────────


@router.get("/pending", response_model=list[BatchProvisionJobPublic])
def list_pending_review(
    session: SessionDep,
    current_user: AdminUser,
) -> list[BatchProvisionJobPublic]:
    _ = current_user  # admin guard via dependency
    jobs = bp_repo.list_pending_review_jobs(session=session)
    return [_build_job_public(session, job) for job in jobs]


@router.get("/", response_model=list[BatchProvisionJobPublic])
def list_review_jobs(
    session: SessionDep,
    current_user: AdminUser,
    status: BatchProvisionJobStatus | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[BatchProvisionJobPublic]:
    """審核頁的完整列表：待審核之外也要看得到已核准 / 已駁回的批次。"""
    _ = current_user  # admin guard via dependency
    jobs = bp_repo.list_review_jobs(session=session, status=status, limit=limit)
    return [_build_job_public(session, job) for job in jobs]


class RecurrencePreview(BaseModel):
    """Next few computed windows for a candidate RRULE — used by the review UI
    to confirm the schedule does what the teacher intended before approving."""

    windows: list[tuple[datetime, datetime]]


@router.get("/{job_id}/recurrence-preview", response_model=RecurrencePreview)
def get_recurrence_preview(
    job_id: uuid.UUID,
    session: SessionDep,
    current_user: AdminUser,
    count: int = 5,
) -> RecurrencePreview:
    _ = current_user
    job = bp_repo.get_job(session=session, job_id=job_id)
    if not job:
        raise NotFoundError(t("batchProvision.jobNotFound"))
    if not job.recurrence_rule or not job.recurrence_duration_minutes:
        return RecurrencePreview(windows=[])

    # Iteratively compute the next ``count`` windows by advancing ``after``.
    from app.services.scheduling.recurrence import compute_next_window

    windows: list[tuple[datetime, datetime]] = []
    after = datetime.now(UTC)
    for _i in range(max(count, 0)):
        result = compute_next_window(
            rule=job.recurrence_rule,
            duration_minutes=job.recurrence_duration_minutes,
            timezone=job.schedule_timezone,
            after=after,
        )
        if result is None:
            break
        windows.append(result)
        # Advance to just past this window's end so the next call returns the
        # following occurrence rather than the same one.
        after = result[1]
    return RecurrencePreview(windows=windows)


@router.post("/{job_id}/review", response_model=BatchProvisionJobPublic)
def review_batch_job(
    job_id: uuid.UUID,
    body: BatchProvisionReviewRequest,
    session: SessionDep,
    current_user: AdminUser,
) -> BatchProvisionJobPublic:
    if body.decision == "approved":
        batch_provision_service.approve_batch_job(
            session=session,
            job_id=job_id,
            reviewer_id=current_user.id,
            review_comment=body.review_comment,
        )
    else:
        batch_provision_service.reject_batch_job(
            session=session,
            job_id=job_id,
            reviewer_id=current_user.id,
            review_comment=body.review_comment,
        )

    job = bp_repo.get_job(session=session, job_id=job_id)
    if not job:
        raise NotFoundError(t("batchProvision.jobNotFound"))
    return _build_job_public(session, job)


@router.post(
    "/class/{class_id}/review",
    response_model=list[BatchProvisionJobPublic],
)
def review_teaching_class_jobs(
    class_id: uuid.UUID,
    body: BatchProvisionReviewRequest,
    session: SessionDep,
    current_user: AdminUser,
) -> list[BatchProvisionJobPublic]:
    teaching_class = session.get(TeachingClass, class_id)
    if teaching_class is None:
        raise NotFoundError(t("batchProvision.classNotFound"))
    nodes = list(
        session.exec(
            select(TeachingClassMachineNode).where(
                TeachingClassMachineNode.class_id == class_id
            )
        ).all()
    )
    job_ids = [node.batch_job_id for node in nodes if node.batch_job_id]
    jobs = [
        job
        for job_id in job_ids
        if (job := bp_repo.get_job(session=session, job_id=job_id)) is not None
    ]
    pending_ids = [
        job.id
        for job in jobs
        if job.status == BatchProvisionJobStatus.pending_review
    ]
    if not pending_ids:
        raise BadRequestError(t("batchProvision.noPendingJobs"))
    decision = BatchProvisionJobStatus(body.decision)
    reviewed = batch_provision_service.review_batch_jobs(
        session=session,
        job_ids=pending_ids,
        reviewer_id=current_user.id,
        decision=decision,
        review_comment=body.review_comment,
    )
    if decision == BatchProvisionJobStatus.approved:
        teaching_class.status = TeachingClassStatus.provisioning
    else:
        all_tasks = [
            task
            for job in jobs
            for task in bp_repo.get_job_tasks(session=session, job_id=job.id)
        ]
        if any(task.vmid is not None for task in all_tasks):
            teaching_class.status = TeachingClassStatus.partial_failed
        else:
            for node in nodes:
                node.batch_job_id = None
                session.add(node)
            class_capacity_service.release(session, class_id=class_id)
            teaching_class.status = TeachingClassStatus.planning
            teaching_class.locked_at = None
    teaching_class.updated_at = get_datetime_utc()
    session.add(teaching_class)
    session.commit()
    return [_build_job_public(session, job) for job in reviewed]
