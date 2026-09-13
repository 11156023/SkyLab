"""
AI Monitoring Schemas — Admin 全局 AI 使用監控
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


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


class AIMonitoringSummary(BaseModel):
    """可直接呈現在監控總覽的期間摘要。"""

    total_calls: int
    successful_calls: int
    failed_calls: int
    error_rate: float | None = None
    total_tokens: int
    avg_latency_ms: int | None = None
    active_users: int


class AIMonitoringPeriodComparison(BaseModel):
    """目前期間相較於前一個等長期間的變化。"""

    total_calls_delta: int = 0
    total_calls_percent: float | None = None
    failed_calls_delta: int = 0
    failed_calls_percent: float | None = None
    error_rate_delta: float | None = None
    avg_latency_ms_delta: int | None = None


class AIMonitoringTrendPoint(BaseModel):
    """單一時間 bucket 的呼叫與錯誤趨勢。"""

    bucket_start: datetime
    total_calls: int
    successful_calls: int
    failed_calls: int
    total_tokens: int = 0
    error_rate: float | None = None
    avg_latency_ms: int | None = None
    proxy_calls: int = 0
    template_calls: int = 0


class AIMonitoringModelSummary(BaseModel):
    """期間內依模型聚合的用量與錯誤摘要。"""

    model_name: str
    total_calls: int
    total_tokens: int = 0
    failed_calls: int
    error_rate: float | None = None
    avg_latency_ms: int | None = None


class AIMonitoringOverview(BaseModel):
    """AI 監控首頁所需的摘要、比較與趨勢資料。"""

    start_date: datetime | None = None
    end_date: datetime | None = None
    bucket: str
    summary: AIMonitoringSummary
    comparison: AIMonitoringPeriodComparison
    series: list[AIMonitoringTrendPoint]
    model_breakdown: list[AIMonitoringModelSummary]


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
    failed_calls: int = 0
    error_rate: float | None = None
    avg_latency_ms: int | None = None


class AIUsersUsageResponse(BaseModel):
    """使用者用量彙總回應"""

    data: list[AIUserUsageSummary]
    count: int


class AILiteLLMModelStatus(BaseModel):
    """不含內部 URL 或憑證的公開模型健康摘要。"""

    name: str
    status: str
    healthy_deployments: int = 0
    unhealthy_deployments: int = 0


class AILiteLLMRuntimeGateway(BaseModel):
    status: str
    liveliness: bool
    readiness: bool


class AILiteLLMRuntimeSummary(BaseModel):
    online: int = 0
    degraded: int = 0
    offline: int = 0
    unknown: int = 0


class AILiteLLMRuntimeSnapshot(BaseModel):
    """LiteLLM runtime 的去敏監控快照。"""

    checked_at: datetime
    # 保留舊版平面欄位，讓既有管理端整合不需同步切換。
    liveliness: bool
    readiness: bool
    gateway: AILiteLLMRuntimeGateway
    summary: AILiteLLMRuntimeSummary
    models: list[AILiteLLMModelStatus] = Field(default_factory=list)
    model_discovery: str = "unavailable"
    healthy_deployment_count: int = 0
    unhealthy_deployment_count: int = 0
    deployment_status_code: int | None = None


__all__ = [
    "AIMonitoringStats",
    "AIMonitoringSummary",
    "AIMonitoringPeriodComparison",
    "AIMonitoringTrendPoint",
    "AIMonitoringModelSummary",
    "AIMonitoringOverview",
    "AIProxyCallRecord",
    "AIProxyCallsResponse",
    "AITemplateCallRecord",
    "AITemplateCallsResponse",
    "AIUserUsageSummary",
    "AIUsersUsageResponse",
    "AILiteLLMModelStatus",
    "AILiteLLMRuntimeGateway",
    "AILiteLLMRuntimeSummary",
    "AILiteLLMRuntimeSnapshot",
]
