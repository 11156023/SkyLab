"""
AI Monitoring Routes — Admin 全局 AI 使用監控

掛載在 /ai-api/monitoring/ 前綴下
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.api.deps import AIAPIViewAllUser, SessionDep
from app.core.i18n import t
from app.features.ai.config import settings as ai_api_settings
from app.schemas.ai_monitoring import (
    AILiteLLMRuntimeSnapshot,
    AIMonitoringOverview,
    AIMonitoringStats,
    AIProxyCallsResponse,
    AITemplateCallsResponse,
    AIUsersUsageResponse,
)
from app.services.llm_gateway import ai_gateway_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-api/monitoring", tags=["ai-monitoring"])


@router.get(
    "/overview",
    response_model=AIMonitoringOverview,
    summary="AI 用量與錯誤趨勢總覽",
)
def get_overview(
    session: SessionDep,
    _current_user: AIAPIViewAllUser,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    bucket: Literal["hour", "day"] = "hour",
    compare: bool = True,
):
    """提供管理員首頁使用的時間序列與模型聚合資料。"""
    return ai_gateway_service.get_monitoring_overview(
        session=session,
        start_date=start_date,
        end_date=end_date,
        bucket=bucket,
        compare=compare,
    )


@router.get(
    "/stats",
    response_model=AIMonitoringStats,
    summary="全局 AI 統計卡片",
)
def get_stats(
    session: SessionDep,
    _current_user: AIAPIViewAllUser,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
):
    """全局 AI 使用統計（Admin only）"""
    return ai_gateway_service.get_monitoring_stats(
        session=session,
        start_date=start_date,
        end_date=end_date,
    )


@router.get(
    "/api-calls",
    response_model=AIProxyCallsResponse,
    summary="Proxy 呼叫清單",
)
def list_api_calls(
    session: SessionDep,
    _current_user: AIAPIViewAllUser,
    user_id: uuid.UUID | None = None,
    model_name: str | None = Query(default=None, max_length=255),
    status: str | None = Query(default=None, max_length=50),
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    """列出所有 Proxy 呼叫紀錄，支援篩選（Admin only）"""
    return ai_gateway_service.list_proxy_calls(
        session=session,
        user_id=user_id,
        model_name=model_name,
        call_status=status,
        start_date=start_date,
        end_date=end_date,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/template-calls",
    response_model=AITemplateCallsResponse,
    summary="Template 呼叫清單",
)
def list_template_calls(
    session: SessionDep,
    _current_user: AIAPIViewAllUser,
    user_id: uuid.UUID | None = None,
    call_type: str | None = Query(default=None, max_length=30),
    preset: str | None = Query(default=None, max_length=50),
    status: str | None = Query(default=None, max_length=50),
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    """列出所有 Template 呼叫紀錄，支援篩選（Admin only）"""
    return ai_gateway_service.list_template_calls(
        session=session,
        user_id=user_id,
        call_type=call_type,
        preset=preset,
        call_status=status,
        start_date=start_date,
        end_date=end_date,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/users",
    response_model=AIUsersUsageResponse,
    summary="使用者用量彙總",
)
def list_users_usage(
    session: SessionDep,
    _current_user: AIAPIViewAllUser,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    """每個使用者的 AI 用量彙總（Admin only）"""
    return ai_gateway_service.list_users_usage(
        session=session,
        start_date=start_date,
        end_date=end_date,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/litellm-runtime",
    response_model=AILiteLLMRuntimeSnapshot,
    summary="LiteLLM runtime snapshot",
)
async def get_litellm_runtime_snapshot(_current_user: AIAPIViewAllUser):
    """Return staging LiteLLM health to an authorised Campus administrator.

    The public `ai-proxy` relay never exposes LiteLLM health or management
    endpoints. This deliberately returns a compact, secret-free snapshot and
    fails closed when the optional internal observation credential is absent.
    """
    api_key = ai_api_settings.litellm_runtime_api_key
    if not api_key:
        raise HTTPException(
            status_code=503, detail=t("aiMonitoring.runtimeNotConfigured")
        )

    base_url = ai_api_settings.litellm_runtime_base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            async def _get_probe(
                path: str, *, authenticated: bool = False
            ) -> httpx.Response | None:
                try:
                    request_kwargs = {"headers": headers} if authenticated else {}
                    return await client.get(f"{base_url}{path}", **request_kwargs)
                except httpx.RequestError:
                    return None

            # All runtime probes are independent.  Keep model discovery from
            # adding a second network round-trip after the health probes.
            liveliness, readiness, deployments, models_response = await asyncio.gather(
                _get_probe("/health/liveliness"),
                _get_probe("/health/readiness"),
                _get_probe("/health", authenticated=True),
                _get_probe("/v1/models", authenticated=True),
            )
    except httpx.RequestError:
        logger.warning("LiteLLM runtime snapshot request failed")
        raise HTTPException(
            status_code=503, detail=t("aiMonitoring.runtimeUnavailable")
        ) from None

    if liveliness is None or readiness is None or deployments is None:
        logger.warning("LiteLLM runtime health request failed")
        raise HTTPException(
            status_code=503, detail=t("aiMonitoring.runtimeUnavailable")
        ) from None

    try:
        deployment_health = deployments.json() if deployments.is_success else {}
    except ValueError:
        deployment_health = {}
    if not isinstance(deployment_health, dict):
        deployment_health = {}

    # `/health` has changed shape across LiteLLM versions. Preserve only the
    # status counts here, never a raw upstream response that could reveal an
    # internal URL or a future sensitive field.
    healthy = deployment_health.get("healthy_endpoints", [])
    unhealthy = deployment_health.get("unhealthy_endpoints", [])
    if not isinstance(healthy, list):
        healthy = []
    if not isinstance(unhealthy, list):
        unhealthy = []

    def _model_name(value: object, *, include_id: bool = False) -> str | None:
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned and not cleaned.startswith(("http://", "https://")):
                return cleaned
            return None
        if not isinstance(value, dict):
            return None
        keys: tuple[str, ...] = (
            "id",
            "model_name",
            "model_id",
            "public_model_name",
            "model",
        )
        if not include_id:
            keys = keys[1:]
        for key in keys:
            candidate = value.get(key)
            resolved = _model_name(candidate)
            if resolved:
                return resolved
        model_info = value.get("model_info")
        if isinstance(model_info, dict):
            for key in ("id", "model_name", "model_id"):
                candidate = model_info.get(key)
                resolved = _model_name(candidate)
                if resolved:
                    return resolved
        litellm_params = value.get("litellm_params")
        if isinstance(litellm_params, dict):
            candidate = litellm_params.get("model")
            if isinstance(candidate, str):
                return _model_name(candidate)
        return None

    healthy_names: set[str] = set()
    for entry in healthy:
        name = _model_name(entry)
        if name:
            healthy_names.add(name)
    unhealthy_names: set[str] = set()
    for entry in unhealthy:
        name = _model_name(entry)
        if name:
            unhealthy_names.add(name)
    advertised_names: set[str] = set()
    if models_response is not None and models_response.is_success:
        try:
            models_payload = models_response.json()
        except ValueError:
            models_payload = {}
        model_entries = models_payload.get("data", []) if isinstance(models_payload, dict) else []
        if not isinstance(model_entries, list):
            model_entries = []
        for entry in model_entries:
            name = _model_name(entry, include_id=True)
            if name:
                advertised_names.add(name)

    discovered_names = advertised_names | healthy_names | unhealthy_names
    models = []
    for name in sorted(discovered_names):
        healthy_count = sum(1 for entry in healthy if _model_name(entry) == name)
        unhealthy_count = sum(1 for entry in unhealthy if _model_name(entry) == name)
        if healthy_count and unhealthy_count:
            status = "degraded"
        elif healthy_count:
            status = "online"
        elif unhealthy_count:
            status = "offline"
        else:
            status = "unknown"
        models.append(
            {
                "name": name,
                "status": status,
                "healthy_deployments": healthy_count,
                "unhealthy_deployments": unhealthy_count,
            }
        )

    liveliness_ok = liveliness.is_success
    readiness_ok = readiness.is_success
    gateway_status = (
        "available" if liveliness_ok and readiness_ok else
        "degraded" if liveliness_ok else "unavailable"
    )
    model_summary = {
        "online": sum(1 for model in models if model["status"] == "online"),
        "degraded": sum(1 for model in models if model["status"] == "degraded"),
        "offline": sum(1 for model in models if model["status"] == "offline"),
        "unknown": sum(1 for model in models if model["status"] == "unknown"),
    }
    return {
        "checked_at": datetime.now(timezone.utc),
        "liveliness": liveliness_ok,
        "readiness": readiness_ok,
        "gateway": {
            "status": gateway_status,
            "liveliness": liveliness_ok,
            "readiness": readiness_ok,
        },
        "summary": model_summary,
        "models": models,
        "model_discovery": "available" if discovered_names else "unavailable",
        "healthy_deployment_count": len(healthy) if isinstance(healthy, list) else 0,
        "unhealthy_deployment_count": len(unhealthy) if isinstance(unhealthy, list) else 0,
        "deployment_status_code": deployments.status_code,
    }
