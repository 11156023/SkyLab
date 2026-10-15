"""Lifecycle of one whole-class Teacher Judge execution."""

import uuid
from datetime import datetime
from typing import Literal

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel

from .base import get_datetime_utc


class TeacherJudgeRunBatch(SQLModel, table=True):
    __tablename__ = "teacher_judge_run_batches"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["session_id", "teaching_class_id"],
            ["teacher_judge_sessions.id", "teacher_judge_sessions.teaching_class_id"],
            name="fk_teacher_judge_batches_session_same_class",
        ),
        sa.Index(
            "ix_teacher_judge_batches_class_created", "teaching_class_id", "created_at"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'completed_with_failures', 'failed', 'cancelled')",
            name="ck_teacher_judge_batches_status",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    teaching_class_id: uuid.UUID = Field(
        foreign_key="teaching_classes.id", ondelete="CASCADE"
    )
    session_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="teacher_judge_sessions.id",
        ondelete="SET NULL",
        index=True,
    )
    artifact_set_id: uuid.UUID | None = Field(default=None)
    started_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    request_id: str | None = Field(default=None, max_length=64, index=True)
    status: Literal[
        "pending",
        "running",
        "completed",
        "completed_with_failures",
        "failed",
        "cancelled",
    ] = Field(default="pending", sa_column=Column(sa.String(32), nullable=False))
    error_message: str | None = Field(default=None, sa_type=sa.Text)
    # Historical batches have no observed request/preparation timestamps.
    requested_at: datetime | None = Field(
        default=None, sa_type=sa.DateTime(timezone=True)
    )
    prepared_at: datetime | None = Field(
        default=None, sa_type=sa.DateTime(timezone=True)
    )
    started_at: datetime | None = Field(
        default=None, sa_type=sa.DateTime(timezone=True)
    )
    finished_at: datetime | None = Field(
        default=None, sa_type=sa.DateTime(timezone=True)
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc, sa_type=sa.DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(
            sa.DateTime(timezone=True), nullable=False, onupdate=get_datetime_utc
        ),
    )
