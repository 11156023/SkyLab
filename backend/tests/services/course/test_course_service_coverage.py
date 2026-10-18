"""Coverage tests for app.services.course.course_service (CRUD, publishing, student views)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    CoursePath,
    CoursePathStatus,
    CourseQuestion,
    CourseQuestionType,
    CourseRoom,
    CourseTask,
    TeachingClass,
    TeachingClassStatus,
    TeachingClassStudent,
    User,
    UserRole,
)
from app.models.course import CourseDifficulty
from app.schemas.course import (
    CoursePathCreate,
    CoursePathUpdate,
    CourseQuestionCreate,
    CourseQuestionUpdate,
    CourseRoomCreate,
    CourseRoomUpdate,
    CourseTaskCreate,
    CourseTaskUpdate,
)
from app.services.course import course_service, flag_service


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _user(session: Session, email: str, role: UserRole = UserRole.student) -> User:
    user = User(email=email, role=role, full_name=None, hashed_password="x")
    session.add(user)
    session.commit()
    return user


def _class(
    session: Session,
    *,
    owner_id: uuid.UUID,
    code: str,
    start_date: date,
    end_date: date,
    weekday: int,
    timezone: str = "Asia/Taipei",
    name: str = "Class",
) -> TeachingClass:
    item = TeachingClass(
        name=name,
        code=code,
        term="2026-1",
        location="Room A",
        owner_id=owner_id,
        start_date=start_date,
        end_date=end_date,
        weekday=weekday,
        start_time=time(9),
        end_time=time(11),
        timezone=timezone,
        status=TeachingClassStatus.active,
    )
    session.add(item)
    session.commit()
    return item


def _enroll(session: Session, teaching_class: TeachingClass, user: User) -> None:
    session.add(TeachingClassStudent(class_id=teaching_class.id, user_id=user.id))
    session.commit()


def _published_path(
    session: Session, *, owner: User, teaching_class: TeachingClass | None
) -> CoursePath:
    path = CoursePath(
        title="Path",
        created_by=owner.id,
        teaching_class_id=teaching_class.id if teaching_class else None,
        status=CoursePathStatus.published,
    )
    session.add(path)
    session.commit()
    return path


# ── get_*_or_404 / _touch_path ─────────────────────────────────────────────


def test_get_or_404_helpers_raise_for_missing_rows(session: Session) -> None:
    missing = uuid.uuid4()
    with pytest.raises(NotFoundError):
        course_service.get_path_or_404(session, missing)
    with pytest.raises(NotFoundError):
        course_service.get_room_or_404(session, missing)
    with pytest.raises(NotFoundError):
        course_service.get_task_or_404(session, missing)
    with pytest.raises(NotFoundError):
        course_service.get_question_or_404(session, missing)
    # Touching a missing path is a no-op.
    course_service._touch_path(session, missing)


# ── Path CRUD ──────────────────────────────────────────────────────────────


def test_path_crud_lifecycle(session: Session) -> None:
    teacher = _user(session, "t@example.edu", UserRole.teacher)
    other = _user(session, "o@example.edu", UserRole.teacher)
    cls = _class(
        session,
        owner_id=teacher.id,
        code="c1",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        weekday=0,
        name="Linux",
    )

    created = course_service.create_path(
        session,
        user_id=teacher.id,
        data=CoursePathCreate(
            title="Linux Path", description="d", teaching_class_id=cls.id
        ),
    )
    assert created.teaching_class_name == "Linux"
    assert created.room_count == 0
    assert created.status == CoursePathStatus.draft

    unlinked = course_service.create_path(
        session, user_id=other.id, data=CoursePathCreate(title="Free")
    )
    assert unlinked.teaching_class_name is None

    # Linking a second path to the same class is rejected.
    with pytest.raises(BadRequestError):
        course_service.create_path(
            session,
            user_id=teacher.id,
            data=CoursePathCreate(title="Dup", teaching_class_id=cls.id),
        )

    all_paths = course_service.list_paths(session)
    assert {p.id for p in all_paths} == {created.id, unlinked.id}
    own = course_service.list_paths(session, owner_id=teacher.id)
    assert [p.id for p in own] == [created.id]

    updated = course_service.update_path(
        session,
        path_id=created.id,
        data=CoursePathUpdate(title="Renamed", description="new"),
    )
    assert updated.title == "Renamed"
    assert updated.description == "new"
    assert updated.teaching_class_id == cls.id

    # Re-asserting the same class is allowed (excluding itself).
    same = course_service.update_path(
        session,
        path_id=created.id,
        data=CoursePathUpdate(teaching_class_id=cls.id),
    )
    assert same.teaching_class_id == cls.id

    # The other path cannot steal the linked class.
    with pytest.raises(BadRequestError):
        course_service.update_path(
            session,
            path_id=unlinked.id,
            data=CoursePathUpdate(teaching_class_id=cls.id),
        )

    # Explicitly unlinking via None.
    unlinked_now = course_service.update_path(
        session,
        path_id=created.id,
        data=CoursePathUpdate(teaching_class_id=None),
    )
    assert unlinked_now.teaching_class_id is None

    published = course_service.set_path_published(
        session, path_id=created.id, published=True
    )
    assert published.status == CoursePathStatus.published
    drafted = course_service.set_path_published(
        session, path_id=created.id, published=False
    )
    assert drafted.status == CoursePathStatus.draft

    course_service.delete_path(session, path_id=unlinked.id)
    assert session.get(CoursePath, unlinked.id) is None


def test_ensure_class_path_creates_and_publishes(session: Session) -> None:
    teacher = _user(session, "t@example.edu", UserRole.teacher)
    cls = _class(
        session,
        owner_id=teacher.id,
        code="c1",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        weekday=0,
    )
    draft = course_service.ensure_class_path(session, teaching_class=cls)
    assert draft.status == CoursePathStatus.draft
    again = course_service.ensure_class_path(session, teaching_class=cls)
    assert again.id == draft.id
    assert again.status == CoursePathStatus.draft
    published = course_service.ensure_class_path(
        session, teaching_class=cls, published=True
    )
    assert published.id == draft.id
    assert published.status == CoursePathStatus.published

    cls2 = _class(
        session,
        owner_id=teacher.id,
        code="c2",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        weekday=0,
    )
    fresh = course_service.ensure_class_path(
        session, teaching_class=cls2, published=True
    )
    assert fresh.status == CoursePathStatus.published


# ── Room / task / question CRUD ───────────────────────────────────────────


def test_room_task_question_crud(session: Session) -> None:
    teacher = _user(session, "t@example.edu", UserRole.teacher)
    path = course_service.create_path(
        session, user_id=teacher.id, data=CoursePathCreate(title="P")
    )

    with pytest.raises(NotFoundError):
        course_service.list_rooms(session, path_id=uuid.uuid4())
    with pytest.raises(NotFoundError):
        course_service.create_room(
            session, data=CourseRoomCreate(path_id=uuid.uuid4(), title="R")
        )

    room = course_service.create_room(
        session,
        data=CourseRoomCreate(path_id=path.id, title="Room B", order=2),
    )
    room_a = course_service.create_room(
        session,
        data=CourseRoomCreate(path_id=path.id, title="Room A", order=1),
    )
    assert room.task_count == 0
    listed = course_service.list_rooms(session, path_id=path.id)
    assert [r.id for r in listed] == [room_a.id, room.id]

    updated_room = course_service.update_room(
        session,
        room_id=room.id,
        data=CourseRoomUpdate(
            title="Room B2",
            description="desc",
            difficulty=CourseDifficulty.hard,
            category="net",
            order=5,
        ),
    )
    assert updated_room.title == "Room B2"
    assert updated_room.difficulty == CourseDifficulty.hard
    assert updated_room.category == "net"
    assert updated_room.order == 5
    untouched = course_service.update_room(
        session, room_id=room.id, data=CourseRoomUpdate()
    )
    assert untouched.title == "Room B2"

    # Tasks
    with pytest.raises(NotFoundError):
        course_service.list_tasks(session, room_id=uuid.uuid4())
    task = course_service.create_task(
        session, data=CourseTaskCreate(room_id=room.id, title="T1", content="c")
    )
    assert course_service.list_tasks(session, room_id=room.id)[0].id == task.id
    assert course_service.list_rooms(session, path_id=path.id)[1].task_count == 1
    updated_task = course_service.update_task(
        session,
        task_id=task.id,
        data=CourseTaskUpdate(title="T2", content="c2", order=3),
    )
    assert (updated_task.title, updated_task.content, updated_task.order) == (
        "T2",
        "c2",
        3,
    )
    same_task = course_service.update_task(
        session, task_id=task.id, data=CourseTaskUpdate()
    )
    assert same_task.title == "T2"

    # Questions
    with pytest.raises(NotFoundError):
        course_service.list_questions(session, task_id=uuid.uuid4())
    with pytest.raises(BadRequestError):
        course_service.create_question(
            session,
            data=CourseQuestionCreate(task_id=task.id, prompt="q", flag=None),
        )
    with pytest.raises(BadRequestError):
        course_service.create_question(
            session,
            data=CourseQuestionCreate(task_id=task.id, prompt="q", flag="   "),
        )
    flag_q = course_service.create_question(
        session,
        data=CourseQuestionCreate(task_id=task.id, prompt="flag?", flag="FLAG{x}"),
    )
    stored = session.get(CourseQuestion, flag_q.id)
    assert stored is not None
    assert stored.flag_hash == flag_service.hash_flag("FLAG{x}")
    read_q = course_service.create_question(
        session,
        data=CourseQuestionCreate(
            task_id=task.id,
            prompt="read",
            question_type=CourseQuestionType.no_answer,
            order=1,
        ),
    )
    stored_read = session.get(CourseQuestion, read_q.id)
    assert stored_read is not None
    assert stored_read.flag_hash is None
    listed_q = course_service.list_questions(session, task_id=task.id)
    assert [q.id for q in listed_q] == [flag_q.id, read_q.id]

    # update_question: blank flag rejected
    with pytest.raises(BadRequestError):
        course_service.update_question(
            session, question_id=flag_q.id, data=CourseQuestionUpdate(flag="  ")
        )
    session.rollback()
    # switching a no_answer question to flag without a flag is rejected
    with pytest.raises(BadRequestError):
        course_service.update_question(
            session,
            question_id=read_q.id,
            data=CourseQuestionUpdate(question_type=CourseQuestionType.flag),
        )
    session.rollback()
    # full update with a new flag
    changed = course_service.update_question(
        session,
        question_id=flag_q.id,
        data=CourseQuestionUpdate(prompt="new?", flag="FLAG{y}", points=50, order=9),
    )
    assert (changed.prompt, changed.points, changed.order) == ("new?", 50, 9)
    refreshed = session.get(CourseQuestion, flag_q.id)
    assert refreshed is not None
    assert refreshed.flag_hash == flag_service.hash_flag("FLAG{y}")
    # switching to no_answer clears the hash
    to_read = course_service.update_question(
        session,
        question_id=flag_q.id,
        data=CourseQuestionUpdate(question_type=CourseQuestionType.no_answer),
    )
    assert to_read.question_type == CourseQuestionType.no_answer
    cleared = session.get(CourseQuestion, flag_q.id)
    assert cleared is not None
    assert cleared.flag_hash is None

    # deletions
    course_service.delete_question(session, question_id=read_q.id)
    assert session.get(CourseQuestion, read_q.id) is None
    course_service.delete_task(session, task_id=task.id)
    assert session.get(CourseTask, task.id) is None
    course_service.delete_room(session, room_id=room_a.id)
    assert session.get(CourseRoom, room_a.id) is None


# ── Student views ─────────────────────────────────────────────────────────


def test_get_student_class_for_path(session: Session) -> None:
    teacher = _user(session, "t@example.edu", UserRole.teacher)
    student = _user(session, "s@example.edu")
    unlinked = _published_path(session, owner=teacher, teaching_class=None)
    assert (
        course_service.get_student_class_for_path(
            session, user_id=student.id, path_id=unlinked.id
        )
        is None
    )
    cls = _class(
        session,
        owner_id=teacher.id,
        code="c1",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        weekday=0,
    )
    _enroll(session, cls, student)
    linked = _published_path(session, owner=teacher, teaching_class=cls)
    found = course_service.get_student_class_for_path(
        session, user_id=student.id, path_id=linked.id
    )
    assert found is not None and found.id == cls.id

    summaries = course_service.list_published_paths(session, user_id=student.id)
    assert [s.id for s in summaries] == [linked.id]
    assert summaries[0].total_questions == 0
    assert summaries[0].progress_percent == 0.0


def test_get_published_path_and_enrollment_guards(session: Session) -> None:
    teacher = _user(session, "t@example.edu", UserRole.teacher)
    student = _user(session, "s@example.edu")
    draft = CoursePath(title="Draft", created_by=teacher.id)
    session.add(draft)
    session.commit()
    with pytest.raises(NotFoundError):
        course_service.get_published_path_or_404(session, draft.id)

    unlinked = _published_path(session, owner=teacher, teaching_class=None)
    course_service.require_path_enrollment(session, path=unlinked, user_id=student.id)

    cls = _class(
        session,
        owner_id=teacher.id,
        code="c1",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        weekday=0,
    )
    linked = _published_path(session, owner=teacher, teaching_class=cls)
    with pytest.raises(NotFoundError):
        course_service.require_path_enrollment(session, path=linked, user_id=student.id)
    _enroll(session, cls, student)
    course_service.require_path_enrollment(session, path=linked, user_id=student.id)


def test_list_student_schedule_states(session: Session) -> None:
    teacher = _user(session, "t@example.edu", UserRole.teacher)
    student = _user(session, "s@example.edu")
    today = date(2026, 8, 26)
    # 00:00 UTC == 08:00 Asia/Taipei: before a 09:00 session today.
    morning = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)

    later_cls = _class(
        session,
        owner_id=teacher.id,
        code="later",
        start_date=today - timedelta(days=7),
        end_date=today + timedelta(days=30),
        weekday=today.weekday(),
    )
    # Ends today; its weekday was yesterday, so next session is past end_date
    # and the schedule falls back to the most recent session.
    past_cls = _class(
        session,
        owner_id=teacher.id,
        code="past",
        start_date=today - timedelta(days=30),
        end_date=today,
        weekday=(today.weekday() - 1) % 7,
    )
    # Term has not started yet → skipped.
    future_cls = _class(
        session,
        owner_id=teacher.id,
        code="future",
        start_date=today + timedelta(days=10),
        end_date=today + timedelta(days=60),
        weekday=today.weekday(),
    )
    # Unknown timezone + owner without a user row → teacher fallback text.
    orphan_cls = _class(
        session,
        owner_id=uuid.uuid4(),
        code="orphan",
        start_date=today - timedelta(days=7),
        end_date=today + timedelta(days=30),
        weekday=today.weekday(),
        timezone="Not/AZone",
    )
    for cls in (later_cls, past_cls, future_cls, orphan_cls):
        _enroll(session, cls, student)
        _published_path(session, owner=teacher, teaching_class=cls)

    rows = course_service.list_student_schedule(
        session, user_id=student.id, now=morning
    )
    by_class = {row.teaching_class_id: row for row in rows}
    assert future_cls.id not in by_class
    assert by_class[later_cls.id].state == "later"
    assert by_class[orphan_cls.id].state == "later"
    assert by_class[past_cls.id].state == "available"
    assert by_class[past_cls.id].session_date == today - timedelta(days=1)
    assert by_class[later_cls.id].teacher == "t@example.edu"
    assert by_class[orphan_cls.id].teacher not in {"", "t@example.edu"}

    # 04:00 UTC == 12:00 Asia/Taipei: today's session already ended.
    afternoon = datetime(2026, 8, 26, 4, 0, tzinfo=UTC)
    rows = course_service.list_student_schedule(
        session, user_id=student.id, now=afternoon
    )
    states = {row.teaching_class_id: row.state for row in rows}
    assert states[later_cls.id] == "ended"

    # 02:00 UTC == 10:00 Asia/Taipei: in session.
    rows = course_service.list_student_schedule(
        session, user_id=student.id, now=datetime(2026, 8, 26, 2, 0, tzinfo=UTC)
    )
    assert {row.teaching_class_id: row.state for row in rows}[later_cls.id] == "now"
    assert rows[0].state == "now"
