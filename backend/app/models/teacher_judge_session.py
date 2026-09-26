"""Persistent Teacher Judge session and message models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel

from .base import get_datetime_utc


class TeacherJudgeSessionStatus(str, enum.Enum):
    active = "active"
    archived = "archived"


class TeacherJudgeMessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class TeacherJudgeMessageType(str, enum.Enum):
    chat = "chat"
    rubric_proposal = "rubric_proposal"
    system_notice = "system_notice"


class TeacherJudgeSession(SQLModel, table=True):
    __tablename__ = "teacher_judge_sessions"
    __table_args__ = (
        sa.Index(
            "ix_teacher_judge_sessions_class_activity",
            "teaching_class_id",
            "last_activity_at",
        ),
        sa.Index(
            "ix_teacher_judge_sessions_class_pinned_activity",
            "teaching_class_id",
            "pinned_at",
            "last_activity_at",
        ),
        # A rubric source is session-owned.  Keep NULL available for legacy
        # chat-first sessions, but never allow two sessions to point at the
        # same active source.
        sa.Index(
            "uq_teacher_judge_sessions_selected_file",
            "selected_file_id",
            unique=True,
            postgresql_where=sa.text("selected_file_id IS NOT NULL"),
            sqlite_where=sa.text("selected_file_id IS NOT NULL"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    teaching_class_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("teaching_classes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    teaching_class_week_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey(
                "teaching_class_weeks.id",
                name="fk_teacher_judge_sessions_week_id",
                ondelete="SET NULL",
            ),
            nullable=True,
            index=True,
        ),
    )
    title: str = Field(max_length=255)
    status: TeacherJudgeSessionStatus = Field(
        default=TeacherJudgeSessionStatus.active,
        sa_column=Column(
            sa.Enum(TeacherJudgeSessionStatus), nullable=False, index=True
        ),
    )
    selected_file_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("teacher_judge_files.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    summary: str = Field(default="", sa_column=Column(sa.Text, nullable=False))
    # The boundary is persisted so a completed background summary is not
    # scheduled repeatedly, and a later boundary can never be overwritten by
    # an older worker that finishes out of order.
    summary_through_message_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(sa.Uuid, nullable=True),
    )
    summary_through_assistant_count: int = Field(
        default=0,
        sa_column=Column(sa.Integer, nullable=False, server_default="0"),
    )
    created_by: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )
    last_activity_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False, index=True),
    )
    pinned_at: datetime | None = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True, index=True),
    )


class TeacherJudgeSessionMessage(SQLModel, table=True):
    __tablename__ = "teacher_judge_session_messages"
    __table_args__ = (
        sa.Index(
            "ix_teacher_judge_session_messages_session_created",
            "session_id",
            "created_at",
            "id",
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
    role: TeacherJudgeMessageRole = Field(
        sa_column=Column(sa.Enum(TeacherJudgeMessageRole), nullable=False)
    )
    content: str = Field(sa_column=Column(sa.Text, nullable=False))
    message_type: TeacherJudgeMessageType = Field(
        default=TeacherJudgeMessageType.chat,
        sa_column=Column(sa.Enum(TeacherJudgeMessageType), nullable=False),
    )
    metadata_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(sa.JSON, nullable=False)
    )
    created_by: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False, index=True),
    )


__all__ = [
    "TeacherJudgeMessageRole",
    "TeacherJudgeMessageType",
    "TeacherJudgeSession",
    "TeacherJudgeSessionMessage",
    "TeacherJudgeSessionStatus",
]
