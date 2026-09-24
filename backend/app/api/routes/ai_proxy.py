"""Campus-owned, OpenAI-compatible AI API relay.

The relay deliberately exposes a small data-plane allowlist.  It validates a
Campus ``ccai_*`` credential, applies the Campus rate limit, then replaces the
client's Authorization header with the restricted LiteLLM service key.  It is
not a generic proxy to the LiteLLM administration or health APIs.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import logging
import time
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.api.deps import AIAPIUserDep, SessionDep
from app.core.i18n import t
from app.features.ai.config import settings as ai_api_settings
from app.infrastructure.redis import (
    ai_proxy_rate_limit_key,
    check_rate_limit_sliding_window,
    get_redis,
    peek_rate_limit_by_key,
)
from app.schemas.ai_proxy import RateLimitStatusResponse, UsageStatsResponse
from app.services.llm_gateway import ai_gateway_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-proxy", tags=["ai_proxy"])

_GENERATION_ENDPOINTS = {
    "chat/completions": "chat_completion",
    "completions": "completion",
    "responses": "response",
}
_REQUEST_HEADER_ALLOWLIST = (
    "accept",
    "openai-beta",
    "openai-organization",
    "openai-project",
    "x-request-id",
)
_RESPONSE_HEADER_ALLOWLIST = (
    "content-type",
    "openai-processing-ms",
    "retry-after",
    "x-request-id",
)


def _openai_error(
    status_code: int,
    message: str,
    *,
    error_type: str,
    code: str | None = None,
) -> JSONResponse:
    """Return a compact OpenAI-compatible error without leaking upstream data."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": error_type,
                "param": None,
                "code": code,
            }
        },
    )


def _upstream_failure(
    *,
    request: Request,
    upstream: httpx.Response,
    body: bytes,
    context: str,
) -> JSONResponse:
    """上游的錯誤 body 一律不轉給呼叫端。

    LiteLLM 的錯誤訊息會夾帶內部模型別名、後端 URL、服務金鑰片段與 traceback；
    對外只保留 status code 與泛用訊息，原文連同 request id 寫進 log 供追查。
    """
    request_id = (
        upstream.headers.get("x-request-id")
        or request.headers.get("x-request-id")
        or "-"
    )
    logger.warning(
        "AI API upstream error: context=%s status=%s request_id=%s body=%s",
        context,
        upstream.status_code,
        request_id,
        body[:2048].decode("utf-8", "replace"),
    )
    return _openai_error(
        upstream.status_code,
        "The model service rejected this request.",
        error_type="api_error",
        code="upstream_error",
    )


def _service_headers(request: Request) -> dict[str, str]:
    """Build the only headers allowed to cross the Campus → LiteLLM boundary."""
    headers = {
        "Authorization": f"Bearer {ai_api_settings.ai_api_upstream_api_key}",
        "Content-Type": "application/json",
    }
    for name in _REQUEST_HEADER_ALLOWLIST:
        value = request.headers.get(name)
        if value:
            headers[name] = value
    return headers


def _response_headers(upstream_headers: httpx.Headers) -> dict[str, str]:
    """Pass only response headers useful to OpenAI API clients.

    Host, Content-Length, connection-specific and implementation headers are
    intentionally never copied into the public response.
    """
    return {
        name: upstream_headers[name]
        for name in _RESPONSE_HEADER_ALLOWLIST
        if name in upstream_headers
    }


def _upstream_url(endpoint: str, query: str) -> str:
    base_url = ai_api_settings.resolved_vllm_base_url.rstrip("/")
    url = f"{base_url}/v1/{endpoint}"
    return f"{url}?{query}" if query else url


def _usage_tokens(payload: Any) -> tuple[int, int]:
    """Extract token counts from chat/completions/responses response shapes."""
    if not isinstance(payload, dict):
        return 0, 0
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0, 0
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
    try:
        return int(input_tokens or 0), int(output_tokens or 0)
    except (TypeError, ValueError):
        return 0, 0


def _update_stream_usage(line: str, usage: dict[str, int]) -> None:
    """Update usage from one SSE data line, preserving the bytes sent to clients."""
    if not line.startswith("data: "):
        return
    data = line[6:].strip()
    if not data or data == "[DONE]":
        return
    try:
        input_tokens, output_tokens = _usage_tokens(json.loads(data))
    except json.JSONDecodeError:
        return
    if input_tokens or output_tokens:
        usage["input_tokens"] = input_tokens
        usage["output_tokens"] = output_tokens


