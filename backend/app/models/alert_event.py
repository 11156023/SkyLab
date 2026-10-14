"""資源警告事件模型。"""

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Enum, Field, SQLModel


class AlertScope(str, enum.Enum):
    cluster = "cluster"
    node = "node"
    vm = "vm"
    # 平台本身（排程任務、背景迴圈、worker、Redis、PVE 連線）的健康告警
    system = "system"


class AlertMetric(str, enum.Enum):
    cpu = "cpu"
    memory = "memory"
    disk = "disk"
    # system scope 專用：value／threshold 不是百分比（見 health_policy.build_findings）
    health = "health"


class AlertEvent(SQLModel, table=True):
    """資源閾值警告事件（open = resolved_at IS NULL）。"""

    __tablename__ = "alert_events"
    __table_args__ = (
        sa.Index("ix_alert_events_target_metric", "target", "metric"),
        sa.Index("ix_alert_events_resolved_at", "resolved_at"),
        # 告警清單依時間倒序、閾值檢查查近 N 分鐘；這張表不會清理
        sa.Index("ix_alert_events_created_at", "created_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    scope: AlertScope = Field(
        sa_column=Column(Enum(AlertScope), nullable=False),
    )
    # 告警所屬的 PVE 連線（叢集）：多叢集下 vmid 只有搭配連線才唯一，
    # 開啟／解除都以 (connection_id, target, metric) 為鍵。
    # NULL＝無法判定（舊資料、尚未歸屬連線的節點、平台告警）；連線刪除時改回 NULL。
    connection_id: int | None = Field(
        default=None,
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey(
                "proxmox_connections.id",
                name="fk_alert_events_connection_id",
                ondelete="SET NULL",
            ),
            nullable=True,
            index=True,
        ),
    )
    target: str = Field(max_length=255, description="節點名稱或 vmid")
    metric: AlertMetric = Field(
        sa_column=Column(Enum(AlertMetric), nullable=False),
    )
    value: float = Field(description="觸發時的量測值（percent）")
    threshold: float = Field(description="觸發時的閾值（percent）")
    message: str
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    resolved_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    acknowledged_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL", index=True
    )
    acknowledged_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


__all__ = ["AlertEvent", "AlertMetric", "AlertScope"]
