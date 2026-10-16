from __future__ import annotations

import uuid
from datetime import date, time

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.i18n import t
from app.exceptions import NotFoundError
from app.models import User, UserRole
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
from app.models.teacher_judge_session import TeacherJudgeSession
from app.models.teacher_judge_student_submission import TeacherJudgeStudentSubmission
from app.models.teaching_class import (
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStudent,
    TeachingClassWeek,
)
from app.services.course import ai_assignment_service, weekly_task_service
from app.services.course.ai_assignment_service import list_student_ai_assignments


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _published_week(
    session: Session, teaching_class_id: uuid.UUID
) -> TeachingClassWeek:
    """作業跟著每週內容發布：測試裡的對話都綁在已發布的週次上。"""
    number = (
        len(
            session.exec(
                select(TeachingClassWeek).where(
                    TeachingClassWeek.class_id == teaching_class_id
                )
            ).all()
        )
        + 1
    )
    week = TeachingClassWeek(
        class_id=teaching_class_id,
        week_number=number,
        session_date=date(2026, 9, number),
        title=f"第 {number} 週",
        status="published",
    )
    session.add(week)
    session.flush()
    return week


def _judge(
    session: Session, *, teaching_class_id: uuid.UUID, title: str
) -> TeacherJudgeSession:
    """一週一個對話：學生看到的作業就是這個對話。"""
    week = _published_week(session, teaching_class_id)
    judge = TeacherJudgeSession(
        teaching_class_id=teaching_class_id, title=title, teaching_class_week_id=week.id
    )
    session.add(judge)
    session.flush()
    return judge


