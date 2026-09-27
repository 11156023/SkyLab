"""全域預設資源配額（單列 singleton）。"""

from datetime import datetime

from sqlmodel import Column, DateTime, Field, SQLModel

from .base import get_datetime_utc


class QuotaConfig(SQLModel, table=True):
    """全域預設配額（單列 singleton，id 固定為 1）

    沒有個人覆寫（ResourceQuota）的使用者套用此值。欄位刻意與 ResourceQuota
    同名，讓 quota_policy.resolve_effective_quota 兩種來源共用同一套解析。
    各上限欄位 0 = 無限制（不執法）。
    """

    __tablename__ = "quota_config"

    id: int = Field(default=1, primary_key=True)
    max_cpu_cores: int = Field(default=8, ge=0, le=256)
    max_memory_mb: int = Field(default=16384, ge=0, le=1048576)
    max_disk_gb: int = Field(default=100, ge=0, le=65536)
    max_instances: int = Field(default=5, ge=0, le=100)
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=get_datetime_utc),
    )


__all__ = ["QuotaConfig"]
