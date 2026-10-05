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
import math
import random
import re
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

import anyio
import httpx
from fastapi import Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlmodel import Session
from starlette.requests import ClientDisconnect

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
# 串流回應要逐段送到使用者手上。X-Accel-Buffering 讓沿路的 nginx（主系統、
# 部署端自己多接的一層）只對這個回應關掉 proxy_buffering，不受各層緩衝設定
# 影響；其他 API 照常緩衝。no-cache 避免中間代理快取串流。
_STREAM_RESPONSE_HEADERS = {
    "cache-control": "no-cache",
    "x-accel-buffering": "no",
}

# 單一 backend process 的固定 admission contract。這些值同時限制送往
# LiteLLM 的 active requests 與 shared HTTP connection pool；不是部署設定，
# 避免不同環境各自漂移成無法比較的併發語意。
AI_PROXY_MAX_ACTIVE = 20
AI_PROXY_MODEL_MAX_ACTIVE = 10
AI_PROXY_MAX_WAITING = 40
AI_PROXY_QUEUE_TIMEOUT_SECONDS = 70.0
# 留出既有 120 秒 client I/O timeout 的錯誤回傳餘裕；不修改外層設定。
AI_PROXY_GENERATION_TIMEOUT_SECONDS = 110.0
AI_PROXY_REQUEST_TIMEOUT_SECONDS = 115.0
AI_PROXY_MAX_ATTEMPTS = 8
AI_PROXY_RETRY_AFTER_SECONDS = 5


class AdmissionRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(eq=False)
class AdmissionTicket:
    model: str | None
    sequence: int
    deadline: float
    wait_remaining: float


@dataclass
class ModelAdmission:
    active: int = 0
    cooldown_until: float = 0.0
    epoch: int = 0
    failures: int = 0
    recovering: bool = False
    probe: AdmissionLease | None = None


class AdmissionLease:
    def __init__(
        self, queue: AdmissionQueue, ticket: AdmissionTicket, epoch: int, *, probe: bool
    ) -> None:
        self._queue = queue
        self.ticket = ticket
        self.epoch = epoch
        self.probe = probe
        self._released = False
        self.owner = asyncio.current_task()

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._queue.release(self)


