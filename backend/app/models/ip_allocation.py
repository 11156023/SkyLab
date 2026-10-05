"""IP allocation record model."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from .base import get_datetime_utc


class IpAllocation(SQLModel, table=True):
    """Track allocated IP addresses within the managed subnet."""

    __tablename__ = "ip_allocation"
    __table_args__ = (
        # resource_vmid 只能是空的（資源已刪或從未建立）或就是這筆的 vmid
        sa.CheckConstraint(
            "resource_vmid IS NULL OR (vmid IS NOT NULL AND resource_vmid = vmid)",
            name="ck_ip_allocation_resource_vmid_matches",
        ),
    )

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
    reservation_key: str | None = Field(
        default=None,
        sa_column=sa.Column(sa.String(200), nullable=True, unique=True, index=True),
        description="Stable class/student/node key used before a VMID exists",
    )
    teaching_class_id: uuid.UUID | None = Field(
        default=None,
        sa_column=sa.Column(
            sa.Uuid,
            sa.ForeignKey("teaching_classes.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
    )
    allocated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=sa.DateTime(timezone=True),
    )


__all__ = ["IpAllocation"]
