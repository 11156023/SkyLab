"""VM request schemas."""

import unicodedata
import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from app.models.user import UserRole
from app.models.vm_request import VMMigrationStatus, VMRequestStatus


def _validate_unicode_hostname(v: str) -> str:
    if not v:
        raise ValueError("Hostname cannot be empty")
    if v.startswith("-") or v.endswith("-"):
        raise ValueError("Hostname cannot start or end with a hyphen")
    for ch in v:
        if ch == "-":
            continue
        cat = unicodedata.category(ch)
        if not (cat.startswith("L") or cat.startswith("N")):
            raise ValueError(
                "Only Unicode letters, digits, and hyphens are allowed in hostname"
            )
    # 檢查 Punycode 編碼後的長度是否仍在 DNS label 限制內（≤ 63 字元）
    try:
        encoded = v.encode("punycode").decode("ascii")
        if not v.isascii():
            ace_label = f"xn--{encoded}"
        else:
            ace_label = v
        if len(ace_label) > 63:
            raise ValueError(
                f"Hostname exceeds 63 characters after Punycode encoding "
                f"(encoded length: {len(ace_label)})"
            )
    except UnicodeError as e:
        raise ValueError(f"Hostname cannot be encoded as valid Punycode: {e}") from e
    return v


UnicodeHostname = Annotated[str, AfterValidator(_validate_unicode_hostname)]


class VMRequestCreate(BaseModel):
    reason: str = Field(min_length=10)
    resource_type: str
    hostname: UnicodeHostname = Field(min_length=1, max_length=63)
    cores: int = 2
    memory: int = 2048
    password: str = Field(min_length=8, max_length=128)
    storage: str = "local-lvm"
    environment_type: str = "Custom"
    os_info: str | None = None
    expiry_date: date | None = None
    mode: Literal["immediate", "scheduled"] = "scheduled"
    start_at: datetime | None = None
    end_at: datetime | None = None

    ostemplate: str | None = None
    rootfs_size: int | None = None

    template_id: int | None = None
    disk_size: int | None = None
    username: str | None = None
    gpu_mapping_id: str | None = None

    service_template_slug: str | None = None
    service_template_script_path: str | None = None


class VMRequestReview(BaseModel):
    status: Literal["approved", "rejected"]
    review_comment: str | None = None


