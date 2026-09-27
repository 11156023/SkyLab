"""防火牆佈局模型"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Field, SQLModel, UniqueConstraint

from .base import get_datetime_utc


class FirewallLayout(SQLModel, table=True):
    """儲存防火牆圖形介面中每位使用者的節點位置。
    防火牆規則本身儲存在 Proxmox，此表只記錄 UI 的節點座標。
    """

    __tablename__ = "firewall_layout"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "vmid", "node_type",
            name="uq_firewall_layout_user_node",
        ),
        # 上面的 UNIQUE 對 vmid 為 NULL 的 gateway 列不生效（NULL 彼此不相等）
        sa.Index(
            "uq_firewall_layout_user_gateway",
            "user_id",
            unique=True,
            postgresql_where=sa.text("vmid IS NULL"),
            sqlite_where=sa.text("vmid IS NULL"),
        ),
        sa.CheckConstraint(
            "(node_type = 'gateway') = (vmid IS NULL)",
            name="ck_firewall_layout_gateway_has_no_vmid",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id",
        ondelete="CASCADE",
        index=True,
        description="擁有此佈局的使用者 ID",
    )
    vmid: int | None = Field(
        default=None,
        # 佈局隨資源刪除；None 代表 gateway（網關）節點
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("resources.vmid", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        description="VM ID；None 代表 gateway（網關）節點",
    )
    node_type: str = Field(
        description="節點類型: 'vm' | 'gateway'",
    )
    position_x: float = Field(default=100.0, description="節點 X 座標")
    position_y: float = Field(default=100.0, description="節點 Y 座標")
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=get_datetime_utc),
    )


__all__ = ["FirewallLayout"]
