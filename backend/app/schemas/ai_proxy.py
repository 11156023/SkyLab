"""
AI Proxy API Schemas - OpenAI 兼容格式
"""

from datetime import datetime

from pydantic import BaseModel, Field


# ===== 聊天补全相关 =====
class ChatMessage(BaseModel):
    """聊天消息"""

    role: str = Field(..., description="消息角色: system, user, assistant")
    content: str = Field(..., description="消息内容")


class ChatCompletionRequest(BaseModel):
    """聊天补全请求（OpenAI 兼容）"""

    model: str = Field(..., description="模型名称，如 gpt-oss-20B")
    messages: list[ChatMessage] = Field(..., description="对话历史")
    max_tokens: int | None = Field(default=2048, description="最大生成 tokens")
    temperature: float | None = Field(
        default=0.8, ge=0.0, le=2.0, description="采样温度"
    )
    top_p: float | None = Field(default=0.95, ge=0.0, le=1.0, description="核采样")
    stream: bool | None = Field(default=False, description="是否流式响应")
    # 其他 OpenAI 参数
    n: int | None = Field(default=1, description="生成多少个补全")
    stop: str | list[str] | None = Field(default=None, description="停止序列")
    presence_penalty: float | None = Field(default=0.0, description="存在惩罚")
    frequency_penalty: float | None = Field(default=0.0, description="频率惩罚")


class UsageInfo(BaseModel):
    """使用量信息"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionChoice(BaseModel):
    """聊天补全选择"""

    index: int
    message: ChatMessage
    finish_reason: str | None = None


class ChatCompletionResponse(BaseModel):
    """聊天补全响应（OpenAI 兼容）"""

    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: UsageInfo
    duration_ms: int | None = None  # 本次請求耗時（毫秒），由 SkyLab 附加


# ===== 流式响应相关 =====
class DeltaMessage(BaseModel):
    """流式响应中的增量消息"""

    role: str | None = None
    content: str | None = None


class ChatCompletionStreamChoice(BaseModel):
    """流式响应的选择"""

    index: int
    delta: DeltaMessage
    finish_reason: str | None = None


class ChatCompletionStreamResponse(BaseModel):
    """流式聊天补全响应"""

    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChatCompletionStreamChoice]


# ===== 模型列表相关 =====
class ModelInfo(BaseModel):
    """模型信息"""

    id: str
    object: str = "model"
    created: int | None = None  # vLLM may not return this field
    owned_by: str = "SkyLab"


class ModelsResponse(BaseModel):
    """模型列表响应"""

    data: list[ModelInfo]
    object: str = "list"


# ===== 使用量统计相关 =====
class UsageByModel(BaseModel):
    """按模型分組的使用量"""

    requests: int
    input_tokens: int
    output_tokens: int


class UsageStatsResponse(BaseModel):
    """Proxy 使用量統計回應"""

    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    by_model: dict[str, UsageByModel]
    start_date: datetime
    end_date: datetime


class TemplateUsageByCallType(BaseModel):
    """按 call_type 分組的 template 使用量"""

    calls: int
    input_tokens: int
    output_tokens: int


class TemplateUsageStatsResponse(BaseModel):
    """Template 呼叫量統計回應"""

    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    by_call_type: dict[str, TemplateUsageByCallType]
    start_date: datetime
    end_date: datetime


# ===== 速率限制相关 =====
class RateLimitStatusResponse(BaseModel):
    """速率限制状态"""

    limit_per_minute: int
    current_usage: int
    remaining: int
    reset_at: datetime
    disabled: bool = False  # 是否已禁用速率限制（Redis 未啟用時為 True）
    error: str | None = None  # Redis 錯誤訊息（如有）


__all__ = [
    # Request
    "ChatMessage",
    "ChatCompletionRequest",
    # Response
    "UsageInfo",
    "ChatCompletionChoice",
    "ChatCompletionResponse",
    # Streaming
    "DeltaMessage",
    "ChatCompletionStreamChoice",
    "ChatCompletionStreamResponse",
    # Models
    "ModelInfo",
    "ModelsResponse",
    # Usage Stats
    "UsageByModel",
    "UsageStatsResponse",
    "TemplateUsageByCallType",
    "TemplateUsageStatsResponse",
    # Rate Limit
    "RateLimitStatusResponse",
]
