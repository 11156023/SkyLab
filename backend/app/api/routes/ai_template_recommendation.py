from __future__ import annotations

import asyncio
import logging
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from time import monotonic, perf_counter
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.ai.monitoring import new_ai_request_id
from app.ai.template_recommendation.config import settings
from app.ai.template_recommendation.node_service import (
    build_resource_option_bundle,
    load_live_device_nodes,
)
from app.ai.template_recommendation.prompt import (
    build_chat_runtime_context,
    build_chat_system_prompt,
    build_intake_focus_block,
)
from app.ai.template_recommendation.recommendation_service import (
    generate_ai_plan,
    infer_intent_from_chat,
    normalize_ai_result,
)
from app.ai.template_recommendation.schemas import (
    ChatRequest,
    ChatResponse,
    RecommendationRequest,
)
from app.ai.utils import (
    apply_thinking_control,
    ensure_conversation_within_limits,
    strip_think_tags,
)
from app.api.deps import CurrentUser, SessionDep
from app.api.deps.rate_limit import rate_limit_by_user
from app.core.i18n import t
from app.core.permissions import Permission, has_permission
from app.infrastructure.ai.template_recommendation import client
from app.repositories import vm_request as vm_request_repo
from app.repositories import vm_template as vm_template_repo
from app.services.llm_gateway import ai_gateway_service
from app.services.proxmox import gpu_service
from app.services.template import template_service

logger = logging.getLogger(__name__)

_GPU_OPTIONS_CACHE_TTL_SECONDS = 20.0
_LIVE_NODES_CACHE_TTL_SECONDS = 15.0
_RESOURCE_OPTIONS_CACHE_TTL_SECONDS = 300.0
_gpu_options_cache: dict[str, Any] = {"at": 0.0, "items": []}
_live_nodes_cache: dict[str, Any] = {"at": 0.0, "items": []}
_base_resource_options_cache: dict[str, Any] = {"at": 0.0, "items": None}
_application_templates_cache: dict[str, Any] = {"at": 0.0, "items": None}

router = APIRouter(
    prefix="/ai/template-recommendation",
    tags=["ai-template-recommendation"],
)

# 這兩支會實際打模型：一個帳號沒有節流就能把 GPU 佔滿並累積 token 費用
_MODEL_CALL_RATE_LIMIT = Depends(
    rate_limit_by_user(scope="ai-template", limit=30, window_seconds=60)
)


def _latest_user_text(request: ChatRequest) -> str:
    for message in reversed(request.messages):
        if str(message.role).strip().lower() == "user":
            return str(message.content or "")
    return ""


def _should_include_gpu_runtime_context(request: ChatRequest) -> bool:
    form_context = request.form_context
    if form_context and (
        (form_context.resource_type and str(form_context.resource_type).lower() == "vm")
        or form_context.selected_gpu_mapping_id
    ):
        return True

    text = _latest_user_text(request).lower()
    keywords = (
        "gpu",
        "vram",
        "cuda",
        "nvidia",
        "pytorch",
        "tensorflow",
        "llm",
        "yolo",
        "訓練",
        "推理",
        "顯卡",
    )
    return any(keyword in text for keyword in keywords)


def _get_base_gpu_options_cached() -> list[dict[str, Any]]:
    now = monotonic()
    cached_at = float(_gpu_options_cache.get("at") or 0.0)
    cached_items = list(_gpu_options_cache.get("items") or [])
    if cached_items and (now - cached_at) <= _GPU_OPTIONS_CACHE_TTL_SECONDS:
        return [dict(item) for item in cached_items]

    fresh_items = [
        item.model_dump(mode="json") for item in gpu_service.list_gpu_options()
    ]
    _gpu_options_cache["at"] = now
    _gpu_options_cache["items"] = fresh_items
    return [dict(item) for item in fresh_items]


