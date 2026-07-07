"""教學體驗 API schemas（E2/E3/E6）。"""

import uuid

from pydantic import BaseModel, Field


class ConfigPushAccepted(BaseModel):
    task_id: str


class ConfigPushItemPublic(BaseModel):
    vmid: int
    status: str  # pending | running | ok | error
    error: str | None = None


class ConfigPushStatusPublic(BaseModel):
    task_id: str
    file_name: str
    target_path: str
    items: list[ConfigPushItemPublic]


class HeatmapEntry(BaseModel):
    vmid: int
    name: str | None = None
    owner_id: uuid.UUID
    owner_name: str | None = None
    status: str
    cpu_percent: float
    mem_percent: float
    uptime_seconds: int
    activity: str  # running | idle | stale | stopped


class BatchSpecRequest(BaseModel):
    vmids: list[int] | None = None
    group_id: uuid.UUID | None = None
    cores: int | None = Field(default=None, ge=1, le=256)
    memory_mb: int | None = Field(default=None, ge=128, le=1048576)


class BatchSpecAccepted(BaseModel):
    task_id: str


class BatchSpecItemPublic(BaseModel):
    vmid: int
    status: str  # pending | running | ok | needs_restart | quota_exceeded | error
    error: str | None = None


class BatchSpecStatusPublic(BaseModel):
    task_id: str
    items: list[BatchSpecItemPublic]
