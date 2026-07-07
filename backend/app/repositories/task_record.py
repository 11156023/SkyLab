"""TaskRecord CRUD helpers."""

import json
import uuid
from datetime import datetime, timezone
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


def get_task_record(
    *, session: Session, task_id: uuid.UUID
) -> TaskRecord | None:
    return session.get(TaskRecord, task_id)


def list_task_records_by_user(
    *,
    session: Session,
    user_id: uuid.UUID,
    limit: int = 50,
) -> list[TaskRecord]:
    stmt = (
        select(TaskRecord)
        .where(TaskRecord.user_id == user_id)
        .order_by(TaskRecord.created_at.desc())  # type: ignore[attr-defined]
        .limit(limit)
    )
    return list(session.exec(stmt).all())


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


def set_task_progress(
    *, session: Session, task_id: uuid.UUID, progress: int
) -> None:
    record = session.get(TaskRecord, task_id)
    if record is None:
        return
    record.progress = max(0, min(100, progress))
    session.add(record)
    session.commit()
