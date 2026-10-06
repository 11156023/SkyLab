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
    source: Literal["all", "api_key"] = "all",
):
    """提供管理員首頁使用的時間序列與模型聚合資料。"""
    return ai_gateway_service.get_monitoring_overview(
        session=session,
        start_date=start_date,
        end_date=end_date,
        bucket=bucket,
        compare=compare,
        include_template=source == "all",
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
    source: Literal["all", "api_key"] = "all",
):
    """每個使用者的 AI 用量彙總（Admin only）"""
    return ai_gateway_service.list_users_usage(
        session=session,
        start_date=start_date,
        end_date=end_date,
        skip=skip,
        limit=limit,
        include_template=source == "all",
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
    async with httpx.AsyncClient(timeout=5.0) as client:

        async def _get_probe(
            path: str, *, authenticated: bool = False
        ) -> httpx.Response | None:
            # 連線錯誤一律回 None，由下方統一轉成 503
            try:
                return await client.get(
                    f"{base_url}{path}", headers=headers if authenticated else None
                )
            except httpx.RequestError:
                return None

        # All runtime probes are independent.  Keep model discovery from
        # adding a second network round-trip after the health probes.
        (
            liveliness,
            readiness,
            deployments,
            model_info_response,
            models_response,
        ) = await asyncio.gather(
            _get_probe("/health/liveliness"),
            _get_probe("/health/readiness"),
            _get_probe("/health", authenticated=True),
            _get_probe("/model/info", authenticated=True),
            _get_probe("/v1/models", authenticated=True),
        )

    if liveliness is None or readiness is None or deployments is None:
        logger.warning("LiteLLM runtime health request failed")
        raise HTTPException(
            status_code=503, detail=t("aiMonitoring.runtimeUnavailable")
        )

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

    def _clean_name(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        if cleaned and not cleaned.startswith(("http://", "https://")):
            return cleaned
        return None

    def _data_entries(response: httpx.Response | None) -> list[dict[str, object]]:
        if response is None or not response.is_success:
            return []
        try:
            payload = response.json()
        except ValueError:
            return []
        entries = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(entries, list):
            return []
        return [entry for entry in entries if isinstance(entry, dict)]

    # `/health` entries only carry the upstream `model` and the deployment hash
    # (`model_id`); `/model/info` maps that hash back to the public alias.
    alias_by_deployment_id: dict[str, str] = {}
    advertised_names: set[str] = set()
    for entry in _data_entries(model_info_response):
        alias = _clean_name(entry.get("model_name"))
        if not alias:
            continue
        advertised_names.add(alias)
        model_info = entry.get("model_info")
        deployment_id = model_info.get("id") if isinstance(model_info, dict) else None
        if isinstance(deployment_id, str) and deployment_id:
            alias_by_deployment_id[deployment_id] = alias
    if model_info_response is None or not model_info_response.is_success:
        logger.error(
            "LiteLLM /model/info probe failed (status=%s); deployment health "
            "cannot be mapped to public model names",
            getattr(model_info_response, "status_code", None),
        )
    for entry in _data_entries(models_response):
        alias = _clean_name(entry.get("id"))
        if alias:
            advertised_names.add(alias)

    def _deployment_alias(entry: object) -> str | None:
        if not isinstance(entry, dict):
            return None
        deployment_id = entry.get("model_id")
        if not deployment_id:
            model_info = entry.get("model_info")
            if isinstance(model_info, dict):
                deployment_id = model_info.get("id")
        if isinstance(deployment_id, str) and deployment_id:
            alias = alias_by_deployment_id.get(deployment_id)
            if alias:
                return alias
        # Never fall back to `model` or the deployment hash: those are the
        # upstream name and an internal id, not something to display.
        return _clean_name(entry.get("model_name"))

    healthy_aliases = [_deployment_alias(entry) for entry in healthy]
    unhealthy_aliases = [_deployment_alias(entry) for entry in unhealthy]
    unmapped = healthy_aliases.count(None) + unhealthy_aliases.count(None)
    if unmapped:
        logger.warning(
            "LiteLLM /health reported %s deployment(s) without a public model alias",
            unmapped,
        )

    discovered_names = (
        advertised_names
        | {name for name in healthy_aliases if name}
        | {name for name in unhealthy_aliases if name}
    )
    models = []
    for name in sorted(discovered_names):
        healthy_count = healthy_aliases.count(name)
        unhealthy_count = unhealthy_aliases.count(name)
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
        "available"
        if liveliness_ok and readiness_ok
        else "degraded"
        if liveliness_ok
        else "unavailable"
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
        "healthy_deployment_count": len(healthy),
        "unhealthy_deployment_count": len(unhealthy),
        "deployment_status_code": deployments.status_code,
    }
