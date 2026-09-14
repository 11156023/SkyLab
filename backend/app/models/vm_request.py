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
    """Review lifecycle for a VM/LXC request.

    Runtime resource state belongs to ResourcePublic.status, not here.
    """

    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"
    # 使用時段已結束卻始終沒被審核 —— 由 scheduler 自動標記，非人為決定。
    expired = "expired"


class VMProvisioningStatus(str, enum.Enum):
    idle = "idle"
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    blocked = "blocked"


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
        sa.Index(
            "ix_vm_requests_placement_group",
            "placement_group_id",
            "start_at",
            "end_at",
        ),
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
    # vGPU 規格（mdev type，如 'nvidia-1436'）；None = 不指定（passthrough 或整卡）
    gpu_mdev_profile: str | None = Field(default=None)

    # VM vs LXC 自動判斷（模組C）：manual = 使用者自選；auto = 規則引擎決定
    requested_mode: str = Field(default="manual")
    auto_decision_reason: str | None = Field(default=None)

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
    provisioning_status: VMProvisioningStatus = Field(
        default=VMProvisioningStatus.idle,
        sa_column=Column(
            Enum(VMProvisioningStatus),
            nullable=False,
            default=VMProvisioningStatus.idle,
        ),
    )
    provisioning_error: str | None = Field(default=None)
    resource_warning: str | None = Field(default=None)

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

    # 同一組機器（快速練習 Session／課堂班級／課程部署）共用的群組鍵。
    # placement 以此把整組約束在同一 connection（跨叢集 L2 不通，硬約束）
    # 與同一節點（連貫環境的 attacker/target 需要互通）。
    # None = 不屬於任何群組，行為與加入此欄位前相同。
    placement_group_id: uuid.UUID | None = Field(default=None)

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
    "VMProvisioningStatus",
    "VMRequestStatus",
    "VMRequest",
]
