"""使用者回饋 schemas"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.user_feedback import (
    USER_FEEDBACK_CONTENT_MAX_LENGTH,
    USER_FEEDBACK_CONTENT_MIN_LENGTH,
    UserFeedbackCategory,
    UserFeedbackStatus,
)

# ===== Request Schemas =====


class UserFeedbackCreate(BaseModel):
    """送出回饋（任何登入者）"""

    category: UserFeedbackCategory
    content: str = Field(
        min_length=USER_FEEDBACK_CONTENT_MIN_LENGTH,
        max_length=USER_FEEDBACK_CONTENT_MAX_LENGTH,
        description="回饋內容",
    )
    app_version: str | None = Field(default=None, max_length=64)

    @field_validator("content")
    @classmethod
    def _strip_content(cls, value: str) -> str:
        """去掉前後空白後再套長度限制：一串空白不算有內容"""
        stripped = value.strip()
        if len(stripped) < USER_FEEDBACK_CONTENT_MIN_LENGTH:
            raise ValueError(
                f"content must be at least {USER_FEEDBACK_CONTENT_MIN_LENGTH} characters"
            )
        return stripped


class UserFeedbackStatusUpdate(BaseModel):
    """管理員改處理狀態"""

    status: UserFeedbackStatus


# ===== Response Schemas =====


class UserFeedbackPublic(BaseModel):
    """回饋內容與送出者（送出者帳號已刪時 user_* 為空）"""

    id: uuid.UUID
    user_id: uuid.UUID | None
    user_email: str | None = None
    user_full_name: str | None = None
    category: UserFeedbackCategory
    content: str
    app_version: str | None = None
    status: UserFeedbackStatus
    resolved_by_id: uuid.UUID | None = None
    resolved_by_email: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime


class UserFeedbacksPublic(BaseModel):
    """回饋列表"""

    data: list[UserFeedbackPublic]
    count: int
