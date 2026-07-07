"""背景任務狀態紀錄（Redis 任務隊列的 DB 側狀態）"""

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Enum, Field, SQLModel

from .base import get_datetime_utc


class TaskRecordStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class TaskRecord(SQLModel, table=True):
    """一筆背景任務的狀態與結果（id 同時作為 arq job id）"""

    __tablename__ = "task_records"
    __table_args__ = (
        sa.Index("ix_task_records_user_created", "user_id", "created_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    task_type: str = Field(max_length=64, index=True)
    user_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    template_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("vm_templates.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    payload: str = Field(
        sa_column=Column(sa.Text, nullable=False),
        description="JSON-encoded 任務參數",
    )
    status: TaskRecordStatus = Field(
        default=TaskRecordStatus.queued,
        sa_column=Column(
            Enum(TaskRecordStatus),
            nullable=False,
            default=TaskRecordStatus.queued,
        ),
    )
    progress: int = Field(default=0, description="0-100")
    result: str | None = Field(
        default=None,
        sa_column=Column(sa.Text, nullable=True),
        description="JSON-encoded 任務結果",
    )
    error: str | None = Field(default=None, max_length=1000)
    resource_vmid: int | None = Field(
        default=None,
        description="任務產出的 VM/LXC VMID（若適用）",
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
    started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    finished_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


__all__ = ["TaskRecord", "TaskRecordStatus"]
