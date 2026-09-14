"""監控與治理 API schemas。"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models import AlertMetric, AlertScope


class NodeMetrics(BaseModel):
    """單一節點即時用量（來源：PVE /nodes）。"""

    node: str
    status: str
    cpu: float
    maxcpu: int
    mem: int
    maxmem: int
    disk: int
    maxdisk: int
    uptime: int
    vm_count: int = 0
    connection_name: str | None = None  # 所屬 PVE 連線；None = 尚未歸屬（舊資料）


class VMTopEntry(BaseModel):
    """高耗用 VM/LXC 條目（來源：PVE cluster/resources）。"""

    vmid: int
    name: str
    node: str
    type: str
    cpu: float
    mem: int
    maxmem: int
    status: str


class MonitoringThresholds(BaseModel):
    """即時快看使用的 CPU／RAM 閾值（百分比）。"""

    cpu: float = Field(default=90.0, ge=0, le=100)
    memory: float = Field(default=90.0, ge=0, le=100)


class MonitoringSignal(BaseModel):
    """單一資源超過閾值的指標。"""

    metric: Literal["cpu", "memory"]
    value: float = Field(..., ge=0)
    threshold: float = Field(..., ge=0, le=100)


class MonitoringIssue(BaseModel):
    """首頁快看需要管理員注意的單一目標。

    同一台機器可同時有 CPU/RAM 多個 signal，但 issues 只計一筆，
    避免摘要數字與明細數量不一致。
    """

    kind: Literal["node_offline", "node_overloaded", "guest_overloaded"]
    severity: Literal["critical", "warning"]
    scope: Literal["node", "qemu", "lxc"]
    target: str
    node: str | None = None
    vmid: int | None = None
    signals: list[MonitoringSignal] = Field(default_factory=list)


class MonitoringOverview(BaseModel):
    """全域監控匯總。"""

    collected_at: datetime
    data_status: Literal["fresh", "stale", "partial"] = "fresh"
    cache_age_seconds: int = Field(default=0, ge=0)
    overall_status: Literal["healthy", "warning", "critical", "unknown"]
    thresholds: MonitoringThresholds
    nodes_online: int
    nodes_total: int
    nodes_saturated: int = 0
    cpu_used: float
    cpu_total: int
    mem_used: int
    mem_total: int
    disk_used: int
    disk_total: int
    vms_running: int
    vms_stopped: int
    vms_saturated: int = 0
    lxc_running: int
    lxc_stopped: int
    lxc_saturated: int = 0
    nodes: list[NodeMetrics]
    top_cpu: list[VMTopEntry]
    top_mem: list[VMTopEntry]
    issues: list[MonitoringIssue] = Field(default_factory=list)


class AlertEventPublic(BaseModel):
    """警告事件（open = resolved_at 為 None）。"""

    id: uuid.UUID
    scope: AlertScope
    target: str
    metric: AlertMetric
    value: float
    threshold: float
    message: str
    created_at: datetime
    resolved_at: datetime | None = None
    acknowledged_by: uuid.UUID | None = None
    acknowledged_at: datetime | None = None


class GovernanceConfigPublic(BaseModel):
    alerts_enabled: bool
    alert_cpu_threshold: float
    alert_memory_threshold: float
    alert_disk_threshold: float
    alert_cooldown_minutes: int
    alert_check_interval_seconds: int
    alert_email_enabled: bool
    ttl_enabled: bool
    expiry_warn_days: int
    expiry_grace_delete_days: int
    idle_detection_enabled: bool
    idle_cpu_threshold_percent: float
    idle_window_hours: int
    idle_notify_after_hours: int
    idle_grace_hours: int
    idle_scan_batch_size: int
    workload_advisor_enabled: bool
    mining_detection_enabled: bool
    mining_cpu_threshold_percent: float
    mining_window_hours: int
    mining_scan_batch_size: int
    mining_auto_suspend: bool
    provision_max_concurrency: int
    snapshot_cleanup_enabled: bool
    snapshot_retention_days: int
    student_snapshot_max_count: int
    course_ttl_hours: int
    course_max_active_per_user: int
    updated_at: datetime


class GovernanceConfigUpdate(BaseModel):
    """治理設定更新（partial；範圍約束與 model 一致）。"""

    alerts_enabled: bool | None = None
    alert_cpu_threshold: float | None = Field(default=None, ge=50, le=100)
    alert_memory_threshold: float | None = Field(default=None, ge=50, le=100)
    alert_disk_threshold: float | None = Field(default=None, ge=50, le=100)
    alert_cooldown_minutes: int | None = Field(default=None, ge=1, le=1440)
    alert_check_interval_seconds: int | None = Field(default=None, ge=15, le=3600)
    alert_email_enabled: bool | None = None
    ttl_enabled: bool | None = None
    expiry_warn_days: int | None = Field(default=None, ge=1, le=30)
    expiry_grace_delete_days: int | None = Field(default=None, ge=0, le=90)
    idle_detection_enabled: bool | None = None
    idle_cpu_threshold_percent: float | None = Field(default=None, ge=0.1, le=20)
    idle_window_hours: int | None = Field(default=None, ge=1, le=720)
    idle_notify_after_hours: int | None = Field(default=None, ge=1, le=720)
    idle_grace_hours: int | None = Field(default=None, ge=1, le=720)
    idle_scan_batch_size: int | None = Field(default=None, ge=1, le=200)
    workload_advisor_enabled: bool | None = None
    mining_detection_enabled: bool | None = None
    mining_cpu_threshold_percent: float | None = Field(default=None, ge=50, le=100)
    mining_window_hours: int | None = Field(default=None, ge=1, le=72)
    mining_scan_batch_size: int | None = Field(default=None, ge=1, le=200)
    mining_auto_suspend: bool | None = None
    provision_max_concurrency: int | None = Field(default=None, ge=1, le=16)
    snapshot_cleanup_enabled: bool | None = None
    snapshot_retention_days: int | None = Field(default=None, ge=1, le=90)
    student_snapshot_max_count: int | None = Field(default=None, ge=1, le=10)
    course_ttl_hours: int | None = Field(default=None, ge=1, le=24)
    course_max_active_per_user: int | None = Field(default=None, ge=1, le=5)
