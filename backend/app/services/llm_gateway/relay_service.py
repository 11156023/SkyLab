"""AI API relay 的資料面：把已驗證的請求轉給 LiteLLM、串流回傳並記錄用量。

路由（``app/api/routes/ai_proxy.py``）只負責金鑰驗證、限流與請求 body 檢查，
之後的上游 URL／標頭組裝、httpx 呼叫、SSE 用量解析與用量入帳都在這裡。
上游只開放一小組 data-plane 端點，不是通往 LiteLLM 管理或健康檢查 API 的
通用代理。
"""

from __future__ import annotations

import asyncio
import codecs
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlmodel import Session

from app.core.db import engine
from app.features.ai.config import settings as ai_api_settings
from app.services.llm_gateway import ai_gateway_service
from app.services.monitoring import ai_metrics

logger = logging.getLogger(__name__)

GENERATION_ENDPOINTS = {
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


def openai_error(
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


def upstream_failure(
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
    return openai_error(
        upstream.status_code,
        "The model service rejected this request.",
        error_type="api_error",
        code="upstream_error",
    )


def service_headers(request: Request, *, request_id: str | None = None) -> dict[str, str]:
    """Build the only headers allowed to cross the Campus → LiteLLM boundary."""
    headers = {
        "Authorization": f"Bearer {ai_api_settings.ai_api_api_key}",
        "Content-Type": "application/json",
    }
    for name in _REQUEST_HEADER_ALLOWLIST:
        value = request.headers.get(name)
        if value:
            headers[name] = value
    if request_id:
        headers["x-request-id"] = request_id
    return headers


def response_headers(upstream_headers: httpx.Headers) -> dict[str, str]:
    """Pass only response headers useful to OpenAI API clients.

    Host, Content-Length, connection-specific and implementation headers are
    intentionally never copied into the public response.
    """
    return {
        name: upstream_headers[name]
        for name in _RESPONSE_HEADER_ALLOWLIST
        if name in upstream_headers
    }


def upstream_url(endpoint: str, query: str) -> str:
    base_url = ai_api_settings.resolved_upstream_base_url.rstrip("/")
    url = f"{base_url}/v1/{endpoint}"
    return f"{url}?{query}" if query else url


def request_id_for(request: Request) -> str:
    supplied = (request.headers.get("x-request-id") or "").strip()
    return supplied[:255] if supplied else str(uuid.uuid4())


def usage_details(payload: Any) -> tuple[int, int, bool, str | None]:
    """Extract token counts from chat/completions/responses response shapes."""
    if not isinstance(payload, dict):
        return 0, 0, False, None
    response = payload.get("response")
    if isinstance(response, dict):
        payload = response
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        model = payload.get("model")
        return 0, 0, False, str(model)[:255] if model else None
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
    model = payload.get("model")
    try:
        return (
            int(input_tokens or 0),
            int(output_tokens or 0),
            True,
            str(model)[:255] if model else None,
        )
    except (TypeError, ValueError):
        return 0, 0, True, str(model)[:255] if model else None


def update_stream_usage(
    line: str, usage: dict[str, Any], *, started_at: float | None = None
) -> None:
    """Update usage from one SSE data line, preserving the bytes sent to clients."""
    if not line.startswith("data:"):
        return
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return
    try:
        payload = json.loads(data)
        input_tokens, output_tokens, reported, response_model = usage_details(payload)
    except json.JSONDecodeError:
        return
    if reported:
        usage["input_tokens"] = input_tokens
        usage["output_tokens"] = output_tokens
        usage["usage_reported"] = True
    if response_model:
        usage["response_model"] = response_model
    if usage.get("first_token_ms") is None and stream_event_has_output(payload):
        if started_at is not None:
            usage["first_token_ms"] = int((time.monotonic() - started_at) * 1000)


def stream_event_has_output(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    event_type = str(payload.get("type") or "")
    if event_type.endswith(".delta") and payload.get("delta") not in (None, "", []):
        return True
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return False
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if isinstance(delta, dict) and any(
            delta.get(key) not in (None, "", [])
            for key in ("content", "tool_calls", "function_call")
        ):
            return True
        if choice.get("text") not in (None, ""):
            return True
    return False


def record_usage_safely(
    *,
    session: Any,
    user: Any,
    credential: Any,
    model_name: str,
    request_type: str,
    request_id: str | None = None,
    upstream_request_id: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_ms: int | None = None,
    first_token_ms: int | None = None,
    stream: bool = False,
    usage_reported: bool = False,
    response_model: str | None = None,
    record_status: str = "success",
    error_message: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> None:
    ai_metrics.observe_call(
        source="api_key",
        model=model_name,
        request_type=request_type,
        record_status=record_status,
        error_message=error_message,
        duration_ms=duration_ms,
        first_token_ms=first_token_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stream=stream,
    )
    try:
        ai_gateway_service.record_usage(
            session=session,
            user_id=user.id,
            credential_id=credential.id,
            model_name=model_name,
            request_type=request_type,
            request_id=request_id,
            upstream_request_id=upstream_request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_duration_ms=duration_ms,
            first_token_ms=first_token_ms,
            stream=stream,
            usage_reported=usage_reported,
            response_model=response_model,
            status=record_status,
            error_message=error_message,
            started_at=started_at,
            completed_at=completed_at,
        )
    except Exception:
        # Accounting must not turn a completed model response into an error.
        logger.exception("Failed to record AI API usage")


def stream_payload(payload: dict[str, Any], endpoint: str) -> dict[str, Any]:
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


async def stream_upstream_response(
    *,
    client: httpx.AsyncClient,
    upstream: httpx.Response,
    user: Any,
    credential: Any,
    model_name: str,
    request_type: str,
    request_id: str,
    upstream_request_id: str | None,
    started_at: float,
    started_at_utc: datetime,
) -> AsyncGenerator[bytes, None]:
    """Pass through SSE bytes while recording final usage after the stream ends."""
    usage: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "usage_reported": False,
        "response_model": None,
        "first_token_ms": None,
    }
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
                update_stream_usage(line.rstrip("\r"), usage, started_at=started_at)
            yield chunk
        line_buffer += decoder.decode(b"", final=True)
        if line_buffer:
            update_stream_usage(line_buffer.rstrip("\r"), usage, started_at=started_at)
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
        # 串流結束時請求的 session 早已隨 response 關閉，入帳要自己開一條
        try:
            with Session(engine) as record_session:
                record_usage_safely(
                    session=record_session,
                    user=user,
                    credential=credential,
                    model_name=model_name,
                    request_type=request_type,
                    request_id=request_id,
                    upstream_request_id=upstream_request_id,
                    input_tokens=usage["input_tokens"],
                    output_tokens=usage["output_tokens"],
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                    first_token_ms=usage["first_token_ms"],
                    stream=True,
                    usage_reported=usage["usage_reported"],
                    response_model=usage["response_model"],
                    record_status=record_status,
                    error_message=error_message,
                    started_at=started_at_utc,
                    completed_at=datetime.now(timezone.utc),
                )
        except Exception:
            logger.exception("Failed to create AI API stream usage session")


async def relay_generation(
    *,
    endpoint: str,
    request: Request,
    payload: dict[str, Any],
    model_name: str,
    user: Any,
    credential: Any,
    session: Any,
) -> Response:
    """把已通過驗證、限流與 body 檢查的生成請求轉給上游並入帳。"""
    request_type = GENERATION_ENDPOINTS[endpoint]
    target_url = upstream_url(endpoint, request.url.query)
    request_id = request_id_for(request)
    headers = service_headers(request, request_id=request_id)
    started_at = time.monotonic()
    started_at_utc = datetime.now(timezone.utc)
    is_stream = payload.get("stream") is True
    if is_stream:
        payload = stream_payload(payload, endpoint)

    client = httpx.AsyncClient(timeout=ai_api_settings.ai_api_timeout)
    try:
        outbound = client.build_request("POST", target_url, json=payload, headers=headers)
        upstream = await client.send(outbound, stream=is_stream)
    except httpx.RequestError:
        await client.aclose()
        record_usage_safely(
            session=session,
            user=user,
            credential=credential,
            model_name=model_name,
            request_type=request_type,
            request_id=request_id,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            stream=is_stream,
            record_status="error",
            error_message="upstream_unavailable",
            started_at=started_at_utc,
            completed_at=datetime.now(timezone.utc),
        )
        logger.warning("AI API upstream unavailable for model=%s", model_name)
        return openai_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Model service is temporarily unavailable. Please try again later.",
            error_type="api_connection_error",
            code="upstream_unavailable",
        )

    public_headers = response_headers(upstream.headers)
    public_headers.setdefault("x-request-id", request_id)
    upstream_request_id = upstream.headers.get("x-request-id")
    if is_stream and upstream.is_success:
        return StreamingResponse(
            stream_upstream_response(
                client=client,
                upstream=upstream,
                user=user,
                credential=credential,
                model_name=model_name,
                request_type=request_type,
                request_id=request_id,
                upstream_request_id=upstream_request_id,
                started_at=started_at,
                started_at_utc=started_at_utc,
            ),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
            headers=public_headers,
        )

    try:
        content = await upstream.aread()
        result: Any = json.loads(content) if upstream.is_success else None
    except json.JSONDecodeError:
        result = None
    finally:
        await upstream.aclose()
        await client.aclose()

    input_tokens, output_tokens, usage_reported, response_model = usage_details(result)
    record_usage_safely(
        session=session,
        user=user,
        credential=credential,
        model_name=model_name,
        request_type=request_type,
        request_id=request_id,
        upstream_request_id=upstream_request_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=int((time.monotonic() - started_at) * 1000),
        stream=False,
        usage_reported=usage_reported,
        response_model=response_model,
        record_status="success" if 200 <= upstream.status_code < 300 else "error",
        error_message=None
        if upstream.is_success
        else f"upstream_http_{upstream.status_code}",
        started_at=started_at_utc,
        completed_at=datetime.now(timezone.utc),
    )
    if not upstream.is_success:
        return upstream_failure(
            request=request,
            upstream=upstream,
            body=content,
            context=f"relay:{endpoint}",
        )
    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=public_headers,
        media_type=upstream.headers.get("content-type"),
    )


async def list_models(request: Request, *, user: Any) -> Response:
    """列出受限 LiteLLM 身分可用的模型（補上缺漏的 created 時間戳）。"""
    target_url = upstream_url("models", request.url.query)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            upstream = await client.get(target_url, headers=service_headers(request))
    except httpx.RequestError:
        logger.warning("AI API model list upstream unavailable")
        return openai_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Model service is temporarily unavailable. Please try again later.",
            error_type="api_connection_error",
            code="upstream_unavailable",
        )

    public_headers = response_headers(upstream.headers)
    if not upstream.is_success:
        return upstream_failure(
            request=request,
            upstream=upstream,
            body=upstream.content,
            context="models",
        )

    try:
        result = upstream.json()
    except ValueError:
        return openai_error(
            status.HTTP_502_BAD_GATEWAY,
            "Model service returned an invalid response.",
            error_type="api_error",
            code="invalid_upstream_response",
        )

    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        return openai_error(
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
    return JSONResponse(content=result, headers=public_headers)
