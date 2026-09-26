import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlmodel import Field, Relationship, SQLModel

from .base import get_datetime_utc

if TYPE_CHECKING:
    from .user import User


class AITemplateCallLog(SQLModel, table=True):
    """AI Template 呼叫記錄表（chat / recommend）"""

    __tablename__ = "ai_template_call_logs"
    __table_args__ = (
        sa.Index("ix_ai_template_call_logs_user_created", "user_id", "created_at"),
        sa.Index("ix_ai_template_call_logs_status_created", "status", "created_at"),
        sa.Index(
            "ix_ai_template_call_logs_call_type_created", "call_type", "created_at"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    call_type: str = Field(max_length=30)  # "chat" | "recommend"
    model_name: str = Field(max_length=255)
    preset: str | None = Field(default=None, max_length=50)
    request_id: str | None = Field(default=None, max_length=255, index=True)
    upstream_request_id: str | None = Field(default=None, max_length=255)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    request_duration_ms: int | None = Field(default=None)
    first_token_ms: int | None = Field(default=None)
    stream: bool = Field(default=False)
    usage_reported: bool = Field(default=False)
    response_model: str | None = Field(default=None, max_length=255)
    status: str = Field(max_length=50)  # "success" | "error"
    error_message: str | None = Field(default=None)
    started_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=sa.DateTime(timezone=True),
        index=True,
    )

    # 關聯
    user: "User" = Relationship()


__all__ = ["AITemplateCallLog"]
