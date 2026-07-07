"""監控與治理 API schemas。"""

import uuid
from datetime import datetime

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


class MonitoringOverview(BaseModel):
    """全域監控匯總。"""

    nodes_online: int
    nodes_total: int
    cpu_used: float
    cpu_total: int
    mem_used: int
    mem_total: int
    disk_used: int
    disk_total: int
    vms_running: int
    vms_stopped: int
    lxc_running: int
    lxc_stopped: int
    nodes: list[NodeMetrics]
    top_cpu: list[VMTopEntry]
    top_mem: list[VMTopEntry]


class AlertEventPublic(BaseModel):
    """告警事件（open = resolved_at 為 None）。"""

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
