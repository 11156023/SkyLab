"""統一背景任務 (Jobs) 的 API schemas。

聚合來自不同領域的「需要等待的任務」：
- vm_request:     VM/LXC 開機申請 (vm_requests)
- spec_change:    規格變更申請 (spec_change_requests)
- deletion:       VM/LXC 刪除 (deletion_requests)
- template:       機器範本任務 (task_records：轉換／克隆／刪除／更新循環)

所有來源被正規化到統一的 JobItem 結構，以便前端 Job 中心一致顯示。
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.course import CourseReminderStudent


class JobKind(str, enum.Enum):
    vm_request = "vm_request"
    spec_change = "spec_change"
    deletion = "deletion"
    template = "template"
    # 以下兩種同樣來自 TaskRecord（arq 任務），依 task_type 分類

    resource_reset = "resource_reset"
    batch_provision = "batch_provision"



class JobStatus(str, enum.Enum):
    """正規化狀態。各來源的原始狀態會 map 到此枚舉。"""

    pending = "pending"        # 等待中（尚未開始）
    running = "running"        # 執行中
    completed = "completed"    # 成功完成
    failed = "failed"          # 失敗
    blocked = "blocked"        # 受阻
    cancelled = "cancelled"    # 已取消


# 仍視為「進行中」的狀態（會出現在 active count）
ACTIVE_JOB_STATUSES: set[JobStatus] = {
    JobStatus.pending,
    JobStatus.running,
    JobStatus.blocked,
}


class JobItem(BaseModel):
    """統一的 Job 顯示模型。"""

    id: str = Field(description="複合 ID，格式：<kind>:<source_id>")
    kind: JobKind
    title: str
    status: JobStatus
    progress: int | None = Field(default=None, ge=0, le=100, description="0-100, 若不可估算則為 null")
    message: str | None = None
    user_id: uuid.UUID | None = None
    user_email: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    detail_url: str | None = Field(
        default=None,
        description="前端可點擊跳轉的相對路徑",
    )
    meta: dict[str, Any] = Field(default_factory=dict)


class JobsListResponse(BaseModel):
    items: list[JobItem]
    total: int
    active_count: int
    reminders: list[CourseReminderStudent] | None = Field(
        default=None,
        description="/ws/jobs 推送時附帶的個人提醒；REST 端點不填（None）",
    )


class JobDetail(BaseModel):
    """Job 詳細資訊，包含 kind 特定的額外欄位（如腳本輸出、規格 diff 等）。"""

    item: JobItem
    # 各 kind 特定的詳細欄位（前端按 kind 渲染對應區塊）
    output: str | None = Field(
        default=None,
        description="完整文字輸出",
    )
    error: str | None = None
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="kind 特定附加資訊（如 spec_change diff、vm_request 規格）",
    )


__all__ = [
    "ACTIVE_JOB_STATUSES",
    "JobDetail",
    "JobItem",
    "JobKind",
    "JobStatus",
    "JobsListResponse",
]
