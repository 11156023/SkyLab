from __future__ import annotations

import logging
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from time import monotonic, perf_counter
from typing import Any

from fastapi import APIRouter, HTTPException

from app.ai.template_recommendation.catalog_service import get_catalog
from app.ai.template_recommendation.config import settings
from app.ai.template_recommendation.node_service import (
    build_resource_option_bundle,
    load_live_device_nodes,
)
from app.ai.template_recommendation.prompt import (
    build_chat_catalog_context,
    build_chat_runtime_context,
    build_chat_system_prompt,
)
from app.ai.template_recommendation.recommendation_service import (
    extract_intent_from_chat,
    generate_ai_plan,
    normalize_ai_result,
)
from app.ai.template_recommendation.schemas import (
    ChatRequest,
    ChatResponse,
    RecommendationRequest,
)
from app.ai.utils import apply_thinking_control, strip_think_tags
from app.api.deps import CurrentUser, SessionDep
from app.infrastructure.ai.template_recommendation import client
from app.repositories import vm_request as vm_request_repo
from app.services.llm_gateway import ai_gateway_service
from app.services.proxmox import gpu_service

logger = logging.getLogger(__name__)

_GPU_OPTIONS_CACHE_TTL_SECONDS = 20.0
_LIVE_NODES_CACHE_TTL_SECONDS = 15.0
_RESOURCE_OPTIONS_CACHE_TTL_SECONDS = 20.0
_gpu_options_cache: dict[str, Any] = {"at": 0.0, "items": []}
_live_nodes_cache: dict[str, Any] = {"at": 0.0, "items": []}
_base_resource_options_cache: dict[str, Any] = {"at": 0.0, "items": None}

router = APIRouter(
    prefix="/ai/template-recommendation",
    tags=["ai-template-recommendation"],
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

    fresh_items = [item.model_dump(mode="json") for item in gpu_service.list_gpu_options()]
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


def _get_base_resource_options_cached() -> dict[str, Any]:
    now = monotonic()
    cached_at = float(_base_resource_options_cache.get("at") or 0.0)
    cached_items = _base_resource_options_cache.get("items")
    if cached_items is not None and (now - cached_at) <= _RESOURCE_OPTIONS_CACHE_TTL_SECONDS:
        return deepcopy(cached_items)

    fresh_items = build_resource_option_bundle(gpu_options=[])
    fresh_items["gpu_options"] = []
    _base_resource_options_cache["at"] = now
    _base_resource_options_cache["items"] = fresh_items
    return deepcopy(fresh_items)


def _build_resource_options_with_gpu(gpu_options: list[dict[str, Any]]) -> dict[str, Any]:
    resource_options = _get_base_resource_options_cached()
    resource_options["gpu_options"] = [dict(item) for item in gpu_options]
    return resource_options


def _resolve_recommend_gpu_options(request: ChatRequest, *, requires_gpu: bool) -> list[dict[str, Any]]:
    form_context = request.form_context
    if form_context and form_context.gpu_options:
        return [item.model_dump(mode="json") for item in form_context.gpu_options]

    selected_gpu_mapping_id = (
        form_context.selected_gpu_mapping_id if form_context else None
    )
    if not requires_gpu and not selected_gpu_mapping_id:
        return []

    return _get_base_gpu_options_cached()


def _resolve_chat_gpu_options(request: ChatRequest, session: SessionDep) -> list[dict[str, Any]]:
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
        device_count = int(option.get("device_count") or 0)
        used_count = int(option.get("used_count") or 0)
        available_count = int(option.get("available_count") or 0)
        if reserved <= 0:
            adjusted.append(dict(option))
            continue

        updated = dict(option)
        updated["used_count"] = min(device_count, used_count + reserved)
        updated["available_count"] = max(0, available_count - reserved)
        adjusted.append(updated)

    return adjusted


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest, current_user: CurrentUser, session: SessionDep
) -> ChatResponse:
    model_name = settings.VLLM_MODEL_NAME
    if not model_name:
        raise HTTPException(
            status_code=503,
            detail="AI model binding is missing in config/system-ai.json.",
        )

    catalog = get_catalog()
    is_first_turn = len(request.messages) <= 1
    catalog_context = build_chat_catalog_context(
        catalog,
        request.messages,
        top_k=request.top_k,
    )
    form_context = request.form_context
    gpu_options = _resolve_chat_gpu_options(request, session)
    runtime_context = (
        build_chat_runtime_context(
            resource_type=(form_context.resource_type if form_context else None),
            gpu_options=gpu_options,
        )
        if gpu_options
        else ""
    )
    system_prompt = build_chat_system_prompt(
        is_first_turn=is_first_turn,
        catalog_context=catalog_context,
        runtime_context=runtime_context,
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

    try:
        started_at = perf_counter()
        data = await client.create_chat_completion(payload)
        elapsed_seconds = max(perf_counter() - started_at, 0.0)
        usage = data.get("usage") or {}
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
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                request_duration_ms=duration_ms,
                status="success",
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
                status="error",
                error_message=str(exc)[:500],
            )
        except Exception:
            # 記錄失敗 log 時出錯不得掩蓋原始錯誤
            pass
        raise


@router.post("/recommend", response_model=dict[str, Any])
async def recommend(
    request: ChatRequest, current_user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    model_name = settings.VLLM_MODEL_NAME or "unknown"
    started_at = perf_counter()

    extracted_intent = await extract_intent_from_chat(request)
    live_nodes = _get_live_device_nodes_cached()
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
        device_nodes=live_nodes or request.device_nodes,
        form_context=form_context,
        top_k=request.top_k,
    )

    catalog = get_catalog()
    resource_options = _build_resource_options_with_gpu(gpu_options)

    try:
        ai_result, ai_metrics = await generate_ai_plan(
            merged_request,
            merged_request.device_nodes,
            catalog,
            request.messages,
            resource_options=resource_options,
        )
        result = normalize_ai_result(
            ai_result,
            merged_request,
            merged_request.device_nodes,
            catalog,
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
                input_tokens=int(ai_metrics.get("prompt_tokens") or 0),
                output_tokens=int(ai_metrics.get("completion_tokens") or 0),
                request_duration_ms=int((perf_counter() - started_at) * 1000),
                status="success",
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
                request_duration_ms=int(elapsed_seconds * 1000),
                status="error",
                error_message=str(exc)[:500],
            )
        except Exception:
            # 記錄失敗 log 時出錯不得掩蓋原始錯誤
            pass
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
