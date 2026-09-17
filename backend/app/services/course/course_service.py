"""課程內容管理：路徑/房間/任務/題目 CRUD、發布狀態機、學生視圖組裝。

Flag 明文只在 create/update 進入，經 flag_service 雜湊後入庫；
所有輸出 schema 不含 flag_hash。
"""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlmodel import Session, func, select

from app.core.i18n import t
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
    VMTemplate,
    VMTemplateStatus,
    get_datetime_utc,
)
from app.schemas.course import (
    CoursePathCreate,
    CoursePathDetail,
    CoursePathPublic,
    CoursePathSummary,
    CoursePathUpdate,
    CourseQuestionCreate,
    CourseQuestionPublic,
    CourseQuestionStudent,
    CourseQuestionUpdate,
    CourseRoomCreate,
    CourseRoomPublic,
    CourseRoomStudentDetail,
    CourseRoomSummary,
    CourseRoomUpdate,
    CourseScheduleStudent,
    CourseTaskCreate,
    CourseTaskPublic,
    CourseTaskStudent,
    CourseTaskUpdate,
)
from app.services.course import flag_service, progress_service

# ── 內部取用 helpers ────────────────────────────────────────────────────────


def get_path_or_404(session: Session, path_id: uuid.UUID) -> CoursePath:
    path = session.get(CoursePath, path_id)
    if path is None:
        raise NotFoundError(t("course.path_not_found"))
    return path


def get_room_or_404(session: Session, room_id: uuid.UUID) -> CourseRoom:
    room = session.get(CourseRoom, room_id)
    if room is None:
        raise NotFoundError(t("course.room_not_found"))
    return room


def get_task_or_404(session: Session, task_id: uuid.UUID) -> CourseTask:
    task = session.get(CourseTask, task_id)
    if task is None:
        raise NotFoundError(t("course.task_not_found"))
    return task


def get_question_or_404(
    session: Session, question_id: uuid.UUID
) -> CourseQuestion:
    question = session.get(CourseQuestion, question_id)
    if question is None:
        raise NotFoundError(t("course.question_not_found"))
    return question


def _touch_path(session: Session, path_id: uuid.UUID) -> None:
    path = session.get(CoursePath, path_id)
    if path is not None:
        path.updated_at = get_datetime_utc()
        session.add(path)


def _require_ready_template(session: Session, template_id: uuid.UUID) -> VMTemplate:
    template = session.get(VMTemplate, template_id)
    if template is None:
        raise BadRequestError(t("course.template_not_found"))
    if template.status != VMTemplateStatus.ready:
        raise BadRequestError(
            t("course.template_not_ready", status=template.status.value)
        )
    return template


# ── 路徑 CRUD ──────────────────────────────────────────────────────────────


def _room_count(session: Session, path_id: uuid.UUID) -> int:
    return int(
        session.exec(
            select(func.count())
            .select_from(CourseRoom)
            .where(CourseRoom.path_id == path_id)
        ).one()
    )


def _path_public(session: Session, path: CoursePath) -> CoursePathPublic:
    teaching_class = (
        session.get(TeachingClass, path.teaching_class_id)
        if path.teaching_class_id
        else None
    )
    return CoursePathPublic(
        id=path.id,
        title=path.title,
        description=path.description,
        status=path.status,
        created_by=path.created_by,
        created_at=path.created_at,
        updated_at=path.updated_at,
        room_count=_room_count(session, path.id),
        teaching_class_id=path.teaching_class_id,
        teaching_class_name=teaching_class.name if teaching_class else None,
    )


def list_paths(
    session: Session, *, owner_id: uuid.UUID | None = None
) -> list[CoursePathPublic]:
    statement = select(CoursePath)
    if owner_id is not None:
        statement = statement.where(CoursePath.created_by == owner_id)
    paths = session.exec(
        statement.order_by(CoursePath.created_at.desc())
    ).all()
    return [_path_public(session, p) for p in paths]


def create_path(
    session: Session, *, user_id: uuid.UUID, data: CoursePathCreate
) -> CoursePathPublic:
    _ensure_class_available(session, data.teaching_class_id)
    path = CoursePath(
        title=data.title,
        description=data.description,
        created_by=user_id,
        teaching_class_id=data.teaching_class_id,
    )
    session.add(path)
    session.commit()
    session.refresh(path)
    return _path_public(session, path)


