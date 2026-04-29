"""批量建立資源的工作模型"""

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Enum, Field, SQLModel


class BatchProvisionJobStatus(str, enum.Enum):
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class BatchProvisionTaskStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class BatchProvisionJob(SQLModel, table=True):
    """批量建立工作（一個群組一次操作）"""

    __tablename__ = "batch_provision_jobs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    group_id: uuid.UUID = Field(
        sa_column=Column(
            sa.ForeignKey("group.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    initiated_by: uuid.UUID = Field(
        sa_column=Column(
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        )
    )
    resource_type: str = Field(max_length=10)  # "lxc" or "qemu"
    hostname_prefix: str = Field(max_length=63)
    # JSON-encoded 建立參數（不含 hostname，由 service 自動組合）
    template_params: str = Field(sa_column=Column(sa.Text, nullable=False))
    status: BatchProvisionJobStatus = Field(
        default=BatchProvisionJobStatus.pending,
        sa_column=Column(
            Enum(BatchProvisionJobStatus),
            nullable=False,
            default=BatchProvisionJobStatus.pending,
        ),
    )
    total: int = Field(default=0)
    done: int = Field(default=0)
    failed_count: int = Field(default=0)
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True)
    )
    finished_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    reviewer_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    reviewed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    review_comment: str | None = Field(default=None, max_length=500)

    # Recurrence schedule applied to every member's vm_request when the job is approved.
    recurrence_rule: str | None = Field(default=None)
    recurrence_duration_minutes: int | None = Field(default=None)
    schedule_timezone: str | None = Field(default=None)


class BatchProvisionTask(SQLModel, table=True):
    """批量工作中的單一成員建立任務"""

    __tablename__ = "batch_provision_tasks"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    job_id: uuid.UUID = Field(
        sa_column=Column(
            sa.ForeignKey("batch_provision_jobs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    user_id: uuid.UUID = Field(
        sa_column=Column(
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    member_index: int = Field(description="成員序號（用於 hostname suffix）")
    vmid: int | None = Field(default=None, description="建立成功後的 VMID")
    status: BatchProvisionTaskStatus = Field(
        default=BatchProvisionTaskStatus.pending,
        sa_column=Column(
            Enum(BatchProvisionTaskStatus),
            nullable=False,
            default=BatchProvisionTaskStatus.pending,
        ),
    )
    error: str | None = Field(
        default=None,
        sa_column=Column(sa.String(500), nullable=True),
    )
    started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    finished_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


__all__ = [
    "BatchProvisionJob",
    "BatchProvisionJobStatus",
    "BatchProvisionTask",
    "BatchProvisionTaskStatus",
]
