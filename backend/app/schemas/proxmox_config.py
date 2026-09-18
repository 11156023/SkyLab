"""Proxmox 設定相關 schemas"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.infrastructure.proxmox import DEFAULT_PROXMOX_POOL_NAME


class ProxmoxConfigPublic(BaseModel):
    """回傳給前端的 Proxmox 設定（不含密碼與憑證原文）"""

    host: str
    user: str
    verify_ssl: bool
    iso_storage: str
    data_storage: str
    api_timeout: int
    task_check_interval: int
    pool_name: str
    gateway_ip: str | None = None  # 可能尚未設定（舊資料相容）
    local_subnet: str | None = None
    default_node: str | None = None
    cpu_overcommit_ratio: float = 2.0
    disk_overcommit_ratio: float = 1.0
    placement_reassignment_cost: float = 0.15
    placement_peak_cpu_margin: float = 1.1
    placement_peak_memory_margin: float = 1.05
    placement_loadavg_warn_per_core: float = 0.8
    placement_loadavg_max_per_core: float = 1.5
    placement_loadavg_penalty_weight: float = 0.9
    placement_disk_contention_warn_share: float = 0.7
    placement_disk_contention_high_share: float = 0.9
    placement_disk_penalty_weight: float = 0.75
    placement_cpu_peak_warn_share: float = 0.7
    placement_cpu_peak_high_share: float = 1.2
    placement_memory_peak_warn_share: float = 0.8
    placement_memory_peak_high_share: float = 0.85
    placement_resource_weight_cpu: float = 1.0
    placement_resource_weight_memory: float = 1.0
    placement_resource_weight_disk: float = 1.0
    scheduled_boot_batch_size: int = 5
    scheduled_boot_batch_interval_seconds: int = 10
    scheduled_boot_lead_time_minutes: int = 5
    window_grace_period_minutes: int = 30
    practice_session_hours: int = 3
    practice_warning_minutes: int = 30
    expiry_warning_hours: int = 24
    updated_at: datetime | None = None
    is_configured: bool
    has_ca_cert: bool
    ca_fingerprint: str | None = None  # SHA-256 指紋，供前端顯示確認


class ProxmoxConfigUpdate(BaseModel):
    """更新 Proxmox 設定的請求 schema。

    PUT / 為**部分更新**：route 以 ``model_dump(exclude_unset=True)`` 判斷
    payload 實際帶了哪些欄位，沒出現的欄位維持 DB 現值（尚無設定列時
    退回此 schema 的預設值）。呼叫端只需送自己管的欄位——資源排程頁
    因此不必回送連線欄位。欄位預設值僅在「初次建立且未帶該欄位」時生效。
    """

    host: str = ""
    user: str = ""
    password: str | None = None  # None 表示不更新密碼
    verify_ssl: bool = False
    iso_storage: str = "local"
    data_storage: str = "local-lvm"
    api_timeout: int = Field(default=30, ge=1, le=300)
    task_check_interval: int = Field(default=2, ge=1, le=60)
    pool_name: str = DEFAULT_PROXMOX_POOL_NAME
    ca_cert: str | None = None  # None 表示不更新；空字串表示清除
    gateway_ip: str | None = None
    local_subnet: str | None = None
    default_node: str | None = None
    cpu_overcommit_ratio: float = Field(default=2.0, ge=1.0, le=8.0)
    disk_overcommit_ratio: float = Field(default=1.0, ge=1.0, le=5.0)
    placement_reassignment_cost: float = Field(default=0.15, ge=0.0, le=5.0)
    placement_peak_cpu_margin: float = Field(default=1.1, ge=1.0, le=2.0)
    placement_peak_memory_margin: float = Field(default=1.05, ge=1.0, le=2.0)
    placement_loadavg_warn_per_core: float = Field(default=0.8, ge=0.0, le=4.0)
    placement_loadavg_max_per_core: float = Field(default=1.5, ge=0.1, le=8.0)
    placement_loadavg_penalty_weight: float = Field(default=0.9, ge=0.0, le=5.0)
    placement_disk_contention_warn_share: float = Field(default=0.7, ge=0.0, le=1.5)
    placement_disk_contention_high_share: float = Field(default=0.9, ge=0.1, le=2.0)
    placement_disk_penalty_weight: float = Field(default=0.75, ge=0.0, le=5.0)
    placement_cpu_peak_warn_share: float = Field(default=0.7, ge=0.0, le=2.0)
    placement_cpu_peak_high_share: float = Field(default=1.2, ge=0.1, le=3.0)
    placement_memory_peak_warn_share: float = Field(default=0.8, ge=0.0, le=2.0)
    placement_memory_peak_high_share: float = Field(default=0.85, ge=0.1, le=3.0)
    placement_resource_weight_cpu: float = Field(default=1.0, ge=0.0, le=10.0)
    placement_resource_weight_memory: float = Field(default=1.0, ge=0.0, le=10.0)
    placement_resource_weight_disk: float = Field(default=1.0, ge=0.0, le=10.0)
    scheduled_boot_batch_size: int = Field(default=5, ge=1, le=100)
    scheduled_boot_batch_interval_seconds: int = Field(default=10, ge=0, le=600)
    scheduled_boot_lead_time_minutes: int = Field(default=5, ge=0, le=120)
    window_grace_period_minutes: int = Field(default=30, ge=0, le=240)
    practice_session_hours: int = Field(default=3, ge=1, le=24)
    practice_warning_minutes: int = Field(default=30, ge=1, le=120)
    expiry_warning_hours: int = Field(default=24, ge=1, le=720)


class ProxmoxConnectionPublic(BaseModel):
    """回傳給前端的 PVE 連線資訊（不含密碼與憑證原文）"""

    id: int
    name: str
    host: str
    port: int
    user: str
    verify_ssl: bool
    api_timeout: int
    pool_name: str
    iso_storage: str
    data_storage: str
    task_check_interval: int
    gateway_ip: str | None = None
    local_subnet: str | None = None
    default_node: str | None = None
    enabled: bool
    is_default: bool
    has_ca_cert: bool
    node_count: int = 0
    updated_at: datetime | None = None


class ProxmoxConnectionCreate(BaseModel):
    """新增 PVE 連線的請求 schema"""

    name: str = Field(min_length=1, max_length=255)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=8006, ge=1, le=65535)
    user: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1)
    verify_ssl: bool = False
    ca_cert: str | None = None
    api_timeout: int = Field(default=30, ge=1, le=300)
    pool_name: str = Field(default=DEFAULT_PROXMOX_POOL_NAME, max_length=255)
    iso_storage: str = Field(default="local", max_length=255)
    data_storage: str = Field(default="local-lvm", max_length=255)
    task_check_interval: int = Field(default=2, ge=1, le=60)
    gateway_ip: str | None = None
    local_subnet: str | None = None
    default_node: str | None = None
    enabled: bool = True
    is_default: bool = False


class ProxmoxConnectionUpdateIn(BaseModel):
    """更新 PVE 連線的請求 schema"""

    name: str = Field(min_length=1, max_length=255)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=8006, ge=1, le=65535)
    user: str = Field(min_length=1, max_length=255)
    password: str | None = None  # None 表示不更新密碼
    verify_ssl: bool = False
    ca_cert: str | None = None  # None 表示不更新；空字串表示清除
    api_timeout: int = Field(default=30, ge=1, le=300)
    pool_name: str = Field(default=DEFAULT_PROXMOX_POOL_NAME, max_length=255)
    iso_storage: str = Field(default="local", max_length=255)
    data_storage: str = Field(default="local-lvm", max_length=255)
    task_check_interval: int = Field(default=2, ge=1, le=60)
    gateway_ip: str | None = None
    local_subnet: str | None = None
    default_node: str | None = None
    enabled: bool = True
    is_default: bool = False


class CertParseResult(BaseModel):
    """解析憑證 PEM 的結果"""

    valid: bool
    fingerprint: str | None = None  # SHA-256 指紋（冒號分隔大寫十六進位）
    subject: str | None = None
    issuer: str | None = None
    not_before: str | None = None
    not_after: str | None = None
    error: str | None = None


class ProxmoxConnectionTestResult(BaseModel):
    """連線測試結果"""

    success: bool
    message: str


class ProxmoxNodePublic(BaseModel):
    """回傳給前端的節點資訊"""

    id: int | None = None
    name: str
    host: str
    port: int
    is_primary: bool
    is_online: bool
    last_checked: datetime | None = None
    priority: int = 5
    enabled: bool = True


class ProxmoxNodeUpdate(BaseModel):
    """更新單一節點設定的請求 schema"""

    host: str
    port: int = Field(default=8006, ge=1, le=65535)
    priority: int = Field(default=5, ge=1, le=10)
    enabled: bool = True


class ConnectionSyncResult(BaseModel):
    """單一連線同步節點與 Storage 的結果"""

    success: bool
    connection_id: int
    nodes: list[ProxmoxNodePublic]
    storage_count: int
    error: str | None = None


class ClusterPreviewResult(BaseModel):
    """偵測叢集節點的預覽結果（不儲存）"""

    success: bool
    is_cluster: bool          # True 代表有多個節點
    nodes: list[ProxmoxNodePublic]
    error: str | None = None


class ProxmoxStoragePublic(BaseModel):
    """回傳給前端的 Storage 資訊。

    共享 Storage（``is_shared``）是整個叢集共用同一份實體儲存，
    列表中只保留一筆代表記錄，涵蓋的節點列在 ``node_names``。
    """

    id: int
    node_name: str            # 代表記錄所在的節點
    node_names: list[str] = []  # 這筆代表的所有節點（非共享時僅一個）
    connection_id: int | None = None
    connection_name: str | None = None
    storage: str
    storage_type: str | None = None
    total_gb: float
    used_gb: float
    avail_gb: float
    can_vm: bool
    can_lxc: bool
    can_iso: bool
    can_backup: bool
    is_shared: bool
    active: bool
    enabled: bool
    speed_tier: str   # "nvme"|"ssd"|"hdd"|"unknown"
    user_priority: int


class ProxmoxStorageUpdate(BaseModel):
    """更新 Storage 使用者設定的請求 schema"""

    enabled: bool
    speed_tier: str = Field(pattern=r"^(nvme|ssd|hdd|unknown)$")
    user_priority: int = Field(ge=1, le=10)


class SyncNowResult(BaseModel):
    """同步節點與 Storage 結果"""

    success: bool
    nodes: list[ProxmoxNodePublic]
    storage_count: int
    error: str | None = None


__all__ = [
    "ConnectionSyncResult",
    "ProxmoxConfigPublic",
    "ProxmoxConfigUpdate",
    "ProxmoxConnectionCreate",
    "ProxmoxConnectionPublic",
    "ProxmoxConnectionTestResult",
    "ProxmoxConnectionUpdateIn",
    "CertParseResult",
    "ProxmoxNodePublic",
    "ClusterPreviewResult",
    "ProxmoxNodeUpdate",
    "ProxmoxStoragePublic",
    "ProxmoxStorageUpdate",
    "SyncNowResult",
]
