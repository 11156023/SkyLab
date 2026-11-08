"""Fast admission, immutable selection and lost-acknowledgement regressions."""

from __future__ import annotations

import asyncio
import json
import uuid
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlmodel import Session, select

from app.ai.teacher_judge import script_executor_service as executor
from app.ai.teacher_judge import script_run_service as service
from app.ai.teacher_judge.run_record_service import update_batch
from app.ai.teacher_judge.schemas import TeacherJudgeScriptSetRunRequest
from app.api.routes import teacher_judge_sessions as routes
from app.models.teacher_judge_run_batch import TeacherJudgeRunBatch
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunStatus,
)
from tests.ai.teacher_judge.helpers import make_script_workspace, make_session
from tests.ai.teacher_judge.test_script_artifact_service_coverage import _add_node


@pytest.fixture
def context(monkeypatch):
    with make_session() as db:
        class_id, set_id = uuid.uuid4(), uuid.uuid4()
        workspace = make_script_workspace(db, class_id)
        workspace.active_artifact_set_id = set_id
        db.add(workspace)
        _add_node(db, class_id, "web")
        db.add(
            TeacherJudgeScriptArtifact(
                teaching_class_id=class_id,
                session_id=workspace.id,
                artifact_set_id=set_id,
                target_node_key="web",
                name="web",
                template_key="linux",
                script_content="pass",
                status=TeacherJudgeScriptStatus.approved,
            )
        )
        db.commit()
        members = [
            {
                "student_id": f"student-{index}",
                "user_id": f"user-{index}",
                "vmid": 1000 + index,
                "node_key": "web",
                "email": f"s{index}@example.edu",
            }
            for index in range(100)
        ]
        monkeypatch.setattr(
            service, "_class_member_by_node_key", lambda **kwargs: {"web": members}
        )
        monkeypatch.setattr(executor, "engine", db.get_bind())
        yield db, workspace, set_id, members


def admit(context, **kwargs):
    db, workspace, set_id, _ = context
    return service.create_script_run_batch(
        session=db,
        teaching_class_id=workspace.teaching_class_id,
        artifact_set_id=set_id,
        session_id=workspace.id,
        started_by=None,
        **kwargs,
    )


