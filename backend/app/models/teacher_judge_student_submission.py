"""Student completion signal for an approved Teacher Judge assignment."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel

from .base import get_datetime_utc


class TeacherJudgeStudentSubmission(SQLModel, table=True):
    """A student-declared completion state; this never starts an AI run."""

    __tablename__ = "teacher_judge_student_submissions"
    __table_args__ = (
        sa.UniqueConstraint(
            "artifact_id",
            "student_id",
            name="uq_teacher_judge_student_submission_artifact_student",
        ),
        sa.Index(
            "ix_teacher_judge_student_submissions_class_artifact_ready",
            "teaching_class_id",
            "artifact_id",
            "is_ready",
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
    artifact_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("teacher_judge_script_artifacts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    student_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    completed_item_ids: list[str] = Field(
        default_factory=list,
        sa_column=Column(sa.JSON, nullable=False),
    )
    is_ready: bool = Field(default=False, nullable=False)
    ready_at: datetime | None = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )


__all__ = ["TeacherJudgeStudentSubmission"]
