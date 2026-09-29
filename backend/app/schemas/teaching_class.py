"""Request/response schemas for the teacher-facing teaching-class API."""

import uuid
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator

from app.core.i18n import t


def _require_known_timezone(value: str | None) -> str | None:
    """時區必須是 IANA 名稱。

    排程器每一輪都會對每個班級做 ``ZoneInfo(teaching_class.timezone)``；
    存進一個 "Taipei" 或 "UTC+8" 這種值，整輪的到期回收與週期開機視窗
    都會跟著中斷，影響的是所有班級，不只這一班。
    """
    if value is None:
        return value
    try:
        ZoneInfo(value)
    except (KeyError, ValueError, OSError) as exc:
        # ZoneInfoNotFoundError 是 KeyError；空字串、路徑字元是 ValueError；
        # 部分平台對目錄名稱（例如 "America"）會丟 OSError
        raise ValueError(t("availability.invalid_timezone")) from exc
    return value


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    term: str = Field(min_length=1, max_length=80)
    location: str | None = Field(default=None, max_length=255)
    start_date: date
    end_date: date
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    timezone: str = Field(default="Asia/Taipei", min_length=1, max_length=64)
    boot_lead_minutes: int = Field(default=10, ge=0, le=120)
    shutdown_grace_minutes: int = Field(default=30, ge=0, le=240)

    check_timezone = field_validator("timezone")(_require_known_timezone)


class ClassPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    term: str | None = Field(default=None, min_length=1, max_length=80)
    location: str | None = Field(default=None, max_length=255)
    start_date: date | None = None
    end_date: date | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    start_time: time | None = None
    end_time: time | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    boot_lead_minutes: int | None = Field(default=None, ge=0, le=120)
    shutdown_grace_minutes: int | None = Field(default=None, ge=0, le=240)

    check_timezone = field_validator("timezone")(_require_known_timezone)


class ClassExtend(BaseModel):
    end_date: date


class ClassArchive(BaseModel):
    reclaim_resources: bool = True
    force: bool = False


class StudentAdd(BaseModel):
    emails: list[str]


class InstructorMachineIn(BaseModel):
    enabled: bool


class MachineNodeIn(BaseModel):
    node_key: str
    source_type: str = "template"
    source_template_id: uuid.UUID | None = None
    custom_image_ref: str | None = None
    custom_storage: str | None = None
    custom_username: str | None = None
    custom_unprivileged: bool = True
    name: str
    role: str
    resource_type: str
    cpu: int
    memory_mb: int
    disk_gb: int
    network: str | None = None


class CourseSelect(BaseModel):
    course_version_id: uuid.UUID


class WeekFileIn(BaseModel):
    """週次教材只以既有檔案的 id 指定。

    storage_key 是上傳時由伺服器產生的磁碟位置，不能讓 client 指定：
    收下客戶端送來的值，等於任何老師都可以把別的班級的檔案（或任何
    猜得到的儲存路徑）掛進自己的週次，再用學生端的下載端點取回。
    """

    id: uuid.UUID
    target_path: str | None = Field(default=None, max_length=500)


class WeekIn(BaseModel):
    week_number: int
    session_date: date
    title: str = ""
    # None = 全部機器；否則必須是班級機器節點的 node_key（replace_weeks 檢查）
    target_node_key: str | None = None
    status: Literal["draft", "published", "completed"] = "draft"
    files: list[WeekFileIn] = Field(default_factory=list)

    @field_validator("target_node_key")
    @classmethod
    def _blank_target_is_all_machines(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None


class ClassResourceUsageItem(BaseModel):
    vmid: int
    status: str
    cpu_usage_pct: float | None = None
    ram_usage_pct: float | None = None
    mem_used_bytes: int | None = None
    mem_total_bytes: int | None = None


class ClassResourceUsageResponse(BaseModel):
    collected_at: datetime
    items: list[ClassResourceUsageItem]


__all__ = [
    "ClassArchive",
    "ClassCreate",
    "ClassExtend",
    "ClassPatch",
    "ClassResourceUsageItem",
    "ClassResourceUsageResponse",
    "CourseSelect",
    "MachineNodeIn",
    "StudentAdd",
    "WeekFileIn",
    "WeekIn",
]
