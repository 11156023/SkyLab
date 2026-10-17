"""app.repositories.task_record 的狀態轉換與不存在任務分支覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import TaskRecord, TaskRecordStatus
from app.repositories import task_record as repo


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _record(session: Session, *, commit: bool = True) -> TaskRecord:
    return repo.create_task_record(
        session=session,
        task_type="template_clone",
        user_id=uuid.uuid4(),
        payload={"a": 1},
        commit=commit,
    )


def test_create_without_commit_flushes(session: Session) -> None:
    record = _record(session, commit=False)
    assert record.id is not None
    assert record.status == TaskRecordStatus.queued


def test_lifecycle_updates(session: Session) -> None:
    record = _record(session)

    repo.mark_task_running(session=session, task_id=record.id)
    session.refresh(record)
    assert record.status == TaskRecordStatus.running
    assert record.started_at is not None

    repo.set_task_progress(session=session, task_id=record.id, progress=150)
    session.refresh(record)
    assert record.progress == 100

    repo.mark_task_requeued(session=session, task_id=record.id)
    session.refresh(record)
    assert record.status == TaskRecordStatus.queued
    assert record.started_at is None

    repo.mark_task_finished(
        session=session,
        task_id=record.id,
        status=TaskRecordStatus.failed,
        result={"ok": False},
        error="e" * 2000,
        resource_vmid=321,
    )
    session.refresh(record)
    assert record.status == TaskRecordStatus.failed
    assert record.result == {"ok": False}
    assert len(record.error) == 1000
    assert record.resource_vmid == 321

    repo.mark_task_finished(
        session=session, task_id=record.id, status=TaskRecordStatus.succeeded
    )
    session.refresh(record)
    assert record.progress == 100


def test_missing_task_ids_are_ignored(session: Session) -> None:
    missing = uuid.uuid4()
    repo.mark_task_running(session=session, task_id=missing)
    repo.mark_task_finished(
        session=session, task_id=missing, status=TaskRecordStatus.failed
    )
    repo.mark_task_requeued(session=session, task_id=missing)
    repo.set_task_progress(session=session, task_id=missing, progress=10)
    assert session.get(TaskRecord, missing) is None
