"""Files attached to persistent Teacher Judge chat messages."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel

from .base import get_datetime_utc


class TeacherJudgeAttachmentStatus(str, enum.Enum):
    ready = "ready"
    failed = "failed"


class TeacherJudgeSessionAttachment(SQLModel, table=True):
    """A parsed, message-scoped document used as AI chat context."""

    __tablename__ = "teacher_judge_session_attachments"
    __table_args__ = (
        sa.Index(
            "ix_teacher_judge_session_attachments_session_created",
            "session_id",
            "created_at",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("teacher_judge_sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    message_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("teacher_judge_session_messages.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
    )
    uploaded_by: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    original_filename: str = Field(max_length=255)
    media_type: str | None = Field(default=None, max_length=150)
    size_bytes: int = Field(default=0, ge=0)
    file_hash: str = Field(max_length=64, index=True)
    storage_key: str = Field(max_length=255, unique=True)
    extracted_text: str = Field(default="", sa_column=Column(sa.Text, nullable=False))
    status: TeacherJudgeAttachmentStatus = Field(
        default=TeacherJudgeAttachmentStatus.ready,
        sa_column=Column(sa.Enum(TeacherJudgeAttachmentStatus), nullable=False, index=True),
    )
    error_message: str | None = Field(default=None, sa_column=Column(sa.Text, nullable=True))
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False, index=True),
    )


__all__ = ["TeacherJudgeAttachmentStatus", "TeacherJudgeSessionAttachment"]
