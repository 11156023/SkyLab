"""防火牆相關 API schemas"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ─── 基礎型別 ──────────────────────────────────────────────────────────────────


class PortSpec(BaseModel):
    """端口規格（port=0 表示無端口協定，如 icmp/esp 等）

    三種入站存取模式：
    - domain 有值 → 反向代理（Traefik）
    - external_port 有值 → Port 轉發（haproxy）
    - 兩者皆無 → 僅開放防火牆
    """

    port: int = Field(ge=0, le=65535, description="端口號；0 表示無端口協定")
    # 協定名稱會被寫進 PVE 防火牆規則與 haproxy 設定檔（frontend/backend 名稱），
    # 只允許小寫英數與連字號，避免換行等字元污染產生的設定
    protocol: str = Field(
        default="tcp",
        pattern=r"^[a-z0-9-]{1,16}$",
        description="協定 (tcp/udp/icmp/esp/ah/...)",
    )
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

    @field_validator("protocol", mode="before")
    @classmethod
    def _normalize_protocol(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


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


# ─── 對外服務（單台 VM 的 Internet 入站發布） ──────────────────────────────────

PublishMode = Literal["domain", "port_forward", "firewall_only"]


def _normalize_protocol_value(value: object) -> object:
    if isinstance(value, str):
        return value.strip().lower()
    return value


class PublishedServiceRef(BaseModel):
    """用內部 port + 協定指認一條已發布的服務"""

    port: int = Field(ge=0, le=65535, description="VM 內部 port；0 代表無端口協定")
    protocol: str = Field(default="tcp", pattern=r"^[a-z0-9-]{1,16}$")

    @field_validator("protocol", mode="before")
    @classmethod
    def _normalize(cls, value: object) -> object:
        return _normalize_protocol_value(value)


class PublishedServiceCreate(BaseModel):
    """發布一條對外服務：三種模式對應 PortSpec 的 domain / external_port / 皆無"""

    port: int = Field(ge=1, le=65535, description="VM 內部 port")
    protocol: str = Field(default="tcp", pattern=r"^[a-z0-9-]{1,16}$")
    mode: PublishMode = Field(default="firewall_only")
    domain: str | None = Field(default=None, max_length=255, description="完整網域（mode=domain）")
    enable_https: bool = Field(default=True)
    external_port: int | None = Field(
        default=None, ge=1, le=65535, description="外部 port（mode=port_forward）"
    )

    @field_validator("protocol", mode="before")
    @classmethod
    def _normalize(cls, value: object) -> object:
        return _normalize_protocol_value(value)

    @field_validator("domain", mode="before")
    @classmethod
    def _normalize_domain(cls, value: object) -> object:
        if isinstance(value, str):
            cleaned = value.strip().lower().rstrip(".")
            return cleaned or None
        return value

    @model_validator(mode="after")
    def _check_mode_fields(self) -> "PublishedServiceCreate":
        if self.mode == "domain":
            if not self.domain:
                raise ValueError("domain is required when mode is 'domain'")
            if self.protocol != "tcp":
                raise ValueError("domain publishing only supports tcp")
            self.external_port = None
        elif self.mode == "port_forward":
            if self.external_port is None:
                raise ValueError("external_port is required when mode is 'port_forward'")
            self.domain = None
        else:
            self.domain = None
            self.external_port = None
        return self

    def to_port_spec(self) -> PortSpec:
        return PortSpec(
            port=self.port,
            protocol=self.protocol,
            external_port=self.external_port,
            domain=self.domain,
            enable_https=self.enable_https,
        )


class PublishedServiceUpdate(BaseModel):
    """把既有的一條服務換成新的設定（先刪後建）"""

    current: PublishedServiceRef
    replacement: PublishedServiceCreate


class PublishedService(BaseModel):
    """對外服務（回應）"""

    port: int
    protocol: str = "tcp"
    mode: PublishMode
    domain: str | None = None
    enable_https: bool = True
    external_port: int | None = None
    url: str | None = Field(default=None, description="mode=domain 時的完整網址")
    firewall_rule_present: bool = Field(
        default=True,
        description="Proxmox 上是否有對應的 SkyLab 入站規則；False 代表只有 DB 紀錄",
    )


# ─── 防火牆規則 CRUD ───────────────────────────────────────────────────────────
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
    "LayoutNodeUpdate",
    "LayoutUpdate",
    "FirewallRuleCreate",
    "FirewallRuleUpdate",
    "FirewallRulePublic",
    "FirewallOptionsPublic",
    "TopologyNode",
    "TopologyEdge",
    "TopologyResponse",
    "NATRulePublic",
    "ReverseProxyRulePublic",
    "PublishMode",
    "PublishedService",
    "PublishedServiceCreate",
    "PublishedServiceRef",
    "PublishedServiceUpdate",
]
