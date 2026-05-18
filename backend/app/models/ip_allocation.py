"""IP allocation record model."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel

from .base import get_datetime_utc


class IpAllocation(SQLModel, table=True):
    """Track allocated IP addresses within the managed subnet."""

    __tablename__ = "ip_allocation"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    ip_address: str = Field(max_length=50, unique=True, index=True)
    purpose: str = Field(max_length=30)
    vmid: int | None = Field(default=None, index=True)
    resource_vmid: int | None = Field(
        default=None,
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("resources.vmid", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        description="Linked resource VMID; vmid remains available for reserved IP snapshots",
    )
    description: str | None = Field(default=None, max_length=255)
    allocated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),
    )


__all__ = ["IpAllocation"]