def _get_live_device_nodes_cached() -> list[Any]:
    now = monotonic()
    cached_at = float(_live_nodes_cache.get("at") or 0.0)
    cached_items = list(_live_nodes_cache.get("items") or [])
    if cached_at > 0 and (now - cached_at) <= _LIVE_NODES_CACHE_TTL_SECONDS:
        return [item.model_copy() for item in cached_items]

    fresh_items = load_live_device_nodes()
    _live_nodes_cache["at"] = now
    _live_nodes_cache["items"] = fresh_items
    return [item.model_copy() for item in fresh_items]


async def _get_live_device_nodes_safely() -> list[Any]:
    try:
        return await asyncio.to_thread(_get_live_device_nodes_cached)
    except Exception as exc:
        logger.warning("Unable to refresh live nodes for AI recommendation: %s", exc)
        return []


def _get_base_resource_options_cached() -> dict[str, Any]:
    now = monotonic()
    cached_at = float(_base_resource_options_cache.get("at") or 0.0)
    cached_items = _base_resource_options_cache.get("items")
    if (
        cached_items is not None
        and (now - cached_at) <= _RESOURCE_OPTIONS_CACHE_TTL_SECONDS
    ):
        return deepcopy(cached_items)

    fresh_items = build_resource_option_bundle(gpu_options=[])
    fresh_items["gpu_options"] = []
    _base_resource_options_cache["at"] = now
    _base_resource_options_cache["items"] = fresh_items
    return deepcopy(fresh_items)


def _build_resource_options_with_gpu(
    gpu_options: list[dict[str, Any]],
) -> dict[str, Any]:
    resource_options = _get_base_resource_options_cached()
    resource_options["gpu_options"] = [dict(item) for item in gpu_options]
    return resource_options


def _get_application_templates_cached(session: SessionDep) -> list[dict[str, Any]]:
    """已開放的應用範本目錄。

    目錄與使用者無關（開放與否是範本自己的旗標），所以整個程序共用一份快取；
    來源一律由伺服器決定，不採用客戶端送來的清單，否則模型的候選會變成前端
    可以偽造的東西。
    """
    now = monotonic()
    cached_at = float(_application_templates_cache.get("at") or 0.0)
    cached_items = _application_templates_cache.get("items")
    if (
        cached_items is not None
        and (now - cached_at) <= _RESOURCE_OPTIONS_CACHE_TTL_SECONDS
    ):
        return deepcopy(cached_items)
    try:
        catalog = template_service.list_student_catalog(session=session)
    except Exception as exc:  # pragma: no cover - PVE 失敗不該擋住建議
        logger.warning("Unable to load the application template catalog: %s", exc)
        return []
    items = [
        {
            "template_id": item.pve_vmid,
            "name": item.name,
            "description": item.description or "",
            "resource_type": item.resource_type,
            "cores": item.cores,
            "memory_mb": item.memory_mb,
            "disk_gb": item.disk_gb,
        }
        for item in catalog
    ]
    _application_templates_cache["at"] = now
    _application_templates_cache["items"] = items
    return deepcopy(items)


def _allowed_vm_template_ids(
    session: SessionDep,
    user: CurrentUser,
    application_templates: list[dict[str, Any]],
) -> set[int]:
    """使用者實際可以拿來申請的 VM 來源 id（PVE 讀不到時回空集合）。"""
    base = _get_base_resource_options_cached().get("vm_operating_systems") or []
    if not base:
        return set()
    allowed = {int(item.get("template_id") or 0) for item in base}
    if not has_permission(user, Permission.TEMPLATE_MANAGE):
        allowed -= vm_template_repo.registered_pve_vmids(session=session)
    allowed |= {
        int(item.get("template_id") or 0)
        for item in application_templates
        if str(item.get("resource_type")) != "lxc"
    }
    return allowed


