"""Coverage tests for app.services.teaching.class_lifecycle_service."""

import uuid
from datetime import date, time
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    BatchProvisionTask,
    BatchProvisionTaskStatus,
    TeachingClass,
    TeachingClassWeek,
)
from app.models.base import get_datetime_utc
from app.services.teaching import class_lifecycle_service as svc


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _class(**overrides) -> TeachingClass:
    values = {
        "owner_id": uuid.uuid4(),
        "name": "Linux",
        "code": "LNX",
        "term": "115-1",
        "start_date": date(2026, 9, 1),
        "end_date": date(2026, 9, 30),
        "weekday": 1,
        "start_time": time(9, 0),
        "end_time": time(12, 0),
    }
    values.update(overrides)
    return TeachingClass(**values)


def test_validate_schedule_accepts_and_rejects():
    svc.validate_schedule(_class())
    with pytest.raises(BadRequestError):
        svc.validate_schedule(_class(end_date=date(2026, 8, 1)))
    with pytest.raises(BadRequestError):
        svc.validate_schedule(_class(end_time=time(9, 0)))


def test_generate_weeks_without_preserve_creates_rows_for_a_new_class(session):
    item = _class()
    session.add(item)
    session.commit()
    svc.generate_weeks(session, item)
    weeks = session.exec(
        select(TeachingClassWeek).where(TeachingClassWeek.class_id == item.id)
    ).all()
    assert sorted(w.week_number for w in weeks) == [1, 2, 3, 4, 5]
    assert min(w.session_date for w in weeks) == date(2026, 9, 1)


def test_generate_weeks_never_rebuilds_existing_weeks(session):
    """整批重建會刪掉既有週次（AI導師檢查掛在上面），一律擋下。"""
    item = _class()
    session.add(item)
    session.add(
        TeachingClassWeek(
            class_id=item.id, week_number=9, session_date=date(2026, 1, 1)
        )
    )
    session.commit()
    with pytest.raises(BadRequestError):
        svc.generate_weeks(session, item)
    weeks = session.exec(
        select(TeachingClassWeek).where(TeachingClassWeek.class_id == item.id)
    ).all()
    assert [w.week_number for w in weeks] == [9]


def test_clear_schedule_windows(session):
    item = _class()
    session.add(item)
    job = BatchProvisionJob(
        teaching_class_id=item.id,
        resource_type="lxc",
        hostname_prefix="x",
        template_params={},
        created_at=get_datetime_utc(),
        next_window_start=get_datetime_utc(),
        next_window_end=get_datetime_utc(),
    )
    session.add(job)
    session.commit()
    svc.clear_schedule_windows(session, item.id)
    assert job.next_window_start is None
    assert job.next_window_end is None


def test_queue_reclaim_handles_each_resource_state(session, monkeypatch):
    item = _class()
    session.add(item)
    session.commit()
    resources = [SimpleNamespace(vmid=v) for v in (101, 102, 103, 104)]
    lookups = iter([resources, []])
    monkeypatch.setattr(
        svc.resource_repo,
        "get_resources_by_teaching_class",
        lambda **kw: next(lookups),
    )
    monkeypatch.setattr(
        svc.deletion_service, "list_active_for_vmids", lambda **kw: {101}
    )

    def find(vmid):
        if vmid == 102:
            raise NotFoundError("gone")
        if vmid == 103:
            raise RuntimeError("pve down")
        return {"node": "pve1", "type": "lxc"}

    monkeypatch.setattr(svc.proxmox_service, "find_resource", find)
    orphans: list[int] = []
    monkeypatch.setattr(
        svc.resource_service,
        "delete_orphan_db_record",
        lambda **kw: orphans.append(kw["vmid"]),
    )
    monkeypatch.setattr(
        svc.deletion_service,
        "create_deletion_request",
        lambda **kw: SimpleNamespace(vmid=kw["vmid"], force=kw["force"]),
    )
    enqueued: list = []
    monkeypatch.setattr(
        svc.deletion_service,
        "enqueue_processing",
        lambda **kw: enqueued.append(kw["req"].vmid),
    )
    requester = uuid.uuid4()
    result = svc.queue_reclaim(
        session=session, item=item, requested_by=requester, force=False
    )
    assert result["in_progress_vmids"] == [101]
    assert result["cleaned_vmids"] == [102]
    assert [f["vmid"] for f in result["failed"]] == [103]
    assert result["queued_vmids"] == [104]
    assert orphans == [102]
    assert enqueued == [104]
    assert item.reclaim_requested_at is not None
    assert item.resources_reclaimed_at is not None


def test_cancel_jobs_cancels_active_jobs_and_fails_open_tasks(session):
    item = _class()
    session.add(item)
    jobs = {}
    for status in (BatchProvisionJobStatus.running, BatchProvisionJobStatus.completed):
        job = BatchProvisionJob(
            teaching_class_id=item.id,
            resource_type="lxc",
            hostname_prefix="x",
            template_params={},
            status=status,
            created_at=get_datetime_utc(),
        )
        session.add(job)
        jobs[status] = job
    session.flush()
    running = jobs[BatchProvisionJobStatus.running]
    open_task = BatchProvisionTask(
        job_id=running.id,
        user_id=uuid.uuid4(),
        member_index=0,
        status=BatchProvisionTaskStatus.running,
    )
    done_task = BatchProvisionTask(
        job_id=running.id,
        user_id=uuid.uuid4(),
        member_index=1,
        status=BatchProvisionTaskStatus.completed,
    )
    session.add(open_task)
    session.add(done_task)
    session.commit()

    svc.cancel_jobs(session, item.id)

    assert running.status == BatchProvisionJobStatus.cancelled
    assert running.finished_at is not None
    assert jobs[BatchProvisionJobStatus.completed].status == (
        BatchProvisionJobStatus.completed
    )
    assert open_task.status == BatchProvisionTaskStatus.failed
    assert open_task.error == "Teaching class archived before provisioning completed"
    assert done_task.status == BatchProvisionTaskStatus.completed