def ensure_class_path(
    session: Session,
    *,
    teaching_class: TeachingClass,
    published: bool = False,
) -> CoursePath:
    """Ensure every formal class has a student-facing course shell.

    The shell exists independently from rooms, tasks and checkpoints.  This
    keeps an active class visible to enrolled students even when the teacher
    has not authored any learning content yet.
    """

    path = session.exec(
        select(CoursePath).where(
            CoursePath.teaching_class_id == teaching_class.id
        )
    ).first()
    if path is None:
        path = CoursePath(
            title=teaching_class.name,
            description=None,
            created_by=teaching_class.owner_id,
            teaching_class_id=teaching_class.id,
            status=(
                CoursePathStatus.published
                if published
                else CoursePathStatus.draft
            ),
        )
    elif published:
        path.status = CoursePathStatus.published
        path.updated_at = get_datetime_utc()
    session.add(path)
    session.flush()
    return path


def update_path(
    session: Session, *, path_id: uuid.UUID, data: CoursePathUpdate
) -> CoursePathPublic:
    path = get_path_or_404(session, path_id)
    if data.title is not None:
        path.title = data.title
    if data.description is not None:
        path.description = data.description
    if "teaching_class_id" in data.model_fields_set:
        _ensure_class_available(
            session,
            data.teaching_class_id,
            excluding_path_id=path.id,
        )
        path.teaching_class_id = data.teaching_class_id
    path.updated_at = get_datetime_utc()
    session.add(path)
    session.commit()
    session.refresh(path)
    return _path_public(session, path)


def set_path_published(
    session: Session, *, path_id: uuid.UUID, published: bool
) -> CoursePathPublic:
    path = get_path_or_404(session, path_id)
    path.status = (
        CoursePathStatus.published if published else CoursePathStatus.draft
    )
    path.updated_at = get_datetime_utc()
    session.add(path)
    session.commit()
    session.refresh(path)
    return _path_public(session, path)


def delete_path(session: Session, *, path_id: uuid.UUID) -> None:
    path = get_path_or_404(session, path_id)
    session.delete(path)
    session.commit()


def _ensure_class_available(
    session: Session,
    teaching_class_id: uuid.UUID | None,
    *,
    excluding_path_id: uuid.UUID | None = None,
) -> None:
    """Keep the one-class-to-one-path relationship understandable before commit."""

    if teaching_class_id is None:
        return
    statement = select(CoursePath).where(
        CoursePath.teaching_class_id == teaching_class_id
    )
    if excluding_path_id is not None:
        statement = statement.where(CoursePath.id != excluding_path_id)
    if session.exec(statement).first() is not None:
        raise BadRequestError(t("course.class_already_linked"))


# ── 房間 CRUD ──────────────────────────────────────────────────────────────


def _task_count(session: Session, room_id: uuid.UUID) -> int:
    return int(
        session.exec(
            select(func.count())
            .select_from(CourseTask)
            .where(CourseTask.room_id == room_id)
        ).one()
    )


def _room_public(session: Session, room: CourseRoom) -> CourseRoomPublic:
    template_name = None
    if room.template_id is not None:
        template = session.get(VMTemplate, room.template_id)
        template_name = template.name if template else None
    return CourseRoomPublic(
        id=room.id,
        path_id=room.path_id,
        title=room.title,
        description=room.description,
        difficulty=room.difficulty,
        category=room.category,
        template_id=room.template_id,
        template_name=template_name,
        order=room.order,
        task_count=_task_count(session, room.id),
    )


def list_rooms(session: Session, *, path_id: uuid.UUID) -> list[CourseRoomPublic]:
    get_path_or_404(session, path_id)
    rooms = session.exec(
        select(CourseRoom)
        .where(CourseRoom.path_id == path_id)
        .order_by(CourseRoom.order, CourseRoom.title)
    ).all()
    return [_room_public(session, r) for r in rooms]


def create_room(
    session: Session, *, data: CourseRoomCreate
) -> CourseRoomPublic:
    get_path_or_404(session, data.path_id)
    if data.template_id is not None:
        _require_ready_template(session, data.template_id)
    room = CourseRoom(
        path_id=data.path_id,
        title=data.title,
        description=data.description,
        difficulty=data.difficulty,
        category=data.category,
        template_id=data.template_id,
        order=data.order,
    )
    session.add(room)
    _touch_path(session, data.path_id)
    session.commit()
    session.refresh(room)
    return _room_public(session, room)


