"""防火牆相關 API schemas"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ─── 基礎型別 ──────────────────────────────────────────────────────────────────


class PortSpec(BaseModel):
    """端口規格（port=0 表示無端口協定，如 icmp/esp 等）

    三種入站存取模式：
    - domain 有值 → 反向代理（Traefik）
    - external_port 有值 → Port 轉發（haproxy）
    - 兩者皆無 → 僅開放防火牆
    """

    port: int = Field(ge=0, le=65535, description="端口號；0 表示無端口協定")
    protocol: str = Field(default="tcp", description="協定 (tcp/udp/icmp/esp/ah/...)")
    external_port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
        description="外網入站 port（Port 轉發用）",
    )
    domain: str | None = Field(
        default=None,
        max_length=255,
        description="對外網域名稱（反向代理用）",
    )
    enable_https: bool = Field(
        default=True,
        description="反向代理是否啟用 HTTPS（Let's Encrypt）",
    )


# ─── 連線管理 ──────────────────────────────────────────────────────────────────


class ConnectionCreate(BaseModel):
    """建立 VM 間連線（或 VM 到網關，或 Internet 入站）"""

    source_vmid: int | None = Field(description="來源 VM ID；None 代表網關（Internet 入站）")
    target_vmid: int | None = Field(
        default=None, description="目標 VM ID；None 代表網關（上網）"
    )
    ports: list[PortSpec] = Field(description="允許通過的端口列表")
    direction: Literal["one_way", "bidirectional"] = Field(
        default="one_way",
        description="連線方向：one_way（單向）或 bidirectional（雙向）",
    )


class ConnectionDelete(BaseModel):
    """刪除 VM 間連線"""

    source_vmid: int | None = Field(description="來源 VM ID；None 代表網關")
    target_vmid: int | None = Field(
        default=None, description="目標 VM ID；None 代表網關"
    )
    ports: list[PortSpec] | None = Field(
        default=None, description="要刪除的端口；None 代表刪除全部連線"
    )


# ─── 防火牆規則 CRUD ───────────────────────────────────────────────────────────


class FirewallRuleCreate(BaseModel):
    """建立防火牆規則（原始 Proxmox 規則）"""

    type: Literal["in", "out"] = Field(description="規則方向")
    action: Literal["ACCEPT", "DROP", "REJECT"] = Field(description="動作")
    source: str | None = Field(default=None, description="來源 IP/CIDR")
    dest: str | None = Field(default=None, description="目標 IP/CIDR")
    proto: str | None = Field(default=None, description="協定 (tcp/udp/icmp)")
    dport: str | None = Field(default=None, description="目標端口或範圍")
    sport: str | None = Field(default=None, description="來源端口或範圍")
    enable: int = Field(default=1, description="是否啟用 (1=是, 0=否)")
    comment: str | None = Field(default=None, description="備註")


class FirewallRuleUpdate(BaseModel):
    """更新防火牆規則"""

    action: Literal["ACCEPT", "DROP", "REJECT"] | None = None
    source: str | None = None
    dest: str | None = None
    proto: str | None = None
    dport: str | None = None
    sport: str | None = None
    enable: int | None = None
    comment: str | None = None


# ─── 佈局管理 ──────────────────────────────────────────────────────────────────


class LayoutNodeUpdate(BaseModel):
    """更新節點位置"""

    vmid: int | None = Field(default=None, description="VM ID；None 代表 gateway")
    node_type: Literal["vm", "gateway"] = Field(description="節點類型")
    position_x: float = Field(description="X 座標")
    position_y: float = Field(description="Y 座標")


class LayoutUpdate(BaseModel):
    """批次更新圖形佈局"""

    nodes: list[LayoutNodeUpdate]


# ─── 回應 schemas ──────────────────────────────────────────────────────────────


class FirewallRulePublic(BaseModel):
    """防火牆規則（回應）"""

    pos: int
    type: str
    action: str
    source: str | None = None
    dest: str | None = None
    proto: str | None = None
    dport: str | None = None
    sport: str | None = None
    enable: int = 1
    comment: str | None = None
    is_managed: bool = Field(
        default=False,
        description="是否由 SkyLab 管理（comment 含 SkyLab: 前綴）",
    )


class FirewallOptionsPublic(BaseModel):
    """防火牆選項（回應）"""

    enable: bool
    policy_in: str
    policy_out: str


class TopologyNode(BaseModel):
    """拓撲圖中的節點"""

    vmid: int | None = None
    name: str
    node_type: Literal["vm", "gateway"]
    vm_type: Literal["qemu", "lxc"] | None = None
    status: str | None = None
    ip_address: str | None = None
    firewall_enabled: bool = False
    position_x: float = 100.0
    position_y: float = 100.0


class TopologyEdge(BaseModel):
    """拓撲圖中的連線"""

    source_vmid: int | None = None
    target_vmid: int | None = None
    ports: list[PortSpec] = []
    direction: Literal["one_way", "bidirectional"] = "one_way"


class TopologyResponse(BaseModel):
    """完整拓撲資料（節點 + 連線）"""

    nodes: list[TopologyNode]
    edges: list[TopologyEdge]


# ─── NAT 規則 ──────────────────────────────────────────────────────────────────


class NATRulePublic(BaseModel):
    """NAT 端口轉發規則（回應）"""

    id: uuid.UUID
    ssh_host: str
    vmid: int
    vm_ip: str
    external_port: int
    internal_port: int
    protocol: str
    created_at: datetime


class ReverseProxyRulePublic(BaseModel):
    """反向代理規則（回應）"""

    id: uuid.UUID
    vmid: int
    vm_ip: str
    domain: str
    zone_id: str | None = None
    internal_port: int
    enable_https: bool
    dns_provider: str
    created_at: datetime


__all__ = [
    "PortSpec",
    "ConnectionCreate",
    "ConnectionDelete",
    "FirewallRuleCreate",
    "FirewallRuleUpdate",
    "LayoutNodeUpdate",
    "LayoutUpdate",
    "FirewallRulePublic",
    "FirewallOptionsPublic",
    "TopologyNode",
    "TopologyEdge",
    "TopologyResponse",
    "NATRulePublic",
    "ReverseProxyRulePublic",
]
