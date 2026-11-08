"""使用者回饋資料存取"""

import uuid

from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.models.user_feedback import (
    UserFeedback,
    UserFeedbackCategory,
    UserFeedbackStatus,
)


def create_feedback(*, session: Session, feedback: UserFeedback) -> UserFeedback:
    session.add(feedback)
    session.commit()
    session.refresh(feedback)
    return feedback


def get_feedback(*, session: Session, feedback_id: uuid.UUID) -> UserFeedback | None:
    statement = (
        select(UserFeedback)
        .options(selectinload(UserFeedback.user))
        .options(selectinload(UserFeedback.resolved_by))
        .where(UserFeedback.id == feedback_id)
    )
    return session.exec(statement).first()


def list_feedback(
    *,
    session: Session,
    skip: int = 0,
    limit: int = 100,
    status: UserFeedbackStatus | None = None,
    category: UserFeedbackCategory | None = None,
) -> tuple[list[UserFeedback], int]:
    """管理員清單：新到舊，可依狀態、類型篩選；回傳當頁與總數"""
    filters = []
    if status is not None:
        filters.append(UserFeedback.status == status)
    if category is not None:
        filters.append(UserFeedback.category == category)

    count_statement = select(func.count()).select_from(UserFeedback)
    for f in filters:
        count_statement = count_statement.where(f)
    count = session.exec(count_statement).one()

    statement = (
        select(UserFeedback)
        .options(selectinload(UserFeedback.user))
        .options(selectinload(UserFeedback.resolved_by))
        .order_by(UserFeedback.created_at.desc())
    )
    for f in filters:
        statement = statement.where(f)
    statement = statement.offset(skip).limit(limit)
    return list(session.exec(statement).all()), count


def count_open_feedback(*, session: Session) -> int:
    statement = (
        select(func.count())
        .select_from(UserFeedback)
        .where(UserFeedback.status == UserFeedbackStatus.open)
    )
    return session.exec(statement).one()


def save_feedback(*, session: Session, feedback: UserFeedback) -> UserFeedback:
    session.add(feedback)
    session.commit()
    session.refresh(feedback)
    return feedback


def delete_feedback(*, session: Session, feedback: UserFeedback) -> None:
    session.delete(feedback)
    session.commit()
