"""範本系統 2.0 schemas"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import (
    TaskRecord,
    TaskRecordStatus,
    VMTemplateStatus,
    VMTemplateVisibility,
)
from app.utils.hostname import validate_unicode_hostname

# ===== Request Schemas =====


class VMTemplateCreate(BaseModel):
    """把現有 VM/LXC 轉為範本"""

    source_vmid: int = Field(gt=0, description="要轉換的母機 VMID")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    visibility: VMTemplateVisibility = VMTemplateVisibility.private
    default_cores: int | None = Field(default=None, ge=1, le=64)
    default_memory: int | None = Field(default=None, ge=128, description="MB")
    # default_disk 不開放設定：轉換完成時自動偵測母機磁碟大小
    allow_password_change: bool = Field(
        default=True, description="克隆時允許使用者自訂/重設登入密碼"
    )
    requires_gpu: bool = Field(
        default=False, description="使用此範本需要 GPU（僅 qemu 範本可設）"
    )


_UPDATE_NON_NULLABLE_FIELDS = (
    "name",
    "visibility",
    "allow_password_change",
    "requires_gpu",
)


class VMTemplateUpdate(BaseModel):
    """更新範本 metadata / 可見範圍"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    visibility: VMTemplateVisibility | None = None
    default_cores: int | None = Field(default=None, ge=1, le=64)
    default_memory: int | None = Field(default=None, ge=128)
    # default_disk 不開放更新：跟母機一致
    allow_password_change: bool | None = None
    requires_gpu: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_null_for_required_fields(cls, data: Any) -> Any:
        # 欄位型別是 Optional 只為了 PATCH 的「沒送就不改」；這幾欄在 DB 是
        # NOT NULL，明確送 null 直接回 422（service 端另有 400 的同樣檢查）。
        # description / default_cores / default_memory 可為 null，不在此列。
        if isinstance(data, dict):
            for field in _UPDATE_NON_NULLABLE_FIELDS:
                if field in data and data[field] is None:
                    raise ValueError(f"{field} cannot be null")
        return data


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
    default_cores: int | None = None
    default_memory: int | None = None
    default_disk: int | None = None
    allow_password_change: bool = True
    requires_gpu: bool = False
    attachment_count: int = 0
    source_vmid: int | None = None
    version: int
    error_message: str | None = None
    pve_exists: bool = Field(
        default=True, description="PVE 端對帳結果（False 表示 PVE 找不到此範本）"
    )
    created_at: datetime
    updated_at: datetime


class TemplateCatalogItem(BaseModel):
    """A template opened for student self-service requests."""

    id: uuid.UUID
    pve_vmid: int
    name: str
    description: str | None = None
    resource_type: str
    node: str
    version: int
    is_windows: bool = False
    requires_gpu: bool = False
    # False：開出來的機器沿用範本內的密碼，申請表單不必再問密碼
    allow_password_change: bool = True
    cores: int | None = None
    memory_mb: int | None = None
    disk_gb: int | None = None


class TemplateCatalogPublic(BaseModel):
    data: list[TemplateCatalogItem]
    count: int


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
        parsed_result = record.result if isinstance(record.result, dict) else None
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
    # 磁碟不開放調整：克隆固定沿用範本磁碟大小
    login_password: str | None = Field(
        default=None,
        min_length=8,
        max_length=64,
        description="自訂登入密碼（範本 allow_password_change 時才接受）",
    )
    gpu_mapping_id: str | None = Field(
        default=None, description="GPU mapping（範本 requires_gpu 時必填）"
    )
    gpu_mdev_profile: str | None = Field(
        default=None, description="vGPU 規格；未填時自動配最小可用規格"
    )
    start: bool = True

    @field_validator("hostname")
    @classmethod
    def _check_hostname(cls, value: str | None) -> str | None:
        # 與其他開通 schema 一樣走 UnicodeHostname 的規則，但逐段檢查以保留
        # 「web.lab」這種帶點的名稱；空段（a..b、結尾的點）、空白與符號在這裡就
        # 回 422，不要等到 to_punycode_hostname 丟 ValueError 變成 500。
        if value is None:
            return None
        for label in value.split("."):
            validate_unicode_hostname(label)
        return value


class TemplateCloneResponse(BaseModel):
    """每台克隆一個背景任務"""

    tasks: list[TaskRecordPublic]


class TemplateAttachmentPublic(BaseModel):
    """範本附件（使用手冊等）"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    template_id: uuid.UUID
    filename: str
    content_type: str | None = None
    size_bytes: int
    created_at: datetime


class TemplateAttachmentsPublic(BaseModel):
    data: list[TemplateAttachmentPublic]
    count: int


class ResourceTemplateManual(BaseModel):
    """克隆機來源範本的使用手冊（資源詳情頁用）"""

    template_name: str | None = None
    data: list[TemplateAttachmentPublic]
    count: int