class AdmissionQueue:
    """Process-local bounded scheduling: model FIFO, completion-driven dispatch."""

    def __init__(
        self,
        *,
        max_active: int,
        max_waiting: int,
        wait_timeout_seconds: float,
        model_max_active: int = AI_PROXY_MODEL_MAX_ACTIVE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_active = max_active
        self.max_waiting = max_waiting
        self.wait_timeout_seconds = wait_timeout_seconds
        self.model_max_active = model_max_active
        self.clock = clock
        self._models: dict[str | None, ModelAdmission] = {}
        self._waiters: list[tuple[AdmissionTicket, asyncio.Future[AdmissionLease]]] = []
        self._leases: set[AdmissionLease] = set()
        # active request 也預留重排位置；避免 20 active + 40 waiter 同時 429
        # 時，已准入 request 被迫因重排 queue full 失敗或突破 waiting 上限。
        self._retained: set[AdmissionTicket] = set()
        self.requests: set[asyncio.Task[Any]] = set()
        self._sequence = 0
        self._timer: asyncio.TimerHandle | None = None
        self.closed = False
        self._update_metrics()

    @property
    def active(self) -> int:
        return len(self._leases)

    @property
    def waiting(self) -> int:
        return len(self._waiters)

    @property
    def full(self) -> bool:
        return len(self._retained) >= max(self.max_active, self.max_waiting)

    def discard(self, ticket: AdmissionTicket) -> None:
        if ticket in self._retained:
            self._retained.discard(ticket)
            self._update_metrics()

    def reserve(self, ticket: AdmissionTicket) -> None:
        if self.closed:
            raise AdmissionRejected("shutdown")
        if ticket not in self._retained and self.full:
            ai_metrics.record_proxy_admission_rejection("queue_full")
            raise AdmissionRejected("queue_full")
        if ticket not in self._retained:
            self._retained.add(ticket)
            self._update_metrics()

    def ticket(
        self, model: str | None = None, *, deadline: float | None = None
    ) -> AdmissionTicket:
        self._sequence += 1
        return AdmissionTicket(
            model,
            self._sequence,
            deadline if deadline is not None else float("inf"),
            self.wait_timeout_seconds,
        )

    async def acquire(self, ticket: AdmissionTicket | None = None) -> AdmissionLease:
        ticket = ticket or self.ticket()
        if self.closed:
            raise AdmissionRejected("shutdown")
        started = self.clock()
        if started >= ticket.deadline:
            raise AdmissionRejected("request_timeout")
        if ticket.wait_remaining <= 0:
            ai_metrics.record_proxy_admission_rejection("timeout")
            raise AdmissionRejected("timeout")
        state = self._models.setdefault(ticket.model, ModelAdmission())
        if ticket not in self._retained and self.full:
            ai_metrics.record_proxy_admission_rejection("queue_full")
            self._prune()
            raise AdmissionRejected("queue_full")
        earlier_waiting = any(
            not f.done()
            and t.sequence < ticket.sequence
            and self._eligible(self._models[t.model])
            for t, f in self._waiters
        )
        same_model_waiting = any(
            not f.done() and t.model == ticket.model and t.sequence < ticket.sequence
            for t, f in self._waiters
        )
        if not earlier_waiting and not same_model_waiting and self._eligible(state):
            ai_metrics.observe_proxy_queue_wait(0.0)
            return self._grant(ticket, state)
        if self.waiting >= self.max_waiting:
            self.dispatch()
        if self.waiting >= self.max_waiting:
            ai_metrics.record_proxy_admission_rejection("queue_full")
            self._prune()
            raise AdmissionRejected("queue_full")
        waiter: asyncio.Future[AdmissionLease] = (
            asyncio.get_running_loop().create_future()
        )
        self._waiters.append((ticket, waiter))
        self._retained.add(ticket)
        self._waiters.sort(key=lambda item: item[0].sequence)
        self._update_metrics()
        timeout = min(ticket.wait_remaining, ticket.deadline - started)
        reason = (
            "request_timeout"
            if ticket.deadline - started <= ticket.wait_remaining
            else "timeout"
        )
        handle = asyncio.get_running_loop().call_later(
            timeout, self._expire, waiter, reason
        )
        self.dispatch()
        try:
            lease = await waiter
            lease.owner = asyncio.current_task()
            return lease
        except BaseException as exc:
            if waiter.done() and not waiter.cancelled() and waiter.exception() is None:
                waiter.result().release()
            self._retained.discard(ticket)
            if isinstance(exc, asyncio.CancelledError):
                ai_metrics.record_proxy_admission_rejection("cancelled")
            raise
        finally:
            handle.cancel()
            with suppress(ValueError):
                self._waiters.remove((ticket, waiter))
            elapsed = max(0.0, self.clock() - started)
            ticket.wait_remaining -= elapsed
            ai_metrics.observe_proxy_queue_wait(elapsed)
            self.dispatch()

    def _eligible(self, state: ModelAdmission) -> bool:
        return (
            self.active < self.max_active
            and state.active < self.model_max_active
            and self.clock() >= state.cooldown_until
            and (not state.recovering or state.probe is None)
        )

    def _grant(self, ticket: AdmissionTicket, state: ModelAdmission) -> AdmissionLease:
        lease = AdmissionLease(self, ticket, state.epoch, probe=state.recovering)
        self._leases.add(lease)
        self._retained.add(ticket)
        state.active += 1
        if lease.probe:
            state.probe = lease
        self._update_metrics()
        return lease

    def release(self, lease: AdmissionLease) -> None:
        if lease not in self._leases:
            return
        self._leases.remove(lease)
        self._retained.discard(lease.ticket)
        state = self._models[lease.ticket.model]
        state.active -= 1
        if state.probe is lease:
            state.probe = None
            ai_metrics.record_proxy_event(
                lease.ticket.model or "other", "probe_abandoned"
            )
        self.dispatch()

    def accepted(self, lease: AdmissionLease) -> None:
        state = self._models[lease.ticket.model]
        # 舊 success 不能清除新的 cooldown，也不能替當前 probe 解鎖。
        if state.probe is lease and lease.epoch == state.epoch:
            state.probe = None
            state.recovering = False
            state.failures = 0
            ai_metrics.record_proxy_event(
                lease.ticket.model or "other", "probe_success"
            )
            self.dispatch()

    def rate_limited(self, lease: AdmissionLease, retry_after: str | None) -> float:
        state = self._models[lease.ticket.model]
        state.failures += 1
        delay = retry_after_seconds(retry_after)
        if delay is None:
            delay = min(30.0, 5.0 * 2 ** min(state.failures - 1, 3))
            delay = min(30.0, delay * random.uniform(1.0, 1.2))
        state.epoch += 1
        state.cooldown_until = max(state.cooldown_until, self.clock() + delay)
        state.recovering = True
        if state.probe is lease:
            state.probe = None
            ai_metrics.record_proxy_event(
                lease.ticket.model or "other", "probe_failure"
            )
        ai_metrics.observe_proxy_cooldown(lease.ticket.model or "other", delay)
        # 先更新 cooldown 再釋放 slot，避免放出同模型 waiter。
        # 不在這裡 dispatch；呼叫端緊接著以原 sequence acquire，才一起遞補。
        lease._released = True
        self._leases.remove(lease)
        state.active -= 1
        self._update_metrics()
        asyncio.get_running_loop().call_soon(self.dispatch)
        return delay

    def dispatch(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        blocked: set[str | None] = set()
        for ticket, waiter in list(self._waiters):
            if waiter.done():
                self._waiters.remove((ticket, waiter))
                continue
            if self.clock() >= ticket.deadline:
                self._expire(waiter, "request_timeout")
                continue
            state = self._models[ticket.model]
            if ticket.model in blocked or not self._eligible(state):
                blocked.add(ticket.model)
                continue
            self._waiters.remove((ticket, waiter))
            waiter.set_result(self._grant(ticket, state))
        if not self.closed:
            wakeups = [
                self._models[t.model].cooldown_until
                for t, f in self._waiters
                if not f.done() and self._models[t.model].cooldown_until > self.clock()
            ]
            if wakeups:
                self._timer = asyncio.get_running_loop().call_later(
                    max(0.0, min(wakeups) - self.clock()), self.dispatch
                )
        self._update_metrics()
        self._prune()

    def _expire(
        self, waiter: asyncio.Future[AdmissionLease], reason: str = "timeout"
    ) -> None:
        if not waiter.done():
            ai_metrics.record_proxy_admission_rejection("timeout")
            waiter.set_exception(AdmissionRejected(reason))

    def _prune(self) -> None:
        waiting_models = {t.model for t, f in self._waiters if not f.done()}
        for model, state in list(self._models.items()):
            if (
                not state.active
                and model not in waiting_models
                and not state.recovering
            ):
                del self._models[model]

    def _update_metrics(self) -> None:
        active_tickets = {lease.ticket for lease in self._leases}
        waiting_tickets = [
            ticket
            for ticket in self._retained
            if ticket not in active_tickets
        ]
        ai_metrics.update_proxy_admission(
            active=self.active, waiting=len(waiting_tickets)
        )
        counts: dict[str, tuple[int, int]] = {}
        for model, state in self._models.items():
            label = ai_metrics.model_label(model or "other", served=False)
            active, waiting = counts.get(label, (0, 0))
            counts[label] = (
                active + state.active,
                waiting,
            )
        for ticket in waiting_tickets:
            label = ai_metrics.model_label(ticket.model or "other", served=False)
            active, waiting = counts.get(label, (0, 0))
            counts[label] = (active, waiting + 1)
        for label, (active, waiting) in counts.items():
            ai_metrics.update_proxy_model_admission(
                label, active=active, waiting=waiting
            )

    async def close(self) -> None:
        self.closed = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        for _, waiter in self._waiters:
            if not waiter.done():
                waiter.set_exception(AdmissionRejected("shutdown"))
        self._waiters.clear()
        owners = self.requests | {
            lease.owner for lease in self._leases if lease.owner is not None
        }
        owners.discard(asyncio.current_task())
        for task in owners:
            task.cancel()
        if owners:
            await asyncio.gather(*owners, return_exceptions=True)
        for lease in list(self._leases):
            lease.release()
        self._retained.clear()
        self._update_metrics()


def retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    except ValueError:
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                return None
            return max(0.0, deadline.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


def recoverable_model_limit(body: bytes) -> str | None:
    """辨識 LiteLLM deployment pre-call 拒絕，不猜 provider/key 額度。"""
    try:
        error = json.loads(body).get("error", {})
        message = error.get("message", "")
    except (ValueError, AttributeError, RecursionError):
        return None
    if not isinstance(message, str):
        return None
    if re.match(
        r"(?:litellm\.RateLimitError: )?Model rate limit exceeded\. RPM limit=\d+, current usage=\d+",
        message,
    ):
        return "model_rpm"
    if re.match(
        r"(?:litellm\.RateLimitError: )?Deployment over user-defined ratelimit\. rpm limit=\d+",
        message,
    ):
        return "model_rpm"
    if re.match(
        r"(?:litellm\.RateLimitError: )?Deployment has all max_parallel_requests slots in use\.",
        message,
    ):
        return "model_parallel"
    return None


_relay_http_client: httpx.AsyncClient | None = None
_relay_http_client_loop: asyncio.AbstractEventLoop | None = None
_admission_queue: AdmissionQueue | None = None
_admission_queue_loop: asyncio.AbstractEventLoop | None = None
_relay_stopping = False
_usage_tasks: set[asyncio.Task[None]] = set()


def start_relay_runtime() -> None:
    global _relay_stopping
    _relay_stopping = False


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
    if _relay_stopping:
        _admission_queue.closed = True
    return _admission_queue


async def close_relay_runtime() -> None:
    """Close the shared relay client and clear process-local admission state."""
    global _relay_http_client, _relay_http_client_loop
    global _admission_queue, _admission_queue_loop
    global _catalogue_task, _catalogue_models, _catalogue_until
    global _relay_stopping
    _relay_stopping = True
    if _admission_queue is not None:
        await _admission_queue.close()
    if _catalogue_task is not None:
        _catalogue_task.cancel()
        await asyncio.gather(_catalogue_task, return_exceptions=True)
    pending_usage = {
        task
        for task in _usage_tasks
        if not task.done() and task.get_loop() is asyncio.get_running_loop()
    }
    if pending_usage:
        _, pending_usage = await asyncio.wait(pending_usage, timeout=2.0)
        for task in pending_usage:
            task.cancel()
        await asyncio.gather(*pending_usage, return_exceptions=True)
    _catalogue_task = None
    _catalogue_models = set()
    _catalogue_until = 0.0
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
    body: bytes | None = None,
    context: str,
) -> JSONResponse:
    """上游的錯誤 body 一律不轉給呼叫端。

    LiteLLM 的錯誤訊息會夾帶內部模型別名、後端 URL、服務金鑰片段與 traceback；
    對外只保留 status code 與泛用訊息，log 只記安全的 status/context/request id。
    ``body`` 保留為舊呼叫端的相容參數，但刻意不讀取或記錄，避免敏感內容外流。
    """
    del body
    request_id = (
        upstream.headers.get("x-request-id")
        or request.headers.get("x-request-id")
        or "-"
    )
    logger.warning(
        "AI API upstream error: context=%s status=%s request_id=%s",
        context,
        upstream.status_code,
        request_id,
    )
    return openai_error(
        upstream.status_code,
        "The model service rejected this request.",
        error_type="api_error",
        code="upstream_error",
    )


def service_headers(
    request: Request, *, request_id: str | None = None
) -> dict[str, str]:
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


def usage_task_done(task: asyncio.Task[None]) -> None:
    _usage_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error("AI relay terminal usage task failed")


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


class RelayDeadline(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


T = TypeVar("T")


async def await_before(operation: Awaitable[T], deadline: float, reason: str) -> T:
    if time.monotonic() >= deadline:
        close = getattr(operation, "close", None)
        if close is not None:
            close()
        raise RelayDeadline(reason)
    try:
        with anyio.fail_after(max(0.0, deadline - time.monotonic())):
            return await operation
    except TimeoutError as exc:
        raise RelayDeadline(reason) from exc


async def watch_receive_disconnect(
    receive: Callable[[], Awaitable[Mapping[str, Any]]],
) -> None:
    # body 已由 route 讀完；此 watcher 會獨占剩餘的 request receive。
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return
        await asyncio.sleep(0)


async def watch_disconnect(request: Request) -> None:
    await watch_receive_disconnect(request.receive)


async def until_disconnect(request: Request, operation: Awaitable[T]) -> T:
    task = asyncio.ensure_future(operation)
    watcher = asyncio.create_task(watch_disconnect(request))
    try:
        done, _ = await asyncio.wait(
            (task, watcher), return_when=asyncio.FIRST_COMPLETED
        )
        if watcher in done:
            # 同時完成仍以斷線為準，避免把取消記成成功。
            watcher.result()
            if task.done() and not task.cancelled() and task.exception() is None:
                response = task.result()
                if isinstance(response, (RelayStreamingResponse, RelayResponse)):
                    response.observation.record_status = "cancelled"
                    response.observation.error_message = "client_disconnected"
                    response.observation.final_status = 499
                    await response.observation.finish()
            raise asyncio.CancelledError
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        watcher.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)


async def until_receive_disconnect(
    receive: Callable[[], Awaitable[Mapping[str, Any]]], operation: Awaitable[T]
) -> T:
    """Run an ASGI operation while observing a client disconnect message."""
    task = asyncio.ensure_future(operation)
    watcher = asyncio.create_task(watch_receive_disconnect(receive))
    try:
        done, _ = await asyncio.wait(
            (task, watcher), return_when=asyncio.FIRST_COMPLETED
        )
        if watcher in done:
            watcher.result()
            raise asyncio.CancelledError
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        watcher.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)


@dataclass
class RelayObservation:
    user: Any
    credential: Any
    model_name: str
    request_type: str
    request_id: str
    started_at: float
    started_at_utc: datetime
    stream: bool
    upstream: httpx.Response | None = None
    lease: AdmissionLease | None = None
    upstream_request_id: str | None = None
    record_status: str = "error"
    error_message: str | None = None
    final_status: int = 503
    finished: bool = False
    usage: dict[str, Any] = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "usage_reported": False,
            "response_model": None,
            "first_token_ms": None,
        }
    )

    async def close_attempt(self) -> None:
        upstream, self.upstream = self.upstream, None
        try:
            if upstream is not None:
                await upstream.aclose()
        finally:
            if self.lease is not None:
                self.lease.release()
                self.lease = None

    async def finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        # Starlette 取消 scope 不能跳過 connection/slot 清理與最後一次入帳。
        with anyio.CancelScope(shield=True):
            try:
                await self.close_attempt()
            except Exception:
                logger.warning("Failed to close AI relay attempt", exc_info=True)
            ai_metrics.record_proxy_final_status(self.model_name, self.final_status)
            if len(_usage_tasks) >= AI_PROXY_MAX_WAITING:
                ai_metrics.record_proxy_event(self.model_name, "usage_backlog_full")
                logger.warning(
                    "AI relay usage backlog full: request_id=%s", self.request_id
                )
                return
            try:
                task = asyncio.create_task(
                    record_usage_in_threadpool(
                        user_id=self.user.id,
                        credential_id=self.credential.id,
                        model_name=self.model_name,
                        request_type=self.request_type,
                        request_id=self.request_id,
                        upstream_request_id=self.upstream_request_id,
                        duration_ms=int((time.monotonic() - self.started_at) * 1000),
                        stream=self.stream,
                        record_status=self.record_status,
                        error_message=self.error_message,
                        started_at=self.started_at_utc,
                        completed_at=datetime.now(timezone.utc),
                        **self.usage,
                    )
                )
                _usage_tasks.add(task)
                task.add_done_callback(usage_task_done)
                # 入帳仍至多一次；DB 延遲不能延長 public relay 的整筆期限。
                remaining = max(
                    0.0,
                    self.started_at
                    + AI_PROXY_REQUEST_TIMEOUT_SECONDS
                    - time.monotonic(),
                )
                await asyncio.wait({task}, timeout=min(2.0, remaining))
            except Exception:
                logger.exception("Failed to schedule AI relay usage recording")


