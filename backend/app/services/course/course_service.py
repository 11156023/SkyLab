"""課程內容管理：路徑/房間/任務/題目 CRUD、發布狀態機、學生視圖組裝。

Flag 明文只在 create/update 進入，經 flag_service 雜湊後入庫；
所有輸出 schema 不含 flag_hash。
"""

import uuid

from sqlmodel import Session, func, select

from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    CoursePath,
    CoursePathStatus,
    CourseQuestion,
    CourseQuestionType,
    CourseRoom,
    CourseTask,
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
        raise NotFoundError("Course path not found")
    return path


def get_room_or_404(session: Session, room_id: uuid.UUID) -> CourseRoom:
    room = session.get(CourseRoom, room_id)
    if room is None:
        raise NotFoundError("Course room not found")
    return room


def get_task_or_404(session: Session, task_id: uuid.UUID) -> CourseTask:
    task = session.get(CourseTask, task_id)
    if task is None:
        raise NotFoundError("Course task not found")
    return task


def get_question_or_404(
    session: Session, question_id: uuid.UUID
) -> CourseQuestion:
    question = session.get(CourseQuestion, question_id)
    if question is None:
        raise NotFoundError("Course question not found")
    return question


def _touch_path(session: Session, path_id: uuid.UUID) -> None:
    path = session.get(CoursePath, path_id)
    if path is not None:
        path.updated_at = get_datetime_utc()
        session.add(path)


def _require_ready_template(session: Session, template_id: uuid.UUID) -> VMTemplate:
    template = session.get(VMTemplate, template_id)
    if template is None:
        raise BadRequestError("VM template not found")
    if template.status != VMTemplateStatus.ready:
        raise BadRequestError(
            f"VM template is not ready (now: {template.status.value})"
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
    return CoursePathPublic(
        id=path.id,
        title=path.title,
        description=path.description,
        status=path.status,
        created_by=path.created_by,
        created_at=path.created_at,
        updated_at=path.updated_at,
        room_count=_room_count(session, path.id),
    )


def list_paths(session: Session) -> list[CoursePathPublic]:
    paths = session.exec(
        select(CoursePath).order_by(CoursePath.created_at.desc())
    ).all()
    return [_path_public(session, p) for p in paths]


def create_path(
    session: Session, *, user_id: uuid.UUID, data: CoursePathCreate
) -> CoursePathPublic:
    path = CoursePath(
        title=data.title,
        description=data.description,
        created_by=user_id,
    )
    session.add(path)
    session.commit()
    session.refresh(path)
    return _path_public(session, path)


def update_path(
    session: Session, *, path_id: uuid.UUID, data: CoursePathUpdate
) -> CoursePathPublic:
    path = get_path_or_404(session, path_id)
    if data.title is not None:
        path.title = data.title
    if data.description is not None:
        path.description = data.description
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
            raise BadRequestError("Flag question requires a flag answer")
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
            raise BadRequestError("Flag cannot be blank")
        question.flag_hash = flag_service.hash_flag(data.flag)
    if question.question_type == CourseQuestionType.flag and not question.flag_hash:
        raise BadRequestError("Flag question requires a flag answer")
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
    paths = session.exec(
        select(CoursePath)
        .where(CoursePath.status == CoursePathStatus.published)
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


def get_published_path_or_404(
    session: Session, path_id: uuid.UUID
) -> CoursePath:
    path = get_path_or_404(session, path_id)
    if path.status != CoursePathStatus.published:
        raise NotFoundError("Course path not found")
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