def _resolve_resource_options(
    request: ChatRequest,
    gpu_options: list[dict[str, Any]],
    session: SessionDep,
    user: CurrentUser,
) -> dict[str, Any]:
    """候選清單必須跟使用者實際能選的一致。

    母範本同時也是 PVE template，所以伺服器端組清單時要濾掉已註冊的範本，
    再把開放申請的應用範本以獨立清單交給模型；否則模型會推薦到使用者根本
    申請不到（甚至看不到）的來源。
    """
    form_context = request.form_context
    application_templates = _get_application_templates_cached(session)
    if form_context and form_context.resource_options_from_client:
        client_vm_options = [
            item.model_dump(mode="json") for item in form_context.vm_os_options
        ]
        allowed_vm_ids = _allowed_vm_template_ids(session, user, application_templates)
        if allowed_vm_ids:
            client_vm_options = [
                item
                for item in client_vm_options
                if int(item.get("template_id") or 0) in allowed_vm_ids
            ]
        return {
            "lxc_os_images": [
                item.model_dump(mode="json") for item in form_context.lxc_os_options
            ],
            "vm_operating_systems": client_vm_options,
            "application_templates": application_templates,
            "gpu_options": [dict(item) for item in gpu_options],
        }
    resource_options = _build_resource_options_with_gpu(gpu_options)
    if not has_permission(user, Permission.TEMPLATE_MANAGE):
        registered = vm_template_repo.registered_pve_vmids(session=session)
        resource_options["vm_operating_systems"] = [
            item
            for item in resource_options.get("vm_operating_systems") or []
            if int(item.get("template_id") or 0) not in registered
        ]
    resource_options["application_templates"] = application_templates
    return resource_options


def _resolve_recommend_gpu_options(
    request: ChatRequest, *, requires_gpu: bool
) -> list[dict[str, Any]]:
    form_context = request.form_context
    if form_context and form_context.gpu_options:
        return [item.model_dump(mode="json") for item in form_context.gpu_options]

    selected_gpu_mapping_id = (
        form_context.selected_gpu_mapping_id if form_context else None
    )
    if not requires_gpu and not selected_gpu_mapping_id:
        return []

    return _get_base_gpu_options_cached()


def _resolve_chat_gpu_options(
    request: ChatRequest, session: SessionDep
) -> list[dict[str, Any]]:
    if not _should_include_gpu_runtime_context(request):
        return []

    options = _get_base_gpu_options_cached()
    form_context = request.form_context
    if not form_context or not form_context.start_at or not form_context.end_at:
        return options

    start_at = form_context.start_at
    end_at = form_context.end_at
    if end_at <= start_at:
        return options

    overlapping = vm_request_repo.get_approved_vm_requests_overlapping_window(
        session=session,
        window_start=start_at,
        window_end=end_at,
    )
    reserved_counts = Counter(
        str(item.gpu_mapping_id)
        for item in overlapping
        if item.gpu_mapping_id and item.vmid is None
    )

    adjusted: list[dict[str, Any]] = []
    for option in options:
        mapping_id = str(option.get("mapping_id") or "")
        reserved = int(reserved_counts.get(mapping_id, 0))
        capacity_count = int(
            option.get("capacity_count") or option.get("device_count") or 0
        )
        used_count = int(option.get("used_count") or 0)
        available_count = int(option.get("available_count") or 0)
        if reserved <= 0:
            adjusted.append(dict(option))
            continue

        updated = dict(option)
        updated["used_count"] = min(capacity_count, used_count + reserved)
        updated["available_count"] = max(0, available_count - reserved)
        adjusted.append(updated)

    return adjusted


