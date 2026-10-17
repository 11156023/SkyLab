"""導師核查：看全班當週回報完成到哪裡，並只檢查指定的學生。"""

from __future__ import annotations

import uuid
from datetime import date, time
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.ai.teacher_judge import script_run_service as svc
from app.ai.teacher_judge.run_record_service import update_batch
from app.ai.teacher_judge.schemas import TeacherJudgeScriptSetRunRequest
from app.models import TeachingClass, User, UserRole
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunStatus,
)
from app.models.teacher_judge_session import TeacherJudgeSession
from app.models.teacher_judge_student_submission import (
    TeacherJudgeStudentSubmission,
    TeacherJudgeSubmissionItem,
)
from app.models.teaching_class import TeachingClassStudent
from app.services.course import ai_assignment_service
from tests.ai.teacher_judge.helpers import make_script_workspace, make_session


def _user(email: str) -> User:
    return User(email=email, role=UserRole.student, hashed_password="x", full_name=email.split("@")[0])


def test_progress_lists_every_student_with_their_reported_items() -> None:
    db = make_session()
    teacher = User(email="teacher@example.edu", role=UserRole.teacher, hashed_password="x")
    done, partial, silent = _user("a@example.edu"), _user("b@example.edu"), _user("c@example.edu")
    teaching_class = TeachingClass(
        name="網站部署", code="web", term="2026-1", owner_id=teacher.id,
        start_date=date(2026, 9, 1), end_date=date(2026, 12, 31), weekday=1,
        start_time=time(9), end_time=time(11),
    )
    db.add_all([teacher, done, partial, silent, teaching_class])
    db.flush()
    db.add_all(
        [TeachingClassStudent(class_id=teaching_class.id, user_id=user.id) for user in (done, partial, silent)]
        + [TeachingClassStudent(class_id=teaching_class.id, user_id=teacher.id, status="instructor")]
    )
    judge = make_script_workspace(db, teaching_class.id)
    judge.active_artifact_set_id = uuid.uuid4()
    db.add(judge)
    db.flush()
    db.add(
        TeacherJudgeScriptArtifact(
            teaching_class_id=teaching_class.id, session_id=judge.id, name="第 3 週",
            artifact_set_id=judge.active_artifact_set_id, target_node_key="web",
            template_key="linux", script_content="print('{}')",
            status=TeacherJudgeScriptStatus.approved,
            rubric_snapshot_json={"items": [
                {"id": "service", "title": "服務啟動"},
                {"id": "database", "title": "資料庫連線"},
            ]},
        )
    )
    full = TeacherJudgeStudentSubmission(session_id=judge.id, student_id=done.id)
    half = TeacherJudgeStudentSubmission(session_id=judge.id, student_id=partial.id)
    db.add_all([full, half])
    db.flush()
    full.ready_at = full.updated_at
    db.add_all([
        TeacherJudgeSubmissionItem(submission_id=full.id, item_id="service"),
        TeacherJudgeSubmissionItem(submission_id=full.id, item_id="database"),
        TeacherJudgeSubmissionItem(submission_id=half.id, item_id="database"),
    ])
    db.commit()

    progress = ai_assignment_service.session_progress(db, judge)

    assert [item["id"] for item in progress["items"]] == ["service", "database"]
    by_email = {row["email"]: row for row in progress["students"]}
    # 老師自己的選課紀錄不算學生
    assert set(by_email) == {"a@example.edu", "b@example.edu", "c@example.edu"}
    assert by_email["a@example.edu"]["completed_item_ids"] == ["service", "database"]
    assert by_email["a@example.edu"]["ready_at"] is not None
    assert by_email["b@example.edu"]["completed_item_ids"] == ["database"]
    assert by_email["b@example.edu"]["ready_at"] is None
    assert by_email["c@example.edu"]["completed_item_ids"] == []
    assert by_email["c@example.edu"]["updated_at"] is None


def test_set_run_request_requires_students_only_for_student_scope() -> None:
    student = uuid.uuid4()
    request = TeacherJudgeScriptSetRunRequest(target_scope="students", user_ids=[student, student])
    assert request.user_ids == [student]
    with pytest.raises(ValidationError):
        TeacherJudgeScriptSetRunRequest(target_scope="students")
    with pytest.raises(ValidationError):
        TeacherJudgeScriptSetRunRequest(user_ids=[student])
    assert TeacherJudgeScriptSetRunRequest().target_scope == "all_students_in_set"


def _two_machine_set(db, class_id: uuid.UUID) -> uuid.UUID:
    set_id = uuid.uuid4()
    workspace = make_script_workspace(db, class_id)
    workspace.active_artifact_set_id = set_id
    db.add(workspace)
    for node in ("web", "db"):
        db.add(
            TeacherJudgeScriptArtifact(
                teaching_class_id=class_id, artifact_set_id=set_id, target_node_key=node, session_id=workspace.id,
                name=node, template_key="linux", script_content="print('{}')",
                rubric_snapshot_json={"items": []}, status=TeacherJudgeScriptStatus.approved,
            )
        )
    db.commit()
    return set_id


def test_student_scoped_batch_only_runs_machines_those_students_have(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    set_id = _two_machine_set(db, class_id)
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    monkeypatch.setattr(svc, "load_class_machine_nodes", lambda *args: [])
    monkeypatch.setattr(
        svc,
        "_class_member_by_node_key",
        lambda **_: {"web": [{"user_id": alice}], "db": [{"user_id": bob}]},
    )
    batch = svc.create_script_run_batch(
        session=db, teaching_class_id=class_id, artifact_set_id=set_id,
        started_by=None, user_ids=[uuid.UUID(alice)],
    )

    assert [node.target_node_key for node in batch.nodes] == ["web"]
    run = db.get(TeacherJudgeScriptRun, uuid.UUID(batch.nodes[0].run_id))
    assert run.target_snapshot_json["preflight_results"][0]["user"]["id"] == alice
    run.status = TeacherJudgeScriptRunStatus.failed
    db.add(run)
    update_batch(db, uuid.UUID(batch.run_batch_id))
    db.commit()

    # 選到的學生在這組腳本的機器上都沒有 VM：不建立空的批次
    with pytest.raises(HTTPException) as exc:
        svc.create_script_run_batch(
            session=db, teaching_class_id=class_id, artifact_set_id=set_id,
            started_by=None, user_ids=[uuid.uuid4()],
        )
    assert exc.value.status_code == 400


def test_node_targets_keep_only_the_selected_students(monkeypatch: pytest.MonkeyPatch) -> None:
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    members = [
        {"student_id": "e1", "user_id": alice, "vmid": 101, "node_key": "web"},
        {"student_id": "e2", "user_id": bob, "vmid": 102, "node_key": "web"},
    ]
    monkeypatch.setattr(svc, "resolve_class_machine_node", lambda *args: type("Node", (), {"node_key": "web"})())
    monkeypatch.setattr(svc, "_class_member_by_node_key", lambda **_: {"web": members})
    monkeypatch.setattr(svc, "_running_resources_by_vmid", lambda: {})
    monkeypatch.setattr(
        svc,
        "_resolve_running_targets",
        lambda **kwargs: [{"vmid": vmid} for vmid in kwargs["target_vmids"]],
    )

    targets, failures = svc._resolve_node_targets(
        session=None, teaching_class_id=uuid.uuid4(), target_node_key="web", user_ids={bob},
    )

    assert [target["vmid"] for target in targets] == [102]
    assert failures == []
