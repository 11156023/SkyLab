"""Coverage tests for app.services.course.progress_service (answer submit and reports)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import NotFoundError
from app.models import (
    CoursePath,
    CoursePathStatus,
    CourseQuestion,
    CourseQuestionType,
    CourseRoom,
    CourseTask,
    User,
    UserCourseProgress,
    UserRole,
)
from app.services.course import flag_service, progress_service


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def audit_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _log_action(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(progress_service.audit_service, "log_action", _log_action)
    return calls


def _user(session: Session, email: str, name: str | None = None) -> User:
    user = User(email=email, role=UserRole.student, full_name=name, hashed_password="x")
    session.add(user)
    session.commit()
    return user


def _course(
    session: Session, *, status: CoursePathStatus = CoursePathStatus.published
) -> tuple[CoursePath, CourseRoom, CourseTask, CourseQuestion, CourseQuestion]:
    path = CoursePath(title="P", status=status)
    session.add(path)
    session.commit()
    room = CourseRoom(path_id=path.id, title="R")
    session.add(room)
    session.commit()
    task = CourseTask(room_id=room.id, title="T", content="c")
    session.add(task)
    session.commit()
    flag_q = CourseQuestion(
        task_id=task.id,
        prompt="flag",
        question_type=CourseQuestionType.flag,
        flag_hash=flag_service.hash_flag("FLAG{ok}"),
    )
    read_q = CourseQuestion(
        task_id=task.id,
        prompt="read",
        question_type=CourseQuestionType.no_answer,
        order=1,
    )
    session.add(flag_q)
    session.add(read_q)
    session.commit()
    return path, room, task, flag_q, read_q


def test_submit_answer_missing_hierarchy_raises(session: Session) -> None:
    user = _user(session, "s@example.edu")
    with pytest.raises(NotFoundError):
        progress_service.submit_answer(
            session, user=user, question_id=uuid.uuid4(), answer=None
        )

    orphan_task_q = CourseQuestion(
        task_id=uuid.uuid4(), prompt="x", question_type=CourseQuestionType.no_answer
    )
    session.add(orphan_task_q)
    session.commit()
    with pytest.raises(NotFoundError):
        progress_service.submit_answer(
            session, user=user, question_id=orphan_task_q.id, answer=None
        )

    orphan_room_task = CourseTask(room_id=uuid.uuid4(), title="T", content="")
    session.add(orphan_room_task)
    session.commit()
    orphan_room_q = CourseQuestion(
        task_id=orphan_room_task.id,
        prompt="x",
        question_type=CourseQuestionType.no_answer,
    )
    session.add(orphan_room_q)
    session.commit()
    with pytest.raises(NotFoundError):
        progress_service.submit_answer(
            session, user=user, question_id=orphan_room_q.id, answer=None
        )

    _, _, _, draft_flag_q, _ = _course(session, status=CoursePathStatus.draft)
    with pytest.raises(NotFoundError):
        progress_service.submit_answer(
            session, user=user, question_id=draft_flag_q.id, answer="FLAG{ok}"
        )


def test_submit_answer_flow(session: Session, audit_calls: list[dict]) -> None:
    user = _user(session, "s@example.edu")
    path, room, task, flag_q, read_q = _course(session)

    # Wrong flag: not recorded, no event.
    result, path_id, event = progress_service.submit_answer(
        session, user=user, question_id=flag_q.id, answer="FLAG{nope}"
    )
    assert result.correct is False
    assert result.task_completed is False
    assert result.room_progress_percent == 0.0
    assert path_id == path.id
    assert event is None

    # Correct flag: newly completed → event.
    result, _, event = progress_service.submit_answer(
        session, user=user, question_id=flag_q.id, answer=" FLAG{ok} "
    )
    assert result.correct is True
    assert result.task_completed is False
    assert result.room_progress_percent == 50.0
    assert event is not None
    assert event["type"] == "progress"
    assert event["user_email"] == "s@example.edu"
    assert event["room_id"] == str(room.id)
    assert event["task_id"] == str(task.id)

    # Re-submitting a wrong answer for a completed question is idempotent.
    result, _, event = progress_service.submit_answer(
        session, user=user, question_id=flag_q.id, answer="wrong"
    )
    assert result.correct is True
    assert event is None

    # no_answer question completes the task.
    result, _, event = progress_service.submit_answer(
        session, user=user, question_id=read_q.id, answer=None
    )
    assert result.correct is True
    assert result.task_completed is True
    assert result.room_progress_percent == 100.0
    assert event is not None

    assert len(audit_calls) == 4
    assert all(call["action"] == "course_answer_submit" for call in audit_calls)
    assert "correct=False" in audit_calls[0]["details"]


def test_path_progress_report(session: Session) -> None:
    path, room, task, flag_q, read_q = _course(session)
    second_room = CourseRoom(path_id=path.id, title="Empty room", order=1)
    session.add(second_room)
    session.commit()

    alice = _user(session, "alice@example.edu", "Alice")
    bob = _user(session, "bob@example.edu", "Bob")
    session.add(UserCourseProgress(user_id=alice.id, question_id=flag_q.id))
    session.add(UserCourseProgress(user_id=alice.id, question_id=read_q.id))
    session.add(UserCourseProgress(user_id=bob.id, question_id=read_q.id))
    # Progress row for a user that no longer exists is skipped.
    session.add(UserCourseProgress(user_id=uuid.uuid4(), question_id=flag_q.id))
    session.commit()

    report = progress_service.path_progress_report(session, path_id=path.id)
    assert report.total_questions == 2
    assert [s.user_email for s in report.students] == [
        "alice@example.edu",
        "bob@example.edu",
    ]
    alice_row, bob_row = report.students
    assert alice_row.progress_percent == 100.0
    assert alice_row.user_name == "Alice"
    assert bob_row.completed_questions == 1
    assert bob_row.progress_percent == 50.0
    assert [r.room_id for r in alice_row.rooms] == [room.id, second_room.id]
    assert alice_row.rooms[1].total_questions == 0
    assert alice_row.rooms[1].progress_percent == 0.0
