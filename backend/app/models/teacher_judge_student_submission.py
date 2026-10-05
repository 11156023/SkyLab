"""Student completion signal for an approved Teacher Judge assignment."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel

from .base import get_datetime_utc


class TeacherJudgeStudentSubmission(SQLModel, table=True):
    """A student-declared completion state; this never starts an AI run.

    The teaching class comes from the artifact, the completed rubric items live in
    ``teacher_judge_submission_items``, and "ready" is ``ready_at IS NOT NULL``.
    """

    __tablename__ = "teacher_judge_student_submissions"
    __table_args__ = (
        sa.UniqueConstraint(
            "artifact_id",
            "student_id",
            name="uq_teacher_judge_student_submission_artifact_student",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
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
    ready_at: datetime | None = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False, onupdate=get_datetime_utc),
    )


class TeacherJudgeSubmissionItem(SQLModel, table=True):
    """One rubric item a student has marked as completed (one row per item)."""

    __tablename__ = "teacher_judge_submission_items"

    submission_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("teacher_judge_student_submissions.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    item_id: str = Field(
        sa_column=Column(sa.String(255), primary_key=True),
    )


__all__ = ["TeacherJudgeStudentSubmission", "TeacherJudgeSubmissionItem"]
