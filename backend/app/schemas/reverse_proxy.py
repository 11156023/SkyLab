from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ReverseProxyRuleCreate(BaseModel):
    vmid: int = Field(gt=0)
    zone_id: str = Field(min_length=1, max_length=64)
    hostname_prefix: str = Field(default="", max_length=190)
    internal_port: int = Field(ge=1, le=65535)
    enable_https: bool = True


class ReverseProxyRuleUpdate(ReverseProxyRuleCreate):
    pass


class ReverseProxyZoneOption(BaseModel):
    id: str
    name: str


class ReverseProxySetupContext(BaseModel):
    enabled: bool
    gateway_ready: bool
    cloudflare_ready: bool
    reasons: list[str] = Field(default_factory=list)
    zones: list[ReverseProxyZoneOption] = Field(default_factory=list)
    default_dns_target_type: str | None = None
    default_dns_target_value: str | None = None


class DomainAvailability(BaseModel):
    """網域是否可用來建立對外網址。

    ``reason``:
    - system: 本系統已有一條反向代理規則用了這個網域
    - external: Cloudflare 上已有同名的 A / AAAA / CNAME 紀錄（非本系統建立）
    - invalid: 不是合法的主機名稱
    - no_zone: 沒有任何啟用中的 Cloudflare zone 對得上這個網域
    - unverified: 查不到 Cloudflare（暫時性錯誤），只驗過本系統紀錄
    """

    domain: str
    available: bool
    reason: Literal["system", "external", "invalid", "no_zone", "unverified"] | None = None
    message: str | None = None


class ReverseProxyHttpServer(BaseModel):
    """nginx http.conf 裡一個網域區塊（80 轉址與 443 代理合併成一筆）。"""

    name: str
    vmid: int
    domain: str
    upstream: str | None = None
    https: bool = False
    certificate: str | None = None
    # None：不是 HTTPS；False：憑證還沒簽下來，暫用自簽
    certificate_ready: bool | None = None


class ReverseProxyStreamServer(BaseModel):
    """nginx stream.conf 裡一條 Port 轉發。"""

    name: str
    vmid: int
    listen: int
    protocol: str
    upstream: str | None = None


class ReverseProxyCertificate(BaseModel):
    name: str
    expires_at: datetime | None = None


class ReverseProxyRuntimeSnapshot(BaseModel):
    runtime_error: str | None = None
    version: str | None = None
    active: bool = False
    config_valid: bool | None = None
    http_servers: list[ReverseProxyHttpServer] = Field(default_factory=list)
    stream_servers: list[ReverseProxyStreamServer] = Field(default_factory=list)
    certificates: list[ReverseProxyCertificate] = Field(default_factory=list)


__all__ = [
    "ReverseProxyRuleCreate",
    "ReverseProxyRuleUpdate",
    "ReverseProxyZoneOption",
    "ReverseProxySetupContext",
    "ReverseProxyHttpServer",
    "ReverseProxyStreamServer",
    "ReverseProxyCertificate",
    "ReverseProxyRuntimeSnapshot",
]