class VMRequestPublic(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str | None = None
    user_full_name: str | None = None
    reason: str
    resource_type: str
    hostname: str
    cores: int
    memory: int
    storage: str
    environment_type: str
    os_info: str | None = None
    expiry_date: date | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None

    ostemplate: str | None = None
    rootfs_size: int | None = None

    template_id: int | None = None
    disk_size: int | None = None
    username: str | None = None
    gpu_mapping_id: str | None = None

    service_template_slug: str | None = None
    service_template_script_path: str | None = None

    status: VMRequestStatus
    reviewer_id: uuid.UUID | None = None
    review_comment: str | None = None
    reviewed_at: datetime | None = None
    vmid: int | None = None
    assigned_node: str | None = None
    desired_node: str | None = None
    actual_node: str | None = None
    placement_strategy_used: str | None = None
    migration_status: VMMigrationStatus = VMMigrationStatus.idle
    migration_error: str | None = None
    migration_pinned: bool = False
    resource_warning: str | None = None
    rebalance_epoch: int = 0
    last_rebalanced_at: datetime | None = None
    last_migrated_at: datetime | None = None
    recurrence_rule: str | None = None
    recurrence_duration_minutes: int | None = None
    schedule_timezone: str | None = None
    next_window_start: datetime | None = None
    next_window_end: datetime | None = None
    batch_job_id: uuid.UUID | None = None
    created_at: datetime


class VMRequestsPublic(BaseModel):
    data: list[VMRequestPublic]
    count: int


class VMRequestReviewRuntimeResource(BaseModel):
    vmid: int
    name: str
    node: str
    resource_type: str
    status: str
    linked_request_id: uuid.UUID | None = None
    linked_hostname: str | None = None
    linked_actual_node: str | None = None
    linked_desired_node: str | None = None


class VMRequestReviewOverlapItem(BaseModel):
    request_id: uuid.UUID
    hostname: str
    resource_type: str
    start_at: datetime | None = None
    end_at: datetime | None = None
    vmid: int | None = None
    status: VMRequestStatus
    assigned_node: str | None = None
    desired_node: str | None = None
    actual_node: str | None = None
    projected_node: str | None = None
    projected_strategy: str | None = None
    migration_status: VMMigrationStatus = VMMigrationStatus.idle
    last_migrated_at: datetime | None = None
    is_current_request: bool = False
    is_running_now: bool = False
    is_provisioned: bool = False


class VMRequestReviewNodeScore(BaseModel):
    node: str
    balance_score: float = 0.0
    cpu_share: float = 0.0
    memory_share: float = 0.0
    disk_share: float = 0.0
    peak_penalty: float = 0.0
    loadavg_penalty: float = 0.0
    storage_penalty: float = 0.0
    migration_cost: float = 0.0
    priority: int = 5
    is_selected: bool = False
    reason: str | None = None


class VMRequestReviewProjectedNode(BaseModel):
    node: str
    request_count: int = Field(default=0, ge=0)
    includes_current_request: bool = False
    hostnames: list[str] = Field(default_factory=list)


class VMRequestReviewContext(BaseModel):
    request: VMRequestPublic
    window_start: datetime
    window_end: datetime
    window_active_now: bool = False
    feasible: bool = False
    placement_strategy: str | None = None
    projected_node: str | None = None
    summary: str
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    resource_warnings: list[str] = Field(default_factory=list)
    cluster_nodes: list[str] = Field(default_factory=list)
    current_running_resources: list[VMRequestReviewRuntimeResource] = Field(
        default_factory=list
    )
    overlapping_approved_requests: list[VMRequestReviewOverlapItem] = Field(
        default_factory=list
    )
    projected_nodes: list[VMRequestReviewProjectedNode] = Field(default_factory=list)
    node_scores: list[VMRequestReviewNodeScore] = Field(default_factory=list)


class VMRequestAvailabilityRequest(BaseModel):
    resource_type: Literal["lxc", "vm"] = "lxc"
    cores: int = Field(default=2, ge=1, le=256)
    memory: int = Field(default=2048, ge=128, le=1048576, description="MB")
    disk_size: int | None = Field(default=None, ge=1, le=65536)
    rootfs_size: int | None = Field(default=None, ge=1, le=65536)
    instance_count: int = Field(default=1, ge=1, le=100)
    gpu_required: int = Field(default=0, ge=0, le=16)
    days: int = Field(default=7, ge=1, le=14)
    timezone: str = Field(default="Asia/Taipei", min_length=1, max_length=64)
    policy_role: UserRole | None = None


class VMRequestAvailabilitySlot(BaseModel):
    start_at: datetime
    end_at: datetime
    date: date
    hour: int = Field(ge=0, le=23)
    within_policy: bool
    feasible: bool
    status: Literal["available", "limited", "unavailable", "policy_blocked"]
    label: str
    summary: str
    reasons: list[str] = Field(default_factory=list)
    recommended_nodes: list[str] = Field(default_factory=list)
    target_node: str | None = None
    placement_strategy: str | None = None
    node_snapshots: list["VMRequestAvailabilityNodeSnapshot"] = Field(default_factory=list)


class VMRequestAvailabilityStackItem(BaseModel):
    name: str
    count: int = Field(default=0, ge=0)
    pending: bool = False


class VMRequestAvailabilityNodeSnapshot(BaseModel):
    node: str
    status: str
    candidate: bool
    priority: int = Field(default=5, ge=1, le=10)
    is_target: bool = False
    placement_count: int = Field(default=0, ge=0)
    running_resources: int = Field(default=0, ge=0)
    projected_running_resources: int = Field(default=0, ge=0)
    dominant_share: float = Field(default=0.0, ge=0.0)
    average_share: float = Field(default=0.0, ge=0.0)
    cpu_share: float = Field(default=0.0, ge=0.0)
    memory_share: float = Field(default=0.0, ge=0.0)
    disk_share: float = Field(default=0.0, ge=0.0)
    remaining_cpu_cores: float = Field(default=0.0, ge=0.0)
    remaining_memory_gb: float = Field(default=0.0, ge=0.0)
    remaining_disk_gb: float = Field(default=0.0, ge=0.0)
    vm_stack: list[VMRequestAvailabilityStackItem] = Field(default_factory=list)


class VMRequestAvailabilityDay(BaseModel):
    date: date
    available_hours: list[int] = Field(default_factory=list)
    limited_hours: list[int] = Field(default_factory=list)
    blocked_hours: list[int] = Field(default_factory=list)
    unavailable_hours: list[int] = Field(default_factory=list)
    best_hours: list[int] = Field(default_factory=list)
    slots: list[VMRequestAvailabilitySlot] = Field(default_factory=list)


class VMRequestAvailabilitySummary(BaseModel):
    timezone: str
    role: str
    role_label: str
    policy_window: str
    checked_days: int = Field(ge=1, le=14)
    feasible_slot_count: int = Field(default=0, ge=0)
    recommended_slot_count: int = Field(default=0, ge=0)
    current_status: str


class VMRequestAvailabilityResponse(BaseModel):
    summary: VMRequestAvailabilitySummary
    recommended_slots: list[VMRequestAvailabilitySlot] = Field(default_factory=list)
    days: list[VMRequestAvailabilityDay] = Field(default_factory=list)


class VMRequestPlacementPreview(BaseModel):
    request_id: uuid.UUID
    start_at: datetime | None = None
    end_at: datetime | None = None
    duration_hours: int = Field(default=0, ge=0)
    feasible: bool = False
    placement_strategy: str
    selected_status: str
    selected_node: str | None = None
    fallback_node: str | None = None
    summary: str
    warnings: list[str] = Field(default_factory=list)
    recommended_nodes: list[str] = Field(default_factory=list)
    slot_details: list[VMRequestAvailabilitySlot] = Field(default_factory=list)


VMRequestAvailabilitySlot.model_rebuild()
