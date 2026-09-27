import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlmodel import Field, Relationship, SQLModel

from .base import get_datetime_utc

if TYPE_CHECKING:
    from .ai_api_credential import AIAPICredential
    from .user import User


# 呼叫來源：api_key = 使用者以申請的金鑰經 Proxy 呼叫；platform = 平台功能
# 自己發出的 LLM 呼叫（範本推薦／聊天、導覽、情境說明、PVE 助理、Teacher Judge）
USAGE_SOURCE_API_KEY = "api_key"
USAGE_SOURCE_PLATFORM = "platform"


class AIAPIUsage(SQLModel, table=True):
    """所有 LLM 呼叫的用量紀錄（Proxy 金鑰呼叫與平台功能呼叫共用）"""

    __tablename__ = "ai_api_usage"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('success', 'error', 'cancelled')",
            name="ck_ai_api_usage_status",
        ),
        sa.CheckConstraint(
            "source IN ('api_key', 'platform')",
            name="ck_ai_api_usage_source",
        ),
        # 金鑰呼叫一定有憑證，平台呼叫一定沒有
        sa.CheckConstraint(
            "(source = 'api_key') = (credential_id IS NOT NULL)",
            name="ck_ai_api_usage_source_credential",
        ),
        sa.Index("ix_ai_usage_user_source_created", "user_id", "source", "created_at"),
        sa.Index("ix_ai_usage_source_created", "source", "created_at"),
        sa.Index("ix_ai_usage_call_type_created", "call_type", "created_at"),
        sa.Index("ix_ai_usage_model_created", "model_name", "created_at"),
        sa.Index("ix_ai_usage_status_created", "status", "created_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    source: str = Field(default=USAGE_SOURCE_API_KEY, max_length=20)
    credential_id: uuid.UUID | None = Field(
        default=None, foreign_key="ai_api_credentials.id", index=True
    )
    model_name: str = Field(max_length=255)
    # api_key：chat_completion / completion / response；platform：chat / recommend /
    # ai_nav / ai_help / pve_chat / teacher_judge_chat …
    call_type: str = Field(max_length=50)
    preset: str | None = Field(default=None, max_length=50)  # 範本推薦的 persona
    request_id: str | None = Field(default=None, max_length=255, index=True)
    upstream_request_id: str | None = Field(default=None, max_length=255)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    request_duration_ms: int | None = Field(default=None)
    first_token_ms: int | None = Field(default=None)
    stream: bool = Field(default=False)
    usage_reported: bool = Field(default=False)
    response_model: str | None = Field(default=None, max_length=255)
    status: str = Field(max_length=50)  # success, error, cancelled
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
    credential: Optional["AIAPICredential"] = Relationship()


__all__ = ["AIAPIUsage", "USAGE_SOURCE_API_KEY", "USAGE_SOURCE_PLATFORM"]
