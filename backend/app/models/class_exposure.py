"""Class exposure: a teacher opens ports on their own VM to a whole teaching class."""

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Field, SQLModel, UniqueConstraint

from .base import get_datetime_utc


class ResourceClassExposure(SQLModel, table=True):
    """老師事先同意「這台機器的這些埠，班上的學生可以連進來」。

    這是學生發起連線的授權依據：學生建立「自己的機器 → 老師的機器」連線時，
    後端替學生在老師機器上寫入站規則，憑的是這筆事先同意，不是學生的權限。
    埠不在清單上、方向不是單向、學生不在班上，都不放行。
    """

    __tablename__ = "resource_class_exposures"
    __table_args__ = (
        UniqueConstraint("resource_vmid", "class_id", name="uq_resource_class_exposure"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    resource_vmid: int = Field(
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("resources.vmid", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        description="被開放的機器 VMID",
    )
    class_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("teaching_classes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        description="開放給哪個班級",
    )
    ports: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(sa.JSON, nullable=False),
        description='允許學生連的埠，[{"port": 80, "protocol": "tcp"}, ...]',
    )
    created_by: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["ResourceClassExposure"]
