"""規格調整申請模型"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Enum, Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .user import User


class SpecChangeRequestStatus(str, enum.Enum):
    """規格調整申請狀態"""

    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class SpecChangeType(str, enum.Enum):
    """規格調整類型"""

    cpu = "cpu"
    memory = "memory"
    disk = "disk"
    combined = "combined"  # 同時調整多項


class SpecChangeRequest(SQLModel, table=True):
    """規格調整申請表"""

    __tablename__ = "spec_change_requests"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    vmid: int = Field(description="VM/Container ID")
    resource_vmid: int | None = Field(
        default=None,
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("resources.vmid", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        description="Linked resource VMID; vmid is kept as request-time snapshot",
    )
    user_id: uuid.UUID = Field(foreign_key="user.id", description="申請者ID")

    # 調整類型與原因
    change_type: SpecChangeType = Field(
        sa_column=Column(Enum(SpecChangeType), nullable=False), description="調整類型"
    )
    reason: str = Field(description="調整原因")

    # 原始規格
    current_cpu: int | None = Field(default=None, description="當前CPU核心數")
    current_memory: int | None = Field(default=None, description="當前記憶體 (MB)")
    current_disk: int | None = Field(default=None, description="當前磁碟大小 (GB)")

    # 請求的新規格
    requested_cpu: int | None = Field(default=None, description="請求CPU核心數")
    requested_memory: int | None = Field(default=None, description="請求記憶體 (MB)")
    requested_disk: int | None = Field(default=None, description="請求磁碟大小 (GB)")

    # 審核狀態
    status: SpecChangeRequestStatus = Field(
        default=SpecChangeRequestStatus.pending,
        sa_column=Column(
            Enum(SpecChangeRequestStatus), nullable=False, default="pending"
        ),
        description="審核狀態",
    )
    reviewer_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", description="審核者ID"
    )
    review_comment: str | None = Field(default=None, description="審核備註")
    reviewed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
        description="審核時間",
    )
    applied_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
        description="實際調整時間",
    )

    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        description="申請時間",
    )

    # Relationships
    user: Optional["User"] = Relationship(
        back_populates="spec_change_requests",
        sa_relationship_kwargs={"foreign_keys": "[SpecChangeRequest.user_id]"},
    )
    reviewer: Optional["User"] = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[SpecChangeRequest.reviewer_id]"}
    )


__all__ = [
    "SpecChangeRequestStatus",
    "SpecChangeType",
    "SpecChangeRequest",
]