class RelayStreamingResponse(StreamingResponse):
    def __init__(
        self,
        *args: Any,
        observation: RelayObservation,
        deadline: float,
        deadline_reason: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.observation = observation
        self.deadline = deadline
        self.deadline_reason = deadline_reason
        self.admission_queue = (
            observation.lease._queue if observation.lease is not None else None
        )

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        observation = self.observation
        if observation.lease is not None:
            observation.lease.owner = asyncio.current_task()
        terminal: tuple[str, str, int] | None = None
        try:
            await await_before(
                super().__call__(scope, receive, send),
                self.deadline,
                self.deadline_reason,
            )
        except RelayDeadline as exc:
            terminal = ("error", exc.reason, 503)
        except (asyncio.CancelledError, ClientDisconnect):
            shutdown = self.admission_queue is not None and self.admission_queue.closed
            terminal = (
                "cancelled",
                "backend_shutdown" if shutdown else "client_disconnected",
                503 if shutdown else 499,
            )
            raise
        except Exception:
            terminal = (
                "error",
                observation.error_message or "upstream_stream_error",
                502,
            )
            raise
        finally:
            # send(response.start) 失敗時 generator 尚未開始，也必須清理。
            if not observation.finished:
                try:
                    with anyio.CancelScope(shield=True):
                        if isinstance(self.body_iterator, AsyncGenerator):
                            await self.body_iterator.aclose()
                finally:
                    if terminal is not None:
                        (
                            observation.record_status,
                            observation.error_message,
                            observation.final_status,
                        ) = terminal
                    elif observation.record_status == "streaming":
                        observation.record_status = "cancelled"
                        observation.error_message = "client_disconnected"
                        observation.final_status = 499
                    await observation.finish()


class RelayResponse(Response):
    """Bound non-stream ASGI delivery and finalize usage after delivery/cancellation."""

    def __init__(
        self,
        response: Response,
        observation: RelayObservation,
        queue: AdmissionQueue,
        deadline: float,
    ) -> None:
        super().__init__(
            content=response.body,
            status_code=response.status_code,
            headers=dict(response.headers),
        )
        self.observation = observation
        self.queue = queue
        self.deadline = deadline

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        owner = asyncio.current_task()
        if owner is not None:
            self.queue.requests.add(owner)
        try:
            await until_receive_disconnect(
                receive,
                await_before(
                    super().__call__(scope, receive, send),
                    self.deadline,
                    "request_timeout",
                ),
            )
        except RelayDeadline:
            self.observation.record_status = "error"
            self.observation.error_message = "request_timeout"
            self.observation.final_status = 503
        except (asyncio.CancelledError, OSError, ClientDisconnect):
            self.observation.record_status = "cancelled"
            self.observation.error_message = (
                "backend_shutdown" if self.queue.closed else "client_disconnected"
            )
            self.observation.final_status = 503 if self.queue.closed else 499
            raise
        except Exception:
            self.observation.record_status = "error"
            self.observation.error_message = "downstream_response_error"
            self.observation.final_status = 500
            raise
        finally:
            if owner is not None:
                self.queue.requests.discard(owner)
            await self.observation.finish()


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
    observation: RelayObservation | None = None,
    deadline: float | None = None,
    deadline_reason: str = "generation_timeout",
) -> AsyncGenerator[bytes, None]:
    """Relay accepted SSE once; transport errors/deadlines never restart generation."""
    managed = observation is not None
    observation = observation or RelayObservation(
        user,
        credential,
        model_name,
        request_type,
        request_id,
        started_at,
        started_at_utc,
        True,
        upstream=upstream,
        lease=admission_lease,
        upstream_request_id=upstream_request_id,
    )
    deadline = (
        deadline
        if deadline is not None
        else started_at + AI_PROXY_REQUEST_TIMEOUT_SECONDS
    )
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    line_buffer = ""
    try:
        iterator = upstream.aiter_raw().__aiter__()
        while True:
            try:
                chunk = await await_before(anext(iterator), deadline, deadline_reason)
            except StopAsyncIteration:
                break
            line_buffer += decoder.decode(chunk)
            while "\n" in line_buffer:
                line, line_buffer = line_buffer.split("\n", 1)
                update_stream_usage(
                    line.rstrip("\r"), observation.usage, started_at=started_at
                )
            # 限制未換行 SSE 的解析 buffer；原 bytes 仍照常 relay。
            if len(line_buffer) > 1_048_576:
                line_buffer = ""
            yield chunk
        line_buffer += decoder.decode(b"", final=True)
        if line_buffer:
            update_stream_usage(
                line_buffer.rstrip("\r"), observation.usage, started_at=started_at
            )
        observation.record_status = "success"
        observation.final_status = upstream.status_code
    except (asyncio.CancelledError, GeneratorExit):
        observation.record_status = "cancelled"
        observation.error_message = "client_disconnected"
        observation.final_status = 499
        raise
    except RelayDeadline as exc:
        observation.record_status = "error"
        observation.error_message = exc.reason
        observation.final_status = 503
        # downstream headers 已提交，只能結束串流。
    except Exception:
        observation.record_status = "error"
        observation.error_message = "upstream_stream_error"
        observation.final_status = 502
        logger.warning("AI API upstream stream failed: request_id=%s", request_id)
        raise
    finally:
        if managed:
            with anyio.CancelScope(shield=True):
                await observation.close_attempt()
        else:
            await observation.finish()


