"""Proxmox 連線設定模型"""

from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from .base import get_datetime_utc


@dataclass(frozen=True, slots=True)
class ProxmoxConnectionConfig:
    host: str
    user: str
    verify_ssl: bool
    iso_storage: str
    data_storage: str
    api_timeout: int
    task_check_interval: int
    pool_name: str
    ca_cert: str | None
    gateway_ip: str | None
    local_subnet: str | None
    default_node: str | None


@dataclass(frozen=True, slots=True)
class ProxmoxPlacementConfig:
    strategy: str
    cpu_overcommit_ratio: float
    disk_overcommit_ratio: float


@dataclass(frozen=True, slots=True)
class ProxmoxMigrationConfig:
    enabled: bool
    max_per_rebalance: int
    min_interval_minutes: int
    retry_limit: int
    worker_concurrency: int
    job_claim_timeout_seconds: int
    retry_backoff_seconds: int
    lxc_live_enabled: bool


@dataclass(frozen=True, slots=True)
class ProxmoxRebalanceConfig:
    migration_cost: float
    peak_cpu_margin: float
    peak_memory_margin: float
    loadavg_warn_per_core: float
    loadavg_max_per_core: float
    loadavg_penalty_weight: float
    disk_contention_warn_share: float
    disk_contention_high_share: float
    disk_penalty_weight: float
    search_max_relocations: int
    search_depth: int
    cpu_peak_warn_share: float
    cpu_peak_high_share: float
    memory_peak_warn_share: float
    memory_peak_high_share: float
    resource_weight_cpu: float
    resource_weight_memory: float
    resource_weight_disk: float


@dataclass(frozen=True, slots=True)
class ProxmoxSchedulerConfig:
    boot_batch_size: int
    boot_batch_interval_seconds: int
    boot_lead_time_minutes: int
    window_grace_period_minutes: int
    practice_session_hours: int
    practice_warning_minutes: int
    expiry_warning_hours: int


