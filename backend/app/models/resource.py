"""Provisioned resource metadata."""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .user import User
    from .vm_request import VMRequest


class Resource(SQLModel, table=True):
    """Campus Cloud managed VM/LXC metadata."""

    __tablename__ = "resources"
    __table_args__ = (
        sa.Index("ix_resources_user_id", "user_id"),
        sa.Index("ix_resources_user_created", "user_id", "created_at"),
        sa.Index("ix_resources_auto_stop_at", "auto_stop_at"),
    )

    vmid: int = Field(primary_key=True, description="VM/Container ID")
    request_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("vm_requests.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
            index=True,
        ),
        description="VM request that provisioned this resource",
    )
    user_id: uuid.UUID = Field(foreign_key="user.id", description="Owner user ID")
    environment_type: str = Field(description="Environment type")
    os_info: str | None = Field(default=None, description="Operating system info")
    expiry_date: date | None = Field(default=None, description="Expiration date")
    template_id: int | None = Field(default=None, description="Proxmox template ID")
    service_template_slug: str | None = Field(
        default=None,
        description="Service template slug",
    )
    ip_address: str | None = Field(default=None, max_length=64)
    ip_address_cached_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    ssh_private_key_encrypted: str | None = Field(
        default=None,
        description="Encrypted private SSH key",
    )
    ssh_public_key: str | None = Field(
        default=None,
        description="OpenSSH public key",
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        description="Created time",
    )

    batch_job_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("batch_provision_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    auto_stop_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    auto_stop_reason: str | None = Field(default=None, max_length=32)

    user: Optional["User"] = Relationship(back_populates="resources")
    request: Optional["VMRequest"] = Relationship()


__all__ = ["Resource"]
