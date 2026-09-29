"""班級批次審核與「重設失敗班級」的狀態流轉（class_provision_service）。

審核退回、且一台機器都沒建出來時，要和「重設失敗班級」一樣退回 planning：
解除節點與 job 的關聯、放掉容量、解鎖。兩條路徑共用 revert_class_to_planning。
"""

import uuid
from datetime import date, time
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.api.routes import batch_provision as batch_provision_route
from app.exceptions import BadRequestError
from app.models import (
    BatchProvisionJobStatus,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
    TeachingClassStudent,
    TeachingClassStudentMachine,
)
from app.models.base import get_datetime_utc
from app.services.teaching import class_provision_service


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            TeachingClass.__table__,  # type: ignore[arg-type]
            TeachingClassMachineNode.__table__,  # type: ignore[arg-type]
            TeachingClassStudent.__table__,  # type: ignore[arg-type]
            TeachingClassStudentMachine.__table__,  # type: ignore[arg-type]
        ],
    )
    with Session(engine) as session:
        yield session


@pytest.fixture
def released(monkeypatch) -> list[uuid.UUID]:
    calls: list[uuid.UUID] = []
    monkeypatch.setattr(
        class_provision_service.class_capacity_service,
        "release",
        lambda _session, *, class_id: calls.append(class_id) or 0,
    )
    return calls


