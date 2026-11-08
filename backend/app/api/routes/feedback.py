"""使用者回饋：任何登入者可送出；清單、處理狀態與刪除只給管理員"""

import uuid

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.api.deps.rate_limit import rate_limit_by_user
from app.models.user_feedback import UserFeedbackCategory, UserFeedbackStatus
from app.schemas.user_feedback import (
    UserFeedbackCreate,
    UserFeedbackPublic,
    UserFeedbacksPublic,
    UserFeedbackStatusUpdate,
)
from app.services.system import feedback_service

router = APIRouter(prefix="/feedback", tags=["feedback"])

# 同一個人一小時最多送 10 則：擋住誤觸連點與灌水，正常使用碰不到
submit_rate_limit = rate_limit_by_user(scope="feedback", limit=10, window_seconds=3600)


@router.post(
    "/",
    response_model=UserFeedbackPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(submit_rate_limit)],
)
def submit_feedback(
    feedback_in: UserFeedbackCreate,
    session: SessionDep,
    current_user: CurrentUser,
):
    return feedback_service.create(
        session=session, feedback_in=feedback_in, user=current_user
    )


@router.get("/", response_model=UserFeedbacksPublic)
def list_feedback(
    session: SessionDep,
    current_user: AdminUser,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    status: UserFeedbackStatus | None = None,
    category: UserFeedbackCategory | None = None,
):
    return feedback_service.list_all(
        session=session, skip=skip, limit=limit, status=status, category=category
    )


@router.patch("/{feedback_id}", response_model=UserFeedbackPublic)
def update_feedback_status(
    feedback_id: uuid.UUID,
    update: UserFeedbackStatusUpdate,
    session: SessionDep,
    current_user: AdminUser,
):
    return feedback_service.update_status(
        session=session,
        feedback_id=feedback_id,
        status=update.status,
        reviewer=current_user,
    )


@router.delete("/{feedback_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_feedback(
    feedback_id: uuid.UUID,
    session: SessionDep,
    current_user: AdminUser,
):
    feedback_service.delete(session=session, feedback_id=feedback_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
