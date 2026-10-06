"""Campus-owned, OpenAI-compatible AI API relay.

The relay deliberately exposes a small data-plane allowlist.  It validates a
Campus ``ccai_*`` credential, applies the Campus rate limit, then replaces the
client's Authorization header with the restricted LiteLLM service key.  It is
not a generic proxy to the LiteLLM administration or health APIs.

路由只做驗證、限流與 body 檢查；轉送、串流與用量入帳在
``app.services.llm_gateway.relay_service``。
"""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from app.api.deps import AIAPIUserDep, SessionDep
from app.api.request_body import read_limited_body
from app.core.i18n import t
from app.features.ai.config import settings as ai_api_settings
from app.infrastructure.redis import (
    ai_proxy_rate_limit_key,
    check_rate_limit_by_key,
    check_rate_limit_sliding_window,
    get_redis,
    peek_rate_limit_by_key,
)
from app.schemas.ai_proxy import RateLimitStatusResponse, UsageStatsResponse
from app.services.llm_gateway import ai_gateway_service, relay_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-proxy", tags=["ai_proxy"])


def _reject_non_finite_json_number(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"JSON number exceeds finite float range: {value}")
    return parsed


def _credential_rate_limit(credential: Any) -> int:
    """每分鐘上限：金鑰自己的 rate_limit 優先，否則用全站預設（限流與狀態端點共用）。"""
    limit: int = (
        credential.rate_limit
        if credential.rate_limit is not None
        else ai_api_settings.ai_api_rate_limit_per_minute
    )
    return limit


async def _enforce_rate_limit(*, credential: Any, models: bool = False) -> None:
    limit = 30 if models else _credential_rate_limit(credential)
    redis = await get_redis()
    if models:
        allowed, rate_info = await check_rate_limit_by_key(
            redis,
            key=f"ai-models:{credential.request_id}",
            limit=limit,
            window_seconds=60,
            scope="ai-proxy-models",
        )
    else:
        allowed, rate_info = await check_rate_limit_sliding_window(
            redis=redis,
            request_id=str(credential.request_id),
            legacy_credential_ids=credential._rate_limit_legacy_ids,
            limit=limit,
            window_seconds=ai_api_settings.ai_api_rate_limit_window_seconds,
        )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(rate_info["window_seconds"])},
            detail={
                "error": "rate_limit_exceeded",
                "message": t(
                    "aiProxy.rateLimitExceeded",
                    limit=rate_info["limit"],
                    window_seconds=rate_info["window_seconds"],
                ),
                "limit": rate_info["limit"],
                "current": rate_info["current"],
                "reset_at": rate_info["reset_at"].isoformat(),
            },
        )


async def _json_payload(request: Request) -> dict[str, Any] | JSONResponse:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not (
        media_type == "application/json"
        or (media_type.startswith("application/") and media_type.endswith("+json"))
    ):
        return relay_service.openai_error(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Content-Type must be application/json.",
            error_type="invalid_request_error",
            code="unsupported_media_type",
        )

    try:
        body = await read_limited_body(
            request, max_bytes=ai_api_settings.ai_api_max_request_body_bytes
        )
    except HTTPException as exc:
        return relay_service.openai_error(
            exc.status_code,
            "Request body exceeds the configured AI API limit."
            if exc.status_code == 413
            else "Request body upload timed out.",
            error_type="invalid_request_error",
            code="request_too_large" if exc.status_code == 413 else "request_timeout",
        )
    try:
        payload = json.loads(
            body,
            parse_constant=_reject_non_finite_json_number,
            parse_float=_parse_finite_json_float,
        )
    # JSONDecodeError 之外，非 UTF-8（UnicodeDecodeError）、超長整數（ValueError）
    # 與過深巢狀（RecursionError）也都是格式錯誤，一律回 invalid_json 400。
    except (ValueError, RecursionError):
        return relay_service.openai_error(
            status.HTTP_400_BAD_REQUEST,
            "Request body must be valid JSON.",
            error_type="invalid_request_error",
            code="invalid_json",
        )
    if not isinstance(payload, dict):
        return relay_service.openai_error(
            status.HTTP_400_BAD_REQUEST,
            "Request body must be a JSON object.",
            error_type="invalid_request_error",
            code="invalid_request",
        )
    return payload