@router.post(
    "/chat",
    response_model=ChatResponse,
    dependencies=[_MODEL_CALL_RATE_LIMIT],
)
async def chat(
    request: ChatRequest, current_user: CurrentUser, session: SessionDep
) -> ChatResponse:
    ensure_conversation_within_limits(request.messages)
    model_name = settings.VLLM_MODEL_NAME
    if not model_name:
        raise HTTPException(
            status_code=503,
            detail=t("aiTemplateRecommendation.modelBindingMissing"),
        )

    is_first_turn = len(request.messages) <= 1
    form_context = request.form_context
    gpu_options = _resolve_chat_gpu_options(request, session)
    runtime_context = (
        build_chat_runtime_context(
            resource_type=(form_context.resource_type if form_context else None),
            gpu_options=gpu_options,
            form_context=(
                form_context.model_dump(
                    mode="json",
                    exclude={
                        "gpu_options",
                        "lxc_os_options",
                        "vm_os_options",
                        "resource_options_from_client",
                    },
                )
                if form_context
                else None
            ),
        )
        if gpu_options or form_context
        else ""
    )
    system_prompt = build_chat_system_prompt(
        is_first_turn=is_first_turn,
        runtime_context=runtime_context,
    )
    # 配置模式：把這一輪的主題固定住，問句仍由顧問語氣產生
    if request.focus_hint:
        system_prompt = (
            f"{system_prompt}\n\n{build_intake_focus_block(request.focus_hint.strip())}"
        )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for msg in request.messages:
        messages.append({"role": msg.role, "content": msg.content})

    payload = apply_thinking_control(
        {
            "model": model_name,
            "messages": messages,
            "max_tokens": settings.VLLM_CHAT_MAX_TOKENS,
            "temperature": settings.VLLM_CHAT_TEMPERATURE,
            "top_p": settings.VLLM_TOP_P,
            "top_k": settings.VLLM_TOP_K,
            "min_p": settings.VLLM_MIN_P,
            "repetition_penalty": settings.VLLM_REPETITION_PENALTY,
        },
        settings.VLLM_ENABLE_THINKING,
    )

    request_id = new_ai_request_id()
    started_at = perf_counter()
    started_at_utc = datetime.now(timezone.utc)
    try:
        data = await client.create_chat_completion(payload, request_id=request_id)
        elapsed_seconds = max(perf_counter() - started_at, 0.0)
        completed_at = datetime.now(timezone.utc)
        raw_usage = data.get("usage")
        usage_reported = isinstance(raw_usage, dict)
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
        tokens_per_second = (
            output_tokens / elapsed_seconds if elapsed_seconds > 0 else 0.0
        )
        duration_ms = int(elapsed_seconds * 1000)

        content = strip_think_tags(data["choices"][0]["message"]["content"] or "")

        # 記錄 template chat 呼叫
        try:
            ai_gateway_service.record_template_call(
                session=session,
                user_id=current_user.id,
                call_type="chat",
                model_name=model_name,
                request_id=request_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                request_duration_ms=duration_ms,
                usage_reported=usage_reported,
                response_model=str(data.get("model") or "")[:255] or None,
                status="success",
                started_at=started_at_utc,
                completed_at=completed_at,
            )
        except Exception as rec_err:
            logger.error("Failed to record template chat usage: %s", rec_err)

        return ChatResponse(
            reply=content,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=total_tokens,
            elapsed_seconds=round(elapsed_seconds, 3),
            tokens_per_second=round(tokens_per_second, 2),
        )
    except HTTPException:
        raise
    except Exception as exc:
        # 記錄失敗
        try:
            ai_gateway_service.record_template_call(
                session=session,
                user_id=current_user.id,
                call_type="chat",
                model_name=model_name,
                request_id=request_id,
                request_duration_ms=int((perf_counter() - started_at) * 1000),
                status="error",
                error_message=str(exc)[:500],
                started_at=started_at_utc,
                completed_at=datetime.now(timezone.utc),
            )
        except Exception:
            # 記錄失敗 log 時出錯不得掩蓋原始錯誤
            pass
        if isinstance(exc, httpx.HTTPError):
            logger.error("vLLM upstream error: %s", exc)
            raise HTTPException(
                status_code=502,
                detail=t("aiTemplateRecommendation.upstreamError"),
            ) from exc
        raise


