from __future__ import annotations

import uuid
from datetime import date, time

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.course import CoursePath, CoursePathStatus
from app.models.teacher_judge_file import TeacherJudgeFile
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunStatus,
    TeacherJudgeScriptRunTargetScope,
)
from app.models.teaching_class import TeachingClass, TeachingClassStudent
from app.services.course import ai_assignment_service
from app.services.course.ai_assignment_service import list_student_ai_assignments


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _artifact(
    *,
    teaching_class_id: uuid.UUID,
    status: TeacherJudgeScriptStatus,
    name: str,
) -> TeacherJudgeScriptArtifact:
    return TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        name=name,
        template_key="linux",
        rubric_snapshot_json={
            "summary": "完成 Linux 權限設定。",
            "items": [
                {
                    "id": "permissions",
                    "title": "設定檔案權限",
                    "description": "讓指定使用者可以讀寫檔案。",
                    "detectable": "auto",
                    "detection_method": "secret command",
                    "check_steps": [{"command_key": "do-not-leak"}],
                }
            ],
        },
        script_content="print('{}')",
        status=status,
    )


def test_student_sees_only_approved_assignments_from_linked_class() -> None:
    session = _session()
    teacher_id = uuid.uuid4()
    other_teacher_id = uuid.uuid4()
    student_id = uuid.uuid4()
    own_class = TeachingClass(
        name="Linux A 班",
        code="linux-a",
        term="2026-1",
        owner_id=teacher_id,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        weekday=1,
        start_time=time(9),
        end_time=time(11),
    )
    other_class = TeachingClass(
        name="其他老師班級",
        code="other-a",
        term="2026-1",
        owner_id=other_teacher_id,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        weekday=2,
        start_time=time(9),
        end_time=time(11),
    )
    same_teacher_other_class = TeachingClass(
        name="同老師另一班",
        code="linux-b",
        term="2026-1",
        owner_id=teacher_id,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        weekday=3,
        start_time=time(9),
        end_time=time(11),
    )
    path = CoursePath(
        title="Linux",
        status=CoursePathStatus.published,
        created_by=teacher_id,
        teaching_class_id=own_class.id,
    )
    session.add(path)
    session.add(own_class)
    session.add(other_class)
    session.add(same_teacher_other_class)
    session.commit()

    session.add(TeachingClassStudent(class_id=own_class.id, user_id=student_id))
    session.add(TeachingClassStudent(class_id=other_class.id, user_id=student_id))
    session.add(
        TeachingClassStudent(class_id=same_teacher_other_class.id, user_id=student_id)
    )
    session.add(
        _artifact(
            teaching_class_id=own_class.id,
            status=TeacherJudgeScriptStatus.approved,
            name="Linux 權限任務",
        )
    )
    session.add(
        _artifact(
            teaching_class_id=same_teacher_other_class.id,
            status=TeacherJudgeScriptStatus.approved,
            name="同老師但不同班的任務",
        )
    )
    session.add(
        _artifact(
            teaching_class_id=own_class.id,
            status=TeacherJudgeScriptStatus.reviewed,
            name="尚未核准",
        )
    )
    session.add(
        _artifact(
            teaching_class_id=other_class.id,
            status=TeacherJudgeScriptStatus.approved,
            name="其他老師任務",
        )
    )
    session.commit()

    assignments = list_student_ai_assignments(
        session,
        user_id=student_id,
        path_id=path.id,
    )

    assert len(assignments) == 1
    assignment = assignments[0]
    assert assignment.title == "Linux 權限任務"
    assert assignment.teaching_class_name == "Linux A 班"
    assert assignment.items[0].title == "設定檔案權限"
    assert assignment.items[0].detectable == "auto"
    assert not hasattr(assignment.items[0], "detection_method")
    assert not hasattr(assignment.items[0], "check_steps")


