import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel

from .base import get_datetime_utc


class TeacherJudgeTemplateCommand(SQLModel, table=True):
    """Teacher Judge 可引用的 template command catalog."""

    __tablename__ = "teacher_judge_template_commands"
    __table_args__ = (
        sa.UniqueConstraint(
            "template_key",
            "command_key",
            name="uq_teacher_judge_template_command_key",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    template_key: str = Field(max_length=50, index=True)
    command_key: str = Field(max_length=100)
    command_label: str = Field(max_length=100)
    category: str = Field(max_length=50)
    command_template: str = Field(sa_type=sa.Text())
    description: str = Field(sa_type=sa.Text())
    risk_level: str = Field(default="read_only", max_length=30)
    requires_confirmation: bool = Field(default=True)
    enabled: bool = Field(default=True, index=True)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),
    )


__all__ = ["TeacherJudgeTemplateCommand"]
