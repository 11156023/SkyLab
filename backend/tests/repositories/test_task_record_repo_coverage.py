"""app.repositories.task_record 的狀態轉換與不存在任務分支覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

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


def test_latest_template_task(session: Session) -> None:
    template_id = uuid.uuid4()
    assert (
        repo.get_latest_template_task(session=session, template_id=template_id) is None
    )
    record = repo.create_task_record(
        session=session,
        task_type="template_convert",
        user_id=uuid.uuid4(),
        payload={},
        template_id=template_id,
    )
    latest = repo.get_latest_template_task(session=session, template_id=template_id)
    assert latest is not None and latest.id == record.id


def test_reap_stale_task_records(session: Session) -> None:
    now = datetime(2099, 1, 2, tzinfo=UTC)
    running = _record(session)
    running.status = TaskRecordStatus.running
    running.started_at = now - timedelta(hours=3)
    queued = _record(session)
    queued.created_at = now - timedelta(hours=30)
    fresh = _record(session)
    fresh.created_at = now
    for r in (running, queued, fresh):
        session.add(r)
    session.commit()

    assert repo.reap_stale_task_records(session=session, now=now) == 2
    session.refresh(running)
    session.refresh(queued)
    session.refresh(fresh)
    assert running.status == TaskRecordStatus.failed
    assert "still running after 2h" in running.error
    assert queued.status == TaskRecordStatus.failed
    assert "still queued after 24h" in queued.error
    assert fresh.status == TaskRecordStatus.queued
    # 沒有可收的任務時不 commit、回傳 0
    assert repo.reap_stale_task_records(session=session, now=now) == 0


def test_missing_task_ids_are_ignored(session: Session) -> None:
    missing = uuid.uuid4()
    repo.mark_task_running(session=session, task_id=missing)
    repo.mark_task_finished(
        session=session, task_id=missing, status=TaskRecordStatus.failed
    )
    repo.mark_task_requeued(session=session, task_id=missing)
    repo.set_task_progress(session=session, task_id=missing, progress=10)
    assert session.get(TaskRecord, missing) is None