async def _enforce_rate_limit(*, user: Any, credential: Any) -> None:
    limit = (
        credential.rate_limit
        if credential.rate_limit is not None
        else ai_api_settings.ai_api_rate_limit_per_minute
    )
    redis = await get_redis()
    allowed, rate_info = await check_rate_limit_sliding_window(
        redis=redis,
        user_id=str(user.id),
        limit=limit,
        window_seconds=ai_api_settings.ai_api_rate_limit_window_seconds,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
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
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" not in content_type:
        return _openai_error(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Content-Type must be application/json.",
            error_type="invalid_request_error",
            code="unsupported_media_type",
        )

    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            too_large = int(declared_length) > ai_api_settings.ai_api_max_request_body_bytes
        except ValueError:
            too_large = False
        if too_large:
            return _openai_error(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "Request body exceeds the configured AI API limit.",
                error_type="invalid_request_error",
                code="request_too_large",
            )

    body = await request.body()
    if len(body) > ai_api_settings.ai_api_max_request_body_bytes:
        return _openai_error(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "Request body exceeds the configured AI API limit.",
            error_type="invalid_request_error",
            code="request_too_large",
        )
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return _openai_error(
            status.HTTP_400_BAD_REQUEST,
            "Request body must be valid JSON.",
            error_type="invalid_request_error",
            code="invalid_json",
        )
    if not isinstance(payload, dict):
        return _openai_error(
            status.HTTP_400_BAD_REQUEST,
            "Request body must be a JSON object.",
            error_type="invalid_request_error",
            code="invalid_request",
        )
    return payload


def _request_model(payload: dict[str, Any]) -> str | JSONResponse:
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        return _openai_error(
            status.HTTP_400_BAD_REQUEST,
            "model is required and must be a non-empty string.",
            error_type="invalid_request_error",
            code="invalid_model",
        )
    return model.strip()


def _record_usage_safely(
    *,
    session: Any,
    user: Any,
    credential: Any,
    model_name: str,
    request_type: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_ms: int | None = None,
    record_status: str = "success",
    error_message: str | None = None,
) -> None:
    try:
        ai_gateway_service.record_usage(
            session=session,
            user_id=user.id,
            credential_id=credential.id,
            model_name=model_name,
            request_type=request_type,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_duration_ms=duration_ms,
            status=record_status,
            error_message=error_message,
        )
    except Exception:
        # Accounting must not turn a completed model response into an error.
        logger.exception("Failed to record AI API usage")


def _stream_payload(payload: dict[str, Any], endpoint: str) -> dict[str, Any]:
    """Ask chat/completions and completions for their final usage SSE chunk."""
    if endpoint not in {"chat/completions", "completions"}:
        return payload
    stream_options = payload.get("stream_options")
    updated = dict(payload)
    if isinstance(stream_options, dict):
        updated_options = dict(stream_options)
    else:
        updated_options = {}
    updated_options.setdefault("include_usage", True)
    updated["stream_options"] = updated_options
    return updated


async def _stream_upstream_response(
    *,
    client: httpx.AsyncClient,
    upstream: httpx.Response,
    user: Any,
    credential: Any,
    model_name: str,
    request_type: str,
    started_at: float,
) -> AsyncGenerator[bytes, None]:
    """Pass through SSE bytes while recording final usage after the stream ends."""
    usage = {"input_tokens": 0, "output_tokens": 0}
    record_status = "success"
    error_message: str | None = None
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    line_buffer = ""
    try:
        async for chunk in upstream.aiter_raw():
            decoded = decoder.decode(chunk)
            line_buffer += decoded
            while "\n" in line_buffer:
                line, line_buffer = line_buffer.split("\n", 1)
                _update_stream_usage(line.rstrip("\r"), usage)
            yield chunk
        line_buffer += decoder.decode(b"", final=True)
        if line_buffer:
            _update_stream_usage(line_buffer.rstrip("\r"), usage)
    except asyncio.CancelledError:
        record_status = "cancelled"
        error_message = "client_disconnected"
        raise
    except Exception:
        record_status = "error"
        error_message = "upstream_stream_error"
        logger.exception("AI API upstream stream failed for model=%s", model_name)
        raise
    finally:
        await upstream.aclose()
        await client.aclose()
        from sqlmodel import Session  # noqa: PLC0415

        from app.core.db import engine  # noqa: PLC0415

        try:
            with Session(engine) as record_session:
                _record_usage_safely(
                    session=record_session,
                    user=user,
                    credential=credential,
                    model_name=model_name,
                    request_type=request_type,
                    input_tokens=usage["input_tokens"],
                    output_tokens=usage["output_tokens"],
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                    record_status=record_status,
                    error_message=error_message,
                )
        except Exception:
            logger.exception("Failed to create AI API stream usage session")


async def _relay_generation(
    *,
    endpoint: str,
    request: Request,
    user_and_credential: tuple[Any, Any],
    session: Any,
) -> Response:
    user, credential = user_and_credential
    await _enforce_rate_limit(user=user, credential=credential)

    payload = await _json_payload(request)
    if isinstance(payload, JSONResponse):
        return payload
    model_name = _request_model(payload)
    if isinstance(model_name, JSONResponse):
        return model_name

    request_type = _GENERATION_ENDPOINTS[endpoint]
    upstream_url = _upstream_url(endpoint, request.url.query)
    headers = _service_headers(request)
    started_at = time.monotonic()
    is_stream = payload.get("stream") is True
    if is_stream:
        payload = _stream_payload(payload, endpoint)

    client = httpx.AsyncClient(timeout=ai_api_settings.ai_api_timeout)
    try:
        outbound = client.build_request(
            "POST", upstream_url, json=payload, headers=headers
        )
        upstream = await client.send(outbound, stream=is_stream)
    except httpx.RequestError:
        await client.aclose()
        _record_usage_safely(
            session=session,
            user=user,
            credential=credential,
            model_name=model_name,
            request_type=request_type,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            record_status="error",
            error_message="upstream_unavailable",
        )
        logger.warning("AI API upstream unavailable for model=%s", model_name)
        return _openai_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Model service is temporarily unavailable. Please try again later.",
            error_type="api_connection_error",
            code="upstream_unavailable",
        )

    response_headers = _response_headers(upstream.headers)
    if is_stream and upstream.is_success:
        return StreamingResponse(
            _stream_upstream_response(
                client=client,
                upstream=upstream,
                user=user,
                credential=credential,
                model_name=model_name,
                request_type=request_type,
                started_at=started_at,
            ),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
            headers=response_headers,
        )

    try:
        content = await upstream.aread()
        result: Any = json.loads(content) if upstream.is_success else None
    except json.JSONDecodeError:
        result = None
    finally:
        await upstream.aclose()
        await client.aclose()

    input_tokens, output_tokens = _usage_tokens(result)
    _record_usage_safely(
        session=session,
        user=user,
        credential=credential,
        model_name=model_name,
        request_type=request_type,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=int((time.monotonic() - started_at) * 1000),
        record_status="success" if 200 <= upstream.status_code < 300 else "error",
        error_message=None if upstream.is_success else f"upstream_http_{upstream.status_code}",
    )
    if not upstream.is_success:
        return _upstream_failure(
            request=request,
            upstream=upstream,
            body=content,
            context=f"relay:{endpoint}",
        )
    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


