from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, model_validator


def _clip(limit: int) -> BeforeValidator:
    """把字串截到 *limit* 字元。

    表單快照會原樣塞進 prompt；這些欄位是使用者正在編輯的草稿，直接回 422
    會讓助理整個不能用，所以截斷而不是拒絕，只保證 prompt 大小有上限。
    """

    def _cut(value: Any) -> Any:
        return value[:limit] if isinstance(value, str) else value

    return BeforeValidator(_cut)


# 識別字／標籤類欄位的上限；正常值遠低於此（主機名 63、volid 約 60 字元）
ShortText = Annotated[str, _clip(255)]
ReasonText = Annotated[str, _clip(8000)]
SummaryText = Annotated[str, _clip(500)]

PersonaPreset = Literal[
    "student_individual",
    "student_team_project",
    "teaching_class_service",
]


PRESET_RESOURCE_BASELINES: dict[str, dict[str, dict[str, int]]] = {
    "student_individual": {
        "lxc": {"cpu": 1, "memory_mb": 1024, "disk_gb": 8},
        "vm": {"cpu": 2, "memory_mb": 2048, "disk_gb": 20},
    },
    "student_team_project": {
        "lxc": {"cpu": 2, "memory_mb": 2048, "disk_gb": 16},
        "vm": {"cpu": 2, "memory_mb": 4096, "disk_gb": 40},
    },
    "teaching_class_service": {
        "lxc": {"cpu": 4, "memory_mb": 8192, "disk_gb": 40},
        "vm": {"cpu": 4, "memory_mb": 8192, "disk_gb": 60},
    },
}


class DeviceNode(BaseModel):
    node: ShortText
    maxcpu: int = Field(default=0)
    cpu_usage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    maxmem_gb: float = Field(default=0.0, ge=0.0)
    mem_usage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    gpu_count: int = Field(default=0, ge=0)


class ChatMessage(BaseModel):
    # role 不算在對話字數上限內，卻會原樣送進模型，所以要有自己的上限
    role: str = Field(
        ...,
        max_length=32,
        description="Role of the message sender, usually 'user' or 'assistant'.",
    )
    content: str = Field(..., description="Content of the message.")


class ChatResponse(BaseModel):
    reply: str = Field(..., description="AI text reply.")
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    elapsed_seconds: float = Field(default=0.0)
    tokens_per_second: float = Field(default=0.0)


class ExtractedIntent(BaseModel):
    goal_summary: str = Field(..., description="Summary of the user's final goal.")
    role: str = Field(default="student")
    course_context: str = Field(default="coursework")
    budget_mode: str = Field(default="balanced")
    needs_public_web: bool = Field(default=False)
    needs_database: bool = Field(default=False)
    requires_gpu: bool = Field(default=False)
    needs_windows: bool = Field(default=False)


class GPUOptionContext(BaseModel):
    mapping_id: ShortText
    description: ShortText = ""
    model: ShortText = ""
    vram: ShortText = ""
    node: ShortText = ""
    available_count: int = 0
    device_count: int = 0
    capacity_count: int = 0
    used_count: int = 0
    total_vram_mb: int = 0
    used_vram_mb: int = 0
    per_instance_vram_mb: int = 0
    mdev_profile: ShortText = ""
    has_mdev: bool = False
    is_sriov: bool = False


class ScheduleOptionContext(BaseModel):
    start_at: datetime
    end_at: datetime
    status: Literal["available", "limited"] = "available"
    summary: SummaryText = ""
    recommended_nodes: list[ShortText] = Field(default_factory=list, max_length=64)


class LXCOSOptionContext(BaseModel):
    value: ShortText
    label: ShortText = ""


class VMOSOptionContext(BaseModel):
    template_id: int
    label: ShortText = ""
    node: ShortText = ""


class RecommendationFormContext(BaseModel):
    resource_type: Literal["lxc", "vm"] | None = None
    mode: Literal["immediate", "scheduled"] | None = None
    hostname: ShortText | None = None
    reason: ReasonText | None = None
    lxc_os_image: ShortText | None = None
    vm_template_id: int | None = None
    username: ShortText | None = None
    cores: int | None = Field(default=None, ge=1)
    memory_mb: int | None = Field(default=None, ge=128)
    disk_gb: int | None = Field(default=None, ge=1)
    storage: ShortText | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    immediate_no_end: bool | None = None
    selected_gpu_mapping_id: ShortText | None = None
    gpu_options: list[GPUOptionContext] = Field(default_factory=list, max_length=64)
    schedule_options: list[ScheduleOptionContext] = Field(default_factory=list, max_length=12)
    lxc_os_options: list[LXCOSOptionContext] = Field(default_factory=list, max_length=100)
    vm_os_options: list[VMOSOptionContext] = Field(default_factory=list, max_length=100)
    resource_options_from_client: bool = False


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1, description="List of previous chat messages.")
    top_k: int = Field(default=5, ge=1, le=10)
    device_nodes: list[DeviceNode] = Field(default_factory=list, max_length=128)
    form_context: RecommendationFormContext | None = None
    focus_hint: str | None = Field(
        default=None,
        max_length=200,
        description="配置模式：這一輪只問這件事，其餘照原本的顧問語氣。",
    )


class RecommendationRequest(BaseModel):
    goal: str = Field(..., min_length=3)
    preset: PersonaPreset | None = Field(default=None)
    role: str = Field(default="student")
    course_context: str = Field(default="coursework")
    sharing_scope: str = Field(default="personal")
    budget_mode: str = Field(default="balanced")
    expected_users: int = Field(default=1, ge=1, le=100000)
    requires_gpu: bool = False
    needs_windows: bool = False
    needs_public_web: bool = False
    needs_database: bool = False
    device_nodes: list[DeviceNode] = Field(default_factory=list)
    resource_baseline: dict[str, dict[str, int]] = Field(default_factory=dict)
    form_context: RecommendationFormContext | None = None

    @model_validator(mode="after")
    def _infer_preset_when_missing(self) -> RecommendationRequest:
        if self.preset is None:
            if self.role == "teacher" and self.course_context == "teaching":
                self.preset = "teaching_class_service"
            elif self.role == "student" and self.sharing_scope == "shared":
                self.preset = "student_team_project"
            else:
                self.preset = "student_individual"

        if not self.resource_baseline:
            self.resource_baseline = PRESET_RESOURCE_BASELINES[self.preset]

        return self

