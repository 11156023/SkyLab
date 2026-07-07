"""資源告警事件模型。"""

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Enum, Field, SQLModel


class AlertScope(str, enum.Enum):
    cluster = "cluster"
    node = "node"
    vm = "vm"


class AlertMetric(str, enum.Enum):
    cpu = "cpu"
    memory = "memory"
    disk = "disk"


class AlertEvent(SQLModel, table=True):
    """資源閾值告警事件（open = resolved_at IS NULL）。"""

    __tablename__ = "alert_events"
    __table_args__ = (
        sa.Index("ix_alert_events_target_metric", "target", "metric"),
        sa.Index("ix_alert_events_resolved_at", "resolved_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    scope: AlertScope = Field(
        sa_column=Column(Enum(AlertScope), nullable=False),
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
        default=None, foreign_key="user.id"
    )
    acknowledged_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


__all__ = ["AlertEvent", "AlertMetric", "AlertScope"]
