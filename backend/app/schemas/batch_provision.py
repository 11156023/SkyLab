"""Batch provisioning（班級批次建機）審核 API 的請求／回應 schema。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class BatchProvisionReviewRequest(BaseModel):
    """Payload for admin approve/reject of a pending batch."""

    decision: str = Field(..., pattern="^(approved|rejected)$")
    review_comment: str | None = Field(default=None, max_length=500)


class BatchProvisionTaskPublic(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str | None
    user_name: str | None
    member_index: int
    vmid: int | None
    status: str
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class BatchProvisionJobSpec(BaseModel):
    """Spec parameters that apply to every member's resource. Reflects the
    JSON stored in ``BatchProvisionJob.template_params``."""

    cores: int | None = None
    memory: int | None = None
    disk_size: int | None = None
    rootfs_size: int | None = None
    ostemplate: str | None = None
    template_id: int | None = None
    vm_template_id: str | None = None
    username: str | None = None
    environment_type: str | None = None
    os_info: str | None = None
    expiry_date: str | None = None


class BatchProvisionJobPublic(BaseModel):
    id: uuid.UUID
    teaching_class_id: uuid.UUID
    teaching_class_name: str | None = None
    resource_type: str
    hostname_prefix: str
    status: str
    total: int
    done: int
    failed_count: int
    created_at: datetime
    finished_at: datetime | None
    initiated_by: uuid.UUID | None = None
    initiated_by_email: str | None = None
    initiated_by_name: str | None = None
    reviewer_id: uuid.UUID | None = None
    reviewer_email: str | None = None
    reviewed_at: datetime | None = None
    review_comment: str | None = None
    recurrence_rule: str | None = None
    recurrence_duration_minutes: int | None = None
    schedule_timezone: str | None = None
    next_window_start: datetime | None = None
    next_window_end: datetime | None = None
    spec: BatchProvisionJobSpec
    tasks: list[BatchProvisionTaskPublic]


class RecurrencePreview(BaseModel):
    """Next few computed windows for a candidate RRULE — used by the review UI
    to confirm the schedule does what the teacher intended before approving."""

    windows: list[tuple[datetime, datetime]]


__all__ = [
    "BatchProvisionJobPublic",
    "BatchProvisionJobSpec",
    "BatchProvisionReviewRequest",
    "BatchProvisionTaskPublic",
    "RecurrencePreview",
]
