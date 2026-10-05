"""子網配置模型 — 系統級 IP 管理網段設定"""

from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, Field, Relationship, SQLModel

from .base import get_datetime_utc


class SubnetDnsServer(SQLModel, table=True):
    """子網配置的 DNS 伺服器（一列一個位址，position 決定順序）。"""

    __tablename__ = "subnet_dns_servers"
    __table_args__ = (
        sa.UniqueConstraint(
            "subnet_config_id", "address", name="uq_subnet_dns_servers_address"
        ),
    )

    subnet_config_id: int = Field(
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("subnet_config.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    position: int = Field(sa_column=Column(sa.Integer, primary_key=True))
    address: str = Field(max_length=64)


class SubnetBlockedSubnet(SQLModel, table=True):
    """管理員額外封鎖的網段（一列一個 IPv4 位址或 CIDR，position 決定順序）。"""

    __tablename__ = "subnet_blocked_subnets"
    __table_args__ = (
        sa.UniqueConstraint(
            "subnet_config_id", "cidr", name="uq_subnet_blocked_subnets_cidr"
        ),
    )

    subnet_config_id: int = Field(
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("subnet_config.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    position: int = Field(sa_column=Column(sa.Integer, primary_key=True))
    cidr: str = Field(max_length=64)


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
    # 選填：實驗室網段走 802.1Q VLAN 時，VM/LXC 網卡帶 tag=N；None＝untagged
    vlan_tag: int | None = Field(default=None)
    gateway_vm_ip: str = Field(max_length=50)
    # 對外 port 轉發的自動配號池：課程環境逐位學生發布時從這段挑沒用過的。
    # 使用者在拓撲圖上自己填的對外 port 不受此範圍限制。
    forward_port_start: int = Field(default=30000)
    forward_port_end: int = Field(default=39999)
    # 學生看到的入口主機（Gateway 的對外 IP 或網域）；沒設就只給 port
    forward_public_host: str | None = Field(default=None, max_length=255)
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=sa.DateTime(timezone=True),
        sa_column_kwargs={"onupdate": get_datetime_utc},
    )

    # 多值欄位拆成子表（1NF）；寫入走 ip_management_service.upsert_subnet_config
    dns_server_rows: list[SubnetDnsServer] = Relationship(
        sa_relationship_kwargs={
            "order_by": "SubnetDnsServer.position",
            "cascade": "all, delete-orphan",
            "lazy": "selectin",
        }
    )
    blocked_subnet_rows: list[SubnetBlockedSubnet] = Relationship(
        sa_relationship_kwargs={
            "order_by": "SubnetBlockedSubnet.position",
            "cascade": "all, delete-orphan",
            "lazy": "selectin",
        }
    )

    @property
    def dns_server_list(self) -> list[str]:
        return [row.address for row in self.dns_server_rows]

    @property
    def blocked_subnet_list(self) -> list[str]:
        return [row.cidr for row in self.blocked_subnet_rows]


__all__ = ["SubnetBlockedSubnet", "SubnetConfig", "SubnetDnsServer"]