def update_room(
    session: Session, *, room_id: uuid.UUID, data: CourseRoomUpdate
) -> CourseRoomPublic:
    room = get_room_or_404(session, room_id)
    if data.title is not None:
        room.title = data.title
    if data.description is not None:
        room.description = data.description
    if data.difficulty is not None:
        room.difficulty = data.difficulty
    if data.category is not None:
        room.category = data.category
    if data.clear_template:
        room.template_id = None
    elif data.template_id is not None:
        _require_ready_template(session, data.template_id)
        room.template_id = data.template_id
    if data.order is not None:
        room.order = data.order
    session.add(room)
    _touch_path(session, room.path_id)
    session.commit()
    session.refresh(room)
    return _room_public(session, room)


def delete_room(session: Session, *, room_id: uuid.UUID) -> None:
    room = get_room_or_404(session, room_id)
    path_id = room.path_id
    session.delete(room)
    _touch_path(session, path_id)
    session.commit()


# ── 任務 CRUD ──────────────────────────────────────────────────────────────


def _task_public(task: CourseTask) -> CourseTaskPublic:
    return CourseTaskPublic(
        id=task.id,
        room_id=task.room_id,
        title=task.title,
        content=task.content,
        order=task.order,
    )


def list_tasks(session: Session, *, room_id: uuid.UUID) -> list[CourseTaskPublic]:
    get_room_or_404(session, room_id)
    tasks = session.exec(
        select(CourseTask)
        .where(CourseTask.room_id == room_id)
        .order_by(CourseTask.order, CourseTask.title)
    ).all()
    return [_task_public(t) for t in tasks]


def create_task(session: Session, *, data: CourseTaskCreate) -> CourseTaskPublic:
    room = get_room_or_404(session, data.room_id)
    task = CourseTask(
        room_id=data.room_id,
        title=data.title,
        content=data.content,
        order=data.order,
    )
    session.add(task)
    _touch_path(session, room.path_id)
    session.commit()
    session.refresh(task)
    return _task_public(task)


def update_task(
    session: Session, *, task_id: uuid.UUID, data: CourseTaskUpdate
) -> CourseTaskPublic:
    task = get_task_or_404(session, task_id)
    if data.title is not None:
        task.title = data.title
    if data.content is not None:
        task.content = data.content
    if data.order is not None:
        task.order = data.order
    session.add(task)
    session.commit()
    session.refresh(task)
    return _task_public(task)


def delete_task(session: Session, *, task_id: uuid.UUID) -> None:
    task = get_task_or_404(session, task_id)
    session.delete(task)
    session.commit()


# ── 題目 CRUD ──────────────────────────────────────────────────────────────


def _question_public(question: CourseQuestion) -> CourseQuestionPublic:
    return CourseQuestionPublic(
        id=question.id,
        task_id=question.task_id,
        prompt=question.prompt,
        question_type=question.question_type,
        points=question.points,
        order=question.order,
    )


def list_questions(
    session: Session, *, task_id: uuid.UUID
) -> list[CourseQuestionPublic]:
    get_task_or_404(session, task_id)
    questions = session.exec(
        select(CourseQuestion)
        .where(CourseQuestion.task_id == task_id)
        .order_by(CourseQuestion.order)
    ).all()
    return [_question_public(q) for q in questions]


def create_question(
    session: Session, *, data: CourseQuestionCreate
) -> CourseQuestionPublic:
    get_task_or_404(session, data.task_id)
    flag_hash: str | None = None
    if data.question_type == CourseQuestionType.flag:
        if not data.flag or not data.flag.strip():
            raise BadRequestError(t("course.flag_required"))
        flag_hash = flag_service.hash_flag(data.flag)
    question = CourseQuestion(
        task_id=data.task_id,
        prompt=data.prompt,
        question_type=data.question_type,
        flag_hash=flag_hash,
        points=data.points,
        order=data.order,
    )
    session.add(question)
    session.commit()
    session.refresh(question)
    return _question_public(question)


