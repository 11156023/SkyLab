"""使用者回饋模型

帳號設定「關於」分頁的「使用者回饋」區塊送進來的問題回報與建議；
管理員在「使用者回饋」頁查看、標記已處理或刪除。不公開、不需要 GitHub 帳號。
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Enum, Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .user import User


class UserFeedbackCategory(str, enum.Enum):
    """回饋類型"""

    bug = "bug"  # 問題回報
    suggestion = "suggestion"  # 功能建議
    other = "other"  # 其他


class UserFeedbackStatus(str, enum.Enum):
    """處理狀態：管理員看過並處理後標記 resolved，可再改回 open"""

    open = "open"
    resolved = "resolved"


USER_FEEDBACK_CONTENT_MIN_LENGTH = 5
USER_FEEDBACK_CONTENT_MAX_LENGTH = 2000


class UserFeedback(SQLModel, table=True):
    """使用者回饋表"""

    __tablename__ = "user_feedback"
    __table_args__ = (
        # 管理員清單：依狀態篩選、新到舊
        sa.Index("ix_user_feedback_status_created", "status", "created_at"),
        sa.Index("ix_user_feedback_created_at", "created_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    # 帳號刪除後保留回饋內容本身，只是不再知道是誰送的
    user_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="user.id",
        ondelete="SET NULL",
        index=True,
        nullable=True,
        description="送出者；帳號刪除後為空",
    )
    category: UserFeedbackCategory = Field(
        sa_column=Column(Enum(UserFeedbackCategory), nullable=False),
        description="回饋類型",
    )
    content: str = Field(
        sa_column=Column(sa.Text, nullable=False), description="回饋內容"
    )
    app_version: str | None = Field(
        default=None, max_length=64, description="送出當時前端顯示的版本（建置資訊）"
    )

    status: UserFeedbackStatus = Field(
        default=UserFeedbackStatus.open,
        sa_column=Column(Enum(UserFeedbackStatus), nullable=False, default="open"),
        description="處理狀態",
    )
    resolved_by_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="user.id",
        ondelete="SET NULL",
        nullable=True,
        description="標記已處理的管理員",
    )
    resolved_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
        description="標記已處理的時間",
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        description="送出時間",
    )

    user: Optional["User"] = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[UserFeedback.user_id]"}
    )
    resolved_by: Optional["User"] = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[UserFeedback.resolved_by_id]"}
    )


__all__ = [
    "USER_FEEDBACK_CONTENT_MAX_LENGTH",
    "USER_FEEDBACK_CONTENT_MIN_LENGTH",
    "UserFeedback",
    "UserFeedbackCategory",
    "UserFeedbackStatus",
]