class ProxmoxConfig(SQLModel, table=True):
    """Proxmox 連線設定（單列 singleton，id 固定為 1）"""

    __tablename__ = "proxmox_config"

    id: int = Field(default=1, primary_key=True)
    host: str = Field(max_length=255)
    user: str = Field(max_length=255)
    encrypted_password: str = Field(max_length=2048)
    verify_ssl: bool = Field(default=False)
    iso_storage: str = Field(default="local", max_length=255)
    data_storage: str = Field(default="local-lvm", max_length=255)
    api_timeout: int = Field(default=30)
    task_check_interval: int = Field(default=2)
    pool_name: str = Field(default="SkyLab", max_length=255)
    ca_cert: str | None = Field(default=None, sa_type=sa.Text())
    gateway_ip: str | None = Field(default=None, max_length=255)
    local_subnet: str | None = Field(default=None, max_length=50)
    default_node: str | None = Field(default=None, max_length=255)
    placement_strategy: str = Field(default="priority_dominant_share", max_length=64)
    cpu_overcommit_ratio: float = Field(default=2.0)
    disk_overcommit_ratio: float = Field(default=1.0)
    migration_enabled: bool = Field(default=True)
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
    migration_lxc_live_enabled: bool = Field(default=False)
    rebalance_cpu_peak_warn_share: float = Field(default=0.7, ge=0.0, le=2.0)
    rebalance_cpu_peak_high_share: float = Field(default=1.2, ge=0.1, le=3.0)
    rebalance_memory_peak_warn_share: float = Field(default=0.8, ge=0.0, le=2.0)
    rebalance_memory_peak_high_share: float = Field(default=0.85, ge=0.1, le=3.0)
    rebalance_resource_weight_cpu: float = Field(default=1.0, ge=0.0, le=10.0)
    rebalance_resource_weight_memory: float = Field(default=1.0, ge=0.0, le=10.0)
    rebalance_resource_weight_disk: float = Field(default=1.0, ge=0.0, le=10.0)
    # Scheduled boot / auto-stop tuning. The scheduler reads these at every tick.
    scheduled_boot_batch_size: int = Field(default=5, ge=1, le=100)
    scheduled_boot_batch_interval_seconds: int = Field(default=10, ge=0, le=600)
    scheduled_boot_lead_time_minutes: int = Field(default=5, ge=0, le=120)
    window_grace_period_minutes: int = Field(default=30, ge=0, le=240)
    practice_session_hours: int = Field(default=3, ge=1, le=24)
    practice_warning_minutes: int = Field(default=30, ge=1, le=120)
    expiry_warning_hours: int = Field(default=24, ge=1, le=720)
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=sa.DateTime(timezone=True),
    )

    @property
    def connection(self) -> ProxmoxConnectionConfig:
        return ProxmoxConnectionConfig(
            host=self.host,
            user=self.user,
            verify_ssl=self.verify_ssl,
            iso_storage=self.iso_storage,
            data_storage=self.data_storage,
            api_timeout=self.api_timeout,
            task_check_interval=self.task_check_interval,
            pool_name=self.pool_name,
            ca_cert=self.ca_cert,
            gateway_ip=self.gateway_ip,
            local_subnet=self.local_subnet,
            default_node=self.default_node,
        )

    @property
    def placement(self) -> ProxmoxPlacementConfig:
        return ProxmoxPlacementConfig(
            strategy=self.placement_strategy,
            cpu_overcommit_ratio=self.cpu_overcommit_ratio,
            disk_overcommit_ratio=self.disk_overcommit_ratio,
        )

    @property
    def migration(self) -> ProxmoxMigrationConfig:
        return ProxmoxMigrationConfig(
            enabled=self.migration_enabled,
            max_per_rebalance=self.migration_max_per_rebalance,
            min_interval_minutes=self.migration_min_interval_minutes,
            retry_limit=self.migration_retry_limit,
            worker_concurrency=self.migration_worker_concurrency,
            job_claim_timeout_seconds=self.migration_job_claim_timeout_seconds,
            retry_backoff_seconds=self.migration_retry_backoff_seconds,
            lxc_live_enabled=self.migration_lxc_live_enabled,
        )

    @property
    def rebalance(self) -> ProxmoxRebalanceConfig:
        return ProxmoxRebalanceConfig(
            migration_cost=self.rebalance_migration_cost,
            peak_cpu_margin=self.rebalance_peak_cpu_margin,
            peak_memory_margin=self.rebalance_peak_memory_margin,
            loadavg_warn_per_core=self.rebalance_loadavg_warn_per_core,
            loadavg_max_per_core=self.rebalance_loadavg_max_per_core,
            loadavg_penalty_weight=self.rebalance_loadavg_penalty_weight,
            disk_contention_warn_share=self.rebalance_disk_contention_warn_share,
            disk_contention_high_share=self.rebalance_disk_contention_high_share,
            disk_penalty_weight=self.rebalance_disk_penalty_weight,
            search_max_relocations=self.rebalance_search_max_relocations,
            search_depth=self.rebalance_search_depth,
            cpu_peak_warn_share=self.rebalance_cpu_peak_warn_share,
            cpu_peak_high_share=self.rebalance_cpu_peak_high_share,
            memory_peak_warn_share=self.rebalance_memory_peak_warn_share,
            memory_peak_high_share=self.rebalance_memory_peak_high_share,
            resource_weight_cpu=self.rebalance_resource_weight_cpu,
            resource_weight_memory=self.rebalance_resource_weight_memory,
            resource_weight_disk=self.rebalance_resource_weight_disk,
        )

    @property
    def scheduler(self) -> ProxmoxSchedulerConfig:
        return ProxmoxSchedulerConfig(
            boot_batch_size=self.scheduled_boot_batch_size,
            boot_batch_interval_seconds=self.scheduled_boot_batch_interval_seconds,
            boot_lead_time_minutes=self.scheduled_boot_lead_time_minutes,
            window_grace_period_minutes=self.window_grace_period_minutes,
            practice_session_hours=self.practice_session_hours,
            practice_warning_minutes=self.practice_warning_minutes,
            expiry_warning_hours=self.expiry_warning_hours,
        )


__all__ = [
    "ProxmoxConfig",
    "ProxmoxConnectionConfig",
    "ProxmoxMigrationConfig",
    "ProxmoxPlacementConfig",
    "ProxmoxRebalanceConfig",
    "ProxmoxSchedulerConfig",
]
