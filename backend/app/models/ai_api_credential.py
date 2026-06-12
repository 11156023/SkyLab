import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlmodel import Field, Relationship, SQLModel

from .base import get_datetime_utc

if TYPE_CHECKING:
    from .ai_api_request import AIAPIRequest
    from .user import User


class AIAPICredential(SQLModel, table=True):
    __tablename__ = "ai_api_credentials"
    __table_args__ = (
        sa.Index("ix_ai_api_credentials_user_id", "user_id"),
        sa.Index("ix_ai_api_credentials_request_id", "request_id"),
        sa.Index("ix_ai_api_credentials_user_revoked", "user_id", "revoked_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id")
    request_id: uuid.UUID = Field(foreign_key="ai_api_requests.id")
    base_url: str = Field(max_length=2048)
    api_key_encrypted: str = Field(max_length=4096)
    api_key_prefix: str = Field(max_length=32, unique=True, index=True)
    api_key_name: str = Field(default="test", min_length=1, max_length=20)
    rate_limit: int | None = Field(
        default=None, description="每分鐘請求限制（1-1000），None 使用預設值 20"
    )
    expires_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    revoked_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=sa.DateTime(timezone=True),
    )

    user: "User" = Relationship(back_populates="ai_api_credentials")
    request: "AIAPIRequest" = Relationship(back_populates="credentials")


__all__ = ["AIAPICredential"]
