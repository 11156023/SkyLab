"""Cloudflare domain management schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class CloudflareConfigPublic(BaseModel):
    account_id: str | None = None
    is_configured: bool
    has_api_token: bool
    has_default_dns_target: bool
    default_dns_target_type: str | None = None
    default_dns_target_value: str | None = None
    updated_at: datetime | None = None
    last_verified_at: datetime | None = None


class CloudflareConfigUpdate(BaseModel):
    account_id: str | None = Field(default=None, max_length=128)
    api_token: str | None = Field(default=None, min_length=20)
    default_dns_target_type: str | None = Field(default=None, max_length=16)
    default_dns_target_value: str | None = Field(default=None, max_length=255)


class CloudflareConnectionTestResult(BaseModel):
    success: bool
    message: str
    token_status: str | None = None


class CloudflarePageInfoPublic(BaseModel):
    page: int = 1
    per_page: int = 50
    count: int = 0
    total_count: int = 0
    total_pages: int = 1


class CloudflareZonePublic(BaseModel):
    id: str
    name: str
    status: str
    paused: bool = False
    type: str | None = None
    development_mode: int | None = None
    name_servers: list[str] = Field(default_factory=list)
    original_name_servers: list[str] = Field(default_factory=list)
    created_on: datetime | None = None
    modified_on: datetime | None = None
    activated_on: datetime | None = None


class CloudflareZonesPublic(BaseModel):
    items: list[CloudflareZonePublic]
    page_info: CloudflarePageInfoPublic


class CloudflareZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    account_id: str | None = Field(default=None, max_length=128)
    jump_start: bool = False


class CloudflareDNSRecordPublic(BaseModel):
    id: str
    zone_id: str
    type: str
    name: str
    content: str
    ttl: int
    proxied: bool | None = None
    proxiable: bool | None = None
    comment: str | None = None
    priority: int | None = None
    tags: list[str] = Field(default_factory=list)
    created_on: datetime | None = None
    modified_on: datetime | None = None
    managed_by_system: bool = Field(
        default=False,
        description="是否由本系統的對外網址（反向代理規則）建立",
    )
    managed_vmid: int | None = Field(
        default=None, description="由本系統建立時，對應的 VM ID"
    )


class CloudflareDNSRecordsPublic(BaseModel):
    items: list[CloudflareDNSRecordPublic]
    page_info: CloudflarePageInfoPublic


class CloudflareDNSRecordCreate(BaseModel):
    type: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=4096)
    ttl: int = Field(default=1, ge=1, le=86400)
    proxied: bool | None = None
    comment: str | None = Field(default=None, max_length=500)
    priority: int | None = Field(default=None, ge=0, le=65535)


class CloudflareDNSRecordUpdate(CloudflareDNSRecordCreate):
    pass


__all__ = [
    "CloudflareConfigPublic",
    "CloudflareConfigUpdate",
    "CloudflareConnectionTestResult",
    "CloudflarePageInfoPublic",
    "CloudflareZonePublic",
    "CloudflareZonesPublic",
    "CloudflareZoneCreate",
    "CloudflareDNSRecordPublic",
    "CloudflareDNSRecordsPublic",
    "CloudflareDNSRecordCreate",
    "CloudflareDNSRecordUpdate",
]
