"""Coverage tests for app.services.course.reminder_service labels and guards."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import (
    CoursePath,
    CoursePathStatus,
    Resource,
    TeachingClass,
    TeachingClassStudent,
    TeachingClassWeek,
    VMRequest,
    VMRequestStatus,
)
from app.services.course import reminder_service


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_reminders_cover_today_tomorrow_rejected_and_fallback_name(
    session: Session,
) -> None:
    student_id = uuid.uuid4()
    today = date(2026, 8, 25)
    # Naive "now" is treated as UTC: 01:00 UTC == 09:00 Asia/Taipei.
    now = datetime(2026, 8, 25, 1, 0)

    session.add(
        Resource(
            vmid=301,
            user_id=student_id,
            environment_type="LXC",
            expiry_date=today,
            created_at=datetime(2026, 8, 1),
        )
    )
    session.add(
        Resource(
            vmid=302,
            user_id=student_id,
            environment_type="LXC",
            expiry_date=today + timedelta(days=1),
            created_at=datetime(2026, 8, 1),
        )
    )
    request = VMRequest(
        user_id=student_id,
        reason="lab",
        resource_type="lxc",
        hostname="rejected-lab",
        password="test-password",
        status=VMRequestStatus.rejected,
        reviewed_at=datetime(2026, 8, 24, 0, 0),
        created_at=datetime(2026, 8, 23, 0, 0),
    )
    session.add(request)

    teaching_class = TeachingClass(
        name="Linux A",
        code="linux-a",
        term="2026-1",
        owner_id=uuid.uuid4(),
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        weekday=2,
        start_time=time(9),
        end_time=time(11),
    )
    session.add(teaching_class)
    session.add(
        CoursePath(
            title="Linux",
            teaching_class_id=teaching_class.id,
            status=CoursePathStatus.published,
        )
    )
    session.add(TeachingClassStudent(class_id=teaching_class.id, user_id=student_id))
    session.add(
        TeachingClassWeek(
            class_id=teaching_class.id,
            week_number=2,
            session_date=today + timedelta(days=1),
            title="Tomorrow task",
            status="published",
        )
    )
    session.commit()

    rows = reminder_service.list_student_reminders(session, user_id=student_id, now=now)
    by_id = {row.id: row for row in rows}

    expiring_today = by_id[f"resource-expiry:301:{today.isoformat()}"]
    assert expiring_today.tone == "danger"
    assert "301" in expiring_today.title
    assert expiring_today.time_label == reminder_service._date_label(today, today)

    tomorrow = today + timedelta(days=1)
    expiring_tomorrow = by_id[f"resource-expiry:302:{tomorrow.isoformat()}"]
    assert expiring_tomorrow.tone == "danger"
    assert expiring_tomorrow.time_label != expiring_today.time_label
    assert expiring_tomorrow.time_label != f"{tomorrow.month}/{tomorrow.day}"

    review = by_id[f"request-review:{request.id}:rejected"]
    assert review.tone == "danger"
    assert review.icon == "cancel"
    assert review.time_label == "8/24"

    task = next(row for row in rows if row.kind == "class_task")
    assert task.tone == "info"
    assert task.time_label == expiring_tomorrow.time_label


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Returns a resource without expiry for the first query, nothing afterwards."""

    def __init__(self) -> None:
        self.calls = 0

    def exec(self, _statement: Any) -> _Rows:
        self.calls += 1
        if self.calls == 1:
            return _Rows([(Resource(vmid=1, user_id=uuid.uuid4()), None)])
        return _Rows([])


def test_reminders_skip_resource_without_expiry_date() -> None:
    fake = _FakeSession()
    rows = reminder_service.list_student_reminders(
        fake,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
    )
    assert rows == []
    assert fake.calls == 3
