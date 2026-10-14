"""批次建機：心跳、回收後的 worker 行為、核准後入列失敗的安全網（SQLite + mock PVE）。

- 單台 clone 等很久時 worker 心跳讓回收器不誤判
- job 被回收後 worker 不再建已標 failed 的 task；晚到的成功照記、不重複計數
- 核准時入列失敗仍回成功，排程安全網幾分鐘後重新入列
- approved → running 是條件式轉換，同一個 job 不會跑兩次
"""

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

from app.models import (
    Resource,
    TeachingClass,
    TeachingClassStatus,
)
from app.models.batch_provision import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    BatchProvisionTask,
    BatchProvisionTaskStatus,
)
from app.repositories import batch_provision as bp_repo
from app.services.vm import batch_provision_service as svc
from app.services.vm import tasks as vm_tasks

OWNER = uuid.uuid4()
REVIEWER = uuid.uuid4()


@pytest.fixture()
def engine(monkeypatch: pytest.MonkeyPatch) -> Engine:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(svc, "engine", eng)
    monkeypatch.setattr(svc, "_refresh_teaching_class_status", lambda _cid: None)
    return eng


@pytest.fixture()
def db(engine: Engine) -> Session:
    with Session(engine) as session:
        yield session


def _class(db: Session) -> TeachingClass:
    item = TeachingClass(
        owner_id=OWNER,
        name="Linux",
        code=f"cls-{uuid.uuid4().hex[:8]}",
        term="115-1",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 12, 31),
        weekday=1,
        start_time=time(13, 10),
        end_time=time(16, 0),
        status=TeachingClassStatus.provisioning,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _job(
    db: Session,
    item: TeachingClass,
    *,
    users: list[uuid.UUID],
    status: BatchProvisionJobStatus = BatchProvisionJobStatus.approved,
    reviewed_at: datetime | None = None,
    created_at: datetime | None = None,
) -> BatchProvisionJob:
    job = bp_repo.create_job(
        session=db,
        teaching_class_id=item.id,
        initiated_by=OWNER,
        resource_type="lxc",
        hostname_prefix="cls-1",
        template_params={"cores": 1, "memory": 512},
        member_user_ids=users,
        initial_status=status,
    )
    job.reviewer_id = REVIEWER
    job.reviewed_at = reviewed_at or datetime.now(UTC)
    if created_at is not None:
        job.created_at = created_at
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _tasks(db: Session, job_id: uuid.UUID) -> list[BatchProvisionTask]:
    db.expire_all()
    return bp_repo.get_job_tasks(session=db, job_id=job_id)


