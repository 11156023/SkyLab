"""Public schemas for AI Teacher Judge workflows.

Canonical names use the ``TeacherJudge`` prefix so API contracts are easy to
trace back to this feature. Legacy ``Rubric*`` aliases are kept at the bottom
for older import paths and generated-client compatibility during migration.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class TeacherJudgeRubricCheckStep(BaseModel):
    """評分計劃書中的 command catalog 引用。"""

    template_key: str = Field(..., description="評分環境 template key")
    command_key: str = Field(..., description="template command catalog 的穩定 ID")
    command_label: str | None = Field(
        default=None,
        description="template command catalog 的顯示名稱",
    )


class TeacherJudgeRubricItem(BaseModel):
    """單一評分項目。"""

    id: str = Field(..., description="評分項目唯一 ID")
    title: str = Field(..., description="評分項目名稱")
    description: str = Field(default="", description="評分說明")
    checked: bool = Field(default=False, description="是否已達成（有做到就打勾）")
    detectable: Literal["auto", "partial", "manual"] = Field(
        default="manual",
        description="可偵測性：auto | partial | manual",
    )
    detection_method: str | None = Field(
        default=None,
        description="自動偵測方式說明（detectable=auto/partial 時填寫）",
    )
    fallback: str | None = Field(
        default=None,
        description="無法自動偵測時的替代建議",
    )
    check_steps: list[TeacherJudgeRubricCheckStep] = Field(
        default_factory=list,
        description="本階段只產生計劃書，僅引用既有 command_key，不代表已執行。",
    )


class TeacherJudgeRubricAnalysis(BaseModel):
    """AI 分析評分表後的結構化結果。"""

    items: list[TeacherJudgeRubricItem] = Field(default_factory=list)
    total_items: int = Field(default=0)
    checked_count: int = Field(default=0)
    auto_count: int = Field(default=0)
    partial_count: int = Field(default=0)
    manual_count: int = Field(default=0)
    summary: str = Field(default="", description="AI 整體說明（繁體中文）")
    raw_text: str = Field(
        default="", description="解析後的原始文件文字（供後續對話使用）"
    )


class TeacherJudgeRubricChatMessage(BaseModel):
    """對話訊息。"""

    role: Literal["user", "assistant"] = Field(..., description="'user' 或 'assistant'")
    content: str = Field(..., description="訊息內容")


class TeacherJudgeRubricChatRequest(BaseModel):
    """對話請求。"""

    messages: list[TeacherJudgeRubricChatMessage] = Field(..., min_length=1)
    rubric_context: str = Field(
        default="", description="目前評分表的 JSON 字串（作為背景知識）"
    )
    is_refine: bool = Field(
        default=False, description="True = 老師手動調整後觸發的全表潤飾模式"
    )
    template_key: str = Field(
        default="linux",
        description="目前評分環境 template key，用於驗證 check_steps",
    )


class TeacherJudgeRubricChatResponse(BaseModel):
    """對話回應。"""

    reply: str
    updated_items: list[dict[str, Any]] | None = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    elapsed_seconds: float
    tokens_per_second: float


class TeacherJudgeRubricUploadResponse(BaseModel):
    """上傳評分表回應。"""

    analysis: TeacherJudgeRubricAnalysis
    ai_metrics: dict[str, Any]
    template_key: str = "linux"


class TeacherJudgeRubricExportRequest(BaseModel):
    """匯出 Excel 請求。"""

    items: list[dict[str, Any]] = Field(..., min_length=1)
    summary: str = Field(default="")


TeacherJudgeFileStatusLiteral = Literal["active", "replaced"]
TeacherJudgeScriptLanguageLiteral = Literal["python", "shell", "bat"]
TeacherJudgeScriptSourceLiteral = Literal["ai_generated", "regenerated"]
TeacherJudgeScriptStatusLiteral = Literal[
    "draft", "review_failed", "reviewed", "approved", "archived"
]
TeacherJudgeScriptRunTargetScopeLiteral = Literal[
    "all_with_vm", "running_only", "manual"
]
TeacherJudgeScriptRunStatusLiteral = Literal[
    "pending", "running", "completed", "failed", "cancelled"
]


class TeacherJudgeScriptCreateRequest(BaseModel):
    """Create a managed script artifact from the current rubric analysis."""

    name: str = Field(..., min_length=1, max_length=255)
    template_key: str = Field(default="linux", max_length=50)
    rubric_snapshot: TeacherJudgeRubricAnalysis
    source_file_id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("name must not be blank")
        return name

    @field_validator("template_key")
    @classmethod
    def normalize_template_key(cls, value: str) -> str:
        return value.strip().lower() or "linux"


class TeacherJudgeScriptRegenerateRequest(BaseModel):
    """Regenerate a managed script artifact."""

    rubric_snapshot: TeacherJudgeRubricAnalysis | None = None


class TeacherJudgeScriptArtifactPublic(BaseModel):
    id: str
    group_id: str
    name: str
    template_key: str
    rubric_snapshot_json: dict[str, Any]
    source_file_id: str | None
    source_file_snapshot_json: dict[str, Any]
    script_language: TeacherJudgeScriptLanguageLiteral
    script_content: str
    source: TeacherJudgeScriptSourceLiteral
    version: int
    status: TeacherJudgeScriptStatusLiteral
    policy_check_result_json: dict[str, Any]
    ai_review_result_json: dict[str, Any]
    created_by: str | None
    approved_by: str | None
    created_at: str
    updated_at: str
    approved_at: str | None


class TeacherJudgeFilePublic(BaseModel):
    id: str
    group_id: str
    uploaded_by: str | None
    original_filename: str
    file_hash: str
    template_key: str
    analysis_json: dict[str, Any]
    status: TeacherJudgeFileStatusLiteral
    created_at: str
    updated_at: str


class TeacherJudgeFileUploadResponse(BaseModel):
    file: TeacherJudgeFilePublic
    analysis: TeacherJudgeRubricAnalysis
    ai_metrics: dict[str, Any]
    template_key: str = "linux"


class TeacherJudgeFileAnalysisUpdateRequest(BaseModel):
    analysis: TeacherJudgeRubricAnalysis


class TeacherJudgeScriptRunCreateRequest(BaseModel):
    """Create an execution run for an approved managed script."""

    target_scope: TeacherJudgeScriptRunTargetScopeLiteral = "manual"
    target_vmids: list[int] = Field(default_factory=list)

    @field_validator("target_vmids")
    @classmethod
    def validate_target_vmids(cls, value: list[int]) -> list[int]:
        unique_vmids = list(dict.fromkeys(value))
        if not unique_vmids:
            raise ValueError("target_vmids must not be empty")
        return unique_vmids


class TeacherJudgeScriptRunPublic(BaseModel):
    id: str
    group_id: str
    artifact_id: str
    target_scope: TeacherJudgeScriptRunTargetScopeLiteral
    target_snapshot_json: dict[str, Any]
    status: TeacherJudgeScriptRunStatusLiteral
    progress_json: dict[str, Any]
    result_summary_json: dict[str, Any]
    target_results_json: dict[str, Any]
    started_by: str | None
    started_at: str | None
    finished_at: str | None
    created_at: str
    updated_at: str


# Legacy aliases kept for existing imports while new code migrates to the
# TeacherJudge-prefixed schema names above.
RubricCheckStep = TeacherJudgeRubricCheckStep
RubricItem = TeacherJudgeRubricItem
RubricAnalysis = TeacherJudgeRubricAnalysis
ChatMessage = TeacherJudgeRubricChatMessage
RubricChatRequest = TeacherJudgeRubricChatRequest
RubricChatResponse = TeacherJudgeRubricChatResponse
RubricUploadResponse = TeacherJudgeRubricUploadResponse
RubricExportRequest = TeacherJudgeRubricExportRequest

FileStatus = TeacherJudgeFileStatusLiteral
ScriptLanguage = TeacherJudgeScriptLanguageLiteral
ScriptSource = TeacherJudgeScriptSourceLiteral
ScriptStatus = TeacherJudgeScriptStatusLiteral
ScriptRunTargetScope = TeacherJudgeScriptRunTargetScopeLiteral
ScriptRunStatus = TeacherJudgeScriptRunStatusLiteral
