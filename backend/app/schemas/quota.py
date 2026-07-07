"""配額 API schemas。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models import QuotaScope


class ResourceQuotaCreate(BaseModel):
    scope: QuotaScope
    group_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    max_cpu_cores: int = Field(default=8, ge=1, le=256)
    max_memory_mb: int = Field(default=16384, ge=256, le=1048576)
    max_disk_gb: int = Field(default=100, ge=1, le=65536)
    max_instances: int = Field(default=5, ge=1, le=100)

    @model_validator(mode="after")
    def _validate_target(self) -> "ResourceQuotaCreate":
        if self.scope == QuotaScope.group and self.group_id is None:
            raise ValueError("scope=group requires group_id")
        if self.scope == QuotaScope.user and self.user_id is None:
            raise ValueError("scope=user requires user_id")
        return self


class ResourceQuotaUpdate(BaseModel):
    max_cpu_cores: int | None = Field(default=None, ge=1, le=256)
    max_memory_mb: int | None = Field(default=None, ge=256, le=1048576)
    max_disk_gb: int | None = Field(default=None, ge=1, le=65536)
    max_instances: int | None = Field(default=None, ge=1, le=100)


class ResourceQuotaPublic(BaseModel):
    id: uuid.UUID
    scope: QuotaScope
    group_id: uuid.UUID | None
    user_id: uuid.UUID | None
    group_name: str | None = None
    user_email: str | None = None
    max_cpu_cores: int
    max_memory_mb: int
    max_disk_gb: int
    max_instances: int
    created_at: datetime


class EffectiveQuotaPublic(BaseModel):
    max_cpu_cores: int
    max_memory_mb: int
    max_disk_gb: int
    max_instances: int


class QuotaUsagePublic(BaseModel):
    used_cpu_cores: int
    used_memory_mb: int
    used_disk_gb: int
    used_instances: int
    quota: EffectiveQuotaPublic
