"""批次子 run 排隊等待時不會被回收；已被回收的 run 不再執行、也不會被改回 completed。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlmodel import Session

from app.ai.teacher_judge import script_executor_service as executor
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunStatus,
)
from tests.ai.teacher_judge.helpers import make_session

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
LONG_AGO = NOW - timedelta(hours=executor.STALE_RUN_HOURS + 1)
RECENT = NOW - timedelta(minutes=10)


@pytest.fixture
def db_session(monkeypatch: pytest.MonkeyPatch) -> Session:
    session = make_session()
    monkeypatch.setattr(executor, "engine", session.get_bind())
    return session


def _artifact(session: Session) -> TeacherJudgeScriptArtifact:
    artifact = TeacherJudgeScriptArtifact(
        teaching_class_id=uuid.uuid4(),
        name="test",
        template_key="linux",
        rubric_snapshot_json={},
        script_content="print('x')",
        status=TeacherJudgeScriptStatus.approved,
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return artifact


def _run(
    session: Session,
    artifact: TeacherJudgeScriptArtifact,
    *,
    status: TeacherJudgeScriptRunStatus,
    updated_at: datetime,
    batch_id: uuid.UUID | None = None,
) -> uuid.UUID:
    run = TeacherJudgeScriptRun(
        artifact_id=artifact.id,
        run_batch_id=batch_id,
        status=status,
        target_snapshot_json={"targets": [{"vmid": 101}]},
        created_at=updated_at,
        updated_at=updated_at,
    )
    session.add(run)
    session.commit()
    return run.id


def _status(session: Session, run_id: uuid.UUID) -> TeacherJudgeScriptRunStatus:
    session.expire_all()
    run = session.get(TeacherJudgeScriptRun, run_id)
    assert run is not None
    return run.status


def _valid_result() -> executor.RemoteScriptResult:
    return executor.RemoteScriptResult(
        0,
        json.dumps(
            {
                "schema_version": "teacher_judge_result.v1",
                "metadata": {"timestamp": "2026-10-12T00:00:00Z", "platform": "linux"},
                "summary": "done",
                "checks": [],
                "errors": [],
            }
        ),
        "",
    )


def _fake_execution(monkeypatch: pytest.MonkeyPatch, ssh: Any) -> None:
    monkeypatch.setattr(executor, "_live_running_by_vmid", lambda: {})
    monkeypatch.setattr(
        executor, "_resolve_runtime_target", lambda **kwargs: dict(kwargs["target"])
    )
    monkeypatch.setattr(executor, "_execute_target_script", ssh)


def test_queued_batch_children_survive_while_the_batch_progresses(db_session: Session) -> None:
    artifact = _artifact(db_session)
    batch_id = uuid.uuid4()
    finished = _run(
        db_session, artifact, status=TeacherJudgeScriptRunStatus.completed,
        updated_at=LONG_AGO, batch_id=batch_id,
    )
    active = _run(
        db_session, artifact, status=TeacherJudgeScriptRunStatus.running,
        updated_at=RECENT, batch_id=batch_id,
    )
    queued = _run(
        db_session, artifact, status=TeacherJudgeScriptRunStatus.pending,
        updated_at=LONG_AGO, batch_id=batch_id,
    )
    lone = _run(
        db_session, artifact, status=TeacherJudgeScriptRunStatus.pending, updated_at=LONG_AGO
    )

    assert executor.reap_stale_script_runs(db_session, now=NOW) == 1

    assert _status(db_session, queued) == TeacherJudgeScriptRunStatus.pending
    assert _status(db_session, active) == TeacherJudgeScriptRunStatus.running
    assert _status(db_session, finished) == TeacherJudgeScriptRunStatus.completed
    assert _status(db_session, lone) == TeacherJudgeScriptRunStatus.failed


def test_queued_batch_children_are_reaped_once_the_whole_batch_is_idle(
    db_session: Session,
) -> None:
    artifact = _artifact(db_session)
    batch_id = uuid.uuid4()
    stuck = _run(
        db_session, artifact, status=TeacherJudgeScriptRunStatus.running,
        updated_at=LONG_AGO, batch_id=batch_id,
    )
    queued = _run(
        db_session, artifact, status=TeacherJudgeScriptRunStatus.pending,
        updated_at=LONG_AGO, batch_id=batch_id,
    )

    assert executor.reap_stale_script_runs(db_session, now=NOW) == 2

    assert _status(db_session, stuck) == TeacherJudgeScriptRunStatus.failed
    assert _status(db_session, queued) == TeacherJudgeScriptRunStatus.failed


def test_reaper_leaves_a_run_that_progressed_after_it_was_selected(db_session: Session) -> None:
    artifact = _artifact(db_session)
    run_id = _run(
        db_session, artifact, status=TeacherJudgeScriptRunStatus.running, updated_at=RECENT
    )

    executor._mark_run_executor_failed(
        run_id,
        "reaped",
        stale_before=NOW - timedelta(hours=executor.STALE_RUN_HOURS),
    )

    assert _status(db_session, run_id) == TeacherJudgeScriptRunStatus.running


@pytest.mark.asyncio
async def test_reaped_batch_child_is_not_executed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _artifact(db_session)
    batch_id = uuid.uuid4()
    run_id = _run(
        db_session, artifact, status=TeacherJudgeScriptRunStatus.pending,
        updated_at=LONG_AGO, batch_id=batch_id,
    )
    assert executor.reap_stale_script_runs(db_session, now=NOW) == 1
    executed: list[int] = []

    def ssh(*, target: dict[str, Any], script_content: str) -> executor.RemoteScriptResult:
        executed.append(target["vmid"])
        return _valid_result()

    _fake_execution(monkeypatch, ssh)

    await executor.execute_script_run_batch(batch_id)

    assert executed == []
    db_session.expire_all()
    run = db_session.get(TeacherJudgeScriptRun, run_id)
    assert run is not None
    assert run.status == TeacherJudgeScriptRunStatus.failed
    assert "reaped" in run.result_summary_json["executor_error"]
    assert run.target_results_json == {}


@pytest.mark.asyncio
async def test_run_reaped_during_execution_is_not_overwritten_as_completed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _artifact(db_session)
    run_id = _run(
        db_session, artifact, status=TeacherJudgeScriptRunStatus.pending, updated_at=NOW
    )

    def ssh(*, target: dict[str, Any], script_content: str) -> executor.RemoteScriptResult:
        # 模擬回收在 SSH 期間把 run 收成 failed
        executor._mark_run_executor_failed(run_id, "reaped while running")
        return _valid_result()

    _fake_execution(monkeypatch, ssh)

    await executor.execute_script_run(run_id)

    db_session.expire_all()
    run = db_session.get(TeacherJudgeScriptRun, run_id)
    assert run is not None
    assert run.status == TeacherJudgeScriptRunStatus.failed
    assert run.result_summary_json["executor_error"] == "reaped while running"
    assert run.progress_json["stage"] == "failed"
    assert run.target_results_json == {}
