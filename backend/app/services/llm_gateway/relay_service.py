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
from fastapi.concurrency import run_in_threadpool
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

# 單一 backend process 的固定 admission contract。這些值同時限制送往
# LiteLLM 的 active requests 與 shared HTTP connection pool；不是部署設定，
# 避免不同環境各自漂移成無法比較的併發語意。
AI_PROXY_MAX_ACTIVE = 20
AI_PROXY_MAX_WAITING = 40
AI_PROXY_QUEUE_TIMEOUT_SECONDS = 30.0
AI_PROXY_RETRY_AFTER_SECONDS = 5


class AdmissionRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AdmissionLease:
    def __init__(self, queue: AdmissionQueue, token: object) -> None:
        self._queue = queue
        self._token = token
        self._released = False

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._queue.release(self._token)


class AdmissionQueue:
    """Bound active upstream calls while allowing a finite FIFO wait queue."""

    def __init__(
        self,
        *,
        max_active: int,
        max_waiting: int,
        wait_timeout_seconds: float,
    ) -> None:
        self.max_active = max_active
        self.max_waiting = max_waiting
        self.wait_timeout_seconds = wait_timeout_seconds
        self._tokens: asyncio.Queue[object] = asyncio.Queue(maxsize=max_active)
        for _ in range(max_active):
            self._tokens.put_nowait(object())
        self._active = 0
        self._waiting = 0
        self._update_metrics()

    @property
    def active(self) -> int:
        return self._active

    @property
    def waiting(self) -> int:
        return self._waiting

    async def acquire(self) -> AdmissionLease:
        started_at = time.monotonic()
        token: object | None = None
        # 已有 waiter 時不可讓新 request 直接拿走剛歸還的 token，否則高流量下
        # 舊 waiter 可能持續被插隊。
        if self._waiting == 0:
            try:
                token = self._tokens.get_nowait()
            except asyncio.QueueEmpty:
                pass
        if token is None:
            if self._waiting >= self.max_waiting:
                ai_metrics.record_proxy_admission_rejection("queue_full")
                raise AdmissionRejected("queue_full")
            self._waiting += 1
            self._update_metrics()
            try:
                token = await asyncio.wait_for(
                    self._tokens.get(), timeout=self.wait_timeout_seconds
                )
            except asyncio.TimeoutError as exc:
                ai_metrics.record_proxy_admission_rejection("timeout")
                raise AdmissionRejected("timeout") from exc
            finally:
                self._waiting -= 1
                self._update_metrics()

        self._active += 1
        self._update_metrics()
        ai_metrics.observe_proxy_queue_wait(time.monotonic() - started_at)
        return AdmissionLease(self, token)

    def release(self, token: object) -> None:
        if self._active <= 0:
            logger.error("AI proxy admission lease released without an active request")
            return
        self._active -= 1
        self._tokens.put_nowait(token)
        self._update_metrics()

    def _update_metrics(self) -> None:
        ai_metrics.update_proxy_admission(active=self._active, waiting=self._waiting)


_relay_http_client: httpx.AsyncClient | None = None
_relay_http_client_loop: asyncio.AbstractEventLoop | None = None
_admission_queue: AdmissionQueue | None = None
_admission_queue_loop: asyncio.AbstractEventLoop | None = None


def _get_relay_http_client() -> httpx.AsyncClient:
    """Return the shared client for the current application event loop."""
    global _relay_http_client, _relay_http_client_loop
    loop = asyncio.get_running_loop()
    if (
        _relay_http_client is None
        or getattr(_relay_http_client, "is_closed", False)
        or _relay_http_client_loop is not loop
    ):
        _relay_http_client = httpx.AsyncClient(
            timeout=ai_api_settings.ai_api_timeout,
            limits=httpx.Limits(
                max_connections=AI_PROXY_MAX_ACTIVE,
                max_keepalive_connections=AI_PROXY_MAX_ACTIVE,
            ),
        )
        _relay_http_client_loop = loop
    return _relay_http_client


