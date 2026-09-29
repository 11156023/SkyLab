"""Teacher review of a target whose parsed_result carries no usable checks list."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.ai.teacher_judge.schemas import TeacherJudgeTargetReviewUpdate
from app.api.routes import teacher_judge_sessions
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptArtifact
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunStatus,
)
from app.models.teacher_judge_session import TeacherJudgeSession
from tests.ai.teacher_judge.helpers import make_session


def _run_with_parsed_result(parsed_result: Any):
    db = make_session()
    class_id = uuid.uuid4()
    judge_session = TeacherJudgeSession(teaching_class_id=class_id, title="第 4 週任務")
    db.add(judge_session)
    db.commit()
    db.refresh(judge_session)
    artifact = TeacherJudgeScriptArtifact(
        teaching_class_id=class_id,
        session_id=judge_session.id,
        name="檢查",
        template_key="n8n",
        script_content="print('{}')",
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    run = TeacherJudgeScriptRun(
        teaching_class_id=class_id,
        artifact_id=artifact.id,
        status=TeacherJudgeScriptRunStatus.completed,
        target_results_json={
            "schema_version": "teacher_judge_run_results.v2",
            "targets": [
                {
                    "vmid": 501,
                    "status": "completed",
                    "validation": {"valid": True},
                    "parsed_result": parsed_result,
                }
            ],
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return db, class_id, judge_session, run


def _review(monkeypatch, parsed_result: Any, decisions: dict[str, str]):
    db, class_id, judge_session, run = _run_with_parsed_result(parsed_result)
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)
    return teacher_judge_sessions.update_target_review(
        class_id,
        judge_session.id,
        run.id,
        501,
        TeacherJudgeTargetReviewUpdate(feedback="請補充說明。", decisions=decisions),
        db,
        SimpleNamespace(id=uuid.uuid4()),
    )


@pytest.mark.parametrize(
    "parsed_result",
    [{}, {"checks": None}, {"checks": "oops"}, {"checks": {"id": "x"}}],
)
def test_feedback_without_decisions_is_saved_when_checks_missing(
    monkeypatch, parsed_result: Any
) -> None:
    result = _review(monkeypatch, parsed_result, {})

    review = result.target_results_json["targets"][0]["teacher_review"]
    assert review["feedback"] == "請補充說明。"
    assert review["decisions"] == {}


@pytest.mark.parametrize("parsed_result", [{}, {"checks": None}, {"checks": "oops"}])
def test_decision_is_rejected_when_checks_missing(
    monkeypatch, parsed_result: Any
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _review(monkeypatch, parsed_result, {"x": "pass"})

    assert exc_info.value.status_code == 400
