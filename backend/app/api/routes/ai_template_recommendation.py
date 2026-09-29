from __future__ import annotations

import asyncio
import functools
import logging
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.ai.monitoring import (
    new_ai_request_id,
    record_ai_template_call,
    usage_metrics,
)
from app.ai.template_recommendation import options_service
from app.ai.template_recommendation.config import settings
from app.ai.template_recommendation.prompt import (
    build_chat_runtime_context,
    build_chat_system_prompt,
    build_intake_focus_block,
)
from app.ai.template_recommendation.recommendation_service import (
    ensure_recommendation_form_context_within_limits,
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
from app.infrastructure.ai.template_recommendation import client
from app.services.llm_gateway import ai_gateway_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ai/template-recommendation",
    tags=["ai-template-recommendation"],
)

# 這兩支會實際打模型：一個帳號沒有節流就能把 GPU 佔滿並累積 token 費用
_MODEL_CALL_RATE_LIMIT = Depends(
    rate_limit_by_user(scope="ai-template", limit=30, window_seconds=60)
)


async def _record_template_call(**kwargs: Any) -> None:
    """在 worker thread 記錄 template 呼叫（DB 寫入不可卡住 event loop）。

    走 record_ai_template_call 才會同時更新 Prometheus 指標；它本身會吞掉記錄
    錯誤，記錄失敗不會掩蓋原始結果或錯誤。
    """
    await asyncio.to_thread(functools.partial(record_ai_template_call, **kwargs))


async def _record_failed_template_call(
    session: Session,
    *,
    user_id: Any,
    call_type: str,
    model_name: str,
    request_id: str,
    started_at: float,
    started_at_utc: datetime,
    exc: Exception,
) -> None:
    """記錄失敗的 template 呼叫（chat／recommend 共用）。"""
    await _record_template_call(
        session=session,
        user_id=user_id,
        call_type=call_type,
        model_name=model_name,
        metrics=usage_metrics(
            {},
            perf_counter() - started_at,
            request_id=request_id,
            started_at=started_at_utc,
        ),
        status="error",
        error_message=str(exc),
    )


def _raise_if_upstream_error(exc: Exception) -> None:
    """模型上游（vLLM）的 HTTP 錯誤一律轉成 502；其他例外交回呼叫端原樣拋出。"""
    if isinstance(exc, httpx.HTTPError):
        logger.error("vLLM upstream error: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=t("aiTemplateRecommendation.upstreamError"),
        ) from exc


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
    # 同步的 PVE／DB 呼叫一律丟到 worker thread：async 路由直接呼叫會在 PVE 慢或
    # 連線池耗盡時凍住整個 event loop（VNC／終端機／教室 WS 一起卡住）。
    # session 同一時間只交給一個 thread 依序使用，是安全的。
    gpu_options = await asyncio.to_thread(
        options_service.resolve_chat_gpu_options, request, session
    )
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
        metrics = usage_metrics(
            data,
            perf_counter() - started_at,
            request_id=request_id,
            started_at=started_at_utc,
        )
        content = strip_think_tags(data["choices"][0]["message"]["content"] or "")

        await _record_template_call(
            session=session,
            user_id=current_user.id,
            call_type="chat",
            model_name=model_name,
            metrics=metrics,
            status="success",
        )

        elapsed_seconds = float(metrics["elapsed_seconds"])
        completion_tokens = int(metrics["completion_tokens"])
        return ChatResponse(
            reply=content,
            prompt_tokens=int(metrics["prompt_tokens"]),
            completion_tokens=completion_tokens,
            total_tokens=int(metrics["total_tokens"]),
            elapsed_seconds=elapsed_seconds,
            tokens_per_second=(
                round(completion_tokens / elapsed_seconds, 2)
                if elapsed_seconds > 0
                else 0.0
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        await _record_failed_template_call(
            session,
            user_id=current_user.id,
            call_type="chat",
            model_name=model_name,
            request_id=request_id,
            started_at=started_at,
            started_at_utc=started_at_utc,
            exc=exc,
        )
        _raise_if_upstream_error(exc)
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
    live_nodes_task = asyncio.create_task(
        options_service.get_live_device_nodes_safely()
    )
    form_context = request.form_context
    # 同步 PVE／DB 呼叫丟到 worker thread，理由同 chat
    gpu_options = await asyncio.to_thread(
        options_service.resolve_recommend_gpu_options,
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
    )
    # 表單快照過大屬於客戶端錯誤：在記錄用量的 try 之前就擋掉（與 /chat 一致）
    ensure_recommendation_form_context_within_limits(merged_request)

    resource_options = await asyncio.to_thread(
        options_service.resolve_resource_options,
        request,
        gpu_options,
        session,
        current_user,
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

        # 記錄 template recommend 呼叫（耗時為模型呼叫本身，由 generate_ai_plan 量測）
        await _record_template_call(
            session=session,
            user_id=current_user.id,
            call_type="recommend",
            model_name=model_name,
            preset=merged_request.preset,
            metrics=ai_metrics,
            status="success",
        )

        return result
    except Exception as exc:
        await _record_failed_template_call(
            session,
            user_id=current_user.id,
            call_type="recommend",
            model_name=model_name,
            request_id=request_id,
            started_at=started_at,
            started_at_utc=started_at_utc,
            exc=exc,
        )
        _raise_if_upstream_error(exc)
        raise


@router.get("/usage/my", summary="查看我的 Template 使用統計")
def get_my_template_usage(
    current_user: CurrentUser,
    session: SessionDep,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    tz: str | None = Query(default=None, max_length=64),
):
    """查看當前使用者的 Template 呼叫統計（最近 30 天）"""
    start_date, end_date = ai_gateway_service.default_usage_window(
        start_date, end_date
    )
    return ai_gateway_service.get_user_template_usage_stats(
        session=session,
        user_id=current_user.id,
        start_date=start_date,
        end_date=end_date,
        tz=tz,
    )