_catalogue_models: set[str] = set()
_catalogue_until = 0.0
_catalogue_task: asyncio.Task[set[str]] | None = None
_catalogue_loop: asyncio.AbstractEventLoop | None = None
_catalogue_waiters = 0


async def fetch_public_models() -> set[str]:
    """Discover finite identities using only the restricted service identity."""
    try:
        response = await await_before(
            _get_relay_http_client().get(
                upstream_url("models", ""),
                headers={"Authorization": f"Bearer {ai_api_settings.ai_api_api_key}"},
                timeout=10,
            ),
            time.monotonic() + 10.0,
            "catalogue_timeout",
        )
        try:
            data = response.json().get("data") if response.is_success else None
            if not isinstance(data, list):
                return set()
            names = {
                item["id"]
                for item in data
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and 0 < len(item["id"]) <= 100
            }
            ai_metrics.remember_models(sorted(names))
            return {
                name
                for name in names
                if ai_metrics.model_label(name, served=False) == name
            }
        finally:
            await response.aclose()
    except (
        httpx.RequestError,
        RelayDeadline,
        ValueError,
        AttributeError,
        RecursionError,
    ):
        # 目錄不可用時共用 other 邊界，保留上游錯誤契約並禁止猜測 429。
        return set()


async def public_models() -> set[str]:
    global _catalogue_models, _catalogue_until, _catalogue_task, _catalogue_loop
    global _catalogue_waiters
    loop = asyncio.get_running_loop()
    if _catalogue_loop is not loop:
        _catalogue_models = set()
        _catalogue_until = 0.0
        _catalogue_task = None
        _catalogue_waiters = 0
        _catalogue_loop = loop
    if time.monotonic() < _catalogue_until:
        return _catalogue_models
    if _catalogue_waiters >= AI_PROXY_MAX_WAITING:
        raise AdmissionRejected("queue_full")
    if _catalogue_task is None:
        _catalogue_task = asyncio.create_task(fetch_public_models())
    _catalogue_waiters += 1
    try:
        names = await asyncio.shield(_catalogue_task)
        _catalogue_models = names
        _catalogue_until = time.monotonic() + 60.0
        return names
    finally:
        _catalogue_waiters -= 1
        if _catalogue_task is not None and _catalogue_task.done():
            _catalogue_task = None


