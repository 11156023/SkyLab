"""Teaching-class schedule, weekly sessions, archive and resource reclaim."""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta

from sqlmodel import Session, col, delete, select

from app.core.i18n import t
from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    BatchProvisionTask,
    BatchProvisionTaskStatus,
    TeachingClass,
    TeachingClassStatus,
    TeachingClassTaskFile,
    TeachingClassWeek,
)
from app.models.base import get_datetime_utc
from app.repositories import resource as resource_repo
from app.services.course import weekly_task_service
from app.services.course_environment import upload_store
from app.services.proxmox import proxmox_service
from app.services.resource import deletion_service, resource_service
from app.services.teaching import class_capacity_service, class_provision_service

logger = logging.getLogger(__name__)

# 課程期間最多兩年份的課次
MAX_CLASS_WEEKS = 104


def validate_schedule(item: TeachingClass) -> None:
    """結束日不得早於開始日、下課時間必須晚於上課時間，期間不超過上限。"""
    if item.end_date < item.start_date or item.end_time <= item.start_time:
        raise BadRequestError(t("teachingClasses.scheduleInvalid"))
    validate_schedule_span(item.start_date, item.end_date)


def validate_schedule_span(start_date: date, end_date: date) -> None:
    """課程期間上限兩年。

    每一週都會寫一列 teaching_class_weeks，日期範圍沒有上限的話，
    一個手滑打錯的年份就能讓單一班級生出幾萬列課次。
    """
    if (end_date - start_date).days > MAX_CLASS_WEEKS * 7:
        raise BadRequestError(
            t("teachingClasses.scheduleTooLong", weeks=MAX_CLASS_WEEKS)
        )


def remove_task_file_blob(storage_key: str | None) -> None:
    """刪掉磁碟上的教材檔；storage_key 一律當成教材根目錄底下的相對路徑。

    教材根目錄只定義在 ``weekly_task_service.TASK_FILE_ROOT``（學生下載也讀它），
    這裡在呼叫當下才讀該模組屬性，換掉它就同時涵蓋上傳、刪除與下載。
    """
    upload_store.remove_blob(weekly_task_service.TASK_FILE_ROOT, storage_key)


def generate_weeks(
    session: Session, item: TeachingClass, *, preserve: bool = False
) -> None:
    """重建課次；``preserve`` 時沿用既有週次的主題與教材。

    對應的鍵是「第幾週」而不是上課日期：改動每週上課日或開始日期會讓所有日期
    整批位移，用日期比對會一筆都對不上，等於把老師填好的主題與上傳的教材全部
    刪掉。改用 week_number 之後，第 N 週的內容仍然留在第 N 週，只是日期跟著搬。
    """
    validate_schedule_span(item.start_date, item.end_date)
    existing = (
        {
            row.week_number: row
            for row in session.exec(
                select(TeachingClassWeek).where(TeachingClassWeek.class_id == item.id)
            ).all()
        }
        if preserve
        else {}
    )
    if not preserve:
        session.exec(
            delete(TeachingClassWeek).where(TeachingClassWeek.class_id == item.id)
        )
    # 課次推算與排程 RRULE 共用同一個「第一次上課日」
    current = class_provision_service.first_session_date(item)
    number, keep = 1, set()
    while current <= item.end_date and number <= MAX_CLASS_WEEKS:
        keep.add(number)
        row = existing.get(number)
        if row:
            row.session_date = current
            session.add(row)
        else:
            session.add(
                TeachingClassWeek(
                    class_id=item.id, week_number=number, session_date=current
                )
            )
        current += timedelta(days=7)
        number += 1
    removed_storage_keys: list[str] = []
    if preserve:
        dropped = [row for number, row in existing.items() if number not in keep]
        if dropped:
            # 週次被刪時教材檔列會跟著 FK CASCADE 消失，磁碟上的檔案要自己收
            removed_storage_keys = [
                key
                for key in session.exec(
                    select(TeachingClassTaskFile.storage_key).where(
                        col(TeachingClassTaskFile.week_id).in_(
                            [row.id for row in dropped]
                        )
                    )
                ).all()
                if isinstance(key, str)
            ]
        for row in dropped:
            session.delete(row)
    session.commit()
    for storage_key in removed_storage_keys:
        remove_task_file_blob(storage_key)


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
    "MAX_CLASS_WEEKS",
    "archive_and_reclaim",
    "cancel_jobs",
    "clear_schedule_windows",
    "generate_weeks",
    "queue_reclaim",
    "remove_task_file_blob",
    "validate_schedule",
    "validate_schedule_span",
]
