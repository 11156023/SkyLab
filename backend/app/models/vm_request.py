"""VM request models."""

import enum
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Enum, Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .user import User


class VMRequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"


class VMMigrationStatus(str, enum.Enum):
    idle = "idle"
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    blocked = "blocked"


@dataclass(frozen=True, slots=True)
class VMRequestReviewState:
    status: "VMRequestStatus"
    reviewer_id: uuid.UUID | None
    review_comment: str | None
    reviewed_at: datetime | None


@dataclass(frozen=True, slots=True)
class VMRequestScheduleState:
    start_at: datetime | None
    end_at: datetime | None
    recurrence_rule: str | None
    recurrence_duration_minutes: int | None
    schedule_timezone: str | None
    next_window_start: datetime | None
    next_window_end: datetime | None
    batch_job_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class VMRequestProvisioningState:
    vmid: int | None
    assigned_node: str | None
    desired_node: str | None
    actual_node: str | None
    placement_strategy_used: str | None


@dataclass(frozen=True, slots=True)
class VMRequestMigrationState:
    status: "VMMigrationStatus"
    error: str | None
    pinned: bool
    resource_warning: str | None
    rebalance_epoch: int
    last_rebalanced_at: datetime | None
    last_migrated_at: datetime | None


class VMRequest(SQLModel, table=True):
    __tablename__ = "vm_requests"
    __table_args__ = (
        sa.Index("ix_vm_requests_next_window_end", "next_window_end"),
        sa.Index("ix_vm_requests_next_window_start", "next_window_start"),
        sa.Index("ix_vm_requests_user_id", "user_id"),
        sa.Index("ix_vm_requests_vmid", "vmid"),
        sa.Index("ix_vm_requests_user_status_created", "user_id", "status", "created_at"),
        sa.Index("ix_vm_requests_status_created", "status", "created_at"),
        sa.Index("ix_vm_requests_schedule", "status", "start_at", "end_at"),
        sa.Index("ix_vm_requests_gpu_window", "gpu_mapping_id", "start_at", "end_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id")

    reason: str
    resource_type: str
    request_kind: str = Field(
        default="research", description="research or quick_template"
    )

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

    @property
    def review_state(self) -> VMRequestReviewState:
        return VMRequestReviewState(
            status=self.status,
            reviewer_id=self.reviewer_id,
            review_comment=self.review_comment,
            reviewed_at=self.reviewed_at,
        )

    @property
    def schedule(self) -> VMRequestScheduleState:
        return VMRequestScheduleState(
            start_at=self.start_at,
            end_at=self.end_at,
            recurrence_rule=self.recurrence_rule,
            recurrence_duration_minutes=self.recurrence_duration_minutes,
            schedule_timezone=self.schedule_timezone,
            next_window_start=self.next_window_start,
            next_window_end=self.next_window_end,
            batch_job_id=self.batch_job_id,
        )

    @property
    def provisioning(self) -> VMRequestProvisioningState:
        return VMRequestProvisioningState(
            vmid=self.vmid,
            assigned_node=self.assigned_node,
            desired_node=self.desired_node,
            actual_node=self.actual_node,
            placement_strategy_used=self.placement_strategy_used,
        )

    @property
    def migration(self) -> VMRequestMigrationState:
        return VMRequestMigrationState(
            status=self.migration_status,
            error=self.migration_error,
            pinned=self.migration_pinned,
            resource_warning=self.resource_warning,
            rebalance_epoch=self.rebalance_epoch,
            last_rebalanced_at=self.last_rebalanced_at,
            last_migrated_at=self.last_migrated_at,
        )


__all__ = [
    "VMMigrationStatus",
    "VMRequestStatus",
    "VMRequest",
    "VMRequestMigrationState",
    "VMRequestProvisioningState",
    "VMRequestReviewState",
    "VMRequestScheduleState",
]