def _class(db: Session, status: TeachingClassStatus) -> TeachingClass:
    item = TeachingClass(
        owner_id=uuid.uuid4(),
        name="Linux",
        code=f"cls-{uuid.uuid4().hex[:8]}",
        term="115-1",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 15),
        weekday=1,
        start_time=time(13, 10),
        end_time=time(16, 0),
        status=status,
        course_version_id=uuid.uuid4(),
        locked_at=get_datetime_utc(),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _node(db: Session, item: TeachingClass, job_id: uuid.UUID | None):
    node = TeachingClassMachineNode(
        class_id=item.id,
        node_key="web",
        source_type="custom",
        name="web",
        role="server",
        resource_type="lxc",
        cpu=1,
        memory_mb=1024,
        disk_gb=8,
        sort_order=0,
        batch_job_id=job_id,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def _fake_jobs(monkeypatch, *, job_id: uuid.UUID, status, task_vmids):
    job = SimpleNamespace(id=job_id, status=status)
    tasks = [SimpleNamespace(vmid=vmid) for vmid in task_vmids]
    monkeypatch.setattr(
        class_provision_service.bp_repo,
        "get_job",
        lambda *, session, job_id: job if job_id == job.id else None,
    )
    monkeypatch.setattr(
        class_provision_service.bp_repo,
        "get_job_tasks",
        lambda *, session, job_id: tasks,
    )
    reviewed: list[dict] = []

    def fake_review(**kwargs):
        reviewed.append(kwargs)
        return [job]

    monkeypatch.setattr(
        class_provision_service.batch_provision_service,
        "review_batch_jobs",
        fake_review,
    )
    return job, reviewed


def test_reject_without_any_machine_reverts_class_to_planning(
    db: Session, monkeypatch, released
) -> None:
    item = _class(db, TeachingClassStatus.pending_review)
    job_id = uuid.uuid4()
    node = _node(db, item, job_id)
    job, reviewed = _fake_jobs(
        monkeypatch,
        job_id=job_id,
        status=BatchProvisionJobStatus.pending_review,
        task_vmids=[None, None],
    )

    result = class_provision_service.review_teaching_class(
        db,
        item=item,
        reviewer_id=uuid.uuid4(),
        decision=BatchProvisionJobStatus.rejected,
        review_comment="規格太大",
    )

    db.refresh(item)
    db.refresh(node)
    assert result == [job]
    assert reviewed[0]["job_ids"] == [job_id]
    assert reviewed[0]["decision"] == BatchProvisionJobStatus.rejected
    assert item.status == TeachingClassStatus.planning
    assert item.locked_at is None
    assert node.batch_job_id is None
    assert released == [item.id]


def test_reject_after_some_machines_exist_marks_partial_failed(
    db: Session, monkeypatch, released
) -> None:
    item = _class(db, TeachingClassStatus.pending_review)
    job_id = uuid.uuid4()
    node = _node(db, item, job_id)
    _fake_jobs(
        monkeypatch,
        job_id=job_id,
        status=BatchProvisionJobStatus.pending_review,
        task_vmids=[4201, None],
    )

    class_provision_service.review_teaching_class(
        db,
        item=item,
        reviewer_id=uuid.uuid4(),
        decision=BatchProvisionJobStatus.rejected,
    )

    db.refresh(item)
    db.refresh(node)
    assert item.status == TeachingClassStatus.partial_failed
    assert item.locked_at is not None
    assert node.batch_job_id == job_id
    assert released == []


def test_approve_moves_class_to_provisioning(
    db: Session, monkeypatch, released
) -> None:
    item = _class(db, TeachingClassStatus.pending_review)
    job_id = uuid.uuid4()
    _node(db, item, job_id)
    _fake_jobs(
        monkeypatch,
        job_id=job_id,
        status=BatchProvisionJobStatus.pending_review,
        task_vmids=[None],
    )

    class_provision_service.review_teaching_class(
        db,
        item=item,
        reviewer_id=uuid.uuid4(),
        decision=BatchProvisionJobStatus.approved,
    )

    db.refresh(item)
    assert item.status == TeachingClassStatus.provisioning
    assert released == []


def test_review_without_pending_jobs_is_rejected_before_any_change(
    db: Session, monkeypatch, released
) -> None:
    item = _class(db, TeachingClassStatus.pending_review)
    job_id = uuid.uuid4()
    _node(db, item, job_id)
    _, reviewed = _fake_jobs(
        monkeypatch,
        job_id=job_id,
        status=BatchProvisionJobStatus.approved,
        task_vmids=[None],
    )

    with pytest.raises(BadRequestError):
        class_provision_service.review_teaching_class(
            db,
            item=item,
            reviewer_id=uuid.uuid4(),
            decision=BatchProvisionJobStatus.rejected,
        )

    db.refresh(item)
    assert reviewed == []
    assert item.status == TeachingClassStatus.pending_review


def test_reset_failed_class_shares_the_planning_revert(db: Session, released) -> None:
    item = _class(db, TeachingClassStatus.partial_failed)
    node = _node(db, item, uuid.uuid4())
    enrollment = TeachingClassStudent(class_id=item.id, user_id=uuid.uuid4())
    db.add(enrollment)
    db.commit()
    db.add(
        TeachingClassStudentMachine(
            class_student_id=enrollment.id,
            machine_node_id=node.id,
            status="failed",
        )
    )
    db.commit()

    class_provision_service.reset_failed_class(db, item=item)

    db.refresh(item)
    db.refresh(node)
    assert item.status == TeachingClassStatus.planning
    assert item.locked_at is None
    assert node.batch_job_id is None
    assert released == [item.id]
    assert (
        db.exec(
            TeachingClassStudentMachine.__table__.select()  # type: ignore[attr-defined]
        ).all()
        == []
    )


def test_reset_failed_class_refuses_when_a_machine_was_built(
    db: Session, released
) -> None:
    item = _class(db, TeachingClassStatus.partial_failed)
    node = _node(db, item, uuid.uuid4())
    enrollment = TeachingClassStudent(class_id=item.id, user_id=uuid.uuid4())
    db.add(enrollment)
    db.commit()
    db.add(
        TeachingClassStudentMachine(
            class_student_id=enrollment.id,
            machine_node_id=node.id,
            vmid=4300,
            status="completed",
        )
    )
    db.commit()

    with pytest.raises(BadRequestError):
        class_provision_service.reset_failed_class(db, item=item)

    assert released == []


def test_class_review_route_serialises_the_reviewed_jobs(monkeypatch) -> None:
    item = SimpleNamespace(id=uuid.uuid4())
    job = SimpleNamespace(id=uuid.uuid4())
    calls: dict = {}

    def fake_review(session, **kwargs):
        calls.update(kwargs)
        return [job]

    monkeypatch.setattr(
        batch_provision_route.class_provision_service,
        "review_teaching_class",
        fake_review,
    )
    monkeypatch.setattr(
        batch_provision_route.batch_provision_service,
        "to_public",
        lambda _session, reviewed_job: {"id": str(reviewed_job.id)},
    )
    session = SimpleNamespace(get=lambda _model, _key: item)
    admin = SimpleNamespace(id=uuid.uuid4())

    result = batch_provision_route.review_teaching_class_jobs(
        item.id,
        batch_provision_route.BatchProvisionReviewRequest(decision="rejected"),
        session,  # type: ignore[arg-type]
        admin,  # type: ignore[arg-type]
    )

    assert result == [{"id": str(job.id)}]
    assert calls["item"] is item
    assert calls["reviewer_id"] == admin.id
    assert calls["decision"] == BatchProvisionJobStatus.rejected
