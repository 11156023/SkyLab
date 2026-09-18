"""子網配置模型 — 系統級 IP 管理網段設定"""

from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from .base import get_datetime_utc


class SubnetConfig(SQLModel, table=True):
    """子網配置（單列 singleton，id 固定為 1）

    管理者設定系統使用的管理網段，所有 VM/LXC 將從此網段分配靜態 IP。
    未設定時，VM/LXC 相關操作將被封鎖。
    """

    __tablename__ = "subnet_config"

    id: int = Field(default=1, primary_key=True)
    cidr: str = Field(max_length=50)
    gateway: str = Field(max_length=50)
    bridge_name: str = Field(max_length=50)
    gateway_vm_ip: str = Field(max_length=50)
    dns_servers: str | None = Field(default=None, max_length=255)
    extra_blocked_subnets: str | None = Field(default=None, sa_type=sa.Text())
    # 對外 port 轉發的自動配號池：課程環境逐位學生發布時從這段挑沒用過的。
    # 使用者在拓撲圖上自己填的對外 port 不受此範圍限制。
    forward_port_start: int = Field(default=30000)
    forward_port_end: int = Field(default=39999)
    # 學生看到的入口主機（Gateway 的對外 IP 或網域）；沒設就只給 port
    forward_public_host: str | None = Field(default=None, max_length=255)
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=sa.DateTime(timezone=True),
    )


__all__ = ["SubnetConfig"]
