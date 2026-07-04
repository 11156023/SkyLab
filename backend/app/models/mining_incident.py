"""疑似挖礦事件模型（兩段式處置狀態機）。"""

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Enum, Field, SQLModel


class MiningIncidentStatus(str, enum.Enum):
    detected = "detected"      # 已偵測（auto_suspend 停用或暫停失敗時停留於此）
    suspended = "suspended"    # 已自動存證 + 暫停，等待管理員審核
    banned = "banned"          # 管理員確認 → 帳號停權
    dismissed = "dismissed"    # 管理員判定誤判 → 恢復 VM


class MiningIncident(SQLModel, table=True):
    """疑似挖礦事件（open = status in {detected, suspended}）。"""

    __tablename__ = "mining_incidents"
    __table_args__ = (
        sa.Index("ix_mining_incidents_vmid_status", "vmid", "status"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    vmid: int = Field(index=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True)
    node: str = Field(max_length=255)
    resource_type: str = Field(max_length=8, description="qemu | lxc")
    avg_cpu: float = Field(description="偵測視窗內平均 CPU（percent）")
    window_hours: int
    snapshot_name: str | None = Field(
        default=None, max_length=128, description="存證快照名稱；失敗為 null"
    )
    status: MiningIncidentStatus = Field(
        sa_column=Column(Enum(MiningIncidentStatus), nullable=False, index=True),
    )
    detected_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    suspended_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    reviewed_by: uuid.UUID | None = Field(default=None, foreign_key="user.id")
    reviewed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    review_note: str | None = Field(default=None, max_length=1024)


__all__ = ["MiningIncident", "MiningIncidentStatus"]
