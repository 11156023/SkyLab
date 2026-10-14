"""PostgreSQL upgrade/backfill/downgrade on an explicitly isolated test DB."""

from __future__ import annotations

import importlib
import json
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_postgresql_execution_records_backfill_and_reversible_results() -> None:
    destination = os.getenv("TEACHER_JUDGE_TEST_DATABASE_URL")
    if not destination:
        pytest.skip(
            "Set TEACHER_JUDGE_TEST_DATABASE_URL to an isolated PostgreSQL test DB"
        )
    url = sa.engine.make_url(destination)
    assert url.host in {"127.0.0.1", "localhost"} and (url.database or "").endswith(
        "_test"
    )
    migration = importlib.import_module(
        "app.alembic.versions.tjrec01_teacher_judge_execution_records"
    )
    engine = sa.create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                sa.text("CREATE SCHEMA teacher_judge_record_migration_test")
            )
            connection.execute(
                sa.text("SET LOCAL search_path TO teacher_judge_record_migration_test")
            )
            connection.execute(
                sa.text("""
                CREATE TABLE teaching_classes (id uuid PRIMARY KEY);
                CREATE TABLE teacher_judge_sessions (id uuid PRIMARY KEY, teaching_class_id uuid, UNIQUE(id, teaching_class_id));
                CREATE TABLE "user" (id uuid PRIMARY KEY);
                CREATE TABLE teacher_judge_script_artifacts (
                    id uuid PRIMARY KEY, teaching_class_id uuid, session_id uuid, artifact_set_id uuid
                );
                CREATE TABLE teacher_judge_script_runs (
                    id uuid PRIMARY KEY, artifact_id uuid, run_batch_id uuid, started_by uuid,
                    status text, result_summary_json json, target_snapshot_json json,
                    target_results_json json, started_at timestamptz, finished_at timestamptz,
                    created_at timestamptz, updated_at timestamptz
                )
            """)
            )
            class_id, artifact_id, batch_id = [uuid.uuid4() for _ in range(3)]
            connection.execute(
                sa.text("INSERT INTO teaching_classes VALUES (:id)"), {"id": class_id}
            )
            connection.execute(
                sa.text(
                    "INSERT INTO teacher_judge_script_artifacts VALUES (:id, :class_id, NULL, NULL)"
                ),
                {"id": artifact_id, "class_id": class_id},
            )
            completed_id, pending_id = uuid.uuid4(), uuid.uuid4()
            students = [str(uuid.uuid4()) for _ in range(3)]
            legacy = {
                "targets": [
                    {
                        "vmid": 101,
                        "student_id": students[0],
                        "status": "completed",
                        "reason_code": "success",
                        "validation": {"valid": True},
                        "parsed_result": {"checks": []},
                        "teacher_review": {"feedback": "original"},
                        "timing": {
                            "started_at": "2026-10-14T00:00:00Z",
                            "received_at": "bad timestamp",
                            "total_seconds": 7,
                        },
                    },
                    {
                        "vmid": None,
                        "student_id": students[1],
                        "status": "failed",
                        "reason_code": "missing_vmid",
                    },
                ]
            }
            for run_id, status, snapshot, result in (
                (
                    completed_id,
                    "completed",
                    {
                        "targets": [
                            {
                                "vmid": 101,
                                "student_id": students[0],
                                "private_key_pem": "NEVER_COPY",
                            }
                        ]
                    },
                    legacy,
                ),
                (
                    pending_id,
                    "pending",
                    {"targets": [{"vmid": 102, "student_id": students[2]}]},
                    {},
                ),
            ):
                connection.execute(
                    sa.text("""
                    INSERT INTO teacher_judge_script_runs
                    VALUES (:id, :artifact, :batch, NULL, :status, '{}', CAST(:snapshot AS json), CAST(:result AS json),
                            NULL, NULL, '2026-10-14T00:00:00Z', '2026-10-14T00:00:00Z')
                """),
                    {
                        "id": run_id,
                        "artifact": artifact_id,
                        "batch": batch_id,
                        "status": status,
                        "snapshot": json.dumps(snapshot),
                        "result": json.dumps(result),
                    },
                )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                targets = (
                    connection.execute(
                        sa.text(
                            "SELECT * FROM teacher_judge_run_targets ORDER BY vmid NULLS FIRST"
                        )
                    )
                    .mappings()
                    .all()
                )
                assert len(targets) == 3  # includes pending targets and missing VMID
                completed = next(row for row in targets if row["vmid"] == 101)
                assert completed["status"] == "completed"
                assert (
                    completed["finished_at"] is None
                    and completed["received_at"] is None
                )
                assert completed["started_at"] is not None
                assert completed["timing_json"]["total_seconds"] == 7
                assert "NEVER_COPY" not in json.dumps(completed["metadata_json"])
                pending = next(row for row in targets if row["vmid"] == 102)
                assert pending["status"] == "queued" and pending["finished_at"] is None
                assert (
                    connection.execute(
                        sa.text("SELECT requested_at FROM teacher_judge_run_batches")
                    ).scalar_one()
                    is None
                )
                result = dict(completed["result_json"])
                result["teacher_review"] = {"feedback": "updated after migration"}
                connection.execute(
                    sa.text(
                        "UPDATE teacher_judge_run_targets SET result_json = CAST(:result AS json) WHERE id = :id"
                    ),
                    {"result": json.dumps(result), "id": completed["id"]},
                )
                migration.downgrade()
                restored = connection.execute(
                    sa.text(
                        "SELECT target_results_json FROM teacher_judge_script_runs WHERE id = :id"
                    ),
                    {"id": completed_id},
                ).scalar_one()
                assert (
                    next(
                        target
                        for target in restored["targets"]
                        if target["vmid"] == 101
                    )["teacher_review"]["feedback"]
                    == "updated after migration"
                )
                # An inconsistent historical batch must fail rather than
                # silently choosing one class as its owner.
                savepoint = connection.begin_nested()
                try:
                    conflicting_artifact = uuid.uuid4()
                    connection.execute(
                        sa.text(
                            "INSERT INTO teacher_judge_script_artifacts VALUES (:id, :class_id, NULL, NULL)"
                        ),
                        {"id": conflicting_artifact, "class_id": uuid.uuid4()},
                    )
                    connection.execute(
                        sa.text("""
                        INSERT INTO teacher_judge_script_runs
                        VALUES (:id, :artifact, :batch, NULL, 'pending', '{}', '{}', '{}',
                                NULL, NULL, now(), now())
                    """),
                        {
                            "id": uuid.uuid4(),
                            "artifact": conflicting_artifact,
                            "batch": batch_id,
                        },
                    )
                    with pytest.raises(
                        sa.exc.DBAPIError, match="Conflicting Teacher Judge batch"
                    ):
                        migration.upgrade()
                finally:
                    savepoint.rollback()
                migration.upgrade()
                assert (
                    connection.execute(
                        sa.text("SELECT count(*) FROM teacher_judge_run_targets")
                    ).scalar_one()
                    == 3
                )
        finally:
            transaction.rollback()
    engine.dispose()


