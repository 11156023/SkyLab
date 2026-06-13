"""Migration job models."""

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Enum, Field, SQLModel


class VMMigrationJobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    blocked = "blocked"
    cancelled = "cancelled"


class VMMigrationJob(SQLModel, table=True):
    __tablename__ = "vm_migration_jobs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    request_id: uuid.UUID = Field(
        sa_column=Column(
            sa.ForeignKey("vm_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    vmid: int | None = Field(default=None, index=True)
    resource_vmid: int | None = Field(
        default=None,
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("resources.vmid", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        description="Linked resource VMID; vmid remains as migration snapshot",
    )
    source_node: str | None = Field(default=None, max_length=255)
    target_node: str = Field(max_length=255)
    status: VMMigrationJobStatus = Field(
        default=VMMigrationJobStatus.pending,
        sa_column=Column(
            Enum(VMMigrationJobStatus),
            nullable=False,
            default=VMMigrationJobStatus.pending,
        ),
    )
    rebalance_epoch: int = Field(default=0, index=True)
    attempt_count: int = Field(default=0)
    last_error: str | None = Field(default=None, max_length=500)
    requested_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True)
    )
    available_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )
    claimed_by: str | None = Field(default=None, max_length=128)
    claimed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    claim_expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )
    started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    finished_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


__all__ = ["VMMigrationJob", "VMMigrationJobStatus"]
