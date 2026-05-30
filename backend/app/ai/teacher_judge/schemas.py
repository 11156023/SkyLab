"""Schemas for AI Teacher Judge rubric workflows."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RubricCheckStep(BaseModel):
    """評分計劃書中的 command catalog 引用。"""

    template_key: str = Field(..., description="評分環境 template key")
    command_key: str = Field(..., description="template command catalog 的穩定 ID")
    command_label: str | None = Field(
        default=None,
        description="template command catalog 的顯示名稱",
    )


class RubricItem(BaseModel):
    """單一評分項目。"""

    id: str = Field(..., description="評分項目唯一 ID")
    title: str = Field(..., description="評分項目名稱")
    description: str = Field(default="", description="評分說明")
    checked: bool = Field(default=False, description="是否已達成（有做到就打勾）")
    detectable: str = Field(
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
    check_steps: list[RubricCheckStep] = Field(
        default_factory=list,
        description="本階段只產生計劃書，僅引用既有 command_key，不代表已執行。",
    )


class RubricAnalysis(BaseModel):
    """AI 分析評分表後的結構化結果。"""

    items: list[RubricItem] = Field(default_factory=list)
    total_items: int = Field(default=0)
    checked_count: int = Field(default=0)
    auto_count: int = Field(default=0)
    partial_count: int = Field(default=0)
    manual_count: int = Field(default=0)
    summary: str = Field(default="", description="AI 整體說明（繁體中文）")
    raw_text: str = Field(
        default="", description="解析後的原始文件文字（供後續對話使用）"
    )


class ChatMessage(BaseModel):
    """對話訊息。"""

    role: str = Field(..., description="'user' 或 'assistant'")
    content: str = Field(..., description="訊息內容")


class RubricChatRequest(BaseModel):
    """對話請求。"""

    messages: list[ChatMessage] = Field(..., min_length=1)
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


class RubricChatResponse(BaseModel):
    """對話回應。"""

    reply: str
    updated_items: list[dict] | None = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    elapsed_seconds: float
    tokens_per_second: float


class RubricUploadResponse(BaseModel):
    """上傳評分表回應。"""

    analysis: RubricAnalysis
    ai_metrics: dict
    template_key: str = "linux"


class RubricExportRequest(BaseModel):
    """匯出 Excel 請求。"""

    items: list[dict] = Field(..., min_length=1)
    summary: str = Field(default="")