def _artifact(
    *,
    teaching_class_id: uuid.UUID,
    status: TeacherJudgeScriptStatus,
    name: str,
    session_id: uuid.UUID | None = None,
) -> TeacherJudgeScriptArtifact:
    return TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        session_id=session_id,
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
    for class_id, status, name in [
        (own_class.id, TeacherJudgeScriptStatus.approved, "Linux 權限任務"),
        (
            same_teacher_other_class.id,
            TeacherJudgeScriptStatus.approved,
            "同老師但不同班的任務",
        ),
        (own_class.id, TeacherJudgeScriptStatus.reviewed, "尚未核准"),
        (other_class.id, TeacherJudgeScriptStatus.approved, "其他老師任務"),
    ]:
        judge = _judge(session, teaching_class_id=class_id, title=name)
        session.add(
            _artifact(
                teaching_class_id=class_id,
                status=status,
                name=name,
                session_id=judge.id,
            )
        )
    # 沒有對話的舊腳本：學生端看不到
    session.add(
        _artifact(
            teaching_class_id=own_class.id,
            status=TeacherJudgeScriptStatus.approved,
            name="沒有對話的舊腳本",
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
    judge = _judge(
        session, teaching_class_id=teaching_class.id, title="Linux completion toggle"
    )
    artifact = _artifact(
        teaching_class_id=teaching_class.id,
        status=TeacherJudgeScriptStatus.approved,
        name="Linux completion toggle",
        session_id=judge.id,
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
        assignment_id=judge.id,
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
        assignment_id=judge.id,
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
        assignment_id=judge.id,
        item_id="permissions",
        completed=False,
    )

    assert first_item_unchecked.completed is False
    assert first_item_unchecked.completed_item_ids == ["resources"]
    assert first_item_unchecked.ready_at is None
    assert (
        list_student_ai_assignments(
            session,
            user_id=student_id,
            path_id=path.id,
        )[0].completion.completed
        is False
    )
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
    judge = _judge(
        session, teaching_class_id=teaching_class.id, title="Weekly Linux assignment"
    )
    artifact = _artifact(
        teaching_class_id=teaching_class.id,
        status=TeacherJudgeScriptStatus.approved,
        name="Weekly Linux assignment",
        session_id=judge.id,
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
        assignment_id=judge.id,
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
        assignment_id=judge.id,
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
    judge = _judge(session, teaching_class_id=teaching_class.id, title="Linux 權限任務")
    artifact = _artifact(
        teaching_class_id=teaching_class.id,
        status=TeacherJudgeScriptStatus.approved,
        name="Linux 權限任務",
        session_id=judge.id,
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
            assignment_id=judge.id,
        )
    )
    assert document_path == expected_path
    assert filename == "linux-homework.pdf"

    with pytest.raises(NotFoundError) as exc_info:
        ai_assignment_service.get_student_ai_assignment_source_document(
            session,
            user_id=uuid.uuid4(),
            path_id=path.id,
            assignment_id=judge.id,
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
    judge = _judge(session, teaching_class_id=teaching_class.id, title="Linux 權限任務")
    artifact = _artifact(
        teaching_class_id=teaching_class.id,
        status=TeacherJudgeScriptStatus.approved,
        name="Linux 權限任務",
        session_id=judge.id,
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    session.add(
        TeacherJudgeScriptRun(
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


def test_student_script_result_projection_uses_checks_and_coverage() -> None:
    teaching_class_id = uuid.uuid4()
    student_id = uuid.uuid4()
    artifact = _artifact(
        teaching_class_id=teaching_class_id,
        status=TeacherJudgeScriptStatus.approved,
        name="Linux 權限任務",
    )
    artifact.policy_check_result_json = {
        "coverage": {
            "mappings": [
                {
                    "check_id": "permissions.command",
                    "rubric_item_ids": ["permissions"],
                }
            ]
        }
    }
    run = TeacherJudgeScriptRun(
        artifact_id=artifact.id,
        status=TeacherJudgeScriptRunStatus.completed,
        target_results_json={
            "schema_version": "teacher_judge_run_results.v2",
            "targets": [
                {
                    "user": {"id": str(student_id)},
                    "stdout_excerpt": "must not be returned",
                    "parsed_result": {
                        "summary": "腳本完成收集。",
                        "checks": [
                            {
                                "id": "permissions.command",
                                "title": "收集檔案權限",
                                "status": "pass",
                                "evidence": "chmod 結果符合要求。",
                                "raw": "secret command output",
                            }
                        ],
                        "errors": [],
                    },
                }
            ],
        },
    )

    check = ai_assignment_service._check_to_student(
        run,
        artifact=artifact,
        item_id="permissions",
        user_id=student_id,
    )

    assert check.score is None
    assert check.max_score is None
    assert check.summary == "腳本完成收集。"
    assert check.items[0].item_id == "permissions"
    assert check.items[0].status == "pass"
    assert check.items[0].comment == "chmod 結果符合要求。"
    assert "secret command output" not in check.items[0].comment


def test_student_projection_hides_technical_validation_error() -> None:
    """輸出驗證失敗的原始錯誤（pydantic validation errors）只給老師看，學生看到一句說明。"""
    student_id = uuid.uuid4()
    raw_error = (
        "4 validation errors for ManagedScriptResult\n"
        "checks.0.raw\n"
        "  Input should be a valid string"
    )
    # 舊資料相容路徑：學生自己發起、只有一個沒帶 user 快照的 target
    run = TeacherJudgeScriptRun(
        status=TeacherJudgeScriptRunStatus.completed,
        started_by=student_id,
        target_results_json={
            "schema_version": "teacher_judge_run_results.v2",
            "targets": [
                {
                    "status": "failed",
                    "reason_code": "invalid_json",
                    "validation": {"valid": False, "error": raw_error},
                    "parsed_result": None,
                }
            ],
        },
    )

    check = ai_assignment_service._check_to_student(run, user_id=student_id)

    assert "validation error" not in check.error
    assert "ManagedScriptResult" not in check.error
    assert check.error == t("course.ai_check_incomplete")


def test_student_projection_hides_validation_error_in_legacy_judgement_summary() -> (
    None
):
    """舊資料（results v1）的 skipped AI 判讀把驗證錯誤寫進 summary；前端優先顯示 summary，
    不可原樣回給學生。"""
    raw_error = (
        "4 validation errors for ManagedScriptResult\n"
        "checks.0.raw\n"
        "  Input should be a valid string [type=string_type, input_value={}, input_type=dict]"
    )
    student_id = uuid.uuid4()
    # 舊資料相容路徑：學生自己發起、只有一個沒帶 user 快照的 target
    run = TeacherJudgeScriptRun(
        status=TeacherJudgeScriptRunStatus.completed,
        started_by=student_id,
        target_results_json={
            "schema_version": "teacher_judge_run_results.v1",
            "targets": [
                {
                    "status": "failed",
                    "reason_code": "invalid_json",
                    "validation": {"valid": False, "error": raw_error},
                    "parsed_result": None,
                    "ai_judgement": {
                        "status": "skipped",
                        "summary": raw_error,
                        "score": None,
                        "max_score": None,
                        "item_judgements": [],
                    },
                }
            ],
        },
    )

    check = ai_assignment_service._check_to_student(run, user_id=student_id)

    assert check.summary == ""
    assert check.error == t("course.ai_check_incomplete")
    assert all("ManagedScriptResult" not in item.comment for item in check.items)


def test_student_projection_uses_own_batch_target_and_teacher_review() -> None:
    teaching_class_id = uuid.uuid4()
    student_id = uuid.uuid4()
    other_student_id = uuid.uuid4()
    artifact = _artifact(
        teaching_class_id=teaching_class_id,
        status=TeacherJudgeScriptStatus.approved,
        name="人工核查任務",
    )
    run = TeacherJudgeScriptRun(
        artifact_id=artifact.id,
        status=TeacherJudgeScriptRunStatus.completed,
        target_results_json={
            "targets": [
                {
                    "vmid": 101,
                    "user": {"user_id": str(other_student_id)},
                    "parsed_result": {
                        "checks": [
                            {
                                "id": "log",
                                "title": "日誌",
                                "status": "unknown",
                                "evidence": "其他人的資料",
                            }
                        ]
                    },
                    "teacher_review": {
                        "feedback": "其他人的回饋",
                        "decisions": {"log": "fail"},
                    },
                },
                {
                    "vmid": 102,
                    "user": {"user_id": str(student_id)},
                    "parsed_result": {
                        "summary": "已收集",
                        "checks": [
                            {
                                "id": "log",
                                "title": "日誌",
                                "status": "unknown",
                                "evidence": "自己的摘要",
                                "raw": "private raw",
                            }
                        ],
                    },
                    "teacher_review": {
                        "feedback": "請補上例外處理",
                        "decisions": {"log": "pass"},
                    },
                },
            ]
        },
    )

    check = ai_assignment_service._check_to_student(
        run,
        artifact=artifact,
        user_id=student_id,
    )

    assert check.teacher_feedback == "請補上例外處理"
    assert check.items[0].status == "pass"
    assert check.items[0].comment == "自己的摘要"
    assert "其他人的" not in str(check.model_dump())
    assert "private raw" not in str(check.model_dump())


# ── 一週一個對話：多機器、重新製作都還是同一份作業 ─────────────────────────


def _class_with_student(
    session: Session,
) -> tuple[uuid.UUID, TeachingClass, CoursePath]:
    student_id = uuid.uuid4()
    teacher_id = uuid.uuid4()
    teaching_class = TeachingClass(
        name="網站部署",
        code="web-deploy",
        term="2026-1",
        owner_id=teacher_id,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        weekday=1,
        start_time=time(9),
        end_time=time(11),
    )
    path = CoursePath(
        title="網站部署",
        status=CoursePathStatus.published,
        created_by=teacher_id,
        teaching_class_id=teaching_class.id,
    )
    session.add_all(
        [
            teaching_class,
            path,
            TeachingClassStudent(class_id=teaching_class.id, user_id=student_id),
        ]
    )
    session.commit()
    return student_id, teaching_class, path


def _machine_script(
    *,
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    set_id: uuid.UUID,
    node: str,
    item_id: str,
    version: int = 1,
    status: TeacherJudgeScriptStatus = TeacherJudgeScriptStatus.approved,
) -> TeacherJudgeScriptArtifact:
    return TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        session_id=session_id,
        artifact_set_id=set_id,
        target_node_key=node,
        name=f"網站部署 · {node}",
        template_key="linux",
        rubric_snapshot_json={
            "items": [{"id": item_id, "title": item_id, "detectable": "auto"}]
        },
        script_content="print('{}')",
        status=status,
        version=version,
    )


def _student_run(
    artifact: TeacherJudgeScriptArtifact,
    student_id: uuid.UUID,
    checks: list[dict] | None,
) -> TeacherJudgeScriptRun:
    parsed = {"summary": "", "checks": checks} if checks is not None else None
    return TeacherJudgeScriptRun(
        artifact_id=artifact.id,
        status=TeacherJudgeScriptRunStatus.completed,
        target_results_json={
            "targets": [{"user": {"id": str(student_id)}, "parsed_result": parsed}]
        },
    )


def test_every_machine_of_the_week_shows_up_as_one_assignment() -> None:
    session = _session()
    student_id, teaching_class, path = _class_with_student(session)
    judge = _judge(
        session, teaching_class_id=teaching_class.id, title="第 3 週：網站部署"
    )
    set_id = uuid.uuid4()
    session.add_all(
        [
            TeachingClassMachineNode(
                class_id=teaching_class.id,
                node_key="db",
                name="資料庫",
                role="db",
                resource_type="lxc",
                cpu=1,
                memory_mb=1024,
                disk_gb=10,
                sort_order=1,
            ),
            TeachingClassMachineNode(
                class_id=teaching_class.id,
                node_key="web",
                name="網站",
                role="web",
                resource_type="lxc",
                cpu=1,
                memory_mb=1024,
                disk_gb=10,
                sort_order=0,
            ),
            _machine_script(
                teaching_class_id=teaching_class.id,
                session_id=judge.id,
                set_id=set_id,
                node="web",
                item_id="service",
            ),
            _machine_script(
                teaching_class_id=teaching_class.id,
                session_id=judge.id,
                set_id=set_id,
                node="db",
                item_id="database",
            ),
        ]
    )
    session.commit()

    assignments = list_student_ai_assignments(
        session, user_id=student_id, path_id=path.id
    )

    assert len(assignments) == 1
    assert assignments[0].id == judge.id
    assert assignments[0].title == "第 3 週：網站部署"
    assert [(item.id, item.machine_node_key) for item in assignments[0].items] == [
        ("service", "web"),
        ("database", "db"),
    ]


def test_completion_and_results_survive_rebuilding_the_scripts() -> None:
    session = _session()
    student_id, teaching_class, path = _class_with_student(session)
    judge = _judge(session, teaching_class_id=teaching_class.id, title="第 3 週")
    set_id = uuid.uuid4()
    first = _machine_script(
        teaching_class_id=teaching_class.id,
        session_id=judge.id,
        set_id=set_id,
        node="web",
        item_id="service",
    )
    session.add(first)
    session.commit()
    ai_assignment_service.update_student_completion(
        session,
        user_id=student_id,
        path_id=path.id,
        assignment_id=judge.id,
        item_id="service",
        completed=True,
    )
    session.add(_student_run(first, student_id, [{"id": "service", "status": "pass"}]))
    session.commit()

    # 老師重新製作：舊版封存、新版核准，學生回報與舊結果都還在
    first.status = TeacherJudgeScriptStatus.archived
    rebuilt = _machine_script(
        teaching_class_id=teaching_class.id,
        session_id=judge.id,
        set_id=set_id,
        node="web",
        item_id="service",
        version=2,
    )
    session.add_all([first, rebuilt])
    session.commit()

    assignment = list_student_ai_assignments(
        session, user_id=student_id, path_id=path.id
    )[0]
    assert assignment.version == 2
    assert assignment.completion.completed_item_ids == ["service"]
    assert assignment.checkpoint_checks["service"].items[0].status == "pass"
    submissions = session.exec(select(TeacherJudgeStudentSubmission)).all()
    assert [row.session_id for row in submissions] == [judge.id]


def test_an_environment_failure_keeps_the_last_real_verdict() -> None:
    session = _session()
    student_id, teaching_class, path = _class_with_student(session)
    judge = _judge(session, teaching_class_id=teaching_class.id, title="第 3 週")
    script = _machine_script(
        teaching_class_id=teaching_class.id,
        session_id=judge.id,
        set_id=uuid.uuid4(),
        node="web",
        item_id="service",
    )
    session.add(script)
    session.commit()
    session.add(_student_run(script, student_id, [{"id": "service", "status": "fail"}]))
    session.commit()
    # 之後重查時連不上機器：沒有任何逐項判定
    session.add(_student_run(script, student_id, None))
    session.commit()

    assignment = list_student_ai_assignments(
        session, user_id=student_id, path_id=path.id
    )[0]
    assert assignment.checkpoint_checks["service"].items[0].status == "fail"


@pytest.mark.parametrize(
    "script_status",
    [
        None,
        TeacherJudgeScriptStatus.draft,
        TeacherJudgeScriptStatus.reviewed,
        TeacherJudgeScriptStatus.review_failed,
        TeacherJudgeScriptStatus.archived,
    ],
)
def test_students_only_see_tasks_after_scripts_are_approved(
    script_status: TeacherJudgeScriptStatus | None,
) -> None:
    """發布週次不公開仍在設定的任務；製作並核准後才提供任務、PDF 與回報入口。"""
    session = _session()
    student_id, teaching_class, path = _class_with_student(session)
    week = TeachingClassWeek(
        class_id=teaching_class.id,
        week_number=3,
        session_date=date(2026, 9, 15),
        title="網站部署",
        status="draft",
    )
    checklist = TeacherJudgeFile(
        teaching_class_id=teaching_class.id,
        uploaded_by=teaching_class.owner_id,
        original_filename="week3.pdf",
        file_hash="b" * 64,
        template_key="linux",
        source_type="uploaded",
        display_name="第 3 週檢查表",
        analysis_json={
            "summary": "把網站架起來。",
            "items": [
                {"id": "service", "title": "服務啟動", "detectable": "auto"},
                {"id": "database", "title": "資料庫連線", "detectable": "auto"},
            ],
        },
    )
    student = User(
        id=student_id,
        email="student@example.edu",
        role=UserRole.student,
        hashed_password="x",
    )
    session.add_all([week, checklist, student])
    session.flush()
    judge = TeacherJudgeSession(
        teaching_class_id=teaching_class.id,
        title="第 3 週",
        teaching_class_week_id=week.id,
        selected_file_id=checklist.id,
    )
    session.add(judge)
    session.commit()
    script = _machine_script(
        teaching_class_id=teaching_class.id,
        session_id=judge.id,
        set_id=uuid.uuid4(),
        node="web",
        item_id="service",
        status=script_status or TeacherJudgeScriptStatus.draft,
    )
    script.source_file_id = checklist.id
    script.rubric_snapshot_json = checklist.analysis_json
    if script_status is not None:
        session.add(script)
        session.commit()

    # 週次還是草稿：不外流給學生
    assert (
        list_student_ai_assignments(session, user_id=student_id, path_id=path.id) == []
    )

    week.status = "published"
    session.add(week)
    session.commit()

    # 已發布週次仍可看課程內容，但不能從每週內容的 fallback 取得 AI 草稿。
    assert (
        list_student_ai_assignments(session, user_id=student_id, path_id=path.id) == []
    )
    [student_week] = weekly_task_service.list_student_weekly_tasks(
        session,
        user_id=student_id,
        path_id=path.id,
    )
    assert student_week.title == "網站部署"
    assert student_week.checkpoints == []
    assert student_week.source_documents == []
    assert ai_assignment_service.session_progress(session, judge)["items"]

    with pytest.raises(NotFoundError):
        ai_assignment_service.get_student_ai_assignment(
            session,
            user_id=student_id,
            path_id=path.id,
            assignment_id=judge.id,
        )
    with pytest.raises(NotFoundError):
        ai_assignment_service.get_student_ai_assignment_source_document(
            session,
            user_id=student_id,
            path_id=path.id,
            assignment_id=judge.id,
        )
    with pytest.raises(NotFoundError):
        ai_assignment_service.update_student_completion(
            session,
            user_id=student_id,
            path_id=path.id,
            assignment_id=judge.id,
            item_id=None,
            completed=True,
        )
    assert session.exec(select(TeacherJudgeStudentSubmission)).all() == []

    script.status = TeacherJudgeScriptStatus.approved
    session.add(script)
    session.commit()
    [assignment] = list_student_ai_assignments(
        session, user_id=student_id, path_id=path.id
    )
    assert assignment.id == judge.id
    assert assignment.check_available is True
    assert assignment.summary == "把網站架起來。"
    assert [item.id for item in assignment.items] == ["service", "database"]
    assert assignment.source_document is not None
    [student_week] = weekly_task_service.list_student_weekly_tasks(
        session,
        user_id=student_id,
        path_id=path.id,
    )
    assert [item.id for item in student_week.checkpoints] == ["service", "database"]
    [document] = student_week.source_documents
    assert document.assignment_id == assignment.id
    assert document.filename == "week3.pdf"
    assert document.display_name == "第 3 週檢查表"

    ai_assignment_service.update_student_completion(
        session,
        user_id=student_id,
        path_id=path.id,
        assignment_id=judge.id,
        item_id=None,
        completed=True,
    )
    progress = ai_assignment_service.session_progress(session, judge)
    assert [item["id"] for item in progress["items"]] == ["service", "database"]
    [row] = progress["students"]
    assert row["completed_item_ids"] == ["service", "database"]
    assert row["ready_at"] is not None


def test_editing_a_checklist_keeps_only_the_approved_snapshot_visible() -> None:
    session = _session()
    student_id, teaching_class, path = _class_with_student(session)
    judge = _judge(session, teaching_class_id=teaching_class.id, title="第 3 週")
    approved = _machine_script(
        teaching_class_id=teaching_class.id,
        session_id=judge.id,
        set_id=uuid.uuid4(),
        node="web",
        item_id="published-service",
    )
    draft_file = TeacherJudgeFile(
        teaching_class_id=teaching_class.id,
        uploaded_by=teaching_class.owner_id,
        file_hash="d" * 64,
        source_type="created",
        template_key="linux",
        analysis_json={"items": [{"id": "secret-draft", "title": "尚未完成的任務"}]},
    )
    session.add_all([approved, draft_file])
    session.flush()
    judge.selected_file_id = draft_file.id
    session.add(judge)
    session.add(
        _machine_script(
            teaching_class_id=teaching_class.id,
            session_id=judge.id,
            set_id=uuid.uuid4(),
            node="web",
            item_id="secret-draft",
            status=TeacherJudgeScriptStatus.reviewed,
        )
    )
    session.commit()

    [assignment] = list_student_ai_assignments(
        session, user_id=student_id, path_id=path.id
    )
    assert [item.id for item in assignment.items] == ["published-service"]
    [student_week] = weekly_task_service.list_student_weekly_tasks(
        session,
        user_id=student_id,
        path_id=path.id,
    )
    assert [item.id for item in student_week.checkpoints] == ["published-service"]


def test_teacher_reply_is_per_week_overwritten_and_optional() -> None:
    """回覆對的是「這位學生的這一週」：再回覆覆蓋前一則，學生只看到最新的；空白＝不回覆。"""
    session = _session()
    student_id, teaching_class, path = _class_with_student(session)
    session.add(
        User(
            id=student_id,
            email="s@example.edu",
            role=UserRole.student,
            hashed_password="x",
        )
    )
    judge = _judge(session, teaching_class_id=teaching_class.id, title="第 3 週")
    session.add(
        _machine_script(
            teaching_class_id=teaching_class.id,
            session_id=judge.id,
            set_id=uuid.uuid4(),
            node="web",
            item_id="service",
        )
    )
    session.commit()

    # 老師可以不回覆
    [assignment] = list_student_ai_assignments(
        session, user_id=student_id, path_id=path.id
    )
    assert assignment.teacher_reply == ""
    assert assignment.teacher_reply_at is None

    first = ai_assignment_service.save_student_reply(
        session,
        judge,
        student_id=student_id,
        feedback="  服務有起來了  ",
        teacher_id=None,
    )
    assert first["teacher_reply"] == "服務有起來了"
    second = ai_assignment_service.save_student_reply(
        session,
        judge,
        student_id=student_id,
        feedback="記得補上日誌設定",
        teacher_id=None,
    )
    assert second["teacher_reply"] == "記得補上日誌設定"

    [assignment] = list_student_ai_assignments(
        session, user_id=student_id, path_id=path.id
    )
    assert assignment.teacher_reply == "記得補上日誌設定"
    assert assignment.teacher_reply_at is not None
    [row] = ai_assignment_service.session_progress(session, judge)["students"]
    assert row["teacher_reply"] == "記得補上日誌設定"
    [week] = weekly_task_service.list_student_weekly_tasks(
        session, user_id=student_id, path_id=path.id
    )
    assert week.teacher_reply == "記得補上日誌設定"

    # 清空＝收回回覆
    cleared = ai_assignment_service.save_student_reply(
        session,
        judge,
        student_id=student_id,
        feedback="   ",
        teacher_id=None,
    )
    assert cleared == {
        "user_id": str(student_id),
        "teacher_reply": "",
        "teacher_reply_at": None,
    }
    [assignment] = list_student_ai_assignments(
        session, user_id=student_id, path_id=path.id
    )
    assert assignment.teacher_reply == ""

    # 不在這個班的學生不能回覆
    with pytest.raises(NotFoundError):
        ai_assignment_service.save_student_reply(
            session,
            judge,
            student_id=uuid.uuid4(),
            feedback="hi",
            teacher_id=None,
        )
