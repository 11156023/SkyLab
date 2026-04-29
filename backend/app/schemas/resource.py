"""資源與 Proxmox 相關 schemas"""

import unicodedata
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, model_validator


def _validate_unicode_hostname(v: str) -> str:
    """驗證 hostname：允許 Unicode 字母/數字和連字符，並檢查 Punycode 編碼後長度。"""
    if not v:
        raise ValueError("Hostname cannot be empty")
    if v.startswith("-") or v.endswith("-"):
        raise ValueError("Hostname cannot start or end with a hyphen")
    for ch in v:
        if ch == "-":
            continue
        cat = unicodedata.category(ch)
        if not (cat.startswith("L") or cat.startswith("N")):
            raise ValueError(
                "Only Unicode letters, digits, and hyphens are allowed in hostname"
            )
    # 檢查 Punycode 編碼後的長度是否仍在 DNS label 限制內（≤ 63 字元）
    try:
        encoded = v.encode("punycode").decode("ascii")
        # 如果包含非 ASCII 字元，實際 DNS label 會加上 "xn--" 前綴
        if not v.isascii():
            ace_label = f"xn--{encoded}"
        else:
            ace_label = v
        if len(ace_label) > 63:
            raise ValueError(
                f"Hostname exceeds 63 characters after Punycode encoding "
                f"(encoded length: {len(ace_label)})"
            )
    except UnicodeError as e:
        raise ValueError(f"Hostname cannot be encoded as valid Punycode: {e}") from e
    return v


UnicodeHostname = Annotated[str, AfterValidator(_validate_unicode_hostname)]


# ===== Proxmox Info Schemas =====


class NodeSchema(BaseModel):
    """Proxmox 節點資訊"""

    node: str
    status: str
    cpu: float | None = None
    maxcpu: int | None = None
    mem: int | None = None
    maxmem: int | None = None
    uptime: int | None = None


class VMSchema(BaseModel):
    """虛擬機資訊"""

    vmid: int
    name: str
    status: str
    node: str
    type: str
    cpu: float | None = None
    maxcpu: int | None = None
    mem: int | None = None
    maxmem: int | None = None
    uptime: int | None = None
    netin: int | None = None
    diskread: int | None = None
    diskwrite: int | None = None
    disk: int | None = None
    template: int | None = None
    memhost: int | None = None
    maxdisk: int | None = None


class VNCInfoSchema(BaseModel):
    """VNC 連線資訊"""

    vmid: int
    ws_url: str
    ticket: str | None = None
    port: str | None = None
    message: str


class TerminalInfoSchema(BaseModel):
    """LXC Terminal 連線資訊"""

    vmid: int
    ws_url: str
    ticket: str | None = None
    message: str


class TemplateSchema(BaseModel):
    """LXC OS template 資訊"""

    volid: str
    format: str
    size: int


class VMTemplateSchema(BaseModel):
    """VM template 資訊"""

    vmid: int
    name: str
    node: str


class NextVMIDSchema(BaseModel):
    """下一個可用 VMID"""

    next_vmid: int


# ===== Resource Request Schemas =====


class LXCCreateRequest(BaseModel):
    """建立 LXC 容器"""

    hostname: UnicodeHostname = Field(..., min_length=1, max_length=63)
    ostemplate: str
    cores: int = Field(1, ge=1, le=32)
    memory: int = Field(512, ge=128, le=65536)
    rootfs_size: int = Field(8, ge=1, le=1000)
    password: str = Field(..., min_length=6)
    storage: str = "local-lvm"
    environment_type: str
    os_info: str | None = None
    expiry_date: date | None = None
    start: bool = True
    unprivileged: bool = True
    service_template_slug: str | None = None


class VMCreateRequest(BaseModel):
    """建立 VM（cloud-init template）"""

    hostname: UnicodeHostname = Field(..., min_length=1, max_length=63)
    template_id: int
    username: str = Field(..., min_length=1, max_length=32)
    password: str = Field(..., min_length=6)
    cores: int = Field(2, ge=1, le=32)
    memory: int = Field(2048, ge=512, le=65536)
    disk_size: int = Field(20, ge=10, le=1000)
    storage: str = "local-lvm"
    environment_type: str
    os_info: str | None = None
    expiry_date: date | None = None
    start: bool = True
    service_template_slug: str | None = None


# ===== Resource Response Schemas =====


class LXCCreateResponse(BaseModel):
    """建立 LXC 回應"""

    vmid: int
    upid: str
    message: str


class VMCreateResponse(BaseModel):
    """建立 VM 回應"""

    vmid: int
    upid: str
    message: str


