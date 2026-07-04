"""Group model."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime
from sqlmodel import Field, Relationship, SQLModel, UniqueConstraint

from .base import get_datetime_utc

if TYPE_CHECKING:
    from .group_member import GroupMember
    from .user import User


class Group(SQLModel, table=True):
    """Course/project group owned by a teacher or administrator."""

    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_group_owner_name"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    owner_id: uuid.UUID = Field(foreign_key="user.id")
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),
    )

    owner: Optional["User"] = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[Group.owner_id]"}
    )
    members: list["GroupMember"] = Relationship(
        back_populates="group",
        # 不設 cascade 的話 SQLAlchemy 刪 group 時會嘗試把成員的
        # group_id（主鍵欄位）設成 NULL 而直接報錯
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


__all__ = ["Group"]
