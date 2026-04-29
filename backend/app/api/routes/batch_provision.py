"""Batch provisioning APIs for group-owned VM/LXC creation jobs."""

import json
import logging
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import select

from app.api.deps import AdminUser, InstructorUser, SessionDep, rate_limit_by_user
from app.core.authorizers import require_group_access
from app.exceptions import BadRequestError, NotFoundError
from app.models import User
from app.repositories import batch_provision as bp_repo
from app.repositories import group as group_repo
from app.services.vm import batch_provision_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/batch-provision", tags=["batch-provision"])


class BatchProvisionRequest(BaseModel):
    """Payload used to create one batch provisioning job."""

    resource_type: str = Field(..., pattern="^(lxc|qemu)$")
    hostname_prefix: str = Field(
        ..., min_length=1, max_length=50,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9-]*$",
        description="Hostname prefix: ASCII letters, digits, hyphens; cannot start with hyphen",
    )
    password: str = Field(..., min_length=6)
    cores: int = Field(2, ge=1, le=32)
    memory: int = Field(2048, ge=128, le=65536)
    environment_type: str = Field(default="批次部署")
    os_info: str | None = None
    expiry_date: date | None = None

    ostemplate: str | None = None
    rootfs_size: int | None = Field(default=8, ge=1, le=1000)

    template_id: int | None = None
    username: str | None = None
    disk_size: int | None = Field(default=20, ge=10, le=1000)

    # Optional recurring schedule. When set, the scheduler will power on the
    # provisioned VMs at every occurrence of the RRULE.
    recurrence_rule: str | None = Field(default=None, max_length=500)
    recurrence_duration_minutes: int | None = Field(default=None, ge=15, le=10080)
    schedule_timezone: str | None = Field(default=None, max_length=64)


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
    username: str | None = None
    environment_type: str | None = None
    os_info: str | None = None
    expiry_date: str | None = None


class BatchProvisionJobPublic(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID
    group_name: str | None = None
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
    spec: BatchProvisionJobSpec
    tasks: list[BatchProvisionTaskPublic]


def _validate_request(body: BatchProvisionRequest) -> None:
    if body.resource_type == "lxc":
        if not body.ostemplate:
            raise BadRequestError("LXC batch provision requires ostemplate")
        return

    if not body.template_id:
        raise BadRequestError("VM batch provision requires template_id")
    if not body.username:
        raise BadRequestError("VM batch provision requires username")


def _require_group_job_access(
    *,
    session: SessionDep,
    current_user,
    group_id: uuid.UUID,
):
    db_group = group_repo.get_group_by_id(session=session, group_id=group_id)
    if not db_group:
        raise NotFoundError("Group not found")
    require_group_access(current_user, db_group.owner_id)
    return db_group


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

    # Resolve group name in one extra query (cheap, single row).
    group = group_repo.get_group_by_id(session=session, group_id=job.group_id)

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
        group_id=job.group_id,
        group_name=group.name if group else None,
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
        spec=spec,
        tasks=task_publics,
    )


@router.post(
    "/{group_id}",
    response_model=BatchProvisionJobPublic,
    dependencies=[
        Depends(
            rate_limit_by_user(
                scope="batch-provision", limit=5, window_seconds=60
            )
        )
    ],
)
def start_batch_provision(
    group_id: uuid.UUID,
    body: BatchProvisionRequest,
    session: SessionDep,
    current_user: InstructorUser,
) -> BatchProvisionJobPublic:
    _validate_request(body)
    _require_group_job_access(
        session=session,
        current_user=current_user,
        group_id=group_id,
    )

    schedule_keys = {
        "recurrence_rule",
        "recurrence_duration_minutes",
        "schedule_timezone",
    }
    params = body.model_dump(
        exclude={"resource_type", "hostname_prefix"} | schedule_keys,
        exclude_none=False,
    )
    if params.get("expiry_date"):
        params["expiry_date"] = params["expiry_date"].isoformat()

    job_id = batch_provision_service.submit_batch_job(
        session=session,
        group_id=group_id,
        initiated_by_id=current_user.id,
        resource_type=body.resource_type,
        hostname_prefix=body.hostname_prefix,
        params=params,
        recurrence_rule=body.recurrence_rule,
        recurrence_duration_minutes=body.recurrence_duration_minutes,
        schedule_timezone=body.schedule_timezone,
    )

    job = bp_repo.get_job(session=session, job_id=job_id)
    if not job:
        raise NotFoundError("Batch provision job not found")
    return _build_job_public(session, job)


@router.get("/{job_id}/status", response_model=BatchProvisionJobPublic)
def get_batch_status(
    job_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> BatchProvisionJobPublic:
    job = bp_repo.get_job(session=session, job_id=job_id)
    if not job:
        raise NotFoundError("Batch provision job not found")
    _require_group_job_access(
        session=session,
        current_user=current_user,
        group_id=job.group_id,
    )
    return _build_job_public(session, job)


@router.get("/group/{group_id}", response_model=list[BatchProvisionJobPublic])
def list_group_jobs(
    group_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> list[BatchProvisionJobPublic]:
    _require_group_job_access(
        session=session,
        current_user=current_user,
        group_id=group_id,
    )
    jobs = bp_repo.list_jobs_by_group(session=session, group_id=group_id)
    return [_build_job_public(session, job) for job in jobs]


# ─── Admin review endpoints ───────────────────────────────────────────────────


@router.get("/pending", response_model=list[BatchProvisionJobPublic])
def list_pending_review(
    session: SessionDep,
    current_user: AdminUser,
) -> list[BatchProvisionJobPublic]:
    _ = current_user  # admin guard via dependency
    jobs = bp_repo.list_pending_review_jobs(session=session)
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
        raise NotFoundError("Batch provision job not found")
    if not job.recurrence_rule or not job.recurrence_duration_minutes:
        return RecurrencePreview(windows=[])

    # Iteratively compute the next ``count`` windows by advancing ``after``.
    from app.services.scheduling.recurrence import compute_next_window

    windows: list[tuple[datetime, datetime]] = []
    after = datetime.now()
    if after.tzinfo is None:
        from datetime import UTC
        after = after.replace(tzinfo=UTC)
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
        raise NotFoundError("Batch provision job not found")
    return _build_job_public(session, job)
