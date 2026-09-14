"""規格調整申請 schemas"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.spec_change_request import SpecChangeRequestStatus, SpecChangeType

# 已核准申請的套用進度（由 service 依 applied_at / apply_error /
# apply_started_at 與背景執行器狀態推導，非 DB 欄位）
SpecChangeApplyStatus = Literal[
    "ready",  # 已核准，等申請人按「套用」
    "applying",  # 背景任務進行中（關機 → 改規格 → 開機）
    "applied",  # 規格已寫進 Proxmox
    "failed",  # 最近一次套用失敗，可重試
    "interrupted",  # 標記為套用中但背景任務已不存在（服務重啟），可重試
]

# ===== Request Schemas =====


class SpecChangeRequestCreate(BaseModel):
    """建立規格調整申請"""

    vmid: int
    change_type: SpecChangeType
    reason: str = Field(min_length=10, description="調整原因至少10字")
    requested_cpu: int | None = Field(default=None, ge=1, le=32)
    requested_memory: int | None = Field(default=None, ge=512, le=65536)
    requested_disk: int | None = Field(default=None, ge=1, le=1000)
    requested_expiry_date: date | None = Field(
        default=None, description="change_type=expiry：希望延長到的到期日"
    )


class SpecChangeRequestReview(BaseModel):
    """審核規格調整申請：結果只能是核准或駁回"""

    status: SpecChangeRequestStatus
    review_comment: str | None = None

    @field_validator("status")
    @classmethod
    def _decision_only(
        cls, value: SpecChangeRequestStatus
    ) -> SpecChangeRequestStatus:
        if value not in (
            SpecChangeRequestStatus.approved,
            SpecChangeRequestStatus.rejected,
        ):
            raise ValueError("審核結果只能是 approved 或 rejected")
        return value


# ===== Response Schemas =====


class SpecChangeRequestPublic(BaseModel):
    """公開的規格調整申請資訊"""

    id: uuid.UUID
    vmid: int
    resource_name: str | None = None
    resource_exists: bool = True
    user_id: uuid.UUID
    user_email: str | None = None
    user_full_name: str | None = None
    change_type: SpecChangeType
    reason: str
    current_cpu: int | None
    current_memory: int | None
    current_disk: int | None
    requested_cpu: int | None
    requested_memory: int | None
    requested_disk: int | None
    current_expiry_date: date | None = None
    requested_expiry_date: date | None = None
    status: SpecChangeRequestStatus
    reviewer_id: uuid.UUID | None
    review_comment: str | None
    reviewed_at: datetime | None
    applied_at: datetime | None
    apply_started_at: datetime | None = None
    apply_error: str | None = None
    apply_status: SpecChangeApplyStatus | None = None
    created_at: datetime


class SpecChangeRequestsPublic(BaseModel):
    """規格調整申請列表"""

    data: list[SpecChangeRequestPublic]
    count: int


class SpecChangeApplyAccepted(BaseModel):
    """套用已排入背景執行（202）"""

    message: str
    task_id: str
    request: SpecChangeRequestPublic