def _request_model(payload: dict[str, Any]) -> str | JSONResponse:
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        return relay_service.openai_error(
            status.HTTP_400_BAD_REQUEST,
            "model is required and must be a non-empty string.",
            error_type="invalid_request_error",
            code="invalid_model",
        )
    return model.strip()


async def _relay_generation(
    *,
    endpoint: str,
    request: Request,
    user_and_credential: tuple[Any, Any],
) -> Response:
    user, credential = user_and_credential
    await _enforce_rate_limit(credential=credential)

    payload = await _json_payload(request)
    if isinstance(payload, JSONResponse):
        return payload
    model_name = _request_model(payload)
    if isinstance(model_name, JSONResponse):
        return model_name

    return await relay_service.relay_generation(
        endpoint=endpoint,
        request=request,
        payload=payload,
        model_name=model_name,
        user=user,
        credential=credential,
    )


@router.post(
    "/chat/completions",
    summary="Chat completions",
    description="Relay an OpenAI-compatible chat completion to LiteLLM.",
)
async def chat_completions(
    request: Request,
    user_and_credential: AIAPIUserDep,
) -> Response:
    return await _relay_generation(
        endpoint="chat/completions",
        request=request,
        user_and_credential=user_and_credential,
    )


@router.post(
    "/completions",
    summary="Completions",
    description="Relay an OpenAI-compatible completion to LiteLLM.",
)
async def completions(
    request: Request,
    user_and_credential: AIAPIUserDep,
) -> Response:
    return await _relay_generation(
        endpoint="completions",
        request=request,
        user_and_credential=user_and_credential,
    )


@router.post(
    "/responses",
    summary="Responses",
    description="Relay an OpenAI-compatible Responses API request to LiteLLM.",
)
async def responses(
    request: Request,
    user_and_credential: AIAPIUserDep,
) -> Response:
    return await _relay_generation(
        endpoint="responses",
        request=request,
        user_and_credential=user_and_credential,
    )


@router.get(
    "/models",
    summary="List available models",
    description="List the models available through the restricted LiteLLM identity.",
)
async def list_models(request: Request, user_and_credential: AIAPIUserDep) -> Response:
    user, credential = user_and_credential
    await _enforce_rate_limit(credential=credential, models=True)
    return await relay_service.list_models(request, user=user)


@router.get(
    "/usage/my",
    response_model=UsageStatsResponse,
    summary="View my usage statistics",
)
async def get_my_usage_stats(
    user_and_credential: AIAPIUserDep,
    session: SessionDep,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> dict[str, Any]:
    user, _credential = user_and_credential
    start_date, end_date = ai_gateway_service.default_usage_window(
        start_date, end_date
    )
    stats = await run_in_threadpool(
        ai_gateway_service.get_user_usage_stats,
        session=session,
        user_id=user.id,
        start_date=start_date,
        end_date=end_date,
    )
    logger.info("AI API usage requested by user=%s", user.id)
    return stats


@router.get(
    "/rate-limit/status",
    response_model=RateLimitStatusResponse,
    summary="View rate limit status",
)
async def get_rate_limit_status(
    user_and_credential: AIAPIUserDep,
) -> RateLimitStatusResponse:
    _user, credential = user_and_credential
    limit = _credential_rate_limit(credential)
    redis = await get_redis()
    if redis is None:
        return RateLimitStatusResponse(
            limit_per_minute=limit,
            current_usage=0,
            remaining=limit,
            reset_at=datetime.now(tz=timezone.utc),
            disabled=True,
        )

    window_seconds = ai_api_settings.ai_api_rate_limit_window_seconds
    now_ms = int(time.time() * 1000)
    current_usage = await peek_rate_limit_by_key(
        redis,
        key=ai_proxy_rate_limit_key(str(credential.request_id)),
        window_seconds=window_seconds,
        legacy_keys=tuple(
            f"credential:{item}" for item in credential._rate_limit_legacy_ids
        ),
    )
    if current_usage is None:
        return RateLimitStatusResponse(
            limit_per_minute=limit,
            current_usage=0,
            remaining=limit,
            reset_at=datetime.now(tz=timezone.utc),
            error="rate_limit_status_unavailable",
        )
    return RateLimitStatusResponse(
        limit_per_minute=limit,
        current_usage=current_usage,
        remaining=max(0, limit - current_usage),
        reset_at=datetime.fromtimestamp(
            (now_ms + window_seconds * 1000) / 1000, tz=timezone.utc
        ),
    )