@pytest.fixture()
def provision_env(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> dict[str, Any]:
    """把 _provision_one 之後的 PVE／班級掛載都換成假的；回傳呼叫紀錄。"""
    state: dict[str, Any] = {"provisioned": [], "discarded": [], "next_vmid": 300}
    monkeypatch.setattr(svc, "_class_target_node", lambda **_k: None)
    monkeypatch.setattr(
        svc.resource_repo,
        "assign_to_teaching_class",
        lambda **_k: SimpleNamespace(vmid=_k["vmid"]),
    )
    monkeypatch.setattr(
        svc,
        "_discard_provisioned_machine",
        lambda **kw: state["discarded"].append(kw["vmid"]),
    )
    from app.services.resource import reset_service

    monkeypatch.setattr(reset_service, "ensure_init_snapshot", lambda _vmid: None)
    return state


def _fake_provision(state: dict[str, Any], *, during=None, fail: bool = False):
    def provision_one(**kwargs: Any) -> int:
        state["provisioned"].append(kwargs["hostname"])
        if during is not None:
            during()
        if fail:
            raise RuntimeError("PVE clone failed")
        vmid = state["next_vmid"]
        state["next_vmid"] += 1
        with Session(svc.engine) as session:
            session.add(
                Resource(
                    vmid=vmid,
                    user_id=kwargs["user_id"],
                    environment_type="batch",
                    created_at=datetime.now(UTC),
                )
            )
            session.commit()
        return vmid

    return provision_one


# ---------------------------------------------------------------------------
# A4：心跳
# ---------------------------------------------------------------------------


def _running_job_with_old_task(db: Session) -> BatchProvisionJob:
    item = _class(db)
    long_ago = datetime.now(UTC) - timedelta(hours=5)
    job = _job(
        db,
        item,
        users=[uuid.uuid4(), uuid.uuid4()],
        status=BatchProvisionJobStatus.running,
        reviewed_at=long_ago,
        created_at=long_ago,
    )
    first = _tasks(db, job.id)[0]
    first.status = BatchProvisionTaskStatus.running
    first.started_at = long_ago
    db.add(first)
    db.commit()
    return job


def test_heartbeat_keeps_long_clone_from_being_reaped(db: Session) -> None:
    job = _running_job_with_old_task(db)

    assert svc.record_heartbeat(job.id) == 1
    assert svc.reap_stale_batch_jobs() == 0

    db.expire_all()
    assert db.get(BatchProvisionJob, job.id).status == BatchProvisionJobStatus.running
    statuses = [task.status for task in _tasks(db, job.id)]
    assert statuses == [BatchProvisionTaskStatus.running, BatchProvisionTaskStatus.pending]


def test_job_without_heartbeat_is_still_reaped(db: Session) -> None:
    job = _running_job_with_old_task(db)

    assert svc.reap_stale_batch_jobs() == 1

    db.expire_all()
    assert db.get(BatchProvisionJob, job.id).status == BatchProvisionJobStatus.failed


def test_heartbeat_does_not_revive_reaped_tasks(db: Session) -> None:
    job = _running_job_with_old_task(db)
    assert svc.reap_stale_batch_jobs() == 1

    assert svc.record_heartbeat(job.id) == 0
    assert all(
        task.status == BatchProvisionTaskStatus.failed for task in _tasks(db, job.id)
    )


async def test_heartbeat_loop_beats_until_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    beats: list[uuid.UUID] = []
    monkeypatch.setattr(svc, "record_heartbeat", beats.append)
    job_id = uuid.uuid4()

    loop_task = asyncio.create_task(svc.heartbeat_loop(job_id, interval_seconds=0.01))
    await asyncio.sleep(0.1)
    loop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop_task
    count = len(beats)
    await asyncio.sleep(0.05)

    assert count >= 2
    assert len(beats) == count
    assert set(beats) == {job_id}


async def test_worker_handler_runs_heartbeat_only_while_the_job_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def fake_loop(job_id: uuid.UUID) -> None:
        events.append("heartbeat-start")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            events.append("heartbeat-stop")
            raise

    def fake_run(task_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
        import time as _time

        _time.sleep(0.05)  # 讓心跳 task 有機會先開始
        events.append("run")
        return {"job_id": payload["job_id"]}

    monkeypatch.setattr(svc, "heartbeat_loop", fake_loop)
    monkeypatch.setattr(svc, "run_batch_job_task", fake_run)
    job_id = str(uuid.uuid4())

    result = await vm_tasks.run_batch_provision_job(uuid.uuid4(), {"job_id": job_id})
    await asyncio.sleep(0.05)

    assert result == {"job_id": job_id}
    assert events == ["heartbeat-start", "run", "heartbeat-stop"]


# ---------------------------------------------------------------------------
# A4：回收後 worker 不再建、晚到的成功照記
# ---------------------------------------------------------------------------


def test_reaped_job_late_success_is_recorded_and_rest_is_not_built(
    db: Session, provision_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    item = _class(db)
    job = _job(db, item, users=[uuid.uuid4(), uuid.uuid4(), uuid.uuid4()])
    # 第一台 clone 還在等時，回收器判定卡死：整個 job 被結案
    monkeypatch.setattr(
        svc,
        "_provision_one",
        _fake_provision(
            provision_env,
            during=lambda: svc.reap_stale_batch_jobs(max_running_hours=0),
        ),
    )

    svc._run_queue(job.id)

    # 只有第一台真的建過，其餘被回收的 task 沒有再建
    assert provision_env["provisioned"] == ["cls-1-1"]
    tasks = _tasks(db, job.id)
    assert [task.status for task in tasks] == [
        BatchProvisionTaskStatus.completed,
        BatchProvisionTaskStatus.failed,
        BatchProvisionTaskStatus.failed,
    ]
    assert tasks[0].vmid == 300
    assert tasks[0].error is None
    refreshed = db.get(BatchProvisionJob, job.id)
    assert (refreshed.done, refreshed.failed_count) == (1, 2)
    assert refreshed.done + refreshed.failed_count <= refreshed.total
    # 回收時全部失敗標成 failed；晚到的成功把它改回 completed
    assert refreshed.status == BatchProvisionJobStatus.completed
    assert provision_env["discarded"] == []


def test_reaped_job_late_failure_is_not_counted_twice(
    db: Session, provision_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    item = _class(db)
    job = _job(db, item, users=[uuid.uuid4(), uuid.uuid4()])
    monkeypatch.setattr(
        svc,
        "_provision_one",
        _fake_provision(
            provision_env,
            during=lambda: svc.reap_stale_batch_jobs(max_running_hours=0),
            fail=True,
        ),
    )

    svc._run_queue(job.id)

    assert provision_env["provisioned"] == ["cls-1-1"]
    refreshed = db.get(BatchProvisionJob, job.id)
    db.refresh(refreshed)
    assert (refreshed.done, refreshed.failed_count) == (0, 2)
    assert refreshed.status == BatchProvisionJobStatus.failed


def test_late_success_superseded_by_class_retry_is_discarded(
    db: Session, provision_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    item = _class(db)
    student = uuid.uuid4()
    job = _job(db, item, users=[student])

    def reap_then_retry() -> None:
        svc.reap_stale_batch_jobs(max_running_hours=0)
        with Session(svc.engine) as session:
            # 老師按重試：替同一位學生送出新的 job
            _job(
                session,
                session.get(TeachingClass, item.id),
                users=[student],
                status=BatchProvisionJobStatus.pending_review,
                created_at=datetime.now(UTC) + timedelta(seconds=1),
            )

    monkeypatch.setattr(
        svc, "_provision_one", _fake_provision(provision_env, during=reap_then_retry)
    )

    svc._run_queue(job.id)

    assert provision_env["discarded"] == [300]
    task = _tasks(db, job.id)[0]
    assert task.status == BatchProvisionTaskStatus.failed
    refreshed = db.get(BatchProvisionJob, job.id)
    assert (refreshed.done, refreshed.failed_count) == (0, 1)


def test_cancelled_tasks_are_counted_once(
    db: Session, provision_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    item = _class(db)
    job = _job(db, item, users=[uuid.uuid4(), uuid.uuid4()])
    assert bp_repo.transition_job_to_running(session=db, job_id=job.id)
    # 封存：cancel_jobs 先把 job 與未完成的 task 標掉
    from app.services.teaching import class_lifecycle_service

    class_lifecycle_service.cancel_jobs(db, item.id)
    db.commit()
    monkeypatch.setattr(svc, "_provision_one", _fake_provision(provision_env))

    for task in _tasks(db, job.id):
        svc._process_task(job_id=job.id, task_id=task.id)

    assert provision_env["provisioned"] == []
    refreshed = db.get(BatchProvisionJob, job.id)
    db.refresh(refreshed)
    assert (refreshed.done, refreshed.failed_count) == (0, 2)


def test_normal_run_counts_from_task_states(
    db: Session, provision_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    item = _class(db)
    job = _job(db, item, users=[uuid.uuid4(), uuid.uuid4()])
    monkeypatch.setattr(svc, "_provision_one", _fake_provision(provision_env))

    svc._run_queue(job.id)

    assert [task.vmid for task in _tasks(db, job.id)] == [300, 301]
    refreshed = db.get(BatchProvisionJob, job.id)
    assert refreshed.status == BatchProvisionJobStatus.completed
    assert (refreshed.done, refreshed.failed_count) == (2, 0)


# ---------------------------------------------------------------------------
# A5：核准後入列失敗與排程安全網
# ---------------------------------------------------------------------------


def test_job_runs_only_once_even_if_enqueued_twice(
    db: Session, provision_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    item = _class(db)
    job = _job(db, item, users=[uuid.uuid4()])
    monkeypatch.setattr(svc, "_provision_one", _fake_provision(provision_env))

    svc._run_queue(job.id)
    svc._run_queue(job.id)

    assert provision_env["provisioned"] == ["cls-1-1"]


def _pending_review_job(db: Session) -> BatchProvisionJob:
    item = _class(db)
    job = _job(
        db, item, users=[uuid.uuid4()], status=BatchProvisionJobStatus.pending_review
    )
    job.reviewed_at = None
    db.add(job)
    db.commit()
    return job


def test_approve_succeeds_when_enqueue_fails(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _pending_review_job(db)

    def broken_enqueue(**_kwargs: Any) -> None:
        raise TimeoutError("redis unreachable")

    monkeypatch.setattr(svc, "enqueue_task_sync", broken_enqueue)

    svc.approve_batch_job(session=db, job_id=job.id, reviewer_id=REVIEWER)

    db.expire_all()
    refreshed = db.get(BatchProvisionJob, job.id)
    assert refreshed.status == BatchProvisionJobStatus.approved
    assert refreshed.reviewer_id == REVIEWER


def test_class_review_succeeds_when_enqueue_fails(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _pending_review_job(db)

    def broken_enqueue(**_kwargs: Any) -> None:
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(svc, "enqueue_task_sync", broken_enqueue)

    reviewed = svc.review_batch_jobs(
        session=db,
        job_ids=[job.id],
        reviewer_id=REVIEWER,
        decision=BatchProvisionJobStatus.approved,
    )

    assert [row.id for row in reviewed] == [job.id]
    db.expire_all()
    assert db.get(BatchProvisionJob, job.id).status == BatchProvisionJobStatus.approved


def test_requeue_picks_up_only_stalled_approved_jobs(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = _class(db)
    old = datetime.now(UTC) - timedelta(minutes=svc.APPROVED_JOB_REQUEUE_MINUTES + 5)
    stalled = _job(db, item, users=[uuid.uuid4()], reviewed_at=old)
    fresh = _job(db, item, users=[uuid.uuid4()])
    running = _job(
        db, item, users=[uuid.uuid4()], status=BatchProvisionJobStatus.running,
        reviewed_at=old,
    )
    calls: list[dict[str, Any]] = []

    def fake_enqueue(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(svc, "enqueue_task_sync", fake_enqueue)

    assert svc.requeue_stalled_approved_jobs() == 1

    assert [call["payload"] for call in calls] == [{"job_id": str(stalled.id)}]
    assert calls[0]["job_id"] == f"batch_provision:{stalled.id}"
    assert calls[0]["user_id"] == REVIEWER
    assert fresh.id != running.id


def test_requeue_skips_jobs_already_queued(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = _class(db)
    old = datetime.now(UTC) - timedelta(minutes=svc.APPROVED_JOB_REQUEUE_MINUTES + 5)
    _job(db, item, users=[uuid.uuid4()], reviewed_at=old)
    # arq job id 去重：同一 job 還在排隊時 enqueue_task_sync 回 None
    monkeypatch.setattr(svc, "enqueue_task_sync", lambda **_k: None)

    assert svc.requeue_stalled_approved_jobs() == 0


def test_requeue_keeps_going_when_one_enqueue_fails(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = _class(db)
    old = datetime.now(UTC) - timedelta(minutes=svc.APPROVED_JOB_REQUEUE_MINUTES + 5)
    first = _job(db, item, users=[uuid.uuid4()], reviewed_at=old)
    second = _job(db, item, users=[uuid.uuid4()], reviewed_at=old)
    seen: list[str] = []

    def flaky_enqueue(**kwargs: Any) -> SimpleNamespace:
        seen.append(kwargs["payload"]["job_id"])
        if len(seen) == 1:
            raise TimeoutError("redis unreachable")
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(svc, "enqueue_task_sync", flaky_enqueue)

    assert svc.requeue_stalled_approved_jobs() == 1
    assert sorted(seen) == sorted([str(first.id), str(second.id)])
