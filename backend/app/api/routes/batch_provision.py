"""Batch provisioning APIs for formal teaching classes."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, InstructorUser, SessionDep
from app.core.authorizers import require_teaching_access
from app.core.i18n import t
from app.exceptions import NotFoundError
from app.models import BatchProvisionJobStatus, TeachingClass
from app.repositories import batch_provision as bp_repo
from app.schemas.batch_provision import (
    BatchProvisionJobPublic,
    BatchProvisionReviewRequest,
    RecurrencePreview,
)
from app.services.teaching import class_provision_service
from app.services.vm import batch_provision_service

router = APIRouter(prefix="/batch-provision", tags=["batch-provision"])


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
    return batch_provision_service.to_public(session, job)


# ─── Admin review endpoints ───────────────────────────────────────────────────


@router.get("/pending", response_model=list[BatchProvisionJobPublic])
def list_pending_review(
    session: SessionDep,
    _: AdminUser,
) -> list[BatchProvisionJobPublic]:
    jobs = bp_repo.list_pending_review_jobs(session=session)
    return [batch_provision_service.to_public(session, job) for job in jobs]


@router.get("/", response_model=list[BatchProvisionJobPublic])
def list_review_jobs(
    session: SessionDep,
    _: AdminUser,
    status: BatchProvisionJobStatus | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[BatchProvisionJobPublic]:
    """審核頁的完整列表：待審核之外也要看得到已核准 / 已駁回的批次。"""
    jobs = bp_repo.list_review_jobs(session=session, status=status, limit=limit)
    return [batch_provision_service.to_public(session, job) for job in jobs]


@router.get("/{job_id}/recurrence-preview", response_model=RecurrencePreview)
def get_recurrence_preview(
    job_id: uuid.UUID,
    session: SessionDep,
    _: AdminUser,
    count: int = Query(default=5, ge=1, le=50),
) -> RecurrencePreview:
    job = bp_repo.get_job(session=session, job_id=job_id)
    if not job:
        raise NotFoundError(t("batchProvision.jobNotFound"))
    if not job.recurrence_rule or not job.recurrence_duration_minutes:
        return RecurrencePreview(windows=[])

    # Iteratively compute the next ``count`` windows by advancing ``after``.
    from app.services.scheduling.recurrence import compute_next_window

    windows: list[tuple[datetime, datetime]] = []
    after = datetime.now(UTC)
    for _i in range(count):
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
    return batch_provision_service.to_public(session, job)


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
    reviewed = class_provision_service.review_teaching_class(
        session,
        item=teaching_class,
        reviewer_id=current_user.id,
        decision=BatchProvisionJobStatus(body.decision),
        review_comment=body.review_comment,
    )
    return [batch_provision_service.to_public(session, job) for job in reviewed]
