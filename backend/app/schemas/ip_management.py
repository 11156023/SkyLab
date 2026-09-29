"""IP 管理相關的 API Schemas"""

import ipaddress
from datetime import datetime

from pydantic import BaseModel, field_validator, model_validator

from app.core.i18n import t

_MIN_SUBNET_PREFIXLEN = 8


class SubnetConfigCreate(BaseModel):
    """設定/更新子網配置"""

    cidr: str
    gateway: str
    bridge_name: str
    # 選填 802.1Q VLAN ID；None 表示網卡不帶 tag
    vlan_tag: int | None = None
    gateway_vm_ip: str
    dns_servers: str | None = None
    extra_blocked_subnets: list[str] = []
    # 課程環境 port_forward 的自動配號池；1024 以下留給系統服務
    forward_port_start: int = 30000
    forward_port_end: int = 39999
    forward_public_host: str | None = None

    @field_validator("forward_public_host", mode="before")
    @classmethod
    def normalize_public_host(cls, v):
        if v is None:
            return None
        cleaned = str(v).strip()
        if len(cleaned) > 255:
            raise ValueError(t("ip.forward_public_host_too_long"))
        return cleaned or None

    @field_validator("vlan_tag", mode="before")
    @classmethod
    def normalize_vlan_tag(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v

    @field_validator("vlan_tag")
    @classmethod
    def validate_vlan_tag(cls, v: int | None) -> int | None:
        # 0 與 4095 是 802.1Q 保留值，PVE 的 tag 也只收 1–4094
        if v is not None and not (1 <= v <= 4094):
            raise ValueError(t("ip.invalid_vlan_tag"))
        return v

    @model_validator(mode="after")
    def validate_forward_port_range(self) -> "SubnetConfigCreate":
        start, end = self.forward_port_start, self.forward_port_end
        if not (1024 <= start <= end <= 65535):
            raise ValueError(t("ip.forward_port_range_invalid"))
        return self

    @field_validator("cidr")
    @classmethod
    def validate_cidr(cls, v: str) -> str:
        try:
            net = ipaddress.IPv4Network(v, strict=False)
        except (ipaddress.AddressValueError, ValueError) as e:
            raise ValueError(t("ip.invalid_cidr", error=str(e))) from e
        if net.prefixlen == 32:
            raise ValueError(t("ip.cidr_no_slash32"))
        # 只擋明顯不合理的超大網段（/0～/7）；/8 在校園網路很常見，不收緊，
        # 也只在存檔時檢查，既有設定不受影響。
        if net.prefixlen < _MIN_SUBNET_PREFIXLEN:
            raise ValueError(
                t("ip.cidr_prefix_too_short", min_prefix=_MIN_SUBNET_PREFIXLEN)
            )
        return str(net)

    @field_validator("gateway", "gateway_vm_ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        try:
            ipaddress.IPv4Address(v)
        except (ipaddress.AddressValueError, ValueError) as e:
            raise ValueError(t("ip.invalid_ip", error=str(e))) from e
        return v

    @field_validator("extra_blocked_subnets", mode="before")
    @classmethod
    def normalize_blocks(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            v = [s.strip() for s in v.replace("\n", ",").split(",")]
        return [s for s in v if s and s.strip()]

    @field_validator("extra_blocked_subnets")
    @classmethod
    def validate_blocks(cls, v: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in v:
            item = item.strip()
            if not item:
                continue
            try:
                if "/" in item:
                    parsed = str(ipaddress.IPv4Network(item, strict=False))
                else:
                    parsed = str(ipaddress.IPv4Address(item))
            except (ipaddress.AddressValueError, ValueError) as e:
                raise ValueError(
                    t("ip.invalid_blocked_subnet", item=item, error=str(e))
                ) from e
            if parsed not in seen:
                seen.add(parsed)
                normalized.append(parsed)
        return normalized


class BlockSyncError(BaseModel):
    """單台機器套用封鎖網段規則時的失敗記錄"""

    vmid: int | None = None
    error: str


class BlockSyncSummary(BaseModel):
    """額外封鎖網段套用到各機器的結果摘要。

    子網設定存檔後才會真的去改每台機器的防火牆，這段以前只寫 log，
    管理員在畫面上看不出有機器沒套用到；改成隨回應帶回來。
    """

    targets: list[str] = []
    created: int = 0
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    errors: list[BlockSyncError] = []


class SubnetConfigPublic(BaseModel):
    """子網配置公開回傳格式"""

    cidr: str
    gateway: str
    bridge_name: str
    vlan_tag: int | None = None
    gateway_vm_ip: str
    dns_servers: str | None
    extra_blocked_subnets: list[str] = []
    forward_port_start: int = 30000
    forward_port_end: int = 39999
    forward_public_host: str | None = None
    updated_at: datetime
    total_ips: int
    used_ips: int
    available_ips: int
    # 只有 PUT /subnet 會帶：這次存檔順帶同步封鎖網段規則的結果
    block_sync: BlockSyncSummary | None = None


class SubnetStatusResponse(BaseModel):
    """子網狀態摘要"""

    configured: bool
    cidr: str | None = None
    bridge_name: str | None = None
    vlan_tag: int | None = None
    total_ips: int = 0
    used_ips: int = 0
    available_ips: int = 0


class IpAllocationPublic(BaseModel):
    """IP 分配記錄公開格式"""

    ip_address: str
    purpose: str
    vmid: int | None
    description: str | None
    allocated_at: datetime


class IpAllocationListResponse(BaseModel):
    """IP 分配列表回傳"""

    allocations: list[IpAllocationPublic]
    total: int
