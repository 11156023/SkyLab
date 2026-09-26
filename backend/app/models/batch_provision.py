"""批量建立資源的工作模型"""

import enum
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Enum, Field, SQLModel, UniqueConstraint


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
    """正式班級的批量建立工作。"""

    __tablename__ = "batch_provision_jobs"
    __table_args__ = (
        sa.CheckConstraint(
            "resource_type IN ('lxc', 'qemu')",
            name="ck_batch_provision_jobs_resource_type",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    teaching_class_id: uuid.UUID = Field(
        sa_column=Column(
            sa.ForeignKey("teaching_classes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    # 發起人帳號刪除時 SET NULL，所以可為 None
    initiated_by: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        )
    )
    resource_type: str = Field(max_length=10)  # "lxc" or "qemu"
    hostname_prefix: str = Field(max_length=63)
    # JSON-encoded 建立參數（不含 hostname，由 service 自動組合）
    # 建立時的規格快照（cores/memory/範本/IP 預留前綴…）
    template_params: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(sa.JSON, nullable=False)
    )
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
            index=True,
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
    next_window_start: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )
    next_window_end: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class BatchProvisionTask(SQLModel, table=True):
    """批量工作中的單一成員建立任務"""

    __tablename__ = "batch_provision_tasks"
    __table_args__ = (
        UniqueConstraint("job_id", "user_id", name="uq_batch_tasks_job_user"),
    )

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
            index=True,
        )
    )
    member_index: int = Field(description="成員序號（用於 hostname suffix）")
    vmid: int | None = Field(
        default=None,
        # 資源被刪時清成 NULL；clear_task_vmid_references 另外負責把
        # 已完成的 task 轉回 failed 並修正 job 計數
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("resources.vmid", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        description="建立成功後的 VMID",
    )
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
