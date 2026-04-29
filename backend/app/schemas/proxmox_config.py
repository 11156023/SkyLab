"""Proxmox 設定相關 schemas"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.placement.constants import DEFAULT_PLACEMENT_STRATEGY
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
    placement_strategy: str = DEFAULT_PLACEMENT_STRATEGY
    cpu_overcommit_ratio: float = 2.0
    disk_overcommit_ratio: float = 1.0
    migration_enabled: bool = True
    migration_max_per_rebalance: int = 2
    migration_min_interval_minutes: int = 60
    migration_retry_limit: int = 3
    rebalance_migration_cost: float = 0.15
    rebalance_peak_cpu_margin: float = 1.1
    rebalance_peak_memory_margin: float = 1.05
    rebalance_loadavg_warn_per_core: float = 0.8
    rebalance_loadavg_max_per_core: float = 1.5
    rebalance_loadavg_penalty_weight: float = 0.9
    rebalance_disk_contention_warn_share: float = 0.7
    rebalance_disk_contention_high_share: float = 0.9
    rebalance_disk_penalty_weight: float = 0.75
    rebalance_search_max_relocations: int = 2
    rebalance_search_depth: int = 3
    migration_worker_concurrency: int = 2
    migration_job_claim_timeout_seconds: int = 300
    migration_retry_backoff_seconds: int = 120
    migration_lxc_live_enabled: bool = False
    rebalance_cpu_peak_warn_share: float = 0.7
    rebalance_cpu_peak_high_share: float = 1.2
    rebalance_memory_peak_warn_share: float = 0.8
    rebalance_memory_peak_high_share: float = 0.85
    rebalance_resource_weight_cpu: float = 1.0
    rebalance_resource_weight_memory: float = 1.0
    rebalance_resource_weight_disk: float = 1.0
    scheduled_boot_batch_size: int = 5
    scheduled_boot_batch_interval_seconds: int = 10
    scheduled_boot_lead_time_minutes: int = 5
    window_grace_period_minutes: int = 30
    practice_session_hours: int = 3
    practice_warning_minutes: int = 30
    updated_at: datetime | None = None
    is_configured: bool
    has_ca_cert: bool
    ca_fingerprint: str | None = None  # SHA-256 指紋，供前端顯示確認


class ProxmoxConfigUpdate(BaseModel):
    """更新 Proxmox 設定的請求 schema"""

    host: str
    user: str
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
    placement_strategy: str = DEFAULT_PLACEMENT_STRATEGY
    cpu_overcommit_ratio: float = Field(default=2.0, ge=1.0, le=8.0)
    disk_overcommit_ratio: float = Field(default=1.0, ge=1.0, le=5.0)
    migration_enabled: bool = True
    migration_max_per_rebalance: int = Field(default=2, ge=0, le=20)
    migration_min_interval_minutes: int = Field(default=60, ge=0, le=10080)
    migration_retry_limit: int = Field(default=3, ge=0, le=10)
    rebalance_migration_cost: float = Field(default=0.15, ge=0.0, le=5.0)
    rebalance_peak_cpu_margin: float = Field(default=1.1, ge=1.0, le=2.0)
    rebalance_peak_memory_margin: float = Field(default=1.05, ge=1.0, le=2.0)
    rebalance_loadavg_warn_per_core: float = Field(default=0.8, ge=0.0, le=4.0)
    rebalance_loadavg_max_per_core: float = Field(default=1.5, ge=0.1, le=8.0)
    rebalance_loadavg_penalty_weight: float = Field(default=0.9, ge=0.0, le=5.0)
    rebalance_disk_contention_warn_share: float = Field(default=0.7, ge=0.0, le=1.5)
    rebalance_disk_contention_high_share: float = Field(default=0.9, ge=0.1, le=2.0)
    rebalance_disk_penalty_weight: float = Field(default=0.75, ge=0.0, le=5.0)
    rebalance_search_max_relocations: int = Field(default=2, ge=0, le=10)
    rebalance_search_depth: int = Field(default=3, ge=0, le=10)
    migration_worker_concurrency: int = Field(default=2, ge=1, le=20)
    migration_job_claim_timeout_seconds: int = Field(default=300, ge=30, le=86400)
    migration_retry_backoff_seconds: int = Field(default=120, ge=0, le=86400)
    migration_lxc_live_enabled: bool = False
    rebalance_cpu_peak_warn_share: float = Field(default=0.7, ge=0.0, le=2.0)
    rebalance_cpu_peak_high_share: float = Field(default=1.2, ge=0.1, le=3.0)
    rebalance_memory_peak_warn_share: float = Field(default=0.8, ge=0.0, le=2.0)
    rebalance_memory_peak_high_share: float = Field(default=0.85, ge=0.1, le=3.0)
    rebalance_resource_weight_cpu: float = Field(default=1.0, ge=0.0, le=10.0)
    rebalance_resource_weight_memory: float = Field(default=1.0, ge=0.0, le=10.0)
    rebalance_resource_weight_disk: float = Field(default=1.0, ge=0.0, le=10.0)
    scheduled_boot_batch_size: int = Field(default=5, ge=1, le=100)
    scheduled_boot_batch_interval_seconds: int = Field(default=10, ge=0, le=600)
    scheduled_boot_lead_time_minutes: int = Field(default=5, ge=0, le=120)
    window_grace_period_minutes: int = Field(default=30, ge=0, le=240)
    practice_session_hours: int = Field(default=3, ge=1, le=24)
    practice_warning_minutes: int = Field(default=30, ge=1, le=120)


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


class ProxmoxNodeUpdate(BaseModel):
    """更新單一節點設定的請求 schema"""

    host: str
    port: int = Field(default=8006, ge=1, le=65535)
    priority: int = Field(default=5, ge=1, le=10)


class ClusterPreviewResult(BaseModel):
    """偵測叢集節點的預覽結果（不儲存）"""

    success: bool
    is_cluster: bool          # True 代表有多個節點
    nodes: list[ProxmoxNodePublic]
    error: str | None = None


class ProxmoxStoragePublic(BaseModel):
    """回傳給前端的 Storage 資訊"""

    id: int
    node_name: str
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


class NodeStatsPublic(BaseModel):
    """單一節點的即時資源使用狀態"""

    name: str
    status: str
    cpu_usage_pct: float   # 0–100
    cpu_cores: int
    mem_used_gb: float
    mem_total_gb: float
    disk_used_gb: float
    disk_total_gb: float
    vm_count: int = 0


class ClusterStatsPublic(BaseModel):
    """整個叢集的資源加總與各節點狀態"""

    nodes: list[NodeStatsPublic]
    total_cpu_cores: int
    used_cpu_cores: float   # weighted sum from cpu_ratio * maxcpu
    total_mem_gb: float
    used_mem_gb: float
    total_disk_gb: float
    used_disk_gb: float
    online_count: int
    offline_count: int
    total_vm_count: int


__all__ = [
    "ProxmoxConfigPublic",
    "ProxmoxConfigUpdate",
    "ProxmoxConnectionTestResult",
    "CertParseResult",
    "ProxmoxNodePublic",
    "ClusterPreviewResult",
    "ProxmoxNodeUpdate",
    "ProxmoxStoragePublic",
    "ProxmoxStorageUpdate",
    "SyncNowResult",
    "NodeStatsPublic",
    "ClusterStatsPublic",
]