def update_question(
    session: Session, *, question_id: uuid.UUID, data: CourseQuestionUpdate
) -> CourseQuestionPublic:
    question = get_question_or_404(session, question_id)
    if data.prompt is not None:
        question.prompt = data.prompt
    if data.question_type is not None:
        question.question_type = data.question_type
    if data.flag is not None:
        if not data.flag.strip():
            raise BadRequestError(t("course.flag_blank"))
        question.flag_hash = flag_service.hash_flag(data.flag)
    if question.question_type == CourseQuestionType.flag and not question.flag_hash:
        raise BadRequestError(t("course.flag_required"))
    if question.question_type == CourseQuestionType.no_answer:
        question.flag_hash = None
    if data.points is not None:
        question.points = data.points
    if data.order is not None:
        question.order = data.order
    session.add(question)
    session.commit()
    session.refresh(question)
    return _question_public(question)


def delete_question(session: Session, *, question_id: uuid.UUID) -> None:
    question = get_question_or_404(session, question_id)
    session.delete(question)
    session.commit()


# ── 學生視圖 ────────────────────────────────────────────────────────────────


def list_published_paths(
    session: Session, *, user_id: uuid.UUID
) -> list[CoursePathSummary]:
    """List published courses belonging to the student's active enrollments."""
    paths = session.exec(
        select(CoursePath)
        .join(
            TeachingClassStudent,
            TeachingClassStudent.class_id == CoursePath.teaching_class_id,
        )
        .where(
            CoursePath.status == CoursePathStatus.published,
            TeachingClassStudent.user_id == user_id,
            TeachingClassStudent.status == "active",
        )
        .order_by(CoursePath.created_at.desc())
    ).all()
    summaries: list[CoursePathSummary] = []
    for path in paths:
        total, completed = progress_service.path_question_counts(
            session, path_id=path.id, user_id=user_id
        )
        summaries.append(
            CoursePathSummary(
                id=path.id,
                title=path.title,
                description=path.description,
                room_count=_room_count(session, path.id),
                total_questions=total,
                completed_questions=completed,
                progress_percent=flag_service.progress_percent(completed, total),
            )
        )
    return summaries