def _get_admission_queue() -> AdmissionQueue:
    global _admission_queue, _admission_queue_loop
    loop = asyncio.get_running_loop()
    if _admission_queue is None or _admission_queue_loop is not loop:
        _admission_queue = AdmissionQueue(
            max_active=AI_PROXY_MAX_ACTIVE,
            max_waiting=AI_PROXY_MAX_WAITING,
            wait_timeout_seconds=AI_PROXY_QUEUE_TIMEOUT_SECONDS,
        )
        _admission_queue_loop = loop
    return _admission_queue


async def close_relay_runtime() -> None:
    """Close the shared relay client and clear process-local admission state."""
    global _relay_http_client, _relay_http_client_loop
    global _admission_queue, _admission_queue_loop
    client = _relay_http_client
    _relay_http_client = None
    _relay_http_client_loop = None
    _admission_queue = None
    _admission_queue_loop = None
    ai_metrics.update_proxy_admission(active=0, waiting=0)
    if client is not None and not getattr(client, "is_closed", False):
        await client.aclose()


def openai_error(
    status_code: int,
    message: str,
    *,
    error_type: str,
    code: str | None = None,
    headers: dict[str, str] | None = None,
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
        headers=headers,
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
    user_id: Any,
    credential_id: Any,
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
        with Session(engine) as usage_session:
            ai_gateway_service.record_usage(
                session=usage_session,
                user_id=user_id,
                credential_id=credential_id,
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


async def record_usage_in_threadpool(**kwargs: Any) -> None:
    """Write one usage row without blocking the FastAPI event loop."""
    await run_in_threadpool(record_usage_safely, **kwargs)


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
    upstream: httpx.Response,
    admission_lease: AdmissionLease,
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
        try:
            await upstream.aclose()
        except Exception:
            logger.exception("Failed to close AI API upstream stream")
        admission_lease.release()
        try:
            await record_usage_in_threadpool(
                user_id=user.id,
                credential_id=credential.id,
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
            logger.exception("Failed to schedule AI API stream usage recording")


async def relay_generation(
    *,
    endpoint: str,
    request: Request,
    payload: dict[str, Any],
    model_name: str,
    user: Any,
    credential: Any,
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

    try:
        admission_lease = await _get_admission_queue().acquire()
    except AdmissionRejected as exc:
        logger.warning(
            "AI API admission rejected: reason=%s model=%s request_id=%s",
            exc.reason,
            model_name,
            request_id,
        )
        return openai_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AI service is busy. Please retry shortly.",
            error_type="api_error",
            code="server_busy",
            headers={"Retry-After": str(AI_PROXY_RETRY_AFTER_SECONDS)},
        )

    try:
        client = _get_relay_http_client()
        outbound = client.build_request("POST", target_url, json=payload, headers=headers)
        upstream = await client.send(outbound, stream=is_stream)
    except httpx.RequestError:
        admission_lease.release()
        await record_usage_in_threadpool(
            user_id=user.id,
            credential_id=credential.id,
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
    except BaseException:
        admission_lease.release()
        raise

    try:
        public_headers = response_headers(upstream.headers)
        public_headers.setdefault("x-request-id", request_id)
        upstream_request_id = upstream.headers.get("x-request-id")
        if is_stream and upstream.is_success:
            return StreamingResponse(
                stream_upstream_response(
                    upstream=upstream,
                    admission_lease=admission_lease,
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
    except BaseException:
        try:
            await upstream.aclose()
        finally:
            admission_lease.release()
        raise

    try:
        content = await upstream.aread()
        result: Any = json.loads(content) if upstream.is_success else None
    except json.JSONDecodeError:
        result = None
    finally:
        try:
            await upstream.aclose()
        finally:
            admission_lease.release()

    input_tokens, output_tokens, usage_reported, response_model = usage_details(result)
    await record_usage_in_threadpool(
        user_id=user.id,
        credential_id=credential.id,
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
        upstream = await _get_relay_http_client().get(
            target_url,
            headers=service_headers(request),
            timeout=10,
        )
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
