"""Teacher Judge managed script artifact model."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel

from .base import get_datetime_utc


class TeacherJudgeScriptStatus(str, enum.Enum):
    draft = "draft"
    review_failed = "review_failed"
    reviewed = "reviewed"
    approved = "approved"
    archived = "archived"


class TeacherJudgeScriptSource(str, enum.Enum):
    ai_generated = "ai_generated"
    regenerated = "regenerated"


class TeacherJudgeScriptLanguage(str, enum.Enum):
    python = "python"
    shell = "shell"
    bat = "bat"


class TeacherJudgeScriptArtifact(SQLModel, table=True):
    """Reusable managed script generated from a rubric analysis snapshot."""

    __tablename__ = "teacher_judge_script_artifacts"
    __table_args__ = (
        sa.Index(
            "ix_teacher_judge_script_artifacts_group_status",
            "group_id",
            "status",
        ),
        sa.Index(
            "ix_teacher_judge_script_artifacts_group_created",
            "group_id",
            "created_at",
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
    name: str = Field(max_length=255)
    template_key: str = Field(max_length=50, index=True)
    rubric_snapshot_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(sa.JSON, nullable=False),
    )
    source_file_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("teacher_judge_files.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    source_file_snapshot_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(sa.JSON, nullable=False),
    )
    script_language: TeacherJudgeScriptLanguage = Field(
        default=TeacherJudgeScriptLanguage.python,
        sa_column=Column(
            sa.Enum(TeacherJudgeScriptLanguage),
            nullable=False,
            default=TeacherJudgeScriptLanguage.python,
        ),
    )
    script_content: str = Field(sa_column=Column(sa.Text, nullable=False))
    source: TeacherJudgeScriptSource = Field(
        default=TeacherJudgeScriptSource.ai_generated,
        sa_column=Column(
            sa.Enum(TeacherJudgeScriptSource),
            nullable=False,
            default=TeacherJudgeScriptSource.ai_generated,
        ),
    )
    version: int = Field(default=1)
    status: TeacherJudgeScriptStatus = Field(
        default=TeacherJudgeScriptStatus.draft,
        sa_column=Column(
            sa.Enum(TeacherJudgeScriptStatus),
            nullable=False,
            default=TeacherJudgeScriptStatus.draft,
            index=True,
        ),
    )
    policy_check_result_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(sa.JSON, nullable=False),
    )
    ai_review_result_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(sa.JSON, nullable=False),
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
    approved_by: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
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
    approved_at: datetime | None = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )


__all__ = [
    "TeacherJudgeScriptArtifact",
    "TeacherJudgeScriptLanguage",
    "TeacherJudgeScriptSource",
    "TeacherJudgeScriptStatus",
]
