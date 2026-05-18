import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import DateTime
from sqlmodel import Column, Enum, Field, Relationship, SQLModel

from .base import get_datetime_utc

if TYPE_CHECKING:
    from .ai_api_credential import AIAPICredential
    from .user import User


class AIAPIRequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class AIAPIRequest(SQLModel, table=True):
    __tablename__ = "ai_api_requests"
    __table_args__ = (
        sa.Index("ix_ai_api_requests_user_id", "user_id"),
        sa.Index("ix_ai_api_requests_reviewer_id", "reviewer_id"),
        sa.Index("ix_ai_api_requests_user_status_created", "user_id", "status", "created_at"),
        sa.Index("ix_ai_api_requests_status_created", "status", "created_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id")
    purpose: str = Field(max_length=2000)
    api_key_name: str = Field(default="test", max_length=20)
    duration: str = Field(default="never", max_length=20)
    rate_limit: int | None = Field(
        default=None, description="每分鐘請求限制（1-1000），None 使用預設值 20"
    )
    status: AIAPIRequestStatus = Field(
        default=AIAPIRequestStatus.pending,
        sa_column=Column(
            Enum(AIAPIRequestStatus),
            nullable=False,
            default=AIAPIRequestStatus.pending,
        ),
    )
    reviewer_id: uuid.UUID | None = Field(default=None, foreign_key="user.id")
    review_comment: str | None = Field(default=None, max_length=2000)
    reviewed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),
    )

    user: "User" = Relationship(
        back_populates="ai_api_requests",
        sa_relationship_kwargs={"foreign_keys": "[AIAPIRequest.user_id]"},
    )
    reviewer: "User" = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[AIAPIRequest.reviewer_id]"},
    )
    credentials: list["AIAPICredential"] = Relationship(back_populates="request")


__all__ = ["AIAPIRequest", "AIAPIRequestStatus"]
