"""Teacher review persistence for managed-script target results."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

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


def _completed_run():
    db = make_session()
    class_id = uuid.uuid4()
    judge_session = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="第 3 週任務",
    )
    db.add(judge_session)
    db.commit()
    db.refresh(judge_session)
    artifact = TeacherJudgeScriptArtifact(
        teaching_class_id=class_id,
        session_id=judge_session.id,
        name="n8n 檢查",
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
                    "vmid": 483,
                    "status": "completed",
                    "validation": {"valid": True},
                    "parsed_result": {
                        "checks": [
                            {"id": "service", "status": "pass"},
                            {"id": "log", "status": "unknown"},
                        ]
                    },
                }
            ],
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return db, class_id, judge_session, run


def test_teacher_can_save_optional_feedback_and_manual_decision(monkeypatch) -> None:
    db, class_id, judge_session, run = _completed_run()
    teacher_id = uuid.uuid4()
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)

    result = teacher_judge_sessions.update_target_review(
        class_id,
        judge_session.id,
        run.id,
        483,
        TeacherJudgeTargetReviewUpdate(
            feedback="  請補充錯誤處理。  ",
            decisions={"log": "fail"},
        ),
        db,
        SimpleNamespace(id=teacher_id),
    )

    review = result.target_results_json["targets"][0]["teacher_review"]
    assert review["feedback"] == "請補充錯誤處理。"
    assert review["decisions"] == {"log": "fail"}
    assert review["reviewed_by"] == str(teacher_id)
    assert review["updated_at"]


def test_teacher_cannot_override_an_objective_ai_result(monkeypatch) -> None:
    db, class_id, judge_session, run = _completed_run()
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)

    with pytest.raises(HTTPException) as exc_info:
        teacher_judge_sessions.update_target_review(
            class_id,
            judge_session.id,
            run.id,
            483,
            TeacherJudgeTargetReviewUpdate(decisions={"service": "fail"}),
            db,
            SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc_info.value.status_code == 400
    assert "service" in str(exc_info.value.detail)
