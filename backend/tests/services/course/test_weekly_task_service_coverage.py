"""Coverage tests for app.services.course.weekly_task_service guard branches."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, time
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import NotFoundError
from app.models import (
    CoursePath,
    CoursePathStatus,
    TeachingClass,
    TeachingClassStudent,
    TeachingClassTaskFile,
    TeachingClassWeek,
)
from app.models.teacher_judge_session import TeacherJudgeSession
from app.services.course import weekly_task_service


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _InjectJudgeSessions:
    """Delegate to a real session but append extra rows to judge-session queries."""

    def __init__(self, inner: Session, extra: list[TeacherJudgeSession]) -> None:
        self._inner = inner
        self._extra = extra

    def exec(self, statement: Any) -> Any:
        result = self._inner.exec(statement)
        descriptions = getattr(statement, "column_descriptions", [])
        if descriptions and descriptions[0].get("entity") is TeacherJudgeSession:
            return _Rows([*result.all(), *self._extra])
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _linked(
    session: Session, *, enrolled: bool = True
) -> tuple[uuid.UUID, TeachingClass, CoursePath]:
    student_id = uuid.uuid4()
    teaching_class = TeachingClass(
        name="Linux A",
        code=f"c-{uuid.uuid4().hex[:6]}",
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
    if enrolled:
        session.add(
            TeachingClassStudent(class_id=teaching_class.id, user_id=student_id)
        )
    session.commit()
    return student_id, teaching_class, path


def _week(
    session: Session, teaching_class: TeachingClass, **kwargs: Any
) -> TeachingClassWeek:
    values: dict[str, Any] = {
        "class_id": teaching_class.id,
        "week_number": 1,
        "session_date": date(2026, 8, 25),
        "title": "Week one",
        "status": "published",
    }
    values.update(kwargs)
    week = TeachingClassWeek(**values)
    session.add(week)
    session.commit()
    return week


def test_list_returns_empty_without_enrollment_or_visible_weeks(
    session: Session,
) -> None:
    student_id, _, path = _linked(session, enrolled=False)
    assert (
        weekly_task_service.list_student_weekly_tasks(
            session, user_id=student_id, path_id=path.id
        )
        == []
    )

    student_id, teaching_class, path = _linked(session)
    _week(session, teaching_class, status="draft")
    assert (
        weekly_task_service.list_student_weekly_tasks(
            session, user_id=student_id, path_id=path.id
        )
        == []
    )


def test_list_filters_files_and_handles_bare_judge_sessions(session: Session) -> None:
    student_id, teaching_class, path = _linked(session)
    week = _week(session, teaching_class)
    session.add(
        TeachingClassTaskFile(week_id=week.id, filename="notes.docx", storage_key="a")
    )
    session.add(
        TeachingClassTaskFile(week_id=week.id, filename="draft.pdf", storage_key=None)
    )
    session.add(
        TeachingClassTaskFile(week_id=week.id, filename="Guide.PDF", storage_key="g")
    )
    # Judge session with no selected file and no approved assignment → no checkpoints.
    session.add(
        TeacherJudgeSession(
            teaching_class_id=teaching_class.id,
            teaching_class_week_id=week.id,
            title="Bare",
        )
    )
    session.commit()

    # A judge session without a week (defensive skip) injected into the query result.
    weekless = TeacherJudgeSession(
        teaching_class_id=teaching_class.id,
        teaching_class_week_id=None,
        title="Weekless",
    )
    proxy = _InjectJudgeSessions(session, [weekless])

    rows = weekly_task_service.list_student_weekly_tasks(
        proxy,  # type: ignore[arg-type]
        user_id=student_id,
        path_id=path.id,
    )
    assert len(rows) == 1
    assert [f.filename for f in rows[0].files] == ["Guide.PDF"]
    assert rows[0].checkpoints == []


def test_get_pdf_guards_and_success(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(weekly_task_service, "TASK_FILE_ROOT", tmp_path)
    student_id, teaching_class, path = _linked(session)
    week = _week(session, teaching_class)
    good = TeachingClassTaskFile(
        week_id=week.id, filename="guide.pdf", storage_key="guide.task"
    )
    missing_on_disk = TeachingClassTaskFile(
        week_id=week.id, filename="gone.pdf", storage_key="gone.task"
    )
    traversal = TeachingClassTaskFile(
        week_id=week.id, filename="evil.pdf", storage_key="../evil.task"
    )
    for row in (good, missing_on_disk, traversal):
        session.add(row)
    session.commit()
    (tmp_path / "guide.task").write_bytes(b"%PDF-1.4")
    (tmp_path.parent / "evil.task").write_bytes(b"%PDF-1.4")

    stored, filename = weekly_task_service.get_student_weekly_task_pdf(
        session,
        user_id=student_id,
        path_id=path.id,
        week_id=week.id,
        file_id=good.id,
    )
    assert stored == (tmp_path / "guide.task").resolve()
    assert filename == "guide.pdf"

    # Metadata guard: unknown week.
    with pytest.raises(NotFoundError):
        weekly_task_service.get_student_weekly_task_pdf(
            session,
            user_id=student_id,
            path_id=path.id,
            week_id=uuid.uuid4(),
            file_id=good.id,
        )
    # Filesystem guards: missing file and path traversal.
    for row in (missing_on_disk, traversal):
        with pytest.raises(NotFoundError):
            weekly_task_service.get_student_weekly_task_pdf(
                session,
                user_id=student_id,
                path_id=path.id,
                week_id=week.id,
                file_id=row.id,
            )