class ResourcePublic(BaseModel):
    """公開的資源資訊（合併 Proxmox + DB）"""

    vmid: int
    name: str
    status: str
    node: str
    type: str
    environment_type: str | None = None
    os_info: str | None = None
    expiry_date: date | None = None
    ip_address: str | None = None
    ssh_public_key: str | None = None
    service_template_slug: str | None = None
    cpu: float | None = None
    maxcpu: int | None = None
    mem: int | None = None
    maxmem: int | None = None
    uptime: int | None = None
    auto_stop_at: datetime | None = None
    auto_stop_reason: Literal["window_grace", "practice_quota"] | None = None


class SessionStatusResponse(BaseModel):
    """Live status of a VM's auto-stop session, polled by the student UI."""

    vmid: int
    running: bool
    auto_stop_at: datetime | None = None
    auto_stop_reason: Literal["window_grace", "practice_quota"] | None = None
    minutes_until_stop: int | None = None
    should_warn: bool = False
    can_extend: bool = False


class ExtendSessionResponse(BaseModel):
    """Returned after a successful session extension."""

    vmid: int
    auto_stop_at: datetime
    extended_minutes: int


class SSHKeyResponse(BaseModel):
    """SSH 金鑰回應"""

    vmid: int
    ssh_public_key: str | None = None
    ssh_private_key: str | None = None


# ===== Monitoring Schemas =====


class CurrentStatsResponse(BaseModel):
    """資源即時狀態"""

    cpu: float | None = Field(None, description="CPU usage (0-1)")
    maxcpu: int | None = Field(None, description="CPU cores")
    mem: int | None = Field(None, description="Memory usage (bytes)")
    maxmem: int | None = Field(None, description="Max memory (bytes)")
    disk: int | None = Field(None, description="Disk usage (bytes)")
    maxdisk: int | None = Field(None, description="Max disk (bytes)")
    netin: int | None = Field(None, description="Network in (bytes)")
    netout: int | None = Field(None, description="Network out (bytes)")
    uptime: int | None = Field(None, description="Uptime (seconds)")
    status: str = Field(..., description="Status")


class RRDDataPoint(BaseModel):
    """RRD 數據點"""

    time: int = Field(..., description="Timestamp")
    cpu: float | None = None
    maxcpu: int | None = None
    mem: float | None = None
    maxmem: float | None = None
    disk: float | None = None
    maxdisk: float | None = None
    netin: float | None = None
    netout: float | None = None


class RRDDataResponse(BaseModel):
    """RRD 歷史數據"""

    timeframe: str = Field(..., description="Time range")
    data: list[RRDDataPoint] = Field(..., description="Data points")


# ===== Snapshot Schemas =====


class SnapshotInfo(BaseModel):
    """快照資訊"""

    name: str = Field(..., description="Snapshot name")
    description: str | None = Field(None, description="Snapshot description")
    snaptime: int | None = Field(None, description="Creation timestamp")
    vmstate: int | None = Field(None, description="Includes VM state (0/1)")


class SnapshotCreateRequest(BaseModel):
    """建立快照"""

    snapname: str = Field(..., min_length=1, max_length=40, description="Snapshot name")
    description: str | None = Field(None, max_length=255, description="Snapshot description")
    vmstate: bool = Field(False, description="Include RAM state (VM only)")


class SnapshotResponse(BaseModel):
    """快照操作回應"""

    message: str
    task_id: str | None = None


# ===== Admin Spec Update Schema =====


class DirectSpecUpdateRequest(BaseModel):
    """管理員直接調整規格"""

    cores: int | None = Field(None, ge=1, le=32, description="CPU cores")
    memory: int | None = Field(None, ge=512, le=65536, description="Memory (MB)")
    disk_size: str | None = Field(
        None, pattern=r"^\+\d+G$", description='Disk size increment (e.g. "+10G")'
    )

    @model_validator(mode="after")
    def at_least_one_field(self):
        if self.cores is None and self.memory is None and self.disk_size is None:
            raise ValueError("At least one of cores, memory, or disk_size must be provided")
        return self


# ===== Batch Operation Schemas =====


class BatchActionRequest(BaseModel):
    """批次操作請求"""

    vmids: list[int] = Field(..., min_length=1, max_length=100, description="VM IDs to operate on")
    action: str = Field(
        ...,
        description="Action: start, stop, shutdown, reboot, reset, delete",
    )

    @model_validator(mode="after")
    def validate_action(self):
        valid = {"start", "stop", "shutdown", "reboot", "reset", "delete"}
        if self.action not in valid:
            raise ValueError(f"Invalid action '{self.action}'. Must be one of: {', '.join(sorted(valid))}")
        return self


class BatchActionResultItem(BaseModel):
    """單一 VM 的批次操作結果"""

    vmid: int
    success: bool
    message: str


class BatchActionResponse(BaseModel):
    """批次操作回應"""

    total: int
    succeeded: int
    failed: int
    results: list[BatchActionResultItem]
