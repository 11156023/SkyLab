"""Teacher's weekly reply to one student."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel

from .base import get_datetime_utc


class TeacherJudgeStudentReply(SQLModel, table=True):
    """老師給一位學生這一週的回覆。

    一週一個對話（session），回覆對的是「這位學生的這一週」，不是某台機器或某次
    檢查：重新檢查不會清掉回覆；老師再回覆一次就覆蓋前一則，學生只看到最新的。
    老師也可以不回覆——清空回覆就是刪掉這一列。
    """

    __tablename__ = "teacher_judge_student_replies"
    __table_args__ = (
        sa.UniqueConstraint(
            "session_id",
            "student_id",
            name="uq_teacher_judge_student_reply_session_student",
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
    student_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    feedback: str = Field(sa_column=Column(sa.Text, nullable=False))
    updated_by: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )


__all__ = ["TeacherJudgeStudentReply"]
