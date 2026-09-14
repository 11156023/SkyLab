"""Gateway VM 管理相關 schemas"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

GatewayService = Literal["haproxy", "traefik", "frps", "frpc", "wireguard"]
ServiceAction = Literal["start", "stop", "restart", "reload"]


class GatewayConfigPublic(BaseModel):
    host: str
    ssh_port: int
    ssh_user: str
    public_key: str
    is_configured: bool  # host 非空且有 keypair


class GatewayConfigUpdate(BaseModel):
    host: str = Field(max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default="root", max_length=64)


class GatewayConnectionTestResult(BaseModel):
    success: bool
    message: str


class ServiceConfigRead(BaseModel):
    service: str
    content: str


class ServiceConfigWrite(BaseModel):
    content: str = Field(description="設定檔內容")


class ServiceStatusResult(BaseModel):
    service: str
    active: bool
    status_text: str  # systemctl status 的輸出摘要


class ServiceActionResult(BaseModel):
    service: str
    action: str
    success: bool
    output: str


class GatewayServiceVersionInfo(BaseModel):
    service: GatewayService
    current_version: str | None = None
    target_version: str | None = None
    update_available: bool | None = None
    source: str
    detection_error: str | None = None


class GatewayServiceVersionsResult(BaseModel):
    items: list[GatewayServiceVersionInfo]
    checked_at: datetime


class GatewayWireGuardOverview(BaseModel):
    mode: str
    interface: str
    systemd_unit: str
    endpoint: str
    client_subnet: str
    vm_subnet: str
    session_ttl_seconds: int
    reconcile_enabled: bool
    authorized_sessions: int
    expired_sessions: int
    live_peers: int
    recent_handshakes: int
    transfer_rx_bytes: int
    transfer_tx_bytes: int
    listen_port: int | None = None
    inspection_available: bool
    inspected_at: datetime


__all__ = [
    "GatewayService",
    "ServiceAction",
    "GatewayConfigPublic",
    "GatewayConfigUpdate",
    "GatewayConnectionTestResult",
    "ServiceConfigRead",
    "ServiceConfigWrite",
    "ServiceStatusResult",
    "ServiceActionResult",
    "GatewayServiceVersionInfo",
    "GatewayServiceVersionsResult",
    "GatewayWireGuardOverview",
]