def test_student_can_toggle_each_task_item_without_starting_ai_check() -> None:
    session = _session()
    teacher_id = uuid.uuid4()
    student_id = uuid.uuid4()
    teaching_class = TeachingClass(
        name="Linux Toggle",
        code="linux-toggle",
        term="2026-1",
        owner_id=teacher_id,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        weekday=1,
        start_time=time(9),
        end_time=time(11),
    )
    path = CoursePath(
        title="Linux",
        status=CoursePathStatus.published,
        created_by=teacher_id,
        teaching_class_id=teaching_class.id,
    )
    artifact = _artifact(
        teaching_class_id=teaching_class.id,
        status=TeacherJudgeScriptStatus.approved,
        name="Linux completion toggle",
    )
    artifact.rubric_snapshot_json["items"].append(
        {
            "id": "resources",
            "title": "Check system resources",
            "description": "Inspect RAM and CPU information.",
            "detectable": "auto",
        }
    )
    session.add(teaching_class)
    session.add(path)
    session.add(artifact)
    session.add(TeachingClassStudent(class_id=teaching_class.id, user_id=student_id))
    session.commit()

    first_item = ai_assignment_service.update_student_completion(
        session,
        user_id=student_id,
        path_id=path.id,
        assignment_id=artifact.id,
        item_id="permissions",
        completed=True,
    )

    assert first_item.completed is False
    assert first_item.completed_item_ids == ["permissions"]
    assert first_item.ready_at is None
    assert list_student_ai_assignments(
        session,
        user_id=student_id,
        path_id=path.id,
    )[0].completion.completed_item_ids == ["permissions"]
    assert session.exec(select(TeacherJudgeScriptRun)).all() == []

    all_completed = ai_assignment_service.update_student_completion(
        session,
        user_id=student_id,
        path_id=path.id,
        assignment_id=artifact.id,
        item_id="resources",
        completed=True,
    )

    assert all_completed.completed is True
    assert all_completed.completed_item_ids == ["permissions", "resources"]
    assert all_completed.ready_at is not None

    first_item_unchecked = ai_assignment_service.update_student_completion(
        session,
        user_id=student_id,
        path_id=path.id,
        assignment_id=artifact.id,
        item_id="permissions",
        completed=False,
    )

    assert first_item_unchecked.completed is False
    assert first_item_unchecked.completed_item_ids == ["resources"]
    assert first_item_unchecked.ready_at is None
    assert list_student_ai_assignments(
        session,
        user_id=student_id,
        path_id=path.id,
    )[0].completion.completed is False
    assert session.exec(select(TeacherJudgeScriptRun)).all() == []


def test_student_can_toggle_the_whole_weekly_assignment() -> None:
    session = _session()
    student_id = uuid.uuid4()
    teacher_id = uuid.uuid4()
    teaching_class = TeachingClass(
        name="Linux Weekly",
        code="linux-weekly",
        term="2026-1",
        owner_id=teacher_id,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        weekday=1,
        start_time=time(9),
        end_time=time(11),
    )
    path = CoursePath(
        title="Linux",
        status=CoursePathStatus.published,
        created_by=teacher_id,
        teaching_class_id=teaching_class.id,
    )
    artifact = _artifact(
        teaching_class_id=teaching_class.id,
        status=TeacherJudgeScriptStatus.approved,
        name="Weekly Linux assignment",
    )
    artifact.rubric_snapshot_json["items"].append(
        {
            "id": "resources",
            "title": "Check system resources",
            "description": "Inspect RAM and CPU information.",
            "detectable": "auto",
        }
    )
    session.add_all(
        [
            teaching_class,
            path,
            artifact,
            TeachingClassStudent(class_id=teaching_class.id, user_id=student_id),
        ]
    )
    session.commit()

    completed = ai_assignment_service.update_student_completion(
        session,
        user_id=student_id,
        path_id=path.id,
        assignment_id=artifact.id,
        item_id=None,
        completed=True,
    )

    assert completed.completed is True
    assert completed.completed_item_ids == ["permissions", "resources"]
    assert completed.ready_at is not None

    reopened = ai_assignment_service.update_student_completion(
        session,
        user_id=student_id,
        path_id=path.id,
        assignment_id=artifact.id,
        item_id=None,
        completed=False,
    )

    assert reopened.completed is False
    assert reopened.completed_item_ids == []
    assert reopened.ready_at is None

