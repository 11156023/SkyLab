"""Coverage tests for app.services.teaching.class_lifecycle_service."""

import uuid
from datetime import date, time
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    BatchProvisionJob,
    TeachingClass,
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
