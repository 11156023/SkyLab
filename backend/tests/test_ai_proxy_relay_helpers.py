"""Unit tests for the P5 AI relay helpers without a database or live model."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request

from app.api.routes import ai_proxy
from app.features.ai.config import settings as ai_api_settings
from app.services.llm_gateway import relay_service


@pytest.fixture(autouse=True)
def _mock_model_catalogue(monkeypatch) -> None:
    async def catalogue():
        return {"m", "model", "gpt-oss-20B"}

    monkeypatch.setattr(relay_service, "public_models", catalogue)


def _request(
    *,
    body: bytes = b"{}",
    headers: list[tuple[bytes, bytes]] | None = None,
    query: bytes = b"",
) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/ai-proxy/chat/completions",
            "query_string": query,
            "headers": headers or [],
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        },
        receive,
    )


def test_service_headers_replace_the_client_authorization(monkeypatch) -> None:
    monkeypatch.setattr(ai_api_settings, "ai_api_api_key", "restricted-service-key")
    request = _request(
        headers=[
            (b"authorization", b"Bearer ccai_user_key"),
            (b"host", b"attacker.example"),
            (b"x-request-id", b"request-123"),
            (b"openai-beta", b"responses=v1"),
        ]
    )

    headers = relay_service.service_headers(request)

    assert headers["Authorization"] == "Bearer restricted-service-key"
    assert headers["x-request-id"] == "request-123"
    assert headers["openai-beta"] == "responses=v1"
    assert "host" not in headers
    assert "ccai_user_key" not in headers.values()


def test_json_payload_rejects_non_json_and_large_bodies(monkeypatch) -> None:
    non_json = _request(headers=[(b"content-type", b"text/plain")])
    response = asyncio.run(ai_proxy._json_payload(non_json))
    assert response.status_code == 415

    monkeypatch.setattr(ai_api_settings, "ai_api_max_request_body_bytes", 1)
    too_large = _request(body=b"{}", headers=[(b"content-type", b"application/json")])
    response = asyncio.run(ai_proxy._json_payload(too_large))
    assert response.status_code == 413


def test_model_is_forwarded_without_a_campus_allowlist() -> None:
    assert ai_proxy._request_model({"model": "gpt-oss-20B"}) == "gpt-oss-20B"
    assert ai_proxy._request_model({"model": "not-public"}) == "not-public"
    assert relay_service.usage_details(
        {"usage": {"input_tokens": 11, "output_tokens": 7}}
    )[:2] == (11, 7)


@pytest.mark.asyncio
async def test_ai_proxy_rate_limit_uses_approved_request_identity(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_redis() -> object:
        return object()

    async def fake_check(**kwargs):
        captured.update(kwargs)
        return True, {}

    monkeypatch.setattr(ai_proxy, "get_redis", fake_redis)
    monkeypatch.setattr(ai_proxy, "check_rate_limit_sliding_window", fake_check)

    await ai_proxy._enforce_rate_limit(
        credential=SimpleNamespace(
            id="credential-1",
            request_id="request-1",
            _rate_limit_legacy_ids=("credential-1",),
            rate_limit=7,
        )
    )

    assert captured["request_id"] == "request-1"
    assert captured["legacy_credential_ids"] == ("credential-1",)
    assert captured["limit"] == 7


@pytest.mark.asyncio
async def test_rate_limit_status_reads_the_same_approved_request_bucket(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_redis() -> object:
        return object()

    async def fake_peek(
        _redis, *, key: str, window_seconds: int, legacy_keys: tuple[str, ...]
    ) -> int:
        captured["key"] = key
        captured["window_seconds"] = window_seconds
        return 3

    monkeypatch.setattr(ai_proxy, "get_redis", fake_redis)
    monkeypatch.setattr(ai_proxy, "peek_rate_limit_by_key", fake_peek)

    result = await ai_proxy.get_rate_limit_status(
        (
            SimpleNamespace(id="user-1"),
            SimpleNamespace(
                id="credential-1",
                request_id="request-1",
                _rate_limit_legacy_ids=("credential-1",),
                rate_limit=7,
            ),
        )
    )

    assert captured["key"] == "ai-request:request-1"
    assert result.current_usage == 3
    assert result.remaining == 4


def test_usage_recording_failure_does_not_replace_model_response(monkeypatch) -> None:
    class FakeSession:
        def __init__(self, _engine) -> None:
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *_args) -> None:
            return None

    def fail_record(**_kwargs) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(relay_service, "Session", FakeSession)
    monkeypatch.setattr(relay_service.ai_gateway_service, "record_usage", fail_record)

    relay_service.record_usage_safely(
        user_id="user-1",
        credential_id="credential-1",
        model_name="model",
        request_type="chat_completion",
    )


@pytest.mark.asyncio
async def test_usage_recording_uses_independent_session_off_event_loop(
    monkeypatch,
) -> None:
    main_thread_id = threading.get_ident()
    started = threading.Event()
    release = threading.Event()
    usage_session = object()
    observed: dict[str, object] = {}

    class FakeSession:
        def __init__(self, _engine) -> None:
            observed["session_created_in"] = threading.get_ident()

        def __enter__(self):
            return usage_session

        def __exit__(self, *_args) -> None:
            return None

    def slow_record(**kwargs) -> None:
        observed["recorded_in"] = threading.get_ident()
        observed["session"] = kwargs["session"]
        started.set()
        release.wait(timeout=2)

    monkeypatch.setattr(relay_service, "Session", FakeSession)
    monkeypatch.setattr(relay_service.ai_gateway_service, "record_usage", slow_record)

    task = asyncio.create_task(
        relay_service.record_usage_in_threadpool(
            user_id="user-1",
            credential_id="credential-1",
            model_name="model",
            request_type="chat_completion",
        )
    )
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1)
        # DB worker 尚未放行時，event loop 仍可排程其他 coroutine。
        await asyncio.wait_for(asyncio.sleep(0), timeout=0.2)
        assert not task.done()
    finally:
        release.set()
        await task

    assert observed["session"] is usage_session
    assert observed["session_created_in"] != main_thread_id
    assert observed["recorded_in"] != main_thread_id


@pytest.mark.asyncio
async def test_admission_queue_waits_rejects_overflow_and_releases() -> None:
    queue = relay_service.AdmissionQueue(
        max_active=1,
        max_waiting=2,
        wait_timeout_seconds=1,
    )
    first = await queue.acquire()
    second_task = asyncio.create_task(queue.acquire())
    while queue.waiting == 0:
        await asyncio.sleep(0)

    with pytest.raises(relay_service.AdmissionRejected, match="queue_full"):
        await queue.acquire()

    first.release()
    second = await second_task
    assert queue.active == 1
    assert queue.waiting == 0
    second.release()
    assert queue.active == 0


@pytest.mark.asyncio
async def test_admission_queue_does_not_let_new_requests_bypass_waiters() -> None:
    queue = relay_service.AdmissionQueue(
        max_active=1,
        max_waiting=2,
        wait_timeout_seconds=1,
    )
    first = await queue.acquire()
    order: list[str] = []

    async def queued_request() -> None:
        lease = await queue.acquire()
        order.append("waiting")
        lease.release()

    waiter = asyncio.create_task(queued_request())
    while queue.waiting == 0:
        await asyncio.sleep(0)

    first.release()
    newcomer = await queue.acquire()
    order.append("newcomer")
    newcomer.release()
    await waiter

    assert order == ["waiting", "newcomer"]


@pytest.mark.asyncio
async def test_admission_queue_timeout_and_cancel_do_not_leak_waiters() -> None:
    queue = relay_service.AdmissionQueue(
        max_active=1,
        max_waiting=2,
        wait_timeout_seconds=0.01,
    )
    first = await queue.acquire()

    with pytest.raises(relay_service.AdmissionRejected, match="timeout"):
        await queue.acquire()
    assert queue.waiting == 0
    assert queue.active == 1

    queue.wait_timeout_seconds = 1
    waiter = asyncio.create_task(queue.acquire())
    while queue.waiting == 0:
        await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert queue.waiting == 0
    first.release()
    assert queue.active == 0


@pytest.mark.asyncio
async def test_admission_slot_handed_to_cancelled_waiter_is_returned() -> None:
    """token 已交給 waiter、它卻在恢復執行前被取消時，名額要還回去。"""
    queue = relay_service.AdmissionQueue(
        max_active=1,
        max_waiting=2,
        wait_timeout_seconds=1,
    )
    first = await queue.acquire()
    waiter = asyncio.create_task(queue.acquire())
    while queue.waiting == 0:
        await asyncio.sleep(0)

    first.release()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert queue.waiting == 0
    assert queue.active == 0
    again = await asyncio.wait_for(queue.acquire(), timeout=0.1)
    again.release()
    assert queue.active == 0


@pytest.mark.asyncio
async def test_shared_relay_client_is_reused_and_closed_on_shutdown(
    monkeypatch,
) -> None:
    created: list[object] = []
    init_kwargs: dict[str, object] = {}

    class FakeClient:
        is_closed = False

        def __init__(self, **kwargs) -> None:
            created.append(self)
            init_kwargs.update(kwargs)

        async def aclose(self) -> None:
            self.is_closed = True

    await relay_service.close_relay_runtime()
    monkeypatch.setattr(relay_service.httpx, "AsyncClient", FakeClient)

    first = relay_service._get_relay_http_client()
    second = relay_service._get_relay_http_client()

    assert first is second
    assert len(created) == 1
    limits = init_kwargs["limits"]
    assert isinstance(limits, httpx.Limits)
    assert limits.max_connections == relay_service.AI_PROXY_MAX_ACTIVE
    assert limits.max_keepalive_connections == relay_service.AI_PROXY_MAX_ACTIVE
    await relay_service.close_relay_runtime()
    assert first.is_closed is True  # type: ignore[attr-defined]


def test_stream_usage_is_injected_without_mutating_the_original_payload() -> None:
    payload = {"model": "gpt-oss-20B", "stream": True, "stream_options": {}}
    updated = relay_service.stream_payload(payload, "chat/completions")

    assert updated["stream_options"] == {"include_usage": True}
    assert payload["stream_options"] == {}

    usage = {"input_tokens": 0, "output_tokens": 0, "usage_reported": False}
    relay_service.update_stream_usage(
        'data: {"usage":{"prompt_tokens":3,"completion_tokens":2}}', usage
    )
    assert usage == {
        "input_tokens": 3,
        "output_tokens": 2,
        "usage_reported": True,
    }


def test_responses_stream_completed_event_records_nested_usage() -> None:
    observation = {
        "input_tokens": 0,
        "output_tokens": 0,
        "usage_reported": False,
        "response_model": None,
        "first_token_ms": None,
    }

    relay_service.update_stream_usage(
        'data:{"type":"response.completed","response":{"model":"gpt-oss-20B",'
        '"usage":{"input_tokens":11,"output_tokens":7}}}',
        observation,
    )

    assert observation["input_tokens"] == 11
    assert observation["output_tokens"] == 7
    assert observation["usage_reported"] is True
    assert observation["response_model"] == "gpt-oss-20B"


@pytest.mark.asyncio
async def test_stream_completion_records_usage_and_first_token(monkeypatch) -> None:
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"model":"resolved","choices":[{"delta":{"content":"ok"}}]}\n\n'
            yield b'data: {"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n'
            yield b"data: [DONE]\n\n"

    recorded: dict[str, object] = {}
    monkeypatch.setattr(
        relay_service,
        "record_usage_safely",
        lambda **kwargs: recorded.update(kwargs),
    )
    upstream = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        request=httpx.Request("POST", "http://upstream/v1/chat/completions"),
        stream=Stream(),
    )
    started_at = time.monotonic()
    queue = relay_service.AdmissionQueue(
        max_active=1, max_waiting=0, wait_timeout_seconds=1
    )
    lease = await queue.acquire()

    chunks = [
        chunk
        async for chunk in relay_service.stream_upstream_response(
            upstream=upstream,
            admission_lease=lease,
            user=SimpleNamespace(id="user-1"),
            credential=SimpleNamespace(id="credential-1"),
            model_name="requested",
            request_type="chat_completion",
            request_id="request-1",
            upstream_request_id="upstream-1",
            started_at=started_at,
            started_at_utc=datetime.now(timezone.utc),
        )
    ]

    assert b"".join(chunks).endswith(b"data: [DONE]\n\n")
    assert recorded["input_tokens"] == 3
    assert recorded["output_tokens"] == 2
    assert recorded["usage_reported"] is True
    assert recorded["response_model"] == "resolved"
    assert isinstance(recorded["first_token_ms"], int)
    assert recorded["record_status"] == "success"
    assert lease.released is True
    assert queue.active == 0


@pytest.mark.parametrize(
    ("request_type", "chunks", "expected_status", "expected_error"),
    [
        (
            "chat_completion",
            [b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'],
            "error",
            "incomplete_stream",
        ),
        (
            "response",
            [b'data: {"type":"response.completed","response":{}}\n\n'],
            "success",
            None,
        ),
        (
            "response",
            [b'event: response.failed\ndata: {"type":"response.failed"}\n\n'],
            "error",
            "upstream_stream_error",
        ),
        (
            "completion",
            [b'data: {"error":{"message":"synthetic"}}\n\ndata: [DONE]\n\n'],
            "error",
            "upstream_stream_error",
        ),
        (
            "completion",
            [b'data: {"type":[],"choices":[]}\n\ndata: [DONE]\n\n'],
            "success",
            None,
        ),
        (
            "chat_completion",
            [b"data: [DONE]\r\r"],
            "success",
            None,
        ),
        (
            "response",
            [b"event: response.completed"],
            "error",
            "incomplete_stream",
        ),
    ],
)
@pytest.mark.asyncio
async def test_stream_terminal_accounting_preserves_upstream_bytes(
    monkeypatch, request_type, chunks, expected_status, expected_error
) -> None:
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for chunk in chunks:
                yield chunk

    recorded: dict[str, object] = {}
    monkeypatch.setattr(
        relay_service,
        "record_usage_safely",
        lambda **kwargs: recorded.update(kwargs),
    )
    upstream = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        request=httpx.Request("POST", "http://upstream/v1/generation"),
        stream=Stream(),
    )
    queue = relay_service.AdmissionQueue(
        max_active=1, max_waiting=0, wait_timeout_seconds=1
    )
    lease = await queue.acquire()

    relayed = b"".join(
        [
            chunk
            async for chunk in relay_service.stream_upstream_response(
                upstream=upstream,
                admission_lease=lease,
                user=SimpleNamespace(id="user-1"),
                credential=SimpleNamespace(id="credential-1"),
                model_name="requested",
                request_type=request_type,
                request_id="request-1",
                upstream_request_id=None,
                started_at=time.monotonic(),
                started_at_utc=datetime.now(timezone.utc),
            )
        ]
    )

    assert relayed == b"".join(chunks)
    assert recorded["record_status"] == expected_status
    assert recorded["error_message"] == expected_error
    assert lease.released is True


@pytest.mark.asyncio
async def test_cancelled_stream_still_records_partial_observation(monkeypatch) -> None:
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"model":"resolved","choices":[{"delta":{"content":"ok"}}]}\n\n'
            raise asyncio.CancelledError

    recorded: dict[str, object] = {}
    monkeypatch.setattr(
        relay_service,
        "record_usage_safely",
        lambda **kwargs: recorded.update(kwargs),
    )
    upstream = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        request=httpx.Request("POST", "http://upstream/v1/chat/completions"),
        stream=Stream(),
    )
    queue = relay_service.AdmissionQueue(
        max_active=1, max_waiting=0, wait_timeout_seconds=1
    )
    lease = await queue.acquire()
    stream = relay_service.stream_upstream_response(
        upstream=upstream,
        admission_lease=lease,
        user=SimpleNamespace(id="user-1"),
        credential=SimpleNamespace(id="credential-1"),
        model_name="requested",
        request_type="chat_completion",
        request_id="request-1",
        upstream_request_id=None,
        started_at=time.monotonic(),
        started_at_utc=datetime.now(timezone.utc),
    )

    assert await anext(stream) == (
        b'data: {"model":"resolved","choices":[{"delta":{"content":"ok"}}]}\n\n'
    )
    with pytest.raises(asyncio.CancelledError):
        await anext(stream)

    assert recorded["record_status"] == "cancelled"
    assert recorded["error_message"] == "client_disconnected"
    assert recorded["stream"] is True
    assert recorded["usage_reported"] is False
    assert recorded["response_model"] == "resolved"
    assert isinstance(recorded["first_token_ms"], int)
    assert lease.released is True
    assert queue.active == 0


@pytest.mark.asyncio
async def test_models_singleflight_returns_neutral_caller_owned_responses(
    monkeypatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    upstream_requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        started.set()
        await release.wait()
        return httpx.Response(
            200,
            json={"data": [{"id": "model"}]},
            headers={"x-request-id": "upstream-fetch-id"},
        )

    def models_request(request_id: str) -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
                "scheme": "http",
                "path": "/api/v1/ai-proxy/models",
                "query_string": b"",
                "headers": [(b"x-request-id", request_id.encode())],
                "server": ("test", 80),
            }
        )

    monkeypatch.setattr(relay_service, "_models_cache_loop", None)
    monkeypatch.setattr(relay_service, "_relay_stopping", False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay_service, "_get_relay_http_client", lambda: client)
        first_task = asyncio.create_task(
            relay_service.list_models(
                models_request("caller-one"), user=SimpleNamespace(id="user-1")
            )
        )
        await started.wait()
        second_task = asyncio.create_task(
            relay_service.list_models(
                models_request("caller-two"), user=SimpleNamespace(id="user-2")
            )
        )
        await asyncio.sleep(0)
        release.set()
        first, second = await asyncio.gather(first_task, second_task)
        cached = await relay_service.list_models(
            models_request("caller-three"), user=SimpleNamespace(id="user-3")
        )

    assert len(upstream_requests) == 1
    assert "x-request-id" not in upstream_requests[0].headers
    assert len({id(first), id(second), id(cached)}) == 3
    assert all("x-request-id" not in response.headers for response in (first, second, cached))


@pytest.mark.asyncio
async def test_models_rejects_non_finite_upstream_json(monkeypatch) -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"data":[{"id":"model","score":NaN}]}',
            headers={"content-type": "application/json"},
        )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/ai-proxy/models",
            "query_string": b"",
            "headers": [],
            "server": ("test", 80),
        }
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay_service, "_get_relay_http_client", lambda: client)
        response = await relay_service._fetch_models_response(request)

    assert response.status_code == 502
    assert json.loads(response.body)["error"]["code"] == "invalid_upstream_response"


def test_generation_relay_replaces_authorization_and_preserves_query(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def build_request(self, method: str, url: str, **kwargs) -> httpx.Request:
            captured["request"] = httpx.Request(method, url, **kwargs)
            return captured["request"]  # type: ignore[return-value]

        async def send(self, request: httpx.Request, *, stream: bool) -> httpx.Response:
            captured["stream"] = stream
            return httpx.Response(
                200,
                json={
                    "object": "response",
                    "model": "resolved-model",
                    "usage": {"input_tokens": 5, "output_tokens": 3},
                },
                headers={
                    "content-type": "application/json",
                    "x-request-id": "upstream-1",
                },
                request=request,
            )

    async def no_redis():
        return None

    recorded: dict[str, object] = {}
    admission_queue = relay_service.AdmissionQueue(
        max_active=1, max_waiting=0, wait_timeout_seconds=1
    )
    monkeypatch.setattr(ai_proxy, "get_redis", no_redis)
    monkeypatch.setattr(relay_service, "_get_relay_http_client", FakeClient)
    monkeypatch.setattr(
        relay_service,
        "_get_admission_queue",
        lambda: admission_queue,
    )
    monkeypatch.setattr(
        relay_service,
        "record_usage_safely",
        lambda **kwargs: recorded.update(kwargs),
    )
    monkeypatch.setattr(
        ai_api_settings, "ai_api_base_url", "http://litellm.internal:4000"
    )
    monkeypatch.setattr(ai_api_settings, "ai_api_api_key", "restricted-service-key")

    request = _request(
        body=json.dumps({"model": "gpt-oss-20B", "input": "hello"}).encode(),
        headers=[
            (b"content-type", b"application/json"),
            (b"authorization", b"Bearer ccai_user_key"),
        ],
        query=b"include=usage",
    )
    user = SimpleNamespace(id="user-1")
    credential = SimpleNamespace(
        id="credential-1",
        request_id="request-1",
        _rate_limit_legacy_ids=("credential-1",),
        rate_limit=None,
    )

    async def invoke():
        response = await ai_proxy._relay_generation(
            endpoint="responses",
            request=request,
            user_and_credential=(user, credential),
        )

        async def send(_message):
            return None

        await response(request.scope, request.receive, send)
        return response

    response = asyncio.run(invoke())

    outbound = captured["request"]
    assert isinstance(outbound, httpx.Request)
    assert (
        str(outbound.url) == "http://litellm.internal:4000/v1/responses?include=usage"
    )
    assert outbound.headers["authorization"] == "Bearer restricted-service-key"
    assert outbound.headers["x-request-id"]
    assert b"ccai_user_key" not in outbound.content
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "upstream-1"
    assert recorded["request_type"] == "response"
    assert recorded["input_tokens"] == 5
    assert recorded["output_tokens"] == 3
    assert recorded["usage_reported"] is True
    assert recorded["response_model"] == "resolved-model"
    assert recorded["request_id"] == outbound.headers["x-request-id"]
    assert recorded["upstream_request_id"] == "upstream-1"
    assert admission_queue.active == 0


@pytest.mark.parametrize("is_stream", [True, False])
def test_only_stream_responses_disable_proxy_buffering(
    monkeypatch, is_stream: bool
) -> None:
    """串流回應要叫主系統 nginx 別緩衝，否則 SSE 會被攢成一大塊才送出。"""

    class FakeClient:
        def build_request(self, method: str, url: str, **kwargs) -> httpx.Request:
            return httpx.Request(method, url, **kwargs)

        async def send(self, request: httpx.Request, *, stream: bool) -> httpx.Response:
            if stream:
                return httpx.Response(
                    200,
                    content=b"data: [DONE]\n\n",
                    headers={"content-type": "text/event-stream"},
                    request=request,
                )
            return httpx.Response(
                200,
                json={"object": "chat.completion", "model": "m"},
                request=request,
            )

    async def no_redis():
        return None

    admission_queue = relay_service.AdmissionQueue(
        max_active=1, max_waiting=0, wait_timeout_seconds=1
    )
    monkeypatch.setattr(ai_proxy, "get_redis", no_redis)
    monkeypatch.setattr(relay_service, "_get_relay_http_client", FakeClient)
    monkeypatch.setattr(relay_service, "_get_admission_queue", lambda: admission_queue)
    monkeypatch.setattr(relay_service, "record_usage_safely", lambda **_kwargs: None)

    request = _request(
        body=json.dumps({"model": "m", "messages": [], "stream": is_stream}).encode(),
        headers=[(b"content-type", b"application/json")],
    )
    response = asyncio.run(
        ai_proxy._relay_generation(
            endpoint="chat/completions",
            request=request,
            user_and_credential=(
                SimpleNamespace(id="user-1"),
                SimpleNamespace(
                    id="credential-1",
                    request_id="request-1",
                    _rate_limit_legacy_ids=("credential-1",),
                    rate_limit=None,
                ),
            ),
        )
    )

    assert response.status_code == 200
    if is_stream:
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["content-type"].startswith("text/event-stream")
    else:
        assert "x-accel-buffering" not in response.headers


@pytest.mark.asyncio
async def test_upstream_connection_error_releases_admission_slot(
    monkeypatch,
) -> None:
    class FailingClient:
        def build_request(self, method: str, url: str, **kwargs) -> httpx.Request:
            return httpx.Request(method, url, **kwargs)

        async def send(self, request: httpx.Request, *, stream: bool) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

    queue = relay_service.AdmissionQueue(
        max_active=1, max_waiting=0, wait_timeout_seconds=1
    )
    monkeypatch.setattr(relay_service, "_get_relay_http_client", FailingClient)
    monkeypatch.setattr(relay_service, "_get_admission_queue", lambda: queue)
    monkeypatch.setattr(
        relay_service,
        "record_usage_safely",
        lambda **_kwargs: None,
    )

    response = await relay_service.relay_generation(
        endpoint="chat/completions",
        request=_request(),
        payload={"model": "model"},
        model_name="model",
        user=SimpleNamespace(id="user-1"),
        credential=SimpleNamespace(id="credential-1"),
    )

    assert response.status_code == 503
    assert queue.active == 0


@pytest.mark.asyncio
async def test_generation_returns_503_when_admission_queue_is_full(
    monkeypatch,
) -> None:
    queue = relay_service.AdmissionQueue(
        max_active=1, max_waiting=0, wait_timeout_seconds=1
    )
    lease = await queue.acquire()
    monkeypatch.setattr(relay_service, "_get_admission_queue", lambda: queue)
    monkeypatch.setattr(relay_service, "record_usage_safely", lambda **_kwargs: None)

    response = await relay_service.relay_generation(
        endpoint="chat/completions",
        request=_request(),
        payload={"model": "model"},
        model_name="model",
        user=SimpleNamespace(id="user-1"),
        credential=SimpleNamespace(id="credential-1"),
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == str(
        relay_service.AI_PROXY_RETRY_AFTER_SECONDS
    )
    assert json.loads(response.body)["error"]["code"] == "server_busy"
    lease.release()