def test_student_can_view_one_uploaded_pdf_for_multiple_checkpoints(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    teacher_id = uuid.uuid4()
    student_id = uuid.uuid4()
    teaching_class = TeachingClass(
        name="Linux A 班",
        code="linux-pdf",
        term="2026-1",
        owner_id=teacher_id,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        weekday=1,
        start_time=time(9),
        end_time=time(11),
    )
    path = CoursePath(
        title="Linux",
        status=CoursePathStatus.published,
        created_by=teacher_id,
        teaching_class_id=teaching_class.id,
    )
    source_file = TeacherJudgeFile(
        teaching_class_id=teaching_class.id,
        uploaded_by=teacher_id,
        original_filename="linux-homework.pdf",
        file_hash="a" * 64,
        template_key="linux",
        source_type="uploaded",
        display_name="Linux 作業說明",
    )
    artifact = _artifact(
        teaching_class_id=teaching_class.id,
        status=TeacherJudgeScriptStatus.approved,
        name="Linux 權限任務",
    )
    artifact.source_file_id = source_file.id
    artifact.rubric_snapshot_json["items"].append(
        {
            "id": "owner",
            "title": "確認檔案擁有者",
            "description": "依照 PDF 完成第二個檢查點。",
            "detectable": "auto",
        }
    )
    session.add(teaching_class)
    session.add(path)
    session.add(source_file)
    session.add(artifact)
    session.add(TeachingClassStudent(class_id=teaching_class.id, user_id=student_id))
    session.commit()

    monkeypatch.setattr(ai_assignment_service.file_service, "DATA_ROOT", tmp_path)
    expected_path = tmp_path / f"{source_file.id}.pdf"
    expected_path.write_bytes(b"%PDF-1.4 test")

    assignments = list_student_ai_assignments(
        session,
        user_id=student_id,
        path_id=path.id,
    )

    assert len(assignments) == 1
    assert len(assignments[0].items) == 2
    assert assignments[0].source_document is not None
    assert assignments[0].source_document.filename == "linux-homework.pdf"
    assert assignments[0].source_document.display_name == "Linux 作業說明"

    document_path, filename = (
        ai_assignment_service.get_student_ai_assignment_source_document(
            session,
            user_id=student_id,
            path_id=path.id,
            assignment_id=artifact.id,
        )
    )
    assert document_path == expected_path
    assert filename == "linux-homework.pdf"

    with pytest.raises(HTTPException) as exc_info:
        ai_assignment_service.get_student_ai_assignment_source_document(
            session,
            user_id=uuid.uuid4(),
            path_id=path.id,
            assignment_id=artifact.id,
        )
    assert exc_info.value.status_code == 404


def test_student_assignment_includes_only_safe_latest_ai_feedback() -> None:
    session = _session()
    teacher_id = uuid.uuid4()
    student_id = uuid.uuid4()
    teaching_class = TeachingClass(
        name="Linux A 班",
        code="linux-a",
        term="2026-1",
        owner_id=teacher_id,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        weekday=1,
        start_time=time(9),
        end_time=time(11),
    )
    path = CoursePath(
        title="Linux",
        status=CoursePathStatus.published,
        created_by=teacher_id,
        teaching_class_id=teaching_class.id,
    )
    session.add(path)
    session.add(teaching_class)
    session.commit()
    session.add(TeachingClassStudent(class_id=teaching_class.id, user_id=student_id))
    artifact = _artifact(
        teaching_class_id=teaching_class.id,
        status=TeacherJudgeScriptStatus.approved,
        name="Linux 權限任務",
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    session.add(
        TeacherJudgeScriptRun(
            teaching_class_id=teaching_class.id,
            artifact_id=artifact.id,
            target_scope=TeacherJudgeScriptRunTargetScope.manual,
            status=TeacherJudgeScriptRunStatus.completed,
            started_by=student_id,
            target_snapshot_json={
                "script": {"secret": "must-not-leak"},
                "requested_item_id": "permissions",
            },
            target_results_json={
                "targets": [
                    {
                        "stdout": "internal output must not leak",
                        "ai_judgement": {
                            "status": "completed",
                            "score": 4,
                            "max_score": 5,
                            "summary": "權限設定正確，說明可以再完整一點。",
                            "item_judgements": [
                                {
                                    "item_id": "permissions",
                                    "title": "設定檔案權限",
                                    "status": "passed",
                                    "score": 1,
                                    "max_score": 1,
                                    "comment": "chmod 結果符合要求。",
                                    "evidence_refs": ["secret-command-output"],
                                }
                            ],
                        },
                    }
                ]
            },
        )
    )
    session.commit()

    assignments = list_student_ai_assignments(
        session,
        user_id=student_id,
        path_id=path.id,
    )

    check = assignments[0].latest_check
    assert check is not None
    assert check.status == "completed"
    assert check.score == 4
    assert check.summary == "權限設定正確，說明可以再完整一點。"
    assert check.items[0].comment == "chmod 結果符合要求。"
    checkpoint_check = assignments[0].checkpoint_checks["permissions"]
    assert checkpoint_check.score == 1
    assert checkpoint_check.max_score == 1
    assert [item.item_id for item in checkpoint_check.items] == ["permissions"]
    assert not hasattr(check, "target_snapshot_json")
    assert not hasattr(check.items[0], "evidence_refs")