async def relay_generation(
    *,
    endpoint: str,
    request: Request,
    payload: dict[str, Any],
    model_name: str,
    user: Any,
    credential: Any,
) -> Response:
    observation = RelayObservation(
        user,
        credential,
        model_name,
        GENERATION_ENDPOINTS[endpoint],
        request_id_for(request),
        time.monotonic(),
        datetime.now(timezone.utc),
        payload.get("stream") is True,
    )
    queue = _get_admission_queue()
    response_deadline = observation.started_at + AI_PROXY_REQUEST_TIMEOUT_SECONDS
    # 為終止 JSON／best-effort 入帳保留最多 2 秒，整筆仍受原始 D 限制。
    deadline = response_deadline - min(2.0, AI_PROXY_REQUEST_TIMEOUT_SECONDS * 0.05)
    # 序號、等待預算與期限在入口建立，所有重排共用。
    ticket = queue.ticket(deadline=deadline)
    owner = asyncio.current_task()
    handed_off = False
    if owner is not None:
        queue.requests.add(owner)
    try:
        response = await until_disconnect(
            request,
            _relay_attempts(
                endpoint=endpoint,
                request=request,
                payload=payload,
                observation=observation,
                queue=queue,
                ticket=ticket,
            ),
        )
        if (
            isinstance(response, RelayStreamingResponse)
            and observation.lease is not None
        ):
            observation.lease.owner = owner
        elif not isinstance(response, RelayStreamingResponse):
            await observation.close_attempt()
            response = RelayResponse(response, observation, queue, response_deadline)
        handed_off = True
        return response
    except asyncio.CancelledError:
        observation.record_status = "cancelled"
        observation.error_message = (
            "backend_shutdown" if queue.closed else "client_disconnected"
        )
        observation.final_status = 503 if queue.closed else 499
        raise
    finally:
        if owner is not None:
            queue.requests.discard(owner)
        if not handed_off:
            queue.discard(ticket)
            await observation.finish()
        elif not isinstance(response, RelayStreamingResponse):
            queue.discard(ticket)


