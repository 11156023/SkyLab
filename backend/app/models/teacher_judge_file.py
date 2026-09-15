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
            "ix_teacher_judge_files_class_filename",
            "teaching_class_id",
            "original_filename",
        ),
        sa.Index(
            "ix_teacher_judge_files_class_created",
            "teaching_class_id",
            "created_at",
        ),
        sa.Index(
            "uq_teacher_judge_files_active_filename",
            "teaching_class_id",
            "original_filename",
            unique=True,
            postgresql_where=sa.text("status = 'active' AND original_filename IS NOT NULL"),
            sqlite_where=sa.text("status = 'active' AND original_filename IS NOT NULL"),
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
    uploaded_by: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    original_filename: str | None = Field(default=None, max_length=255)
    file_hash: str | None = Field(default=None, max_length=64, index=True)
    template_key: str = Field(max_length=50, index=True)
    source_type: str = Field(default="uploaded", max_length=20, index=True)
    display_name: str = Field(default="檢查表", max_length=255)
    environment_keys: list[str] = Field(
        default_factory=list,
        sa_column=Column(sa.JSON, nullable=False),
    )
    analysis_revision: int = Field(default=1, nullable=False)
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
