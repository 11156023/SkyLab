"""Teacher Judge managed script run model."""

import enum
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel

from .base import get_datetime_utc


class TeacherJudgeScriptRunTargetScope(str, enum.Enum):
    all_with_vm = "all_with_vm"
    running_only = "running_only"
    manual = "manual"


class TeacherJudgeScriptRunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class TeacherJudgeScriptRun(SQLModel, table=True):
    """Execution run for a Teacher Judge managed script artifact."""

    __tablename__ = "teacher_judge_script_runs"
    __table_args__ = (
        sa.Index(
            "ix_teacher_judge_script_runs_group_status",
            "group_id",
            "status",
        ),
        sa.Index(
            "ix_teacher_judge_script_runs_artifact_created",
            "artifact_id",
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
    artifact_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("teacher_judge_script_artifacts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    target_scope: TeacherJudgeScriptRunTargetScope = Field(
        default=TeacherJudgeScriptRunTargetScope.all_with_vm,
        sa_column=Column(
            sa.Enum(TeacherJudgeScriptRunTargetScope),
            nullable=False,
            default=TeacherJudgeScriptRunTargetScope.all_with_vm,
        ),
    )
    target_snapshot_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(sa.JSON, nullable=False),
    )
    status: TeacherJudgeScriptRunStatus = Field(
        default=TeacherJudgeScriptRunStatus.pending,
        sa_column=Column(
            sa.Enum(TeacherJudgeScriptRunStatus),
            nullable=False,
            default=TeacherJudgeScriptRunStatus.pending,
            index=True,
        ),
    )
    progress_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(sa.JSON, nullable=False),
    )
    result_summary_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(sa.JSON, nullable=False),
    )
    target_results_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(sa.JSON, nullable=False),
    )
    started_by: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    started_at: datetime | None = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )
    finished_at: datetime | None = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False, index=True),
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )


__all__ = [
    "TeacherJudgeScriptRun",
    "TeacherJudgeScriptRunStatus",
    "TeacherJudgeScriptRunTargetScope",
]