def _class_timezone(item: TeachingClass) -> ZoneInfo:
    try:
        return ZoneInfo(item.timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Taipei")


def get_student_class_for_path(
    session: Session,
    *,
    user_id: uuid.UUID,
    path_id: uuid.UUID,
) -> TeachingClass | None:
    """Resolve the exact active class enrollment linked to one published path."""

    path = get_published_path_or_404(session, path_id)
    if path.teaching_class_id is None:
        return None
    return session.exec(
        select(TeachingClass)
        .join(
            TeachingClassStudent,
            TeachingClassStudent.class_id == TeachingClass.id,
        )
        .where(
            TeachingClass.id == path.teaching_class_id,
            TeachingClassStudent.user_id == user_id,
            TeachingClassStudent.status == "active",
        )
    ).first()


def list_student_schedule(
    session: Session,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> list[CourseScheduleStudent]:
    """Return every active-term class, highlighting today's real session."""

    current = now or datetime.now(UTC)
    rows = session.exec(
        select(CoursePath, TeachingClass)
        .join(TeachingClass, CoursePath.teaching_class_id == TeachingClass.id)
        .join(
            TeachingClassStudent,
            TeachingClassStudent.class_id == TeachingClass.id,
        )
        .where(
            CoursePath.status == CoursePathStatus.published,
            TeachingClass.status == TeachingClassStatus.active,
            TeachingClassStudent.user_id == user_id,
            TeachingClassStudent.status == "active",
        )
    ).all()

    teacher_ids = {teaching_class.owner_id for _, teaching_class in rows}
    teachers = {
        teacher.id: teacher
        for teacher in session.exec(select(User).where(User.id.in_(teacher_ids))).all()
    } if teacher_ids else {}
    result: list[CourseScheduleStudent] = []
    for path, teaching_class in rows:
        timezone = _class_timezone(teaching_class)
        local_now = current.astimezone(timezone)
        local_date = local_now.date()
        if not teaching_class.start_date <= local_date <= teaching_class.end_date:
            continue
        days_until_session = (teaching_class.weekday - local_date.weekday()) % 7
        session_date = local_date + timedelta(days=days_until_session)
        if session_date > teaching_class.end_date:
            days_since_session = (local_date.weekday() - teaching_class.weekday) % 7
            session_date = local_date - timedelta(days=days_since_session)
        starts_at = datetime.combine(
            session_date,
            teaching_class.start_time,
            tzinfo=timezone,
        )
        ends_at = datetime.combine(
            session_date,
            teaching_class.end_time,
            tzinfo=timezone,
        )
        if session_date != local_date:
            state, label = "available", "可課後練習"
        elif local_now < starts_at:
            state, label = "later", "今天稍後"
        elif local_now <= ends_at:
            state, label = "now", "正在上課"
        else:
            state, label = "ended", "今天已結束"
        total, completed = progress_service.path_question_counts(
            session,
            path_id=path.id,
            user_id=user_id,
        )
        teacher = teachers.get(teaching_class.owner_id)
        result.append(
            CourseScheduleStudent(
                id=path.id,
                title=path.title,
                description=path.description,
                room_count=_room_count(session, path.id),
                total_questions=total,
                completed_questions=completed,
                progress_percent=flag_service.progress_percent(completed, total),
                teaching_class_id=teaching_class.id,
                teaching_class_name=teaching_class.name,
                session_date=session_date,
                start_at=starts_at,
                end_at=ends_at,
                teacher=(teacher.full_name or teacher.email) if teacher else "授課老師",
                location=teaching_class.location,
                state=state,  # type: ignore[arg-type]
                label=label,
            )
        )
    state_order = {"now": 0, "later": 1, "available": 2, "ended": 3}
    return sorted(result, key=lambda row: (state_order[row.state], row.start_at))


def get_published_path_or_404(
    session: Session, path_id: uuid.UUID
) -> CoursePath:
    path = get_path_or_404(session, path_id)
    if path.status != CoursePathStatus.published:
        raise NotFoundError(t("course.path_not_found"))
    return path


def get_path_detail(
    session: Session, *, user_id: uuid.UUID, path_id: uuid.UUID
) -> CoursePathDetail:
    path = get_published_path_or_404(session, path_id)
    rooms = session.exec(
        select(CourseRoom)
        .where(CourseRoom.path_id == path_id)
        .order_by(CourseRoom.order, CourseRoom.title)
    ).all()
    room_summaries: list[CourseRoomSummary] = []
    for room in rooms:
        total, completed = progress_service.room_question_counts(
            session, room_id=room.id, user_id=user_id
        )
        room_summaries.append(
            CourseRoomSummary(
                id=room.id,
                title=room.title,
                description=room.description,
                difficulty=room.difficulty,
                category=room.category,
                has_lab=room.template_id is not None,
                order=room.order,
                total_questions=total,
                completed_questions=completed,
                progress_percent=flag_service.progress_percent(completed, total),
            )
        )
    return CoursePathDetail(
        id=path.id,
        title=path.title,
        description=path.description,
        rooms=room_summaries,
    )


def get_room_student_detail(
    session: Session, *, user_id: uuid.UUID, room_id: uuid.UUID
) -> CourseRoomStudentDetail:
    """學生房間視圖：任務 + 題目（不含 flag_hash）+ 完成標記。

    my_deployment 由 route 層以 deployment_service 另行注入。
    """
    room = get_room_or_404(session, room_id)
    get_published_path_or_404(session, room.path_id)

    completed_ids = progress_service.completed_question_ids_in_room(
        session, room_id=room_id, user_id=user_id
    )
    tasks = session.exec(
        select(CourseTask)
        .where(CourseTask.room_id == room_id)
        .order_by(CourseTask.order, CourseTask.title)
    ).all()
    task_views: list[CourseTaskStudent] = []
    for task in tasks:
        questions = session.exec(
            select(CourseQuestion)
            .where(CourseQuestion.task_id == task.id)
            .order_by(CourseQuestion.order)
        ).all()
        task_views.append(
            CourseTaskStudent(
                id=task.id,
                title=task.title,
                content=task.content,
                order=task.order,
                questions=[
                    CourseQuestionStudent(
                        id=q.id,
                        prompt=q.prompt,
                        question_type=q.question_type,
                        points=q.points,
                        order=q.order,
                        completed=q.id in completed_ids,
                    )
                    for q in questions
                ],
            )
        )
    return CourseRoomStudentDetail(
        id=room.id,
        path_id=room.path_id,
        title=room.title,
        description=room.description,
        difficulty=room.difficulty,
        category=room.category,
        has_lab=room.template_id is not None,
        tasks=task_views,
        my_deployment=None,
    )
