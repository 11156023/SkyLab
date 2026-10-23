"""Administrator-managed user groups (e.g. a school class such as 資管三甲).

Teachers can import a group's students into a teaching class; the import copies
the members at that moment, so later group changes never touch existing classes.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Field, SQLModel

from .base import get_datetime_utc

USER_GROUP_NAME_MAX_LENGTH = 120


class UserGroup(SQLModel, table=True):
    __tablename__ = "user_groups"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(
        sa_column=Column(
            sa.String(USER_GROUP_NAME_MAX_LENGTH), nullable=False, unique=True
        )
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class UserGroupMember(SQLModel, table=True):
    __tablename__ = "user_group_members"

    group_id: uuid.UUID = Field(
        sa_column=Column(
            sa.ForeignKey("user_groups.id", ondelete="CASCADE"), primary_key=True
        )
    )
    user_id: uuid.UUID = Field(
        sa_column=Column(
            sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True, index=True
        )
    )
    added_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