@router.post(
    "/recommend",
    response_model=dict[str, Any],
    dependencies=[_MODEL_CALL_RATE_LIMIT],
)
async def recommend(
    request: ChatRequest, current_user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    ensure_conversation_within_limits(request.messages)
    model_name = settings.VLLM_MODEL_NAME or "unknown"
    started_at = perf_counter()
    started_at_utc = datetime.now(timezone.utc)
    request_id = new_ai_request_id()

    # Keep recommendation to one model round-trip. The planner receives recent
    # conversation verbatim and resolves final intent there.
    extracted_intent = infer_intent_from_chat(request)
    live_nodes_task = asyncio.create_task(_get_live_device_nodes_safely())
    form_context = request.form_context
    gpu_options = _resolve_recommend_gpu_options(
        request,
        requires_gpu=extracted_intent.requires_gpu,
    )
    merged_request = RecommendationRequest(
        goal=extracted_intent.goal_summary,
        role=extracted_intent.role,
        course_context=extracted_intent.course_context,
        budget_mode=extracted_intent.budget_mode,
        needs_public_web=extracted_intent.needs_public_web,
        needs_database=extracted_intent.needs_database,
        requires_gpu=extracted_intent.requires_gpu,
        needs_windows=extracted_intent.needs_windows,
        device_nodes=request.device_nodes,
        form_context=form_context,
        top_k=request.top_k,
    )

    resource_options = _resolve_resource_options(
        request, gpu_options, session, current_user
    )

    try:
        ai_result, ai_metrics = await generate_ai_plan(
            merged_request,
            request.messages,
            resource_options=resource_options,
            request_id=request_id,
        )
        try:
            live_nodes = await asyncio.wait_for(
                asyncio.shield(live_nodes_task),
                timeout=0.25,
            )
        except TimeoutError:
            live_nodes = []
        if live_nodes:
            merged_request.device_nodes = live_nodes
        result = normalize_ai_result(
            ai_result,
            merged_request,
            merged_request.device_nodes,
            resource_options=resource_options,
        )
        result["live_device_nodes"] = [
            node.model_dump() for node in merged_request.device_nodes
        ]
        result["ai_metrics"] = ai_metrics
        result["resource_options"] = resource_options

        # 記錄 template recommend 呼叫
        try:
            ai_gateway_service.record_template_call(
                session=session,
                user_id=current_user.id,
                call_type="recommend",
                model_name=model_name,
                preset=merged_request.preset,
                request_id=request_id,
                input_tokens=int(ai_metrics.get("prompt_tokens") or 0),
                output_tokens=int(ai_metrics.get("completion_tokens") or 0),
                request_duration_ms=int((perf_counter() - started_at) * 1000),
                usage_reported=bool(ai_metrics.get("usage_reported", False)),
                response_model=str(ai_metrics.get("response_model") or "")[:255]
                or None,
                status="success",
                started_at=started_at_utc,
                completed_at=datetime.now(timezone.utc),
            )
        except Exception as rec_err:
            logger.error("Failed to record template recommend usage: %s", rec_err)

        return result
    except Exception as exc:
        elapsed_seconds = max(perf_counter() - started_at, 0.0)
        try:
            ai_gateway_service.record_template_call(
                session=session,
                user_id=current_user.id,
                call_type="recommend",
                model_name=model_name,
                request_id=request_id,
                request_duration_ms=int(elapsed_seconds * 1000),
                status="error",
                error_message=str(exc)[:500],
                started_at=started_at_utc,
                completed_at=datetime.now(timezone.utc),
            )
        except Exception:
            # 記錄失敗 log 時出錯不得掩蓋原始錯誤
            pass
        if isinstance(exc, httpx.HTTPError):
            logger.error("vLLM upstream error: %s", exc)
            raise HTTPException(
                status_code=502,
                detail=t("aiTemplateRecommendation.upstreamError"),
            ) from exc
        raise


@router.get("/usage/my", summary="查看我的 Template 使用統計")
def get_my_template_usage(
    current_user: CurrentUser,
    session: SessionDep,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
):
    """查看當前使用者的 Template 呼叫統計（最近 30 天）"""
    if not end_date:
        end_date = datetime.now(timezone.utc)
    if not start_date:
        start_date = end_date - timedelta(days=30)

    return ai_gateway_service.get_user_template_usage_stats(
        session=session,
        user_id=current_user.id,
        start_date=start_date,
        end_date=end_date,
    )
