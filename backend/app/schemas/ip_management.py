"""IP 管理相關的 API Schemas"""

import ipaddress
from datetime import datetime

from pydantic import BaseModel, field_validator, model_validator

from app.core.i18n import t


class SubnetConfigCreate(BaseModel):
    """設定/更新子網配置"""

    cidr: str
    gateway: str
    bridge_name: str
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


class SubnetConfigPublic(BaseModel):
    """子網配置公開回傳格式"""

    cidr: str
    gateway: str
    bridge_name: str
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


class SubnetStatusResponse(BaseModel):
    """子網狀態摘要"""

    configured: bool
    cidr: str | None = None
    bridge_name: str | None = None
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
