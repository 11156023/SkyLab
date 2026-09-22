"""個別使用者資源配額覆寫。"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Field, SQLModel

from .base import get_datetime_utc


class ResourceQuota(SQLModel, table=True):
    """個別使用者的配額覆寫。各上限欄位 0 = 無限制（不執法）。"""

    __tablename__ = "resource_quotas"
    __table_args__ = (
        sa.UniqueConstraint("user_id", name="uq_resource_quotas_user_id"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE")
    max_cpu_cores: int = Field(default=8, ge=0, le=256)
    max_memory_mb: int = Field(default=16384, ge=0, le=1048576)
    max_disk_gb: int = Field(default=100, ge=0, le=65536)
    max_instances: int = Field(default=5, ge=0, le=100)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["ResourceQuota"]
