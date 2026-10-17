"""app.repositories.batch_provision 的審核轉換、task 認領與計數重算覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.batch_provision import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    BatchProvisionTask,
    BatchProvisionTaskStatus,
)
from app.models.resource import Resource
from app.repositories import batch_provision as repo

S = BatchProvisionJobStatus
T = BatchProvisionTaskStatus


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _job(session: Session, members: int = 2, **kw) -> BatchProvisionJob:
    return repo.create_job(
        session=session,
        teaching_class_id=uuid.uuid4(),
        initiated_by=uuid.uuid4(),
        resource_type="lxc",
        hostname_prefix="cls",
        template_params={"cores": 1},
        member_user_ids=[uuid.uuid4() for _ in range(members)],
        **kw,
    )


def _set_status(session: Session, job: BatchProvisionJob, status) -> None:
    job.status = status
    session.add(job)
    session.commit()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"a": 1}, {"a": 1}),
        ('{"b": 2}', {"b": 2}),
        (None, {}),
        ("not json", {}),
        (123, {}),
        ("[1, 2]", {}),
    ],
)
def test_job_params(value, expected) -> None:
    assert repo.job_params(SimpleNamespace(template_params=value)) == expected


def test_transition_pending_reviews(session: Session) -> None:
    assert (
        repo.transition_pending_reviews(
            session=session, job_ids=[], reviewer_id=uuid.uuid4(), decision=S.approved
        )
        == []
    )
    a = _job(session)
    b = _job(session)
    jobs = repo.transition_pending_reviews(
        session=session,
        job_ids=[a.id, b.id, a.id],
        reviewer_id=uuid.uuid4(),
        decision=S.approved,
        review_comment="ok",
    )
    assert {j.id for j in jobs} == {a.id, b.id}
    assert all(j.status == S.approved and j.review_comment == "ok" for j in jobs)

    c = _job(session)
    # a is no longer pending_review -> whole batch rolls back
    assert (
        repo.transition_pending_reviews(
            session=session,
            job_ids=[a.id, c.id],
            reviewer_id=uuid.uuid4(),
            decision=S.rejected,
        )
        == []
    )
    session.refresh(c)
    assert c.status == S.pending_review


def test_list_pending_and_review_jobs(session: Session) -> None:
    first = _job(session)
    second = _job(session)
    first.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    second.created_at = datetime(2026, 1, 2, tzinfo=UTC)
    session.add(first)
    session.add(second)
    session.commit()
    third = _job(session)
    third.created_at = datetime(2026, 1, 3, tzinfo=UTC)
    _set_status(session, third, S.running)

    assert [j.id for j in repo.list_pending_review_jobs(session=session)] == [
        first.id,
        second.id,
    ]
    assert [j.id for j in repo.list_review_jobs(session=session)] == [
        third.id,
        second.id,
        first.id,
    ]
    assert [j.id for j in repo.list_review_jobs(session=session, status=S.running)] == [
        third.id
    ]
    assert len(repo.list_review_jobs(session=session, limit=1)) == 1


def test_claim_pending_task(session: Session) -> None:
    job = _job(session, members=1)
    task = repo.get_job_tasks(session=session, job_id=job.id)[0]

    # job missing / not running
    assert not repo.claim_pending_task(
        session=session, job_id=uuid.uuid4(), task_id=task.id
    )
    assert not repo.claim_pending_task(session=session, job_id=job.id, task_id=task.id)

    _set_status(session, job, S.running)
    assert repo.claim_pending_task(session=session, job_id=job.id, task_id=task.id)
    session.refresh(task)
    assert task.status == T.running
    # already running -> cannot claim again
    assert not repo.claim_pending_task(session=session, job_id=job.id, task_id=task.id)


def test_mark_task_completed(session: Session) -> None:
    job = _job(session, members=2)
    t1, t2 = repo.get_job_tasks(session=session, job_id=job.id)
    session.add(
        Resource(
            vmid=4100,
            user_id=uuid.uuid4(),
            environment_type="lxc",
            created_at=datetime.now(UTC),
        )
    )
    session.commit()

    assert (
        repo.mark_task_completed(session=session, task_id=uuid.uuid4(), vmid=1) is None
    )
    assert (
        repo.mark_task_completed(session=session, task_id=t1.id, vmid=4100) == T.pending
    )
    assert t1.vmid == 4100
    assert t1.status == T.completed
    # resource row missing -> vmid not linked
    assert (
        repo.mark_task_completed(session=session, task_id=t2.id, vmid=4999) == T.pending
    )
    assert t2.vmid is None


def test_recount_job(session: Session) -> None:
    assert repo.recount_job(session=session, job_id=uuid.uuid4()) is None

    job = _job(session, members=2)
    t1, t2 = repo.get_job_tasks(session=session, job_id=job.id)
    t1.status = T.failed
    t2.status = T.failed
    session.add(t1)
    session.add(t2)
    _set_status(session, job, S.completed)
    out = repo.recount_job(session=session, job_id=job.id)
    assert (out.done, out.failed_count, out.status) == (0, 2, S.failed)

    t2.status = T.completed
    session.add(t2)
    out = repo.recount_job(session=session, job_id=job.id)
    assert (out.done, out.failed_count, out.status) == (1, 1, S.completed)

    # running jobs keep their status
    _set_status(session, job, S.running)
    out = repo.recount_job(session=session, job_id=job.id)
    assert out.status == S.running


@pytest.mark.parametrize("commit", [True, False])
def test_clear_task_vmid_references(session: Session, commit: bool) -> None:
    assert repo.clear_task_vmid_references(session=session, vmid=1, commit=commit) == 0

    job = _job(session, members=3)
    t1, t2, t3 = repo.get_job_tasks(session=session, job_id=job.id)
    t1.status, t1.vmid = T.completed, 5000
    t2.status, t2.vmid = T.running, 5000
    # completed task whose job disappeared
    orphan = BatchProvisionTask(
        job_id=uuid.uuid4(), user_id=uuid.uuid4(), member_index=9, vmid=5000
    )
    orphan.status = T.completed
    job.done = 1
    for row in (t1, t2, orphan, job):
        session.add(row)
    session.commit()

    assert (
        repo.clear_task_vmid_references(session=session, vmid=5000, commit=commit) == 3
    )
    session.refresh(job)
    assert job.done == 0
    assert job.failed_count == 1
    for t in (t1, t2, orphan):
        session.refresh(t)
        assert t.vmid is None
        assert t.status == T.failed
        assert "requires repair" in t.error
    session.refresh(t3)
    assert t3.status == T.pending
