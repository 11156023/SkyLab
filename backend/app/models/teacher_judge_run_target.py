"""Durable result and timing for one logical Teacher Judge target."""

import uuid
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel

from .base import get_datetime_utc


class TeacherJudgeRunTarget(SQLModel, table=True):
    __tablename__ = "teacher_judge_run_targets"
    __table_args__ = (
        sa.UniqueConstraint(
            "run_id", "target_key", name="uq_teacher_judge_targets_run_key"
        ),
        sa.Index("ix_teacher_judge_targets_run_status", "run_id", "status"),
        sa.Index("ix_teacher_judge_targets_finished", "finished_at"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_teacher_judge_targets_status",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    run_id: uuid.UUID = Field(
        foreign_key="teacher_judge_script_runs.id", ondelete="CASCADE"
    )
    target_key: str = Field(max_length=255)
    student_id: str | None = Field(default=None, max_length=36, index=True)
    vmid: int | None = Field(default=None, index=True)
    node_key: str | None = Field(default=None, max_length=80)
    status: str = Field(default="queued", max_length=32)
    stage: str = Field(default="pending_executor", max_length=32)
    reason_code: str | None = Field(default=None, max_length=80)
    exit_code: int | None = Field(default=None)
    # Public execution metadata only; never copy SSH keys/hosts/runtime context.
    metadata_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(sa.JSON, nullable=False)
    )
    timing_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(sa.JSON, nullable=False)
    )
    result_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(sa.JSON, nullable=False)
    )
    queued_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    started_at: datetime | None = Field(
        default=None, sa_type=sa.DateTime(timezone=True)
    )
    script_finished_at: datetime | None = Field(
        default=None, sa_type=sa.DateTime(timezone=True)
    )
    received_at: datetime | None = Field(
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

    def to_result(self) -> dict[str, Any]:
        timing = dict(self.timing_json)
        for key in (
            "queued_at",
            "started_at",
            "script_finished_at",
            "received_at",
            "finished_at",
        ):
            value = getattr(self, key)
            if value is not None:
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                timing[key] = value.isoformat()
        result = {
            **self.metadata_json,
            **self.result_json,
            "vmid": self.vmid,
            "student_id": str(self.student_id) if self.student_id else None,
            "node_key": self.node_key,
            "status": self.status,
            "reason_code": self.reason_code,
            "exit_code": self.exit_code,
        }
        if timing:
            result["timing"] = timing
        return result
