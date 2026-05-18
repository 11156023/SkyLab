"""Resource deletion request model.

刪除虛擬機是潛在耗時的操作（需要 stop polling、Proxmox API 呼叫、清理 reverse proxy/IP/audit logs），
因此和 VM 建立一樣抽成「請求 + scheduler 處理」模式，避免阻塞 API 與前端。
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Enum, Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .user import User


class DeletionRequestStatus(str, enum.Enum):
    pending = "pending"        # 已排入佇列，尚未開始
    running = "running"        # scheduler 正在處理
    completed = "completed"    # 刪除成功
    failed = "failed"          # 刪除失敗
    cancelled = "cancelled"    # 在 pending 階段被使用者取消


class DeletionRequest(SQLModel, table=True):
    __tablename__ = "deletion_requests"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True)
    vmid: int = Field(index=True)
    resource_vmid: int | None = Field(
        default=None,
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("resources.vmid", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        description="Linked resource VMID; vmid is kept as deletion snapshot",
    )

    # 刪除時的快照資訊（避免 resource record 被刪後失去脈絡）
    name: str | None = Field(default=None)
    node: str | None = Field(default=None)
    resource_type: str | None = Field(default=None, description="qemu | lxc")

    # 刪除選項
    purge: bool = Field(default=True)
    force: bool = Field(default=False, description="若 VM 仍 running 是否強制 stop 後刪")

    status: DeletionRequestStatus = Field(
        default=DeletionRequestStatus.pending,
        sa_column=Column(
            Enum(DeletionRequestStatus),
            nullable=False,
            default=DeletionRequestStatus.pending,
        ),
    )
    error_message: str | None = Field(default=None)

    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    user: Optional["User"] = Relationship()
