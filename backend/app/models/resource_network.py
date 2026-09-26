"""Network metadata for provisioned resources."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from .base import get_datetime_utc


class ResourceNetwork(SQLModel, table=True):
    """IP metadata linked to a managed VM/LXC resource."""

    __tablename__ = "resource_networks"
    __table_args__ = (
        sa.UniqueConstraint(
            "resource_vmid", name="uq_resource_networks_resource_vmid"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    resource_vmid: int = Field(
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("resources.vmid", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    ip_address: str | None = Field(default=None, max_length=64, unique=True, index=True)
    source: str | None = Field(default=None, max_length=32)
    cached_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=sa.DateTime(timezone=True),
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=sa.DateTime(timezone=True),
        sa_column_kwargs={"onupdate": get_datetime_utc},
    )


__all__ = ["ResourceNetwork"]
