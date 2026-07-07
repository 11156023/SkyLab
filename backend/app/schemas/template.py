"""範本系統 2.0 schemas"""

import json
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
    TaskRecord,
    TaskRecordStatus,
    VMTemplateStatus,
    VMTemplateVisibility,
)

# ===== Request Schemas =====


class VMTemplateCreate(BaseModel):
    """把現有 VM/LXC 轉為範本"""

    source_vmid: int = Field(gt=0, description="要轉換的母機 VMID")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    visibility: VMTemplateVisibility = VMTemplateVisibility.groups
    group_ids: list[uuid.UUID] = Field(default_factory=list)
    default_cores: int | None = Field(default=None, ge=1, le=64)
    default_memory: int | None = Field(default=None, ge=128, description="MB")
    default_disk: int | None = Field(default=None, ge=1, description="GB")


class VMTemplateUpdate(BaseModel):
    """更新範本 metadata / 可見範圍"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    visibility: VMTemplateVisibility | None = None
    group_ids: list[uuid.UUID] | None = None
    default_cores: int | None = Field(default=None, ge=1, le=64)
    default_memory: int | None = Field(default=None, ge=128)
    default_disk: int | None = Field(default=None, ge=1)


# ===== Response Schemas =====


class VMTemplatePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pve_vmid: int
    name: str
    description: str | None = None
    owner_id: uuid.UUID | None = None
    node: str
    storage: str | None = None
    resource_type: str
    status: VMTemplateStatus
    visibility: VMTemplateVisibility
    group_ids: list[uuid.UUID] = Field(default_factory=list)
    default_cores: int | None = None
    default_memory: int | None = None
    default_disk: int | None = None
    source_vmid: int | None = None
    version: int
    error_message: str | None = None
    pve_exists: bool = Field(
        default=True, description="PVE 端對帳結果（False 表示 PVE 找不到此範本）"
    )
    created_at: datetime
    updated_at: datetime


class VMTemplatesPublic(BaseModel):
    data: list[VMTemplatePublic]
    count: int


class TaskRecordPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_type: str
    status: TaskRecordStatus
    progress: int
    result: dict[str, Any] | None = None
    error: str | None = None
    template_id: uuid.UUID | None = None
    resource_vmid: int | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @classmethod
    def from_record(cls, record: TaskRecord) -> "TaskRecordPublic":
        parsed_result: dict[str, Any] | None = None
        if record.result:
            try:
                loaded = json.loads(record.result)
                if isinstance(loaded, dict):
                    parsed_result = loaded
            except ValueError:
                parsed_result = None
        return cls(
            id=record.id,
            task_type=record.task_type,
            status=record.status,
            progress=record.progress,
            result=parsed_result,
            error=record.error,
            template_id=record.template_id,
            resource_vmid=record.resource_vmid,
            created_at=record.created_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
        )


class TaskRecordsPublic(BaseModel):
    data: list[TaskRecordPublic]
    count: int


class VMTemplateTaskResponse(BaseModel):
    """回傳範本本體 + 觸發的背景任務（前端拿 task.id 輪詢進度）"""

    template: VMTemplatePublic
    task: TaskRecordPublic


class TemplateCloneRequest(BaseModel):
    """從範本克隆開通。student 僅能單台且受配額限制；teacher/admin 可批量。"""

    hostname: str | None = Field(
        default=None,
        min_length=1,
        max_length=63,
        description="主機名稱；未填時以範本名產生。count > 1 時自動加序號",
    )
    count: int = Field(default=1, ge=1, le=50)
    cores: int | None = Field(default=None, ge=1, le=64)
    memory: int | None = Field(default=None, ge=128, description="MB")
    disk: int | None = Field(
        default=None, ge=1, description="GB，僅能放大（僅 qemu 生效）"
    )
    start: bool = True


class TemplateCloneResponse(BaseModel):
    """每台克隆一個背景任務"""

    tasks: list[TaskRecordPublic]
