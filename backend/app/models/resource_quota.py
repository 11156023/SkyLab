"""資源配額模型（群組預設 + 個人覆寫）。"""

import uuid
from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Field, SQLModel

from .base import get_datetime_utc


class QuotaScope(str, Enum):
    group = "group"
    user = "user"


class ResourceQuota(SQLModel, table=True):
    """配額列：scope=group 時 group_id 必填；scope=user 時 user_id 必填（覆寫）。"""

    __tablename__ = "resource_quotas"
    __table_args__ = (
        sa.UniqueConstraint("group_id", name="uq_resource_quotas_group_id"),
        sa.UniqueConstraint("user_id", name="uq_resource_quotas_user_id"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    scope: QuotaScope
    group_id: uuid.UUID | None = Field(default=None, foreign_key="group.id")
    user_id: uuid.UUID | None = Field(default=None, foreign_key="user.id")
    max_cpu_cores: int = Field(default=8, ge=1, le=256)
    max_memory_mb: int = Field(default=16384, ge=256, le=1048576)
    max_disk_gb: int = Field(default=100, ge=1, le=65536)
    max_instances: int = Field(default=5, ge=1, le=100)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["QuotaScope", "ResourceQuota"]
