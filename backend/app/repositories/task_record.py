"""TaskRecord CRUD helpers."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session, select

from app.models import TaskRecord, TaskRecordStatus


def create_task_record(
    *,
    session: Session,
    task_type: str,
    user_id: uuid.UUID,
    payload: dict[str, Any],
    template_id: uuid.UUID | None = None,
    commit: bool = True,
) -> TaskRecord:
    record = TaskRecord(
        task_type=task_type,
        user_id=user_id,
        template_id=template_id,
        payload=json.dumps(payload, ensure_ascii=False),
    )
    session.add(record)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(record)
    return record


def get_latest_template_task(
    *,
    session: Session,
    template_id: uuid.UUID,
) -> TaskRecord | None:
    return session.exec(
        select(TaskRecord)
        .where(TaskRecord.template_id == template_id)
        .order_by(TaskRecord.created_at.desc())  # type: ignore[attr-defined]
    ).first()


def mark_task_running(*, session: Session, task_id: uuid.UUID) -> None:
    record = session.get(TaskRecord, task_id)
    if record is None:
        return
    record.status = TaskRecordStatus.running
    record.started_at = datetime.now(timezone.utc)
    session.add(record)
    session.commit()


def mark_task_finished(
    *,
    session: Session,
    task_id: uuid.UUID,
    status: TaskRecordStatus,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    resource_vmid: int | None = None,
) -> None:
    record = session.get(TaskRecord, task_id)
    if record is None:
        return
    record.status = status
    record.finished_at = datetime.now(timezone.utc)
    if status == TaskRecordStatus.succeeded:
        record.progress = 100
    if result is not None:
        record.result = json.dumps(result, ensure_ascii=False)
    if error is not None:
        record.error = error[:1000]
    if resource_vmid is not None:
        record.resource_vmid = resource_vmid
    session.add(record)
    session.commit()


def mark_task_requeued(*, session: Session, task_id: uuid.UUID) -> None:
    """handler 以 Retry 讓出：退回 queued，清掉這次的開始時間。"""
    record = session.get(TaskRecord, task_id)
    if record is None:
        return
    record.status = TaskRecordStatus.queued
    record.started_at = None
    session.add(record)
    session.commit()


def reap_stale_task_records(
    *,
    session: Session,
    now: datetime | None = None,
    running_hours: float = 2.0,
    queued_hours: float = 24.0,
    limit: int = 50,
) -> int:
    """把被硬殺的任務從 running／queued 收成 failed；回傳處理筆數。

    worker 被 OOM／SIGKILL 殺掉時沒有任何收尾程式碼會跑，arq 也因為
    max_tries=1 不會重跑 handler；沒有這支，TaskRecord 會永遠停在 running。
    """
    current = now or datetime.now(timezone.utc)
    stale = list(
        session.exec(
            select(TaskRecord)
            .where(
                (
                    (TaskRecord.status == TaskRecordStatus.running)
                    & (TaskRecord.started_at <= current - timedelta(hours=running_hours))
                )
                | (
                    (TaskRecord.status == TaskRecordStatus.queued)
                    & (TaskRecord.created_at <= current - timedelta(hours=queued_hours))
                )
            )
            .limit(limit)
        ).all()
    )
    for record in stale:
        record.status = TaskRecordStatus.failed
        record.finished_at = current
        record.error = (
            f"Task lost: still {record.status.value} after "
            f"{running_hours if record.started_at else queued_hours:g}h; "
            "worker restarted or was killed"
        )
        session.add(record)
    if stale:
        session.commit()
    return len(stale)


def set_task_progress(

    *, session: Session, task_id: uuid.UUID, progress: int
) -> None:
    record = session.get(TaskRecord, task_id)
    if record is None:
        return
    record.progress = max(0, min(100, progress))
    session.add(record)
    session.commit()
