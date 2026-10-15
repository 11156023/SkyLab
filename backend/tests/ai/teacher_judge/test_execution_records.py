"""Durability, timing, permissions and canonical execution-result consumers."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app.ai.teacher_judge import script_executor_service as executor
from app.ai.teacher_judge import script_run_service as service
from app.ai.teacher_judge.run_record_service import (
    initialize_targets,
    store_target_result,
    update_batch,
    utc_datetime,
)
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
from app.models.teacher_judge_session import TeacherJudgeSession
from app.services.course.ai_assignment_service import _target_for_student
from tests.ai.teacher_judge.helpers import make_session


def _artifact(db: Session, **kwargs: Any) -> TeacherJudgeScriptArtifact:
    artifact = TeacherJudgeScriptArtifact(
        teaching_class_id=kwargs.pop("teaching_class_id", uuid.uuid4()),
        name="records",
        template_key="linux",
        script_content="pass",
        status=TeacherJudgeScriptStatus.approved,
        **kwargs,
    )
    db.add(artifact)
    db.flush()
    return artifact


def _result(vmid: int, **kwargs: Any) -> dict[str, Any]:
    return {
        "vmid": vmid,
        "status": "completed",
        "reason_code": "success",
        "exit_code": 0,
        "validation": {"valid": True},
        "parsed_result": {"checks": []},
        **kwargs,
    }


def test_targets_without_vmid_are_distinct_and_monitoring_never_copies_ssh_keys() -> (
    None
):
    with make_session() as db:
        artifact = _artifact(db)
        students = [str(uuid.uuid4()), str(uuid.uuid4())]
        run = TeacherJudgeScriptRun(
            artifact_id=artifact.id,
            target_snapshot_json={
                "preflight_results": [
                    {
                        "student_id": student,
                        "vmid": None,
                        "status": "failed",
                        "reason_code": "missing_vmid",
                    }
                    for student in students
                ],
                "targets": [
                    {
                        "vmid": 101,
                        "host": "secret-host",
                        "private_key_pem": "secret-key",
                        "runtime_context": {},
                    }
                ],
            },
        )
        db.add(run)
        initialize_targets(db, run)
        db.commit()
        assert len(run.target_records) == 3
        assert run.target_results_json == {}
        assert len(run.results_document["targets"]) == 2
        page = service.list_run_targets(db, run, skip=0, limit=1, status="failed")
        assert page.total == 2 and len(page.data) == 1
        assert page.data[0].finished_at is None  # unobserved legacy preflight time
        assert "secret" not in json.dumps(
            service.list_run_targets(db, run, skip=0, limit=10).model_dump()
        )
        assert (
            service.list_run_targets(
                db, run, skip=0, limit=10, student_id=uuid.UUID(students[0])
            ).total
            == 1
        )
        assert service.list_run_targets(db, run, skip=0, limit=10, vmid=101).total == 1


def test_batch_totals_use_elapsed_time_and_keep_student_count_before_results() -> None:
    with make_session() as db:
        base = datetime(2026, 10, 14, tzinfo=timezone.utc)
        class_id, student_id = uuid.uuid4(), str(uuid.uuid4())
        batch = TeacherJudgeRunBatch(
            teaching_class_id=class_id,
            requested_at=base,
            prepared_at=base + timedelta(seconds=2),
            created_at=base,
        )
        db.add(batch)
        db.flush()
        runs = []
        for index, node in enumerate(("web", "db")):
            artifact = _artifact(db, teaching_class_id=class_id, target_node_key=node)
            run = TeacherJudgeScriptRun(
                artifact_id=artifact.id,
                run_batch_id=batch.id,
                target_snapshot_json={
                    "targets": [
                        {
                            "vmid": 101 + index,
                            "student_id": student_id,
                            "node_key": node,
                        }
                    ]
                },
            )
            db.add(run)
            initialize_targets(db, run)
            runs.append(run)
        db.commit()
        pending = service.get_script_run_batch_public(
            session=db, teaching_class_id=class_id, run_batch_id=batch.id
        )
        assert pending.summary["students"] == 1 and pending.summary["targets"] == 2
        assert (
            pending.summary["queued"] == 2 and pending.timing["total_seconds"] is None
        )
        for index, run in enumerate(runs):
            run.started_at = base + timedelta(seconds=3 + index * 10)
            run.finished_at = base + timedelta(seconds=13 + index * 10)
            run.status = TeacherJudgeScriptRunStatus.completed
            store_target_result(
                run.target_records[0],
                _result(101 + index, timing={"total_seconds": 100}),
            )
        update_batch(db, batch.id)
        db.commit()
        public = service.get_script_run_batch_public(
            session=db, teaching_class_id=class_id, run_batch_id=batch.id
        )
        assert public.status == "completed"
        assert (
            public.timing["total_seconds"] == 23
        )  # not the sum of machine times (200)
        assert public.timing["preparation_seconds"] == 2
        assert public.timing["queue_seconds"] == 1
        assert public.timing["execution_seconds"] == 20
        assert public.summary["completed"] == 2
        assert db.get(TeacherJudgeRunBatch, batch.id).finished_at is not None


def test_completed_target_is_durable_while_another_runs_and_survives_reaping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    db_engine = create_engine(f"sqlite:///{tmp_path / 'records.sqlite'}")
    SQLModel.metadata.create_all(db_engine)
    with Session(db_engine) as db:
        artifact = _artifact(db)
        run = TeacherJudgeScriptRun(
            artifact_id=artifact.id,
            target_snapshot_json={"targets": [{"vmid": 101}, {"vmid": 102}]},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    release = Event()
    monkeypatch.setattr(executor, "engine", db_engine)
    monkeypatch.setattr(executor, "PROGRESS_UPDATE_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(executor, "_live_running_by_vmid", lambda: {})
    monkeypatch.setattr(
        executor, "_resolve_runtime_target", lambda **kwargs: dict(kwargs["target"])
    )

    def remote(
        *, target: dict[str, Any], script_content: str
    ) -> executor.RemoteScriptResult:
        if target["vmid"] == 102:
            assert release.wait(5)
        return executor.RemoteScriptResult(
            0,
            json.dumps(
                {
                    "schema_version": "teacher_judge_result.v1",
                    "metadata": {
                        "timestamp": "2026-10-14T00:00:00Z",
                        "platform": "linux",
                    },
                    "checks": [],
                    "errors": [],
                }
            ),
            "",
        )

    monkeypatch.setattr(executor, "_execute_target_script", remote)
    with ThreadPoolExecutor(max_workers=1) as worker:
        task = worker.submit(executor._execute_targets, run_id)
        try:
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                with Session(db_engine) as db:
                    stored = db.get(TeacherJudgeScriptRun, run_id)
                    if stored.results_document.get("targets"):
                        break
                time.sleep(0.01)
            else:
                pytest.fail(
                    "Completed target was not persisted while another target was running"
                )
            assert stored.status == TeacherJudgeScriptRunStatus.running
            assert stored.results_document["targets"][0]["vmid"] == 101
            assert stored.target_records[0].finished_at is not None
            executor._mark_run_executor_failed(
                run_id, "Executor lost", reason_code="executor_lost"
            )
        finally:
            release.set()
        collected = task.result(timeout=5)
        assert collected is not None
        executor._save_results(run_id, collected.results)
    with Session(db_engine) as db:
        stored = db.get(TeacherJudgeScriptRun, run_id)
        results = {
            target["vmid"]: target for target in stored.results_document["targets"]
        }
        assert stored.status == TeacherJudgeScriptRunStatus.failed
        assert results[101]["status"] == "completed"
        assert results[102]["reason_code"] == "executor_lost"
        assert stored.result_summary_json["completed"] == 1
        assert stored.result_summary_json["failed"] == 1


async def test_batch_cancellation_closes_later_pending_runs_without_overwriting_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with make_session() as db:
        artifact = _artifact(db)
        batch = TeacherJudgeRunBatch(teaching_class_id=artifact.teaching_class_id)
        db.add(batch)
        db.flush()
        runs = [
            TeacherJudgeScriptRun(
                artifact_id=artifact.id,
                run_batch_id=batch.id,
                target_snapshot_json={"targets": [{"vmid": 101 + index}]},
            )
            for index in range(3)
        ]
        db.add_all(runs)
        for run in runs:
            initialize_targets(db, run)
        runs[0].status = TeacherJudgeScriptRunStatus.completed
        runs[0].finished_at = datetime.now(timezone.utc)
        store_target_result(runs[0].target_records[0], _result(101))
        db.commit()
        run_ids = [run.id for run in runs]
        monkeypatch.setattr(executor, "engine", db.get_bind())
        monkeypatch.setattr(executor, "_batch_run_ids", lambda _: run_ids)

        async def execute(run_id: uuid.UUID) -> None:
            if run_id != run_ids[0]:
                raise asyncio.CancelledError

        monkeypatch.setattr(executor, "_execute_script_run", execute)
        with pytest.raises(asyncio.CancelledError):
            await executor.execute_script_run_batch(batch.id)
        db.expire_all()
        assert (
            db.get(TeacherJudgeScriptRun, run_ids[0]).status
            == TeacherJudgeScriptRunStatus.completed
        )
        for run_id in run_ids[1:]:
            run = db.get(TeacherJudgeScriptRun, run_id)
            assert run.status == TeacherJudgeScriptRunStatus.failed
            assert run.target_records[0].reason_code == "executor_cancelled"
            assert run.finished_at is not None
        assert (
            db.get(TeacherJudgeRunBatch, batch.id).status == "completed_with_failures"
        )


def test_canonical_teacher_review_and_student_lookup_share_one_result_source() -> None:
    from tests.ai.teacher_judge.test_target_reviews import _completed_run

    db, _class_id, _judge_session, run = _completed_run()
    student_id = uuid.uuid4()
    document = run.target_results_json
    document["targets"][0]["user"] = {"id": str(student_id)}
    run.target_results_json = document
    initialize_targets(db, run)
    db.commit()
    result = service.update_target_review(
        session=db,
        run=run,
        vmid=483,
        feedback="已確認",
        decisions={"log": "pass"},
        reviewer_id=uuid.uuid4(),
    )
    assert run.target_results_json == {}
    assert result.target_results_json["targets"][0]["teacher_review"]["decisions"] == {
        "log": "pass"
    }
    assert (
        _target_for_student(run, student_id)["teacher_review"]["feedback"] == "已確認"
    )
    assert _target_for_student(run, uuid.uuid4()) == {}
    service.update_target_review(
        session=db,
        run=run,
        vmid=483,
        feedback="",
        decisions={},
        reviewer_id=uuid.uuid4(),
    )
    assert "teacher_review" not in run.results_document["targets"][0]
    db.close()


def test_target_query_checks_class_session_scope_and_instructor_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with make_session() as db:
        class_id = uuid.uuid4()
        judge_session = TeacherJudgeSession(teaching_class_id=class_id, title="records")
        db.add(judge_session)
        db.flush()
        artifact = _artifact(
            db, teaching_class_id=class_id, session_id=judge_session.id
        )
        run = TeacherJudgeScriptRun(
            artifact_id=artifact.id, target_snapshot_json={"targets": [{"vmid": 101}]}
        )
        db.add(run)
        initialize_targets(db, run)
        db.commit()
        monkeypatch.setattr(routes, "_access", lambda *args: None)
        kwargs = dict(
            session=db,
            current_user=SimpleNamespace(id=uuid.uuid4()),
            skip=0,
            limit=10,
            status=None,
            student_id=None,
            vmid=None,
        )
        result = routes.list_session_run_targets(
            class_id, judge_session.id, run.id, **kwargs
        )
        assert result.total == 1
        with pytest.raises(HTTPException) as error:
            routes.list_session_run_targets(
                uuid.uuid4(), judge_session.id, run.id, **kwargs
            )
        assert error.value.status_code == 404
        with pytest.raises(HTTPException) as error:
            routes.list_session_run_targets(class_id, uuid.uuid4(), run.id, **kwargs)
        assert error.value.status_code == 404

        def deny(*args: Any) -> None:
            raise HTTPException(status_code=403)

        monkeypatch.setattr(routes, "_access", deny)
        with pytest.raises(HTTPException) as error:
            routes.list_session_run_targets(
                class_id, judge_session.id, run.id, **kwargs
            )
        assert error.value.status_code == 403


def test_failed_preparation_keeps_batch_and_rolls_back_incomplete_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with make_session() as db:
        class_id, artifact_set_id = uuid.uuid4(), uuid.uuid4()
        judge_session = TeacherJudgeSession(teaching_class_id=class_id, title="prepare")
        db.add(judge_session)
        db.flush()
        for node in ("web", "db"):
            _artifact(
                db,
                teaching_class_id=class_id,
                session_id=judge_session.id,
                target_node_key=node,
                artifact_set_id=artifact_set_id,
            )
        db.commit()
        session_id = judge_session.id
        calls = []

        def prepare(**kwargs: Any) -> None:
            with Session(db.get_bind()) as observer:
                assert (
                    observer.get(TeacherJudgeRunBatch, kwargs["run_batch_id"]).status
                    == "pending"
                )
            calls.append(kwargs["artifact_id"])
            if len(calls) == 2:
                raise RuntimeError("target resolution unavailable")
            db.add(
                TeacherJudgeScriptRun(
                    artifact_id=kwargs["artifact_id"],
                    run_batch_id=kwargs["run_batch_id"],
                )
            )
            db.flush()

        monkeypatch.setattr(service, "create_script_run", prepare)
        with pytest.raises(RuntimeError, match="target resolution unavailable"):
            service.create_script_run_batch(
                session=db,
                teaching_class_id=class_id,
                artifact_set_id=artifact_set_id,
                started_by=None,
                session_id=session_id,
            )
        batches = list(db.exec(select(TeacherJudgeRunBatch)).all())
        assert len(batches) == 1 and batches[0].status == "failed"
        assert list(db.exec(select(TeacherJudgeScriptRun)).all()) == []
        public = service.get_script_run_batch_public(
            session=db,
            teaching_class_id=class_id,
            run_batch_id=batches[0].id,
            session_id=session_id,
        )
        assert public.status == "failed" and public.summary["targets"] == 0
        assert public.timing["total_seconds"] is not None
        summaries = service.list_session_batch_summaries(
            db, class_id, session_id, 0, 10
        )
        assert len(summaries) == 1 and summaries[0].status == "failed"
        with pytest.raises(HTTPException) as error:
            service.get_script_run_batch_public(
                session=db, teaching_class_id=uuid.uuid4(), run_batch_id=batches[0].id
            )
        assert error.value.status_code == 404


def test_orphan_accepted_batch_is_reaped_after_preparation_process_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with make_session() as db:
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=executor.STALE_RUN_HOURS + 1)
        batch = TeacherJudgeRunBatch(
            teaching_class_id=uuid.uuid4(),
            requested_at=old,
            created_at=old,
            updated_at=old,
        )
        db.add(batch)
        db.commit()
        monkeypatch.setattr(executor, "engine", db.get_bind())
        assert executor.reap_stale_script_runs(db, now=now) == 1
        assert batch.status == "failed" and batch.finished_at is not None
        assert utc_datetime(batch.finished_at) == now
        assert executor.reap_stale_script_runs(db, now=now) == 0


def test_dispatch_rejection_returns_failed_batch_instead_of_permanent_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with make_session() as db:
        class_id = uuid.uuid4()
        judge_session = TeacherJudgeSession(
            teaching_class_id=class_id, title="dispatch"
        )
        db.add(judge_session)
        db.flush()
        artifact = _artifact(
            db, teaching_class_id=class_id, session_id=judge_session.id
        )
        batch = TeacherJudgeRunBatch(
            teaching_class_id=class_id, session_id=judge_session.id
        )
        db.add(batch)
        db.flush()
        run = TeacherJudgeScriptRun(
            artifact_id=artifact.id,
            run_batch_id=batch.id,
            target_snapshot_json={"targets": [{"vmid": 101}]},
        )
        db.add(run)
        initialize_targets(db, run)
        db.commit()
        public = service.get_script_run_batch_public(
            session=db,
            teaching_class_id=class_id,
            run_batch_id=batch.id,
            session_id=judge_session.id,
        )
        monkeypatch.setattr(routes, "_access", lambda *args: None)
        monkeypatch.setattr(routes, "get_artifact_set", lambda **kwargs: None)
        monkeypatch.setattr(routes, "create_script_run_batch", lambda **kwargs: public)
        monkeypatch.setattr(executor, "engine", db.get_bind())

        def reject(coroutine: Any, **kwargs: Any) -> str:
            coroutine.close()
            return ""

        monkeypatch.setattr(routes, "submit", reject)
        response = routes.create_session_script_set_run(
            class_id,
            judge_session.id,
            uuid.uuid4(),
            TeacherJudgeScriptSetRunRequest(),
            db,
            SimpleNamespace(id=uuid.uuid4()),
        )
        assert response.status == "failed" and response.summary["failed"] == 1
        assert (
            db.get(TeacherJudgeScriptRun, run.id).target_records[0].reason_code
            == "executor_unavailable"
        )


def test_two_stale_teacher_sessions_keep_both_canonical_reviews() -> None:
    from tests.ai.teacher_judge.test_target_reviews import _completed_run

    db, _class_id, _judge_session, run = _completed_run()
    document = dict(run.target_results_json)
    first = document["targets"][0]
    document["targets"] = [first, {**first, "vmid": 484}]
    run.target_results_json = document
    initialize_targets(db, run)
    db.commit()
    with Session(db.get_bind()) as other:
        stale = other.get(TeacherJudgeScriptRun, run.id)
        assert len(stale.results_document["targets"]) == 2
        service.update_target_review(
            session=db,
            run=run,
            vmid=483,
            feedback="A",
            decisions={},
            reviewer_id=uuid.uuid4(),
        )
        service.update_target_review(
            session=other,
            run=stale,
            vmid=484,
            feedback="B",
            decisions={},
            reviewer_id=uuid.uuid4(),
        )
    db.expire_all()
    targets = {target["vmid"]: target for target in run.results_document["targets"]}
    assert targets[483]["teacher_review"]["feedback"] == "A"
    assert targets[484]["teacher_review"]["feedback"] == "B"
    db.close()


def test_cancellation_after_last_future_finishes_still_persists_completed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with make_session() as db:
        artifact = _artifact(db)
        run = TeacherJudgeScriptRun(
            artifact_id=artifact.id, target_snapshot_json={"targets": [{"vmid": 101}]}
        )
        db.add(run)
        db.commit()
        stop = Event()
        monkeypatch.setattr(executor, "engine", db.get_bind())
        monkeypatch.setattr(executor, "_live_running_by_vmid", lambda: {})
        monkeypatch.setattr(
            executor, "_resolve_runtime_target", lambda **kwargs: dict(kwargs["target"])
        )

        def remote(**kwargs: Any) -> executor.RemoteScriptResult:
            stop.set()
            return executor.RemoteScriptResult(
                0,
                json.dumps(
                    {
                        "schema_version": "teacher_judge_result.v1",
                        "metadata": {
                            "timestamp": "2026-10-14T00:00:00Z",
                            "platform": "linux",
                        },
                        "checks": [],
                        "errors": [],
                    }
                ),
                "",
            )

        monkeypatch.setattr(executor, "_execute_target_script", remote)
        assert executor._execute_targets(run.id, stop_event=stop) is None
        executor._mark_run_executor_failed(
            run.id, "cancelled", reason_code="executor_cancelled"
        )
        db.expire_all()
        assert run.status == TeacherJudgeScriptRunStatus.failed
        assert run.target_records[0].status == "completed"
        assert run.target_records[0].finished_at is not None
        assert run.result_summary_json["completed"] == 1


def test_reaping_more_than_twenty_children_does_not_extend_remaining_stale_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with make_session() as db:
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=executor.STALE_RUN_HOURS + 1)
        artifact = _artifact(db)
        batch = TeacherJudgeRunBatch(
            teaching_class_id=artifact.teaching_class_id,
            requested_at=old,
            prepared_at=old,
            created_at=old,
            updated_at=old,
        )
        db.add(batch)
        db.flush()
        db.add_all(
            [
                TeacherJudgeScriptRun(
                    artifact_id=artifact.id,
                    run_batch_id=batch.id,
                    target_snapshot_json={"targets": [{"vmid": 101 + index}]},
                    created_at=old,
                    updated_at=old,
                )
                for index in range(21)
            ]
        )
        db.commit()
        monkeypatch.setattr(executor, "engine", db.get_bind())
        assert executor.reap_stale_script_runs(db, now=now) == 20
        db.expire_all()
        assert executor.reap_stale_script_runs(db, now=now) == 1
        db.expire_all()
        assert batch.status == "failed"


def test_progress_heartbeat_preserves_large_results_without_reading_their_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with make_session() as db:
        artifact = _artifact(db)
        targets = [{"vmid": 101}, {"vmid": 102}]
        run = TeacherJudgeScriptRun(
            artifact_id=artifact.id,
            status=TeacherJudgeScriptRunStatus.running,
            target_snapshot_json={"targets": targets},
        )
        db.add(run)
        initialize_targets(db, run)
        store_target_result(
            run.target_records[0], _result(101, raw_result_json="x" * 200000)
        )
        db.commit()
        statements = []

        def observe(
            connection: Any,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: Any,
        ) -> None:
            statements.append(statement)

        monkeypatch.setattr(executor, "engine", db.get_bind())
        event.listen(db.get_bind(), "before_cursor_execute", observe)
        try:
            executor._save_run_progress(
                run_id=run.id,
                stage="executing",
                targets=targets,
                statuses={101: "completed", 102: "running"},
                done=1,
            )
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", observe)
        assert not any(
            statement.lstrip().startswith("SELECT")
            and "teacher_judge_run_targets.result_json" in statement
            for statement in statements
        )
        db.expire_all()
        assert len(run.results_document["targets"][0]["raw_result_json"]) == 200000
