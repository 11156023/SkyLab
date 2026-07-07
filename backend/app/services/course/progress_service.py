"""答題提交與進度統計。

進度只記在 question 層（UserCourseProgress 一列 = 一題完成）；
任務/房間/路徑百分比皆為即時衍生查詢。
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
    User,
    UserCourseProgress,
)
from app.schemas.course import (
    CourseAnswerResult,
    PathProgressReport,
    StudentPathProgress,
    StudentRoomProgress,
)
from app.services.course import flag_service
from app.services.user import audit_service

# ── 計數查詢 ────────────────────────────────────────────────────────────────


def _questions_in_room_query(room_id: uuid.UUID):
    return (
        select(CourseQuestion.id)
        .join(CourseTask, CourseQuestion.task_id == CourseTask.id)
        .where(CourseTask.room_id == room_id)
    )


def _questions_in_path_query(path_id: uuid.UUID):
    return (
        select(CourseQuestion.id)
        .join(CourseTask, CourseQuestion.task_id == CourseTask.id)
        .join(CourseRoom, CourseTask.room_id == CourseRoom.id)
        .where(CourseRoom.path_id == path_id)
    )


def room_question_counts(
    session: Session, *, room_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[int, int]:
    """(房間題目總數, 該學生已完成數)"""
    question_ids = session.exec(_questions_in_room_query(room_id)).all()
    if not question_ids:
        return 0, 0
    completed = int(
        session.exec(
            select(func.count())
            .select_from(UserCourseProgress)
            .where(
                UserCourseProgress.user_id == user_id,
                UserCourseProgress.question_id.in_(question_ids),
            )
        ).one()
    )
    return len(question_ids), completed


def path_question_counts(
    session: Session, *, path_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[int, int]:
    """(路徑題目總數, 該學生已完成數)"""
    question_ids = session.exec(_questions_in_path_query(path_id)).all()
    if not question_ids:
        return 0, 0
    completed = int(
        session.exec(
            select(func.count())
            .select_from(UserCourseProgress)
            .where(
                UserCourseProgress.user_id == user_id,
                UserCourseProgress.question_id.in_(question_ids),
            )
        ).one()
    )
    return len(question_ids), completed


def completed_question_ids_in_room(
    session: Session, *, room_id: uuid.UUID, user_id: uuid.UUID
) -> set[uuid.UUID]:
    rows = session.exec(
        select(UserCourseProgress.question_id)
        .join(
            CourseQuestion,
            UserCourseProgress.question_id == CourseQuestion.id,
        )
        .join(CourseTask, CourseQuestion.task_id == CourseTask.id)
        .where(
            CourseTask.room_id == room_id,
            UserCourseProgress.user_id == user_id,
        )
    ).all()
    return set(rows)


# ── 答題提交 ────────────────────────────────────────────────────────────────


def submit_answer(
    session: Session,
    *,
    user,
    question_id: uuid.UUID,
    answer: str | None,
) -> tuple[CourseAnswerResult, uuid.UUID | None, dict | None]:
    """提交答案。回傳 (結果, path_id, 推播事件)。

    - no_answer 題型：直接記完成（answer 忽略）
    - flag 題型：正規化 + SHA-256 常數時間比對
    - 已完成的題目重複提交：冪等，直接回 correct=True
    - 答錯僅回 correct=False，不記錄進度；提交行為一律寫 audit log
    - 推播事件僅在「新完成一題」時產生（答錯/重複完成為 None）
    """
    question = session.get(CourseQuestion, question_id)
    if question is None:
        raise NotFoundError("Course question not found")

    task = session.get(CourseTask, question.task_id)
    if task is None:
        raise NotFoundError("Course task not found")
    room = session.get(CourseRoom, task.room_id)
    if room is None:
        raise NotFoundError("Course room not found")
    path = session.get(CoursePath, room.path_id)
    if path is None or path.status != CoursePathStatus.published:
        raise NotFoundError("Course path not found")

    already = session.exec(
        select(UserCourseProgress).where(
            UserCourseProgress.user_id == user.id,
            UserCourseProgress.question_id == question_id,
        )
    ).first()

    if question.question_type == CourseQuestionType.flag:
        correct = flag_service.verify_flag(answer, question.flag_hash)
    elif question.question_type == CourseQuestionType.no_answer:
        correct = True
    else:  # pragma: no cover — enum 目前僅兩型
        raise BadRequestError("Unsupported question type")

    newly_completed = False
    if correct and already is None:
        session.add(
            UserCourseProgress(user_id=user.id, question_id=question_id)
        )
        newly_completed = True
    elif already is not None:
        correct = True  # 已完成過 → 冪等視為正確

    audit_service.log_action(
        session=session,
        user_id=user.id,
        action="course_answer_submit",
        details=(
            f"Course answer submit: question={question_id} "
            f"type={question.question_type.value} correct={correct}"
        ),
        commit=False,
    )
    session.commit()

    total, completed = room_question_counts(
        session, room_id=room.id, user_id=user.id
    )
    task_question_ids = set(
        session.exec(
            select(CourseQuestion.id).where(CourseQuestion.task_id == task.id)
        ).all()
    )
    completed_in_room = completed_question_ids_in_room(
        session, room_id=room.id, user_id=user.id
    )
    task_completed = task_question_ids.issubset(completed_in_room)
    room_percent = flag_service.progress_percent(completed, total)

    result = CourseAnswerResult(
        correct=correct,
        question_id=question_id,
        task_completed=task_completed,
        room_progress_percent=room_percent,
    )

    event: dict | None = None
    if newly_completed:
        event = {
            "type": "progress",
            "user_id": str(user.id),
            "user_email": getattr(user, "email", None),
            "room_id": str(room.id),
            "task_id": str(task.id),
            "question_id": str(question_id),
            "room_progress_percent": room_percent,
        }
    return result, room.path_id, event


# ── 老師端全班統計 ──────────────────────────────────────────────────────────


def path_progress_report(
    session: Session, *, path_id: uuid.UUID
) -> PathProgressReport:
    """全班進度：凡在此路徑有任一完成記錄的學生都列入。"""
    rooms = session.exec(
        select(CourseRoom)
        .where(CourseRoom.path_id == path_id)
        .order_by(CourseRoom.order, CourseRoom.title)
    ).all()

    room_questions: dict[uuid.UUID, set[uuid.UUID]] = {}
    for room in rooms:
        room_questions[room.id] = set(
            session.exec(_questions_in_room_query(room.id)).all()
        )
    all_question_ids: set[uuid.UUID] = set().union(*room_questions.values()) if room_questions else set()
    total_questions = len(all_question_ids)

    students: list[StudentPathProgress] = []
    if all_question_ids:
        user_ids = session.exec(
            select(UserCourseProgress.user_id)
            .where(UserCourseProgress.question_id.in_(all_question_ids))
            .distinct()
        ).all()
        for user_id in user_ids:
            user = session.get(User, user_id)
            if user is None:
                continue
            done_ids = set(
                session.exec(
                    select(UserCourseProgress.question_id).where(
                        UserCourseProgress.user_id == user_id,
                        UserCourseProgress.question_id.in_(all_question_ids),
                    )
                ).all()
            )
            room_rows = [
                StudentRoomProgress(
                    room_id=room.id,
                    room_title=room.title,
                    total_questions=len(room_questions[room.id]),
                    completed_questions=len(
                        room_questions[room.id] & done_ids
                    ),
                    progress_percent=flag_service.progress_percent(
                        len(room_questions[room.id] & done_ids),
                        len(room_questions[room.id]),
                    ),
                )
                for room in rooms
            ]
            students.append(
                StudentPathProgress(
                    user_id=user_id,
                    user_email=user.email,
                    user_name=user.full_name,
                    total_questions=total_questions,
                    completed_questions=len(done_ids),
                    progress_percent=flag_service.progress_percent(
                        len(done_ids), total_questions
                    ),
                    rooms=room_rows,
                )
            )
    students.sort(key=lambda s: (-s.progress_percent, s.user_email))
    return PathProgressReport(
        path_id=path_id,
        total_questions=total_questions,
        students=students,
    )
