"""Gateway VM 管理相關 schemas"""

import ipaddress
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

GatewayService = Literal["nginx", "wireguard"]
GatewayInstallState = Literal["idle", "running", "succeeded", "failed", "interrupted"]

# Linux 網卡名稱最長 15 字元；這些值會當成環境變數交給 install.sh，
# 只收字母數字與 _ . - 避免帶進 shell 特殊字元
_INTERFACE_NAME_PATTERN = r"^[A-Za-z0-9_.-]{1,15}$"


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


class GatewayInstallOptions(BaseModel):
    """一鍵安裝時交給 install.sh 的參數（對應腳本裡同名的環境變數）。

    WireGuard 的介面名稱與兩個子網要和後端的 WIREGUARD_* 設定一致，
    所以不開放在這裡改，直接取自 settings。
    """

    ingress_interface: str = Field(default="eth0", pattern=_INTERFACE_NAME_PATTERN)
    vm_interface: str = Field(default="eth1", pattern=_INTERFACE_NAME_PATTERN)
    snat_address: str = Field(default="10.10.0.2", max_length=45)
    listen_port: int = Field(default=51821, ge=1, le=65535)
    forward_port_start: int = Field(default=30000, ge=1, le=65535)
    forward_port_end: int = Field(default=39999, ge=1, le=65535)
    monitoring_allow_from: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("snat_address")
    @classmethod
    def _validate_snat_address(cls, value: str) -> str:
        return str(ipaddress.IPv4Address(value.strip()))

    @field_validator("monitoring_allow_from")
    @classmethod
    def _validate_monitoring_sources(cls, value: list[str]) -> list[str]:
        sources: list[str] = []
        for raw in value:
            item = raw.strip()
            if not item:
                continue
            # 單一 IP 或 CIDR 都收；strict=False 讓 192.168.1.5/24 這種寫法也過
            sources.append(str(ipaddress.ip_network(item, strict=False)))
        return list(dict.fromkeys(sources))

    @model_validator(mode="after")
    def _validate_port_range(self) -> "GatewayInstallOptions":
        if self.forward_port_start > self.forward_port_end:
            raise ValueError("forward_port_start must not exceed forward_port_end")
        if self.forward_port_start <= self.listen_port <= self.forward_port_end:
            raise ValueError("listen_port must not fall inside the forwarding port range")
        return self


class GatewayInstallInterface(BaseModel):
    name: str
    addresses: list[str]


class GatewayInstallStatus(BaseModel):
    state: GatewayInstallState
    # 連線帳號是 root，或能免密碼 sudo；兩者都不是就無法安裝
    root_access: bool
    exit_code: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    os_name: str | None = None
    interfaces: list[GatewayInstallInterface] = Field(default_factory=list)
    # nginx / wireguard / certbot / ufw 是否已安裝
    components: dict[str, bool] = Field(default_factory=dict)
    log: str = ""
    defaults: GatewayInstallOptions
    wireguard_interface: str
    wireguard_client_subnet: str
    wireguard_vm_subnet: str


class PlatformEntryUpdate(BaseModel):
    """平台入口設定；網域與上游格式由 service 檢查（錯誤訊息才能翻譯）。"""

    enabled: bool = False
    domain: str = Field(default="", max_length=255)
    upstream_host: str = Field(default="", max_length=255)
    upstream_port: int = Field(default=8082, ge=1, le=65535)
    enable_https: bool = True


class PlatformEntryPublic(BaseModel):
    enabled: bool
    domain: str
    upstream_host: str
    upstream_port: int
    enable_https: bool
    updated_at: datetime | None = None
    # Gateway 的 SSH 連線設定好了才能套用
    gateway_ready: bool
    # HTTPS 憑證走 Cloudflare DNS-01，需要先設定 API Token
    cloudflare_ready: bool
    # 主系統 nginx 要信任的代理位址（.env 的 SKYLAB_TRUSTED_PROXY 建議值）
    gateway_host: str


class PlatformEntryUpstreamTestRequest(BaseModel):
    upstream_host: str = Field(max_length=255)
    upstream_port: int = Field(default=8082, ge=1, le=65535)


class PlatformEntryUpstreamTest(BaseModel):
    reachable: bool
    detail: str


class PlatformEntryStatus(BaseModel):
    """Gateway 上實際套用的狀態（每次查詢都經 SSH 讀回）。"""

    # Gateway 的 http.conf 與目前儲存的設定一致
    applied: bool
    applied_domain: str | None = None
    applied_upstream: str | None = None
    applied_https: bool | None = None
    certificate: str | None = None
    certificate_ready: bool | None = None
    certificate_expires_at: datetime | None = None
    upstream_reachable: bool | None = None
    upstream_detail: str | None = None
    # 後端從這次請求看到的來源 IP 與通訊協定：經 Gateway 進來卻看到 Gateway 的
    # 位址或 http，代表主系統 nginx 還沒信任 Gateway（SKYLAB_TRUSTED_PROXY）
    observed_client_ip: str | None = None
    observed_scheme: str | None = None
    checked_at: datetime


__all__ = [
    "PlatformEntryUpdate",
    "PlatformEntryPublic",
    "PlatformEntryUpstreamTestRequest",
    "PlatformEntryUpstreamTest",
    "PlatformEntryStatus",
    "GatewayService",
    "GatewayInstallState",
    "GatewayInstallOptions",
    "GatewayInstallInterface",
    "GatewayInstallStatus",
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
