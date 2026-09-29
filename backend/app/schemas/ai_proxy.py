"""
AI 用量與速率限制 schemas（/ai-proxy、/ai-api 的用量統計、呼叫紀錄與限流狀態）
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


# ===== 使用量統計 =====
class UsageByModel(BaseModel):
    """按模型分組的使用量"""

    requests: int
    input_tokens: int
    output_tokens: int


class DailyUsagePoint(BaseModel):
    """逐日用量點（我的用量折線圖用；區間內沒有呼叫的日子補零）"""

    date: date
    requests: int
    input_tokens: int
    output_tokens: int


class UsageStatsResponse(BaseModel):
    """Proxy 使用量統計回應"""

    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    by_model: dict[str, UsageByModel]
    daily: list[DailyUsagePoint] = []
    start_date: datetime
    end_date: datetime


class UsageRecordPublic(BaseModel):
    """使用申請金鑰發出的單筆 API 呼叫紀錄。"""

    id: uuid.UUID
    route: Literal["model"]
    credential_id: uuid.UUID
    api_key_name: str
    api_key_prefix: str
    model_name: str
    call_type: str | None = None
    preset: str | None = None
    request_id: str | None = None
    upstream_request_id: str | None = None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_duration_ms: int | None = None
    first_token_ms: int | None = None
    stream: bool = False
    usage_reported: bool = False
    response_model: str | None = None
    e2e_output_tokens_per_second: float | None = None
    status: str
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class UsageRecordsPublic(BaseModel):
    """使用申請金鑰發出的 API 呼叫紀錄列表。"""

    data: list[UsageRecordPublic]
    count: int


# ===== 速率限制 =====
class RateLimitStatusResponse(BaseModel):
    """速率限制狀態"""

    limit_per_minute: int
    current_usage: int
    remaining: int
    reset_at: datetime
    disabled: bool = False  # 是否已禁用速率限制（Redis 未啟用時為 True）
    error: str | None = None  # Redis 錯誤訊息（如有）


__all__ = [
    # Usage Stats
    "UsageByModel",
    "DailyUsagePoint",
    "UsageStatsResponse",
    "UsageRecordPublic",
    "UsageRecordsPublic",
    # Rate Limit
    "RateLimitStatusResponse",
]