def test_hundred_targets_are_saved_before_any_pve_or_ip_work(context, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("admission contacted PVE/IP/OS or started SSH")

    monkeypatch.setattr(service, "_resolve_member_targets", forbidden)
    monkeypatch.setattr(service, "_running_resources_by_vmid", forbidden)
    monkeypatch.setattr(service.os_identity_service, "ensure_guest_os", forbidden)
    monkeypatch.setattr(service, "resolve_target_ip_address", forbidden)
    monkeypatch.setattr(executor, "_execute_target_script", forbidden)
    batch_id = uuid.uuid4()
    public = admit(context, run_batch_id=batch_id)
    assert public.run_batch_id == str(batch_id)
    assert public.status == "pending" and public.prepared_at is None
    assert public.summary["targets"] == public.summary["queued"] == 100
    assert public.nodes[0].progress_json["stage"] == "preparing"
    db, workspace, _, _ = context
    with Session(db.get_bind()) as observer:
        assert observer.get(TeacherJudgeRunBatch, batch_id).status == "pending"
        run = observer.exec(select(TeacherJudgeScriptRun)).one()
        assert len(run.target_records) == 100 and run.prepared_at is None
        assert {row.vmid for row in run.target_records} == set(range(1000, 1100))
    assert service.list_session_batch_summaries(
        db, workspace.teaching_class_id, workspace.id, 0, 10
    )[0].run_batch_id == str(batch_id)


def test_active_batch_blocks_different_ids_and_admission_locks_workspace(
    context, monkeypatch
):
    db, _, _, _ = context
    statements = []
    original_exec = db.exec

    def capture(statement, *args, **kwargs):
        statements.append(str(statement.compile(dialect=postgresql.dialect())))
        return original_exec(statement, *args, **kwargs)

    monkeypatch.setattr(db, "exec", capture)
    first = admit(context)
    with pytest.raises(HTTPException) as error:
        admit(context, run_batch_id=uuid.uuid4())
    assert error.value.status_code == 409
    assert error.value.detail["run_batch_id"] == first.run_batch_id
    assert any(
        "teacher_judge_sessions" in sql and "FOR UPDATE" in sql for sql in statements
    )
    assert len(db.exec(select(TeacherJudgeRunBatch)).all()) == 1
    assert len(db.exec(select(TeacherJudgeScriptRun)).all()) == 1


def test_same_id_cannot_repeat_a_finished_run(context):
    db, _, _, _ = context
    batch_id = uuid.uuid4()
    admit(context, run_batch_id=batch_id)
    run = db.exec(select(TeacherJudgeScriptRun)).one()
    run.status = TeacherJudgeScriptRunStatus.completed
    db.add(run)
    update_batch(db, batch_id)
    db.commit()
    with pytest.raises(HTTPException) as error:
        admit(context, run_batch_id=batch_id)
    assert error.value.detail["code"] == "teacher_judge_batch_exists"
    assert len(db.exec(select(TeacherJudgeRunBatch)).all()) == 1


def test_background_preparation_uses_saved_machines_and_preserves_failures(
    context, monkeypatch
):
    db, _, _, members = context
    first_user = uuid.uuid4()
    members[0]["user_id"] = str(first_user)
    batch = admit(context, user_ids=[first_user])
    members[0]["vmid"] = 9999
    seen = []

    def resolve(**kwargs):
        seen.extend(member["vmid"] for member in kwargs["members"])
        return [], [
            service._node_target_failure(
                kwargs["members"][0],
                reason_code="target_not_ready",
                detail="no IP",
            )
        ]

    monkeypatch.setattr(service, "_resolve_member_targets", resolve)
    service.prepare_script_run_batch(db, uuid.UUID(batch.run_batch_id))
    assert seen == [1000]
    run = db.exec(select(TeacherJudgeScriptRun)).one()
    assert run.prepared_at is not None
    assert len(run.target_records) == 1
    assert run.target_records[0].vmid == 1000
    assert run.target_records[0].reason_code == "target_not_ready"
    assert run.progress_json["done"] == run.progress_json["total"] == 1
    service.prepare_script_run_batch(db, uuid.UUID(batch.run_batch_id))
    assert seen == [1000]


def test_preparation_does_not_resurrect_a_reaped_run(context, monkeypatch):
    db, _, _, _ = context
    batch = admit(context)
    run_id = uuid.UUID(batch.nodes[0].run_id)

    def resolve(**kwargs):
        executor._mark_run_executor_failed(
            run_id, "interrupted", reason_code="executor_lost"
        )
        return [], []

    monkeypatch.setattr(service, "_resolve_member_targets", resolve)
    service.prepare_script_run_batch(db, uuid.UUID(batch.run_batch_id))
    db.expire_all()
    run = db.get(TeacherJudgeScriptRun, run_id)
    assert run.status == TeacherJudgeScriptRunStatus.failed
    assert run.prepared_at is None
    assert run.progress_json["stage"] == "failed"
    assert all(row.reason_code == "executor_lost" for row in run.target_records)


@pytest.mark.asyncio
async def test_cancellation_drains_preparation_and_never_dispatches_ssh(
    context, monkeypatch
):
    db, _, _, _ = context
    batch = admit(context)
    batch_id = uuid.UUID(batch.run_batch_id)
    started, release = Event(), Event()

    def prepare(_batch_id, *, stop_event):
        started.set()
        assert release.wait(5)
        assert stop_event.is_set()

    monkeypatch.setattr(executor, "_prepare_script_run_batch", prepare)

    async def forbidden(run_id):
        pytest.fail("cancelled preparation dispatched SSH")

    monkeypatch.setattr(executor, "execute_script_run", forbidden)
    task = asyncio.create_task(executor.execute_script_run_batch(batch_id))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.gather(task)
    db.expire_all()
    run = db.exec(select(TeacherJudgeScriptRun)).one()
    assert run.status == TeacherJudgeScriptRunStatus.failed
    assert all(row.reason_code == "executor_cancelled" for row in run.target_records)


def test_route_returns_saved_batch_without_running_preparation(context, monkeypatch):
    db, workspace, set_id, _ = context
    submitted = []
    monkeypatch.setattr(routes, "_access", lambda *args: None)

    def submit(coro, **kwargs):
        submitted.append(kwargs["task_id"])
        coro.close()
        return kwargs["task_id"]

    monkeypatch.setattr(routes, "submit", submit)
    monkeypatch.setattr(
        service,
        "_resolve_member_targets",
        lambda **kwargs: pytest.fail("route waited for preparation"),
    )
    batch_id = uuid.uuid4()
    response = routes.create_session_script_set_run(
        workspace.teaching_class_id,
        workspace.id,
        set_id,
        TeacherJudgeScriptSetRunRequest(run_batch_id=batch_id),
        db,
        SimpleNamespace(id=None),
    )
    assert response.run_batch_id == str(batch_id) and response.prepared_at is None
    assert response.summary["targets"] == 100
    assert submitted == [f"teacher_judge_script_run_batch:{batch_id}"]


def test_cancellation_stops_preparing_the_remaining_machines(context, monkeypatch):
    db, workspace, _, members = context
    stop_event = Event()
    calls = []
    monkeypatch.setattr(service, "_running_resources_by_vmid", lambda: {})

    def resolve(**kwargs):
        calls.extend(kwargs["target_vmids"])
        stop_event.set()
        return []

    monkeypatch.setattr(service, "_resolve_running_targets", resolve)
    with pytest.raises(InterruptedError):
        service._resolve_member_targets(
            session=db,
            teaching_class_id=workspace.teaching_class_id,
            members=members,
            stop_event=stop_event,
        )
    assert calls == [1000]


def test_admission_response_failure_rolls_back_before_acceptance(context, monkeypatch):
    db, _, _, _ = context

    def fail(**kwargs):
        raise RuntimeError("response projection failed")

    monkeypatch.setattr(service, "get_script_run_batch_public", fail)
    with pytest.raises(RuntimeError, match="response projection failed"):
        admit(context)
    assert list(db.exec(select(TeacherJudgeRunBatch)).all()) == []
    assert list(db.exec(select(TeacherJudgeScriptRun)).all()) == []


def test_dispatch_exception_terminates_every_accepted_target(context, monkeypatch):
    db, workspace, set_id, _ = context
    monkeypatch.setattr(routes, "_access", lambda *args: None)

    def fail(*args, **kwargs):
        raise RuntimeError("runner unavailable")

    monkeypatch.setattr(routes, "submit", fail)
    response = routes.create_session_script_set_run(
        workspace.teaching_class_id,
        workspace.id,
        set_id,
        TeacherJudgeScriptSetRunRequest(),
        db,
        SimpleNamespace(id=None),
    )
    assert response.status == "failed"
    assert response.summary["targets"] == response.summary["failed"] == 100
    db.expire_all()
    run = db.exec(select(TeacherJudgeScriptRun)).one()
    assert all(row.reason_code == "executor_unavailable" for row in run.target_records)


@pytest.mark.asyncio
async def test_hundred_saved_targets_prepare_execute_and_persist_once(
    context, monkeypatch
):
    db, workspace, _, _ = context
    batch = admit(context)
    seen = []

    def prepare(**kwargs):
        return [
            {
                **member,
                "name": str(member["vmid"]),
                "resource_type": "lxc",
                "proxmox_node": "test-node",
                "user": {"id": member["user_id"], "email": member["email"]},
            }
            for member in kwargs["members"]
        ], []

    def ssh(*, target, script_content):
        seen.append(target["vmid"])
        return executor.RemoteScriptResult(
            0,
            json.dumps(
                {
                    "schema_version": "teacher_judge_result.v1",
                    "metadata": {
                        "timestamp": "2026-10-16T00:00:00Z",
                        "platform": "linux",
                    },
                    "checks": [{"id": "check", "title": "Check", "status": "pass"}],
                    "errors": [],
                }
            ),
            "",
        )

    monkeypatch.setattr(service, "_resolve_member_targets", prepare)
    monkeypatch.setattr(executor, "_live_running_by_vmid", lambda: {})
    monkeypatch.setattr(
        executor,
        "_resolve_runtime_target",
        lambda **kwargs: {
            **kwargs["target"],
            "run_id": str(kwargs["run"].id),
        },
    )
    monkeypatch.setattr(executor, "_execute_target_script", ssh)
    batch_id = uuid.UUID(batch.run_batch_id)
    await executor.execute_script_run_batch(batch_id)
    await executor.execute_script_run_batch(batch_id)
    db.expire_all()
    public = service.get_script_run_batch_public(
        session=db,
        teaching_class_id=workspace.teaching_class_id,
        run_batch_id=batch_id,
    )
    assert public.status == "completed"
    assert public.summary["targets"] == public.summary["completed"] == 100
    assert len(seen) == len(set(seen)) == 100
    assert public.timing["preparation_seconds"] is not None
    assert public.finished_at is not None
    run = db.exec(select(TeacherJudgeScriptRun)).one()
    assert len(run.target_records) == 100
    assert all(row.status == "completed" for row in run.target_records)
