"""VM request models."""

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Enum, Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .user import User


class VMRequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    provisioning = "provisioning"
    running = "running"
    rejected = "rejected"
    cancelled = "cancelled"
    scheduled = "scheduled"


class VMMigrationStatus(str, enum.Enum):
    idle = "idle"
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    blocked = "blocked"


class VMRequest(SQLModel, table=True):
    __tablename__ = "vm_requests"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id")

    reason: str
    resource_type: str

    hostname: str
    cores: int = Field(default=2)
    memory: int = Field(default=2048, description="MB")
    password: str
    storage: str = Field(default="local-lvm")
    environment_type: str = Field(default="Custom")
    os_info: str | None = Field(default=None)
    expiry_date: date | None = Field(default=None)
    start_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    end_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    ostemplate: str | None = Field(default=None)
    rootfs_size: int | None = Field(default=None)
    unprivileged: bool = Field(default=True)

    template_id: int | None = Field(default=None)
    disk_size: int | None = Field(default=None)
    username: str | None = Field(default=None)
    gpu_mapping_id: str | None = Field(default=None)

    service_template_slug: str | None = Field(default=None)
    service_template_script_path: str | None = Field(default=None)

    status: VMRequestStatus = Field(
        default=VMRequestStatus.pending,
        sa_column=Column(
            Enum(VMRequestStatus),
            nullable=False,
            default=VMRequestStatus.pending,
        ),
    )
    reviewer_id: uuid.UUID | None = Field(default=None, foreign_key="user.id")
    review_comment: str | None = Field(default=None)
    reviewed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    vmid: int | None = Field(default=None)
    assigned_node: str | None = Field(default=None)
    desired_node: str | None = Field(default=None)
    actual_node: str | None = Field(default=None)
    placement_strategy_used: str | None = Field(default=None)
    migration_status: VMMigrationStatus = Field(
        default=VMMigrationStatus.idle,
        sa_column=Column(
            Enum(VMMigrationStatus),
            nullable=False,
            default=VMMigrationStatus.idle,
        ),
    )
    migration_error: str | None = Field(default=None)
    migration_pinned: bool = Field(default=False)
    resource_warning: str | None = Field(default=None)
    rebalance_epoch: int = Field(default=0)
    last_rebalanced_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_migrated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    # Recurrence schedule (RFC 5545 RRULE; e.g. FREQ=WEEKLY;BYDAY=FR;BYHOUR=13;BYMINUTE=0).
    # When set, the scheduler computes the next active window and powers on/off accordingly.
    recurrence_rule: str | None = Field(default=None)
    recurrence_duration_minutes: int | None = Field(default=None)
    schedule_timezone: str | None = Field(default=None)
    next_window_start: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    next_window_end: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    batch_job_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.ForeignKey("batch_provision_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    user: Optional["User"] = Relationship(
        back_populates="vm_requests",
        sa_relationship_kwargs={"foreign_keys": "[VMRequest.user_id]"},
    )
    reviewer: Optional["User"] = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[VMRequest.reviewer_id]"},
    )


__all__ = [
    "VMMigrationStatus",
    "VMRequestStatus",
    "VMRequest",
]
