"""Proxmox 叢集節點模型"""

from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel, UniqueConstraint


class ProxmoxNode(SQLModel, table=True):
    """Proxmox 叢集中每個節點的連線資訊"""

    __tablename__ = "proxmox_nodes"
    __table_args__ = (
        UniqueConstraint("name", name="uq_proxmox_nodes_name"),
    )

    id: int | None = Field(default=None, primary_key=True)
    connection_id: int | None = Field(
        default=None,
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey(
                "proxmox_connections.id",
                name="fk_proxmox_nodes_connection_id",
                ondelete="CASCADE",
            ),
            nullable=True,
            index=True,
        ),
    )  # 所屬連線；None 表示尚未歸屬（舊資料，視同預設連線）
    name: str = Field(max_length=255)           # PVE 節點名稱，例如 "pve", "pve2"
    host: str = Field(max_length=255)           # IP 或 hostname
    port: int = Field(default=8006)
    is_primary: bool = Field(default=False)     # 主節點（使用者最初設定的那台）
    is_online: bool = Field(default=True)       # 最近一次連線結果
    last_checked: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),
    )
    priority: int = Field(default=5, ge=1, le=10)   # 1=最高優先, 10=最低；對應 simulator ServerInput.priority
    enabled: bool = Field(default=True)             # 停用後不接收新 VM（不參與放置）；既有 VM 不受影響


__all__ = ["ProxmoxNode"]