def test_postgresql_runtime_writes_canonical_targets_and_batch_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlmodel import Session

    from app.ai.teacher_judge import script_executor_service as executor
    from app.ai.teacher_judge import script_run_service as service
    from app.ai.teacher_judge.run_record_service import initialize_targets
    from app.models import TeachingClass, User
    from app.models.teacher_judge_run_batch import TeacherJudgeRunBatch
    from app.models.teacher_judge_script_artifact import (
        TeacherJudgeScriptArtifact,
        TeacherJudgeScriptStatus,
    )
    from app.models.teacher_judge_script_run import TeacherJudgeScriptRun
    from app.models.teacher_judge_session import TeacherJudgeSession

    destination = os.getenv("TEACHER_JUDGE_TEST_DATABASE_URL")
    if not destination:
        pytest.skip(
            "Set TEACHER_JUDGE_TEST_DATABASE_URL to a migrated isolated PostgreSQL test DB"
        )
    url = sa.engine.make_url(destination)
    assert url.host in {"127.0.0.1", "localhost"} and (url.database or "").endswith(
        "_test"
    )
    engine = sa.create_engine(url)
    with Session(engine) as db:
        owner = User(
            email=f"record-test-{uuid.uuid4().hex}@example.com",
            hashed_password="unused-test-hash",
        )
        db.add(owner)
        db.flush()
        teaching_class = TeachingClass(
            owner_id=owner.id,
            name="execution record test",
            code="records",
            term="test",
            start_date=date(2026, 10, 14),
            end_date=date(2026, 10, 21),
            weekday=2,
            start_time=time(9),
            end_time=time(10),
        )
        db.add(teaching_class)
        db.flush()
        judge_session = TeacherJudgeSession(
            teaching_class_id=teaching_class.id, title="record test"
        )
        db.add(judge_session)
        db.flush()
        artifact = TeacherJudgeScriptArtifact(
            teaching_class_id=teaching_class.id,
            session_id=judge_session.id,
            name="record test",
            template_key="linux",
            script_content="pass",
            status=TeacherJudgeScriptStatus.approved,
        )
        batch = TeacherJudgeRunBatch(
            teaching_class_id=teaching_class.id,
            session_id=judge_session.id,
            requested_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            prepared_at=datetime.now(timezone.utc),
        )
        db.add_all([artifact, batch])
        db.flush()
        run = TeacherJudgeScriptRun(
            artifact_id=artifact.id,
            run_batch_id=batch.id,
            prepared_at=datetime.now(timezone.utc),
            target_snapshot_json={
                "targets": [
                    {
                        "vmid": 101,
                        "student_id": str(uuid.uuid4()),
                        "user": {"id": str(owner.id)},
                    }
                ]
            },
        )
        db.add(run)
        initialize_targets(db, run)
        db.commit()
        run_id, class_id, batch_id, session_id = (
            run.id,
            teaching_class.id,
            batch.id,
            judge_session.id,
        )
        monkeypatch.setattr(executor, "engine", engine)
        monkeypatch.setattr(executor, "_live_running_by_vmid", lambda: {})
        monkeypatch.setattr(
            executor, "_resolve_runtime_target", lambda **kwargs: dict(kwargs["target"])
        )
        monkeypatch.setattr(
            executor,
            "_execute_target_script",
            lambda **kwargs: executor.RemoteScriptResult(
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
            ),
        )
        try:
            collected = executor._execute_targets(run_id)
            assert collected is not None
            executor._save_results(run_id, collected.results)
            db.expire_all()
            stored = db.get(TeacherJudgeScriptRun, run_id)
            assert (
                stored.target_results_json == {} and stored.status.value == "completed"
            )
            target = stored.target_records[0]
            assert (
                target.queued_at is not None
                and target.started_at is not None
                and target.finished_at is not None
            )
            assert target.finished_at >= target.started_at >= target.queued_at
            assert target.result_json["validation"]["valid"] is True
            assert target.timing_json["run_wait_seconds"] >= 0
            public = service.get_script_run_batch_public(
                session=db,
                teaching_class_id=class_id,
                run_batch_id=batch_id,
                session_id=session_id,
            )
            assert public.status == "completed" and public.summary["completed"] == 1
            assert public.timing["total_seconds"] >= 0
            assert (
                public.students[0]["nodes"][0]["timing"]["completion_wait_seconds"] >= 0
            )
            assert (
                service.list_session_batch_summaries(db, class_id, session_id, 0, 10)[
                    0
                ].summary
                == public.summary
            )
        finally:
            db.expire_all()
            db.delete(artifact)
            db.flush()
            db.delete(batch)
            db.flush()
            db.delete(judge_session)
            db.flush()
            db.delete(teaching_class)
            db.flush()
            db.delete(owner)
            db.commit()
    engine.dispose()
