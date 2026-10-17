"""Coverage tests for app.services.course.ai_assignment_service edge branches."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, time

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.exceptions import NotFoundError
from app.models.course import CoursePath, CoursePathStatus
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunStatus,
)
from app.models.teacher_judge_session import TeacherJudgeSession
from app.models.teaching_class import (
    TeachingClass,
    TeachingClassStudent,
    TeachingClassWeek,
)
from app.services.course import ai_assignment_service


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _run(**kwargs: object) -> TeacherJudgeScriptRun:
    return TeacherJudgeScriptRun(
        artifact_id=uuid.uuid4(),
        status=TeacherJudgeScriptRunStatus.completed,
        **kwargs,
    )


def _artifact(
    *,
    db: Session | None = None,
    teaching_class_id: uuid.UUID,
    name: str,
    items: list[dict] | None = None,
    policy: dict | None = None,
    session_id: uuid.UUID | None = None,
) -> TeacherJudgeScriptArtifact:
    artifact = TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        session_id=session_id,
        name=name,
        template_key="linux",
        rubric_snapshot_json={
            "items": items
            if items is not None
            else [{"id": "perm", "title": "Set permissions"}]
        },
        policy_check_result_json=policy,
        script_content="print('{}')",
        status=TeacherJudgeScriptStatus.approved,
    )
    if db is not None and session_id is not None and artifact.status == TeacherJudgeScriptStatus.approved:
        workspace = db.get(TeacherJudgeSession, session_id)
        artifact.artifact_set_id = artifact.artifact_set_id or uuid.uuid4()
        artifact.target_node_key = artifact.target_node_key or "web"
        workspace.active_artifact_set_id = artifact.artifact_set_id
        db.add(workspace)
    return artifact


def _published_week(session: Session, teaching_class_id: uuid.UUID) -> TeachingClassWeek:
    """作業跟著每週內容發布：測試裡的對話都綁在已發布的週次上。"""
    number = len(
        session.exec(
            select(TeachingClassWeek).where(TeachingClassWeek.class_id == teaching_class_id)
        ).all()
    ) + 1
    week = TeachingClassWeek(
        class_id=teaching_class_id, week_number=number,
        session_date=date(2026, 9, number), title=f"第 {number} 週", status="published",
    )
    session.add(week)
    session.flush()
    return week


def _judge(session: Session, teaching_class_id: uuid.UUID, title: str) -> TeacherJudgeSession:
    week = _published_week(session, teaching_class_id)
    judge = TeacherJudgeSession(
        teaching_class_id=teaching_class_id, title=title, teaching_class_week_id=week.id
    )
    session.add(judge)
    session.flush()
    return judge


def _setup_class(session: Session) -> tuple[uuid.UUID, TeachingClass, CoursePath]:
    student_id = uuid.uuid4()
    teaching_class = TeachingClass(
        name="Linux A",
        code="linux-a",
        term="2026-1",
        owner_id=uuid.uuid4(),
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        weekday=1,
        start_time=time(9),
        end_time=time(11),
    )
    path = CoursePath(
        title="Linux",
        teaching_class_id=teaching_class.id,
        status=CoursePathStatus.published,
    )
    session.add(teaching_class)
    session.add(path)
    session.add(TeachingClassStudent(class_id=teaching_class.id, user_id=student_id))
    session.commit()
    return student_id, teaching_class, path


# ── Pure projection helpers ────────────────────────────────────────────────


def test_target_for_student_skips_non_dict_targets() -> None:
    user_id = uuid.uuid4()
    run = _run(
        target_results_json={
            "targets": ["garbage", {"user": {"user_id": str(user_id)}, "x": 1}]
        }
    )
    assert ai_assignment_service._target_for_student(run, user_id)["x"] == 1


def test_script_checks_mapping_branches() -> None:
    artifact = _artifact(
        teaching_class_id=uuid.uuid4(),
        name="a",
        policy={
            "coverage": {
                "mappings": [
                    {"check_id": "other.check", "rubric_item_ids": ["other"]},
                ]
            }
        },
    )
    target = {
        "parsed_result": {
            "checks": [
                "not-a-dict",
                {"id": "perm", "title": "Perm check", "status": "pass"},
            ]
        }
    }
    # Mapping exists but not for this item → falls back to a same-id check.
    result = ai_assignment_service._script_checks(
        target, artifact=artifact, item_id="perm"
    )
    assert [item.item_id for item in result] == ["perm"]
    assert result[0].title == "Set permissions"
    assert result[0].status == "pass"

    # No mapping and no same-id check → nothing for this item.
    assert (
        ai_assignment_service._script_checks(
            target, artifact=artifact, item_id="missing"
        )
        == []
    )


def test_legacy_judgement_items_filters_non_dicts_and_other_items() -> None:
    judgement = {
        "item_judgements": [
            "junk",
            {"item_id": "other", "title": "Other", "status": "pass"},
            {
                "item_id": "perm",
                "title": "Perm",
                "status": "fail",
                "score": 1,
                "max_score": 5,
            },
        ]
    }
    items = ai_assignment_service._legacy_judgement_items(judgement, item_id="perm")
    assert [(i.item_id, i.score, i.max_score) for i in items] == [("perm", 1, 5)]


# ── DB-backed flows ────────────────────────────────────────────────────────


def test_source_document_missing_raises_not_found(session: Session) -> None:
    student_id, teaching_class, path = _setup_class(session)
    judge = _judge(session, teaching_class.id, "No PDF")
    artifact = _artifact(db=session, teaching_class_id=teaching_class.id, name="No PDF", session_id=judge.id)
    session.add(artifact)
    session.commit()

    with pytest.raises(NotFoundError):
        ai_assignment_service.get_student_ai_assignment_source_document(
            session,
            user_id=student_id,
            path_id=path.id,
            assignment_id=judge.id,
        )


def test_get_student_ai_check_returns_own_run_and_hides_others(
    session: Session,
) -> None:
    student_id, teaching_class, path = _setup_class(session)
    judge = _judge(session, teaching_class.id, "Check")
    sibling_judge = _judge(session, teaching_class.id, "Sibling")
    artifact = _artifact(db=session, teaching_class_id=teaching_class.id, name="Check", session_id=judge.id)
    sibling = _artifact(db=session,
        teaching_class_id=teaching_class.id, name="Sibling", session_id=sibling_judge.id
    )
    session.add(artifact)
    session.add(sibling)
    session.commit()
    own_run = TeacherJudgeScriptRun(
        artifact_id=artifact.id,
        status=TeacherJudgeScriptRunStatus.completed,
        target_snapshot_json={"requested_item_id": "perm"},
        target_results_json={
            "targets": [
                {
                    "user": {"id": str(student_id)},
                    "parsed_result": {
                        "summary": "ok",
                        "checks": [{"id": "perm", "status": "pass"}],
                    },
                }
            ]
        },
    )
    other_run = TeacherJudgeScriptRun(
        artifact_id=artifact.id,
        status=TeacherJudgeScriptRunStatus.completed,
        target_results_json={
            "targets": [{"user": {"id": str(uuid.uuid4())}, "parsed_result": {}}]
        },
    )
    foreign_run = TeacherJudgeScriptRun(
        artifact_id=sibling.id,
        status=TeacherJudgeScriptRunStatus.completed,
        target_results_json={"targets": [{"user": {"id": str(student_id)}}]},
    )
    session.add(own_run)
    session.add(other_run)
    session.add(foreign_run)
    session.commit()
    run_ids = [uuid.uuid4(), other_run.id, foreign_run.id]

    check = ai_assignment_service.get_student_ai_check(
        session,
        user_id=student_id,
        path_id=path.id,
        assignment_id=judge.id,
        run_id=own_run.id,
    )
    assert check.run_id == own_run.id
    assert check.items[0].item_id == "perm"
    assert check.items[0].status == "pass"

    for run_id in run_ids:
        with pytest.raises(NotFoundError):
            ai_assignment_service.get_student_ai_check(
                session,
                user_id=student_id,
                path_id=path.id,
                assignment_id=judge.id,
                run_id=run_id,
            )


def test_update_completion_rejects_unknown_item(session: Session) -> None:
    student_id, teaching_class, path = _setup_class(session)
    judge = _judge(session, teaching_class.id, "Toggle")
    artifact = _artifact(db=session, teaching_class_id=teaching_class.id, name="Toggle", session_id=judge.id)
    session.add(artifact)
    session.commit()

    with pytest.raises(NotFoundError):
        ai_assignment_service.update_student_completion(
            session,
            user_id=student_id,
            path_id=path.id,
            assignment_id=judge.id,
            item_id="nope",
            completed=True,
        )
