"""Provisioned resource metadata."""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Optional

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .user import User
    from .vm_request import VMRequest


class Resource(SQLModel, table=True):
    """SkyLab managed VM/LXC metadata."""

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
    user_id: uuid.UUID = Field(
        foreign_key="user.id",
        description="Assigned user ID; ownership is governed by allocation_scope",
    )
    teaching_class_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("teaching_classes.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        description="Teaching class that governs this resource",
    )
    allocation_scope: str = Field(
        default="personal",
        max_length=24,
        description="personal or teaching_class",
    )
    control_policy: str = Field(
        default="owner",
        max_length=32,
        description="owner or class_member",
    )
    environment_type: str = Field(description="Environment type")
    os_info: str | None = Field(default=None, description="Operating system info")
    guest_os: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(sa.JSON, nullable=True),
        description=(
            "結構化 Guest OS 身份（os_detection 契約：family/id/version/"
            "pretty_name/source/confidence/detected_at）；偵測一次後保存，"
            " Teacher Judge 與資源頁一律讀此欄位"
        ),
    )
    expiry_date: date | None = Field(default=None, description="Expiration date")
    template_id: int | None = Field(default=None, description="Proxmox template ID")
    ssh_private_key_encrypted: str | None = Field(
        default=None,
        description="Encrypted private SSH key",
    )
    ssh_public_key: str | None = Field(
        default=None,
        description="OpenSSH public key",
    )
    login_password_encrypted: str | None = Field(
        default=None,
        description="Encrypted per-clone login password",
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

    # ── TTL / 閒置生命週期（模組C）────────────────────────────────────────
    expiry_notified_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    idle_since: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    idle_notified_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    idle_checked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    scheduled_deletion_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    # ── 反挖礦（模組D）────────────────────────────────────────────────────
    mining_exempt: bool = Field(default=False)
    mining_checked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    user: Optional["User"] = Relationship(back_populates="resources")
    request: Optional["VMRequest"] = Relationship()


__all__ = ["Resource"]
