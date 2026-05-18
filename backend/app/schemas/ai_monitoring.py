"""
AI Monitoring Schemas — Admin 全局 AI 使用監控
"""

import uuid
from datetime import datetime

from pydantic import BaseModel


# ===== 全局統計卡片 =====
class AIMonitoringStats(BaseModel):
    """Admin 全局 AI 統計"""

    proxy_total_calls: int
    proxy_total_input_tokens: int
    proxy_total_output_tokens: int
    template_total_calls: int
    template_total_input_tokens: int
    template_total_output_tokens: int
    successful_calls: int
    failed_calls: int
    success_rate: int
    avg_latency_ms: int
    active_users: int
    models_used: list[str]


# ===== Proxy 呼叫清單 =====
class AIProxyCallRecord(BaseModel):
    """單筆 Proxy 呼叫紀錄"""

    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str | None = None
    user_full_name: str | None = None
    credential_id: uuid.UUID
    model_name: str
    request_type: str
    input_tokens: int
    output_tokens: int
    request_duration_ms: int | None = None
    status: str
    error_message: str | None = None
    created_at: datetime


class AIProxyCallsResponse(BaseModel):
    """Proxy 呼叫清單回應"""

    data: list[AIProxyCallRecord]
    count: int


# ===== Template 呼叫清單 =====
class AITemplateCallRecord(BaseModel):
    """單筆 Template 呼叫紀錄"""

    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str | None = None
    user_full_name: str | None = None
    call_type: str
    model_name: str
    preset: str | None = None
    input_tokens: int
    output_tokens: int
    request_duration_ms: int | None = None
    status: str
    error_message: str | None = None
    created_at: datetime


class AITemplateCallsResponse(BaseModel):
    """Template 呼叫清單回應"""

    data: list[AITemplateCallRecord]
    count: int


# ===== 使用者用量彙總 =====
class AIUserUsageSummary(BaseModel):
    """單一使用者的 AI 用量彙總"""

    user_id: uuid.UUID
    user_email: str | None = None
    user_full_name: str | None = None
    proxy_calls: int
    proxy_input_tokens: int
    proxy_output_tokens: int
    template_calls: int
    template_input_tokens: int
    template_output_tokens: int


class AIUsersUsageResponse(BaseModel):
    """使用者用量彙總回應"""

    data: list[AIUserUsageSummary]
    count: int


__all__ = [
    "AIMonitoringStats",
    "AIProxyCallRecord",
    "AIProxyCallsResponse",
    "AITemplateCallRecord",
    "AITemplateCallsResponse",
    "AIUserUsageSummary",
    "AIUsersUsageResponse",
]