async def _relay_attempts(
    *,
    endpoint: str,
    request: Request,
    payload: dict[str, Any],
    observation: RelayObservation,
    queue: AdmissionQueue,
    ticket: AdmissionTicket,
) -> Response:
    generation_deadline: float | None = None
    attempt = 0
    try:
        if queue.closed:
            raise AdmissionRejected("shutdown")
        if queue.full or (
            queue.active >= queue.max_active and queue.waiting >= queue.max_waiting
        ):
            ai_metrics.record_proxy_admission_rejection("queue_full")
            raise AdmissionRejected("queue_full")
        queue.reserve(ticket)
        discovery_started = time.monotonic()
        try:
            names = await await_before(
                public_models(),
                min(ticket.deadline, discovery_started + ticket.wait_remaining),
                "catalogue_timeout",
            )
        finally:
            ticket.wait_remaining -= time.monotonic() - discovery_started
        ticket.model = (
            observation.model_name if observation.model_name in names else None
        )
        queue._update_metrics()
        headers = service_headers(request, request_id=observation.request_id)
        if observation.stream:
            payload = stream_payload(payload, endpoint)
        client = _get_relay_http_client()
        while True:
            if attempt >= AI_PROXY_MAX_ATTEMPTS:
                raise RelayDeadline("retry_exhausted")
            if (
                generation_deadline is not None
                and time.monotonic() >= generation_deadline
            ):
                raise RelayDeadline("generation_timeout")
            # generation 預算包含 retry cooldown，不因再准入重新起算。
            if generation_deadline is None:
                observation.lease = await queue.acquire(ticket)
            else:
                observation.lease = await await_before(
                    queue.acquire(ticket), generation_deadline, "generation_timeout"
                )
            observation.lease.owner = asyncio.current_task()
            if generation_deadline is None:
                generation_deadline = (
                    time.monotonic() + AI_PROXY_GENERATION_TIMEOUT_SECONDS
                )
            deadline = min(ticket.deadline, generation_deadline)
            deadline_reason = (
                "request_timeout"
                if ticket.deadline <= generation_deadline
                else "generation_timeout"
            )
            attempt += 1
            ai_metrics.record_proxy_event(
                ticket.model or "other", "attempt" if attempt == 1 else "retry"
            )
            outbound = client.build_request(
                "POST",
                upstream_url(endpoint, request.url.query),
                json=payload,
                headers=headers,
            )
            # 先讀 headers，non-stream body 讀取也必須受期限限制。
            upstream = await await_before(
                client.send(outbound, stream=True), deadline, deadline_reason
            )
            observation.upstream = upstream
            observation.upstream_request_id = upstream.headers.get("x-request-id")
            if upstream.status_code == 429:
                content = await await_before(
                    upstream.aread(), deadline, deadline_reason
                )
                reason = (
                    recoverable_model_limit(content)
                    if ticket.model is not None
                    else None
                )
                ai_metrics.record_proxy_event(
                    ticket.model or "other", reason or "upstream_429_unknown"
                )
                if reason is not None:
                    await upstream.aclose()
                    observation.upstream = None
                    queue.rate_limited(
                        observation.lease, upstream.headers.get("retry-after")
                    )
                    observation.lease = None
                    continue
            if upstream.is_success:
                queue.accepted(observation.lease)
            public_headers = response_headers(upstream.headers)
            public_headers.setdefault("x-request-id", observation.request_id)
            if observation.stream and upstream.is_success:
                observation.record_status = "streaming"
                return RelayStreamingResponse(
                    stream_upstream_response(
                        upstream=upstream,
                        admission_lease=observation.lease,
                        user=observation.user,
                        credential=observation.credential,
                        model_name=observation.model_name,
                        request_type=observation.request_type,
                        request_id=observation.request_id,
                        upstream_request_id=observation.upstream_request_id,
                        started_at=observation.started_at,
                        started_at_utc=observation.started_at_utc,
                        observation=observation,
                        deadline=deadline,
                        deadline_reason=deadline_reason,
                    ),
                    observation=observation,
                    deadline=deadline,
                    deadline_reason=deadline_reason,
                    status_code=upstream.status_code,
                    media_type=upstream.headers.get(
                        "content-type", "text/event-stream"
                    ),
                    headers={**public_headers, **_STREAM_RESPONSE_HEADERS},
                )
            content = await await_before(upstream.aread(), deadline, deadline_reason)
            try:
                result = json.loads(content) if upstream.is_success else None
            except ValueError:
                result = None
            inputs, outputs, reported, model = usage_details(result)
            observation.usage.update(
                input_tokens=inputs,
                output_tokens=outputs,
                usage_reported=reported,
                response_model=model,
            )
            observation.record_status = "success" if upstream.is_success else "error"
            observation.error_message = (
                None if upstream.is_success else f"upstream_http_{upstream.status_code}"
            )
            observation.final_status = upstream.status_code
            await observation.close_attempt()
            if not upstream.is_success:
                return upstream_failure(
                    request=request,
                    upstream=upstream,
                    context=f"relay:{endpoint}",
                )
            return Response(
                content=content,
                status_code=upstream.status_code,
                headers=public_headers,
                media_type=upstream.headers.get("content-type"),
            )
    except AdmissionRejected as exc:
        if exc.reason == "request_timeout":
            observation.error_message = "request_timeout"
            return openai_error(
                503,
                "Model service is temporarily unavailable. Please try again later.",
                error_type="api_connection_error",
                code="upstream_unavailable",
            )
        observation.error_message = (
            exc.reason
            if exc.reason.startswith("queue_")
            else f"queue_{exc.reason}"
        )
        return openai_error(
            503,
            "AI service is busy. Please retry shortly.",
            error_type="api_error",
            code="server_busy",
            headers={"Retry-After": str(AI_PROXY_RETRY_AFTER_SECONDS)},
        )
    except RelayDeadline as exc:
        if exc.reason == "catalogue_timeout":
            observation.error_message = "queue_timeout"
            ai_metrics.record_proxy_admission_rejection("timeout")
            return openai_error(
                503,
                "AI service is busy. Please retry shortly.",
                error_type="api_error",
                code="server_busy",
                headers={"Retry-After": str(AI_PROXY_RETRY_AFTER_SECONDS)},
            )
        observation.error_message = exc.reason
        logger.warning(
            "AI relay deadline: reason=%s request_id=%s",
            exc.reason,
            observation.request_id,
        )
        return openai_error(
            503,
            "Model service is temporarily unavailable. Please try again later.",
            error_type="api_connection_error",
            code="upstream_unavailable",
        )
    except httpx.RequestError:
        observation.error_message = "upstream_unavailable"
        logger.warning(
            "AI relay unavailable: reason=%s request_id=%s",
            observation.error_message,
            observation.request_id,
        )
        return openai_error(
            503,
            "Model service is temporarily unavailable. Please try again later.",
            error_type="api_connection_error",
            code="upstream_unavailable",
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
