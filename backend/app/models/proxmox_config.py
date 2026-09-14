"""Proxmox 連線設定模型"""

from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from .base import get_datetime_utc


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
    cpu_overcommit_ratio: float = Field(default=2.0)
    disk_overcommit_ratio: float = Field(default=1.0)
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


__all__ = ["ProxmoxConfig"]
