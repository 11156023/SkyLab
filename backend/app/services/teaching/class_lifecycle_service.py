"""Teaching-class archive and resource reclaim orchestration."""

from __future__ import annotations

import logging
import uuid

from sqlmodel import Session, select

from app.exceptions import NotFoundError
from app.models import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    BatchProvisionTask,
    BatchProvisionTaskStatus,
    TeachingClass,
    TeachingClassStatus,
)
from app.models.base import get_datetime_utc
from app.repositories import resource as resource_repo
from app.services.proxmox import proxmox_service
from app.services.resource import deletion_service, resource_service
from app.services.teaching import class_capacity_service

logger = logging.getLogger(__name__)


def clear_schedule_windows(session: Session, class_id: uuid.UUID) -> None:
    jobs = session.exec(
        select(BatchProvisionJob).where(
            BatchProvisionJob.teaching_class_id == class_id
        )
    ).all()
    for job in jobs:
        job.next_window_start = None
        job.next_window_end = None
        session.add(job)
    session.flush()


def cancel_jobs(session: Session, class_id: uuid.UUID) -> None:
    now = get_datetime_utc()
    jobs = session.exec(
        select(BatchProvisionJob).where(
            BatchProvisionJob.teaching_class_id == class_id
        )
    ).all()
    for job in jobs:
        if job.status in {
            BatchProvisionJobStatus.pending_review,
            BatchProvisionJobStatus.approved,
            BatchProvisionJobStatus.pending,
            BatchProvisionJobStatus.running,
        }:
            job.status = BatchProvisionJobStatus.cancelled
            job.finished_at = now
            session.add(job)
        tasks = session.exec(
            select(BatchProvisionTask).where(
                BatchProvisionTask.job_id == job.id,
                BatchProvisionTask.status.in_(  # type: ignore[union-attr]
                    [
                        BatchProvisionTaskStatus.pending,
                        BatchProvisionTaskStatus.running,
                    ]
                ),
            )
        ).all()
        for task in tasks:
            task.status = BatchProvisionTaskStatus.failed
            task.error = "Teaching class archived before provisioning completed"
            task.finished_at = now
            session.add(task)
    session.flush()


def queue_reclaim(
    *,
    session: Session,
    item: TeachingClass,
    requested_by: uuid.UUID,
    force: bool,
) -> dict:
    """Queue every remaining class resource for idempotent deletion."""
    resources = resource_repo.get_resources_by_teaching_class(
        session=session, teaching_class_id=item.id
    )
    active = deletion_service.list_active_for_vmids(
        session=session,
        vmids=[resource.vmid for resource in resources],
    )
    queued: list[int] = []
    in_progress: list[int] = []
    cleaned: list[int] = []
    failed: list[dict] = []
    item.reclaim_requested_at = get_datetime_utc()
    session.add(item)
    session.commit()

    for resource in resources:
        if resource.vmid in active:
            in_progress.append(resource.vmid)
            continue
        try:
            resource_info = proxmox_service.find_resource(resource.vmid)
        except NotFoundError:
            resource_service.delete_orphan_db_record(
                session=session,
                vmid=resource.vmid,
                user_id=requested_by,
            )
            session.commit()
            cleaned.append(resource.vmid)
            continue
        except Exception:
            logger.exception(
                "Failed to inspect class resource before reclaim class_id=%s vmid=%s",
                item.id,
                resource.vmid,
            )
            failed.append(
                {
                    "vmid": resource.vmid,
                    "error": "Resource reclaim could not be queued; retry later.",
                }
            )
            continue

        request = deletion_service.create_deletion_request(
            session=session,
            user_id=requested_by,
            vmid=resource.vmid,
            resource_info=resource_info,
            purge=True,
            force=force,
        )
        deletion_service.enqueue_processing(session=session, req=request)
        queued.append(resource.vmid)


    if not resource_repo.get_resources_by_teaching_class(
        session=session, teaching_class_id=item.id
    ):
        item.resources_reclaimed_at = get_datetime_utc()
        session.add(item)
        session.commit()

    return {
        "queued_vmids": queued,
        "in_progress_vmids": in_progress,
        "cleaned_vmids": cleaned,
        "failed": failed,
    }


def archive_and_reclaim(
    *,
    session: Session,
    item: TeachingClass,
    requested_by: uuid.UUID,
    force: bool = True,
    reclaim_resources: bool = True,
) -> dict:
    """Archive a class, stop its schedule, release capacity, and reclaim VMs."""
    now = get_datetime_utc()
    item.status = TeachingClassStatus.archived
    item.archived_at = item.archived_at or now
    item.updated_at = now
    cancel_jobs(session, item.id)
    clear_schedule_windows(session, item.id)
    class_capacity_service.release(
        session, class_id=item.id, delete_snapshot=False
    )
    session.add(item)
    session.commit()
    if not reclaim_resources:
        return {
            "queued_vmids": [],
            "in_progress_vmids": [],
            "cleaned_vmids": [],
            "failed": [],
        }
    return queue_reclaim(
        session=session,
        item=item,
        requested_by=requested_by,
        force=force,
    )


__all__ = [
    "archive_and_reclaim",
    "cancel_jobs",
    "clear_schedule_windows",
    "queue_reclaim",
]