@router.post(
    "/chat/completions",
    summary="Chat completions",
    description="Relay an OpenAI-compatible chat completion to LiteLLM.",
)
async def chat_completions(
    request: Request,
    user_and_credential: AIAPIUserDep,
    session: SessionDep,
) -> Response:
    return await _relay_generation(
        endpoint="chat/completions",
        request=request,
        user_and_credential=user_and_credential,
        session=session,
    )


@router.post(
    "/completions",
    summary="Completions",
    description="Relay an OpenAI-compatible completion to LiteLLM.",
)
async def completions(
    request: Request,
    user_and_credential: AIAPIUserDep,
    session: SessionDep,
) -> Response:
    return await _relay_generation(
        endpoint="completions",
        request=request,
        user_and_credential=user_and_credential,
        session=session,
    )


@router.post(
    "/responses",
    summary="Responses",
    description="Relay an OpenAI-compatible Responses API request to LiteLLM.",
)
async def responses(
    request: Request,
    user_and_credential: AIAPIUserDep,
    session: SessionDep,
) -> Response:
    return await _relay_generation(
        endpoint="responses",
        request=request,
        user_and_credential=user_and_credential,
        session=session,
    )


@router.get(
    "/models",
    summary="List available models",
    description="List the models available through the restricted LiteLLM identity.",
)
async def list_models(request: Request, user_and_credential: AIAPIUserDep) -> Response:
    user, _credential = user_and_credential
    upstream_url = _upstream_url("models", request.url.query)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            upstream = await client.get(upstream_url, headers=_service_headers(request))
    except httpx.RequestError:
        logger.warning("AI API model list upstream unavailable")
        return _openai_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Model service is temporarily unavailable. Please try again later.",
            error_type="api_connection_error",
            code="upstream_unavailable",
        )

    response_headers = _response_headers(upstream.headers)
    if not upstream.is_success:
        return _upstream_failure(
            request=request,
            upstream=upstream,
            body=upstream.content,
            context="models",
        )

    try:
        result = upstream.json()
    except ValueError:
        return _openai_error(
            status.HTTP_502_BAD_GATEWAY,
            "Model service returned an invalid response.",
            error_type="api_error",
            code="invalid_upstream_response",
        )

    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        return _openai_error(
            status.HTTP_502_BAD_GATEWAY,
            "Model service returned an invalid response.",
            error_type="api_error",
            code="invalid_upstream_response",
        )

    now_ts = int(time.time())
    data = []
    for model in result["data"]:
        if not isinstance(model, dict):
            continue
        if model.get("created") is None:
            model = {**model, "created": now_ts}
        data.append(model)
    result["data"] = data
    logger.info("AI API model list requested by user=%s", user.id)
    return JSONResponse(content=result, headers=response_headers)


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
):
    user, _credential = user_and_credential
    if not end_date:
        end_date = datetime.now(timezone.utc)
    if not start_date:
        start_date = end_date - timedelta(days=30)

    stats = ai_gateway_service.get_user_usage_stats(
        session=session, user_id=user.id, start_date=start_date, end_date=end_date
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
    session: SessionDep,
):
    user, credential = user_and_credential
    limit = (
        credential.rate_limit
        if credential.rate_limit is not None
        else ai_api_settings.ai_api_rate_limit_per_minute
    )
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
        key=ai_proxy_rate_limit_key(str(user.id)),
        window_seconds=window_seconds,
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
