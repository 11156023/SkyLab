"""app.services.vm.batch_provision_service 的分支補測（SQLite + 假 PVE／佇列）。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import BadRequestError
from app.models import (
    ClassCapacityReservation,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
    TeachingClassStudent,
    TeachingClassStudentMachine,
    User,
)
from app.models.batch_provision import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    BatchProvisionTask,
    BatchProvisionTaskStatus,
)
from app.repositories import batch_provision as bp_repo
from app.services.vm import batch_provision_service as svc

OWNER = uuid.uuid4()
REVIEWER = uuid.uuid4()


@pytest.fixture()
def engine(monkeypatch: pytest.MonkeyPatch) -> Engine:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(svc, "engine", eng)
    return eng


@pytest.fixture()
def db(engine: Engine) -> Session:
    with Session(engine) as session:
        yield session


@pytest.fixture()
def no_refresh(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    calls: list[uuid.UUID] = []
    monkeypatch.setattr(svc, "_refresh_teaching_class_status", calls.append)
    return calls


def _class(
    db: Session,
    *,
    owner_id: uuid.UUID | None = OWNER,
    status: TeachingClassStatus = TeachingClassStatus.provisioning,
) -> TeachingClass:
    item = TeachingClass(
        owner_id=owner_id,
        name="Linux",
        code=f"cls-{uuid.uuid4().hex[:8]}",
        term="115-1",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 12, 31),
        weekday=1,
        start_time=time(13, 10),
        end_time=time(16, 0),
        status=status,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _job(
    db: Session,
    class_id: uuid.UUID,
    *,
    users: list[uuid.UUID],
    status: BatchProvisionJobStatus = BatchProvisionJobStatus.approved,
    initiated_by: uuid.UUID | None = OWNER,
    reviewer_id: uuid.UUID | None = REVIEWER,
    reviewed_at: datetime | None = None,
    params: dict[str, Any] | None = None,
) -> BatchProvisionJob:
    job = bp_repo.create_job(
        session=db,
        teaching_class_id=class_id,
        initiated_by=initiated_by,
        resource_type="lxc",
        hostname_prefix="cls-1",
        template_params=params or {"cores": 1, "memory": 512},
        member_user_ids=users,
        initial_status=status,
    )
    job.reviewer_id = reviewer_id
    job.reviewed_at = reviewed_at
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _tasks(db: Session, job_id: uuid.UUID) -> list[BatchProvisionTask]:
    db.expire_all()
    return bp_repo.get_job_tasks(session=db, job_id=job_id)


# ---------------------------------------------------------------------------
# submit_batch_job_for_users
# ---------------------------------------------------------------------------


def _submit(db: Session, **overrides: Any) -> uuid.UUID:
    kwargs: dict[str, Any] = {
        "session": db,
        "member_user_ids": [uuid.uuid4(), uuid.uuid4()],
        "initiated_by_id": OWNER,
        "resource_type": "lxc",
        "hostname_prefix": "cls",
        "params": {"cores": 1},
        "teaching_class_id": uuid.uuid4(),
    }
    kwargs.update(overrides)
    return svc.submit_batch_job_for_users(**kwargs)


@pytest.fixture()
def ip_ok(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    stats = {"available": 10}
    monkeypatch.setattr(
        svc.ip_management_service, "ensure_subnet_configured", lambda _s: None
    )
    monkeypatch.setattr(svc.ip_management_service, "get_ip_stats", lambda _s: stats)
    return stats


@pytest.mark.parametrize("template", [None, SimpleNamespace(status="building")])
def test_submit_rejects_missing_or_unready_template(
    db: Session, monkeypatch: pytest.MonkeyPatch, template: Any
) -> None:
    monkeypatch.setattr(svc.vm_template_repo, "get_template", lambda **_k: template)
    with pytest.raises(BadRequestError):
        _submit(db, params={"vm_template_id": str(uuid.uuid4())})


def test_submit_rejects_when_not_enough_ips(db: Session, ip_ok: dict[str, int]) -> None:
    ip_ok["available"] = 1
    with pytest.raises(BadRequestError):
        _submit(db)


def test_submit_requires_duration_for_recurrence(
    db: Session, ip_ok: dict[str, int]
) -> None:
    with pytest.raises(BadRequestError):
        _submit(db, recurrence_rule="FREQ=WEEKLY")


# ---------------------------------------------------------------------------
# approve / reject / review
# ---------------------------------------------------------------------------


def test_approve_unknown_job(db: Session) -> None:
    with pytest.raises(BadRequestError):
        svc.approve_batch_job(session=db, job_id=uuid.uuid4(), reviewer_id=REVIEWER)


def test_approve_job_no_longer_pending(db: Session) -> None:
    item = _class(db)
    job = _job(db, item.id, users=[uuid.uuid4()])
    with pytest.raises(BadRequestError):
        svc.approve_batch_job(session=db, job_id=job.id, reviewer_id=REVIEWER)


def test_reject_unknown_job(db: Session) -> None:
    with pytest.raises(BadRequestError):
        svc.reject_batch_job(session=db, job_id=uuid.uuid4(), reviewer_id=REVIEWER)


def test_reject_pending_job_then_again(db: Session) -> None:
    item = _class(db)
    job = _job(
        db,
        item.id,
        users=[uuid.uuid4()],
        status=BatchProvisionJobStatus.pending_review,
        reviewer_id=None,
    )
    svc.reject_batch_job(
        session=db, job_id=job.id, reviewer_id=REVIEWER, review_comment="no"
    )
    db.expire_all()
    assert db.get(BatchProvisionJob, job.id).status == BatchProvisionJobStatus.rejected
    with pytest.raises(BadRequestError):
        svc.reject_batch_job(session=db, job_id=job.id, reviewer_id=REVIEWER)


def test_review_requires_job_ids(db: Session) -> None:
    with pytest.raises(BadRequestError):
        svc.review_batch_jobs(
            session=db,
            job_ids=[],
            reviewer_id=REVIEWER,
            decision=BatchProvisionJobStatus.rejected,
        )


def test_review_fails_when_not_all_pending(db: Session) -> None:
    item = _class(db)
    job = _job(
        db, item.id, users=[uuid.uuid4()], status=BatchProvisionJobStatus.pending_review
    )
    with pytest.raises(BadRequestError):
        svc.review_batch_jobs(
            session=db,
            job_ids=[job.id, uuid.uuid4()],
            reviewer_id=REVIEWER,
            decision=BatchProvisionJobStatus.rejected,
        )


# ---------------------------------------------------------------------------
# to_public
# ---------------------------------------------------------------------------


def _user(db: Session, user_id: uuid.UUID, name: str) -> User:
    user = User(
        id=user_id,
        email=f"{name}-{user_id.hex[:6]}@example.com",
        hashed_password="x",
        full_name=name,
    )
    db.add(user)
    db.commit()
    return user


def test_to_public_includes_people_class_and_spec(db: Session) -> None:
    item = _class(db)
    student = uuid.uuid4()
    _user(db, OWNER, "owner")
    _user(db, REVIEWER, "reviewer")
    _user(db, student, "student")
    missing_student = uuid.uuid4()
    job = _job(
        db,
        item.id,
        users=[student, missing_student],
        params={"cores": 2, "memory": 1024, "expiry_date": "2026-12-31"},
    )

    public = svc.to_public(db, job)

    assert public.teaching_class_name == "Linux"
    assert public.initiated_by_name == "owner"
    assert public.reviewer_email.startswith("reviewer-")
    assert public.spec.cores == 2
    by_user = {task.user_id: task for task in public.tasks}
    assert by_user[student].user_name == "student"
    assert by_user[missing_student].user_email is None


# ---------------------------------------------------------------------------
# requeue_stalled_approved_jobs
# ---------------------------------------------------------------------------


def _old() -> datetime:
    return datetime.now(UTC) - timedelta(minutes=svc.APPROVED_JOB_REQUEUE_MINUTES + 5)


def test_requeue_skips_job_without_anyone_to_enqueue_as(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _job(
        db,
        uuid.uuid4(),
        users=[uuid.uuid4()],
        initiated_by=None,
        reviewer_id=None,
        reviewed_at=_old(),
    )
    monkeypatch.setattr(
        svc, "enqueue_task_sync", lambda **_k: pytest.fail("must not enqueue")
    )
    assert svc.requeue_stalled_approved_jobs() == 0


def test_requeue_survives_database_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "engine", object())
    assert svc.requeue_stalled_approved_jobs() == 0


def test_reap_survives_database_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "engine", object())
    assert svc.reap_stale_batch_jobs() == 0


# ---------------------------------------------------------------------------
# heartbeat / worker handler
# ---------------------------------------------------------------------------


async def test_heartbeat_loop_logs_failures_and_keeps_going(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[uuid.UUID] = []

    def flaky(job_id: uuid.UUID) -> int:
        calls.append(job_id)
        if len(calls) == 1:
            raise RuntimeError("db down")
        raise asyncio.CancelledError

    monkeypatch.setattr(svc, "record_heartbeat", flaky)
    job_id = uuid.uuid4()
    with pytest.raises(asyncio.CancelledError):
        await svc.heartbeat_loop(job_id, interval_seconds=0)
    assert calls == [job_id, job_id]


# ---------------------------------------------------------------------------
# _run_queue tail branches
# ---------------------------------------------------------------------------


def test_run_queue_returns_when_job_disappears(
    db: Session, monkeypatch: pytest.MonkeyPatch, no_refresh: list[uuid.UUID]
) -> None:
    item = _class(db)
    job = _job(db, item.id, users=[])
    monkeypatch.setattr(svc.bp_repo, "lock_job", lambda **_k: None)

    svc._run_queue(job.id)

    assert no_refresh == []


def test_run_queue_keeps_cancelled_result(
    db: Session, monkeypatch: pytest.MonkeyPatch, no_refresh: list[uuid.UUID]
) -> None:
    item = _class(db)
    job = _job(db, item.id, users=[uuid.uuid4()])

    def cancel(*, job_id: uuid.UUID, task_id: uuid.UUID) -> None:
        with Session(svc.engine) as session:
            row = session.get(BatchProvisionJob, job_id)
            row.status = BatchProvisionJobStatus.cancelled
            session.add(row)
            session.commit()

    monkeypatch.setattr(svc, "_process_task", cancel)

    svc._run_queue(job.id)

    db.expire_all()
    assert db.get(BatchProvisionJob, job.id).status == BatchProvisionJobStatus.cancelled
    assert no_refresh == []


# ---------------------------------------------------------------------------
# _refresh_teaching_class_status
# ---------------------------------------------------------------------------


def test_refresh_class_status_recomputes(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.teaching import class_status_service

    seen: list[uuid.UUID] = []
    monkeypatch.setattr(
        class_status_service,
        "recompute",
        lambda *, session, class_id: seen.append(class_id),
    )
    class_id = uuid.uuid4()
    svc._refresh_teaching_class_status(class_id)
    assert seen == [class_id]


def test_refresh_class_status_swallows_and_logs_errors(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.services.teaching import class_status_service

    def boom(**_k: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(class_status_service, "recompute", boom)
    svc._refresh_teaching_class_status(uuid.uuid4())
    assert "Failed to refresh teaching class status" in caplog.text


# ---------------------------------------------------------------------------
# _process_task
# ---------------------------------------------------------------------------


def test_process_task_missing_task_or_job(db: Session) -> None:
    svc._process_task(job_id=uuid.uuid4(), task_id=uuid.uuid4())

    orphan = BatchProvisionTask(
        job_id=uuid.uuid4(), user_id=uuid.uuid4(), member_index=1
    )
    db.add(orphan)
    db.commit()
    svc._process_task(job_id=orphan.job_id, task_id=orphan.id)
    db.expire_all()
    assert (
        db.get(BatchProvisionTask, orphan.id).status == BatchProvisionTaskStatus.pending
    )


@pytest.fixture()
def worker_env(
    db: Session, monkeypatch: pytest.MonkeyPatch, no_refresh: list[uuid.UUID]
) -> dict[str, Any]:
    state: dict[str, Any] = {"discarded": [], "snapshots": []}
    monkeypatch.setattr(svc, "_class_target_node", lambda **_k: None)
    monkeypatch.setattr(svc, "_provision_one", lambda **_k: 300)
    monkeypatch.setattr(
        svc.resource_repo,
        "assign_to_teaching_class",
        lambda **kw: SimpleNamespace(vmid=kw["vmid"]),
    )
    monkeypatch.setattr(
        svc,
        "_discard_provisioned_machine",
        lambda **kw: state["discarded"].append(kw["reason"]),
    )
    from app.services.resource import reset_service

    monkeypatch.setattr(
        reset_service, "ensure_init_snapshot", state["snapshots"].append
    )
    item = _class(db)
    job = _job(db, item.id, users=[uuid.uuid4()])
    bp_repo.transition_job_to_running(session=db, job_id=job.id)
    state["class"] = item
    state["job"] = job
    state["task"] = _tasks(db, job.id)[0]
    return state


def _run_one(state: dict[str, Any]) -> BatchProvisionTask:
    svc._process_task(job_id=state["job"].id, task_id=state["task"].id)
    with Session(svc.engine) as session:
        return session.get(BatchProvisionTask, state["task"].id)


def test_process_task_discards_machine_without_resource_row(
    worker_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        svc.resource_repo, "assign_to_teaching_class", lambda **_k: None
    )
    task = _run_one(worker_env)
    assert task.status == BatchProvisionTaskStatus.failed
    assert worker_env["discarded"] == ["resource 300 has no database record"]


def test_process_task_discards_machine_when_class_archived_mid_build(
    worker_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def provision(**_k: Any) -> int:
        with Session(svc.engine) as session:
            row = session.get(TeachingClass, worker_env["class"].id)
            row.status = TeachingClassStatus.archived
            session.add(row)
            session.commit()
        return 300

    monkeypatch.setattr(svc, "_provision_one", provision)
    task = _run_one(worker_env)
    assert task.status == BatchProvisionTaskStatus.failed
    assert worker_env["discarded"] == ["teaching class archived during provisioning"]


def test_process_task_snapshot_failure_does_not_fail_task(
    worker_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.resource import reset_service

    def boom(_vmid: int) -> None:
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr(reset_service, "ensure_init_snapshot", boom)
    task = _run_one(worker_env)
    assert task.status == BatchProvisionTaskStatus.completed


def test_process_task_discards_machine_when_grant_cannot_be_recorded(
    worker_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**_k: Any) -> bool:
        raise RuntimeError("db write failed")

    monkeypatch.setattr(svc, "_record_task_success", boom)
    task = _run_one(worker_env)
    assert task.status == BatchProvisionTaskStatus.failed
    assert worker_env["discarded"] == [
        f"failed to record class machine grant for task {worker_env['task'].id}"
    ]


# ---------------------------------------------------------------------------
# _superseded_by_retry / _discard_provisioned_machine
# ---------------------------------------------------------------------------


def test_superseded_by_retry_needs_failure_time_and_job(db: Session) -> None:
    task = BatchProvisionTask(
        job_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        member_index=1,
        status=BatchProvisionTaskStatus.failed,
        finished_at=None,
    )
    db.add(task)
    db.commit()
    assert svc._superseded_by_retry(session=db, task_id=task.id) is False


def test_discard_machine_deletes_through_resource_service(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.resource import resource_service

    deleted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        svc.proxmox_service, "find_resource", lambda vmid: {"vmid": vmid}
    )
    monkeypatch.setattr(resource_service, "delete", lambda **kw: deleted.append(kw))
    svc._discard_provisioned_machine(vmid=301, initiated_by_id=OWNER, reason="x")
    assert deleted[0]["vmid"] == 301
    assert deleted[0]["resource_info"] == {"vmid": 301}
    assert deleted[0]["purge"] is True


def test_discard_machine_logs_when_cleanup_fails(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(_vmid: int) -> None:
        raise RuntimeError("pve down")

    monkeypatch.setattr(svc.proxmox_service, "find_resource", boom)
    svc._discard_provisioned_machine(vmid=302, initiated_by_id=OWNER, reason="x")
    assert "manual cleanup required" in caplog.text


# ---------------------------------------------------------------------------
# _class_target_node / _sync_class_machine_mapping
# ---------------------------------------------------------------------------


def _machine_node(
    db: Session, class_id: uuid.UUID, job_id: uuid.UUID
) -> TeachingClassMachineNode:
    node = TeachingClassMachineNode(
        class_id=class_id,
        node_key="web",
        source_type="custom",
        name="web",
        role="web",
        resource_type="lxc",
        cpu=1,
        memory_mb=512,
        disk_gb=8,
        batch_job_id=job_id,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def test_class_target_node_without_machine_node(db: Session) -> None:
    assert (
        svc._class_target_node(session=db, job_id=uuid.uuid4(), user_id=uuid.uuid4())
        is None
    )


def test_class_target_node_resolves_and_falls_back(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.teaching import class_capacity_service

    item = _class(db)
    job = _job(db, item.id, users=[])
    _machine_node(db, item.id, job.id)
    monkeypatch.setattr(
        class_capacity_service, "target_node_for_machine", lambda *_a, **_k: "pve2"
    )
    assert svc._class_target_node(session=db, job_id=job.id, user_id=OWNER) == "pve2"

    def boom(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("no plan")

    monkeypatch.setattr(class_capacity_service, "target_node_for_machine", boom)
    assert svc._class_target_node(session=db, job_id=job.id, user_id=OWNER) is None


def test_sync_mapping_skips_unknown_job(db: Session) -> None:
    svc._sync_class_machine_mapping(
        session=db, job_id=uuid.uuid4(), task_id=uuid.uuid4(), user_id=uuid.uuid4()
    )


def test_sync_mapping_upserts_and_commits(db: Session) -> None:
    item = _class(db)
    student = uuid.uuid4()
    job = _job(db, item.id, users=[student])
    node = _machine_node(db, item.id, job.id)
    enrollment = TeachingClassStudent(class_id=item.id, user_id=student)
    db.add(enrollment)
    db.commit()
    task = _tasks(db, job.id)[0]

    svc._sync_class_machine_mapping(
        session=db, job_id=job.id, task_id=task.id, user_id=student
    )

    with Session(svc.engine) as session:
        mapping = session.exec(
            TeachingClassStudentMachine.__table__.select().where(
                TeachingClassStudentMachine.__table__.c.machine_node_id == node.id
            )
        ).first()
    assert mapping is not None


# ---------------------------------------------------------------------------
# _provision_one
# ---------------------------------------------------------------------------


def test_provision_one_clones_vm_template_with_reservation(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    class_id = uuid.uuid4()
    db.add(
        ClassCapacityReservation(
            class_id=class_id,
            course_version_id=uuid.uuid4(),
            student_count=1,
            machine_count=1,
            cpu_cores=1,
            memory_mb=512,
            disk_gb=8,
            ip_count=1,
            network_count=1,
            status="reserved",
        )
    )
    db.commit()
    payloads: list[dict[str, Any]] = []
    monkeypatch.setattr(
        svc.password_policy,
        "find_template",
        lambda _s, *, template_id: SimpleNamespace(password_settable=False),
    )
    monkeypatch.setattr(
        svc.clone_service,
        "run_clone_task",
        lambda _tid, payload: payloads.append(payload) or {"vmid": "321"},
    )
    template_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()

    vmid = svc._provision_one(
        session=db,
        resource_type="qemu",
        hostname="h-1",
        user_id=user_id,
        params={
            "vm_template_id": template_id,
            "cores": 2,
            "ip_reservation_prefix": "cls",
        },
        start=False,
        batch_job_id=job_id,
        teaching_class_id=class_id,
    )

    assert vmid == 321
    payload = payloads[0]
    assert payload["allow_password_reset"] is False
    assert payload["template_id"] == str(template_id)
    assert payload["batch_job_id"] == str(job_id)
    assert payload["ip_reservation_key"] == f"cls:{user_id}"
    assert payload["environment_type"] == "批量建立"


@pytest.fixture()
def quota_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        svc.quota_service,
        "check_quota",
        lambda _s, _uid, **kw: calls.append(kw),
    )
    return calls


def test_provision_one_creates_vm_and_requires_vmid(
    db: Session, monkeypatch: pytest.MonkeyPatch, quota_calls: list[dict[str, Any]]
) -> None:
    results = [SimpleNamespace(vmid=402), SimpleNamespace(vmid=None)]
    created: list[dict[str, Any]] = []
    monkeypatch.setattr(
        svc.provisioning_service,
        "create_vm",
        lambda **kw: created.append(kw) or results.pop(0),
    )
    params = {
        "template_id": 9000,
        "cores": 2,
        "memory": 2048,
        "password": "Secret123!",
        "expiry_date": "not-a-date",
    }
    assert (
        svc._provision_one(
            session=db,
            resource_type="qemu",
            hostname="h-1",
            user_id=uuid.uuid4(),
            params=params,
        )
        == 402
    )
    req = created[0]["vm_data"]
    assert req.username == "student"
    assert req.password == "Secret123!"
    assert req.expiry_date is None

    with pytest.raises(RuntimeError, match="did not return a vmid"):
        svc._provision_one(
            session=db,
            resource_type="qemu",
            hostname="h-2",
            user_id=uuid.uuid4(),
            params=params,
        )
