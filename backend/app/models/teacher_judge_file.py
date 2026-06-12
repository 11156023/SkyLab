"""Teacher Judge uploaded rubric file model."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel

from .base import get_datetime_utc


class TeacherJudgeFileStatus(str, enum.Enum):
    active = "active"
    replaced = "replaced"


class TeacherJudgeFile(SQLModel, table=True):
    """Original rubric file uploaded for Teacher Judge analysis."""

    __tablename__ = "teacher_judge_files"
    __table_args__ = (
        sa.Index(
            "ix_teacher_judge_files_group_filename",
            "group_id",
            "original_filename",
        ),
        sa.Index(
            "ix_teacher_judge_files_group_created",
            "group_id",
            "created_at",
        ),
        sa.Index(
            "uq_teacher_judge_files_active_filename",
            "group_id",
            "original_filename",
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
            sqlite_where=sa.text("status = 'active'"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    group_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("group.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
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
    file_hash: str = Field(max_length=64, index=True)
    template_key: str = Field(max_length=50, index=True)
    analysis_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(sa.JSON, nullable=False),
    )
    status: TeacherJudgeFileStatus = Field(
        default=TeacherJudgeFileStatus.active,
        sa_column=Column(
            sa.Enum(TeacherJudgeFileStatus),
            nullable=False,
            default=TeacherJudgeFileStatus.active,
            index=True,
        ),
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False, index=True),
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )


__all__ = ["TeacherJudgeFile", "TeacherJudgeFileStatus"]
