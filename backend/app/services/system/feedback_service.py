"""使用者回饋：送出、管理員清單、標記已處理、刪除"""

import uuid
from typing import Any

from sqlmodel import Session

from app.core.i18n import t
from app.exceptions import NotFoundError
from app.models.base import get_datetime_utc
from app.models.user_feedback import (
    UserFeedback,
    UserFeedbackCategory,
    UserFeedbackStatus,
)
from app.repositories import user_feedback as feedback_repo
from app.schemas.user_feedback import (
    UserFeedbackCreate,
    UserFeedbackPublic,
    UserFeedbacksPublic,
)


def _to_public(feedback: UserFeedback) -> UserFeedbackPublic:
    return UserFeedbackPublic(
        id=feedback.id,
        user_id=feedback.user_id,
        user_email=feedback.user.email if feedback.user else None,
        user_full_name=feedback.user.full_name if feedback.user else None,
        category=feedback.category,
        content=feedback.content,
        app_version=feedback.app_version,
        status=feedback.status,
        resolved_by_id=feedback.resolved_by_id,
        resolved_by_email=feedback.resolved_by.email if feedback.resolved_by else None,
        resolved_at=feedback.resolved_at,
        created_at=feedback.created_at,
    )


def _get_or_404(*, session: Session, feedback_id: uuid.UUID) -> UserFeedback:
    feedback = feedback_repo.get_feedback(session=session, feedback_id=feedback_id)
    if feedback is None:
        raise NotFoundError(t("feedback.not_found"))
    return feedback


def create(
    *, session: Session, feedback_in: UserFeedbackCreate, user: Any
) -> UserFeedbackPublic:
    feedback = UserFeedback(
        user_id=user.id,
        category=feedback_in.category,
        content=feedback_in.content,
        app_version=feedback_in.app_version or None,
        status=UserFeedbackStatus.open,
        created_at=get_datetime_utc(),
    )
    feedback = feedback_repo.create_feedback(session=session, feedback=feedback)
    # 回傳要帶送出者的名字：剛建立的物件還沒載入關聯，重新查一次
    return _to_public(_get_or_404(session=session, feedback_id=feedback.id))


def list_all(
    *,
    session: Session,
    skip: int = 0,
    limit: int = 100,
    status: UserFeedbackStatus | None = None,
    category: UserFeedbackCategory | None = None,
) -> UserFeedbacksPublic:
    rows, count = feedback_repo.list_feedback(
        session=session, skip=skip, limit=limit, status=status, category=category
    )
    return UserFeedbacksPublic(data=[_to_public(r) for r in rows], count=count)


def count_open(*, session: Session) -> int:
    return feedback_repo.count_open_feedback(session=session)


def update_status(
    *,
    session: Session,
    feedback_id: uuid.UUID,
    status: UserFeedbackStatus,
    reviewer: Any,
) -> UserFeedbackPublic:
    """標記已處理（記下是誰、何時）；改回 open 時清掉處理紀錄"""
    feedback = _get_or_404(session=session, feedback_id=feedback_id)
    feedback.status = status
    if status == UserFeedbackStatus.resolved:
        feedback.resolved_by_id = reviewer.id
        feedback.resolved_at = get_datetime_utc()
    else:
        feedback.resolved_by_id = None
        feedback.resolved_at = None
    feedback_repo.save_feedback(session=session, feedback=feedback)
    return _to_public(_get_or_404(session=session, feedback_id=feedback_id))


def delete(*, session: Session, feedback_id: uuid.UUID) -> None:
    feedback = _get_or_404(session=session, feedback_id=feedback_id)
    feedback_repo.delete_feedback(session=session, feedback=feedback)
