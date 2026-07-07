"""治理設定模型（告警閾值、TTL/閒置政策、Auto 判斷開關）。"""

from datetime import datetime

from sqlmodel import Column, DateTime, Field, SQLModel

from .base import get_datetime_utc


class GovernanceConfig(SQLModel, table=True):
    """治理設定（單列 singleton，id 固定為 1）"""

    __tablename__ = "governance_config"

    id: int = Field(default=1, primary_key=True)

    # ── 資源告警 ──────────────────────────────────────────────────────────
    alerts_enabled: bool = Field(default=True)
    alert_cpu_threshold: float = Field(default=90.0, ge=50, le=100)
    alert_memory_threshold: float = Field(default=90.0, ge=50, le=100)
    alert_disk_threshold: float = Field(default=90.0, ge=50, le=100)
    alert_cooldown_minutes: int = Field(default=30, ge=1, le=1440)
    alert_check_interval_seconds: int = Field(default=60, ge=15, le=3600)
    alert_email_enabled: bool = Field(default=True)

    # ── TTL 漸進回收 ──────────────────────────────────────────────────────
    ttl_enabled: bool = Field(default=True)
    expiry_warn_days: int = Field(default=3, ge=1, le=30)
    expiry_grace_delete_days: int = Field(default=7, ge=0, le=90)

    # ── 閒置偵測 ──────────────────────────────────────────────────────────
    idle_detection_enabled: bool = Field(default=True)
    idle_cpu_threshold_percent: float = Field(default=1.0, ge=0.1, le=20)
    idle_window_hours: int = Field(default=48, ge=1, le=720)
    idle_grace_hours: int = Field(default=24, ge=1, le=720)
    idle_scan_batch_size: int = Field(default=20, ge=1, le=200)

    # ── VM vs Container 自動判斷 ─────────────────────────────────────────
    workload_advisor_enabled: bool = Field(default=True)

    # ── 反挖礦偵測（模組 D）──────────────────────────────────────────────
    mining_detection_enabled: bool = Field(default=True)
    mining_cpu_threshold_percent: float = Field(default=90.0, ge=50, le=100)
    mining_window_hours: int = Field(default=6, ge=1, le=72)
    mining_scan_batch_size: int = Field(default=20, ge=1, le=200)
    mining_auto_suspend: bool = Field(default=True)

    # ── 克隆並行化（模組 D）──────────────────────────────────────────────
    provision_max_concurrency: int = Field(default=4, ge=1, le=16)

    # ── 快照治理（模組 E）─────────────────────────────────────────────────────────
    snapshot_cleanup_enabled: bool = Field(default=True)
    snapshot_retention_days: int = Field(default=7, ge=1, le=90)
    student_snapshot_max_count: int = Field(default=3, ge=1, le=10)

    # ── 課程實驗室（Course Lab）──────────────────────────────────────────
    course_ttl_hours: int = Field(default=3, ge=1, le=24)
    course_max_active_per_user: int = Field(default=1, ge=1, le=5)

    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["GovernanceConfig"]
