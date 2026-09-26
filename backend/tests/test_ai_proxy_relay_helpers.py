"""Unit tests for the P5 AI relay helpers without a database or live model."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
import sqlmodel
from starlette.requests import Request

from app.api.routes import ai_proxy
from app.features.ai.config import settings as ai_api_settings


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

    headers = ai_proxy._service_headers(request)

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
    assert ai_proxy._usage_tokens(
        {"usage": {"input_tokens": 11, "output_tokens": 7}}
    ) == (11, 7)


def test_usage_recording_failure_does_not_replace_model_response(monkeypatch) -> None:
    def fail_record(**_kwargs) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(ai_proxy.ai_gateway_service, "record_usage", fail_record)

    ai_proxy._record_usage_safely(
        session=object(),
        user=SimpleNamespace(id="user-1"),
        credential=SimpleNamespace(id="credential-1"),
        model_name="model",
        request_type="chat_completion",
    )


def test_stream_usage_is_injected_without_mutating_the_original_payload() -> None:
    payload = {"model": "gpt-oss-20B", "stream": True, "stream_options": {}}
    updated = ai_proxy._stream_payload(payload, "chat/completions")

    assert updated["stream_options"] == {"include_usage": True}
    assert payload["stream_options"] == {}

    usage = {"input_tokens": 0, "output_tokens": 0, "usage_reported": False}
    ai_proxy._update_stream_usage(
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

    ai_proxy._update_stream_usage(
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

    class FakeClient:
        async def aclose(self) -> None:
            return None

    class FakeSession:
        def __init__(self, _engine) -> None:
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *_args) -> None:
            return None

    recorded: dict[str, object] = {}
    monkeypatch.setattr(sqlmodel, "Session", FakeSession)
    monkeypatch.setattr(
        ai_proxy,
        "_record_usage_safely",
        lambda **kwargs: recorded.update(kwargs),
    )
    upstream = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        request=httpx.Request("POST", "http://upstream/v1/chat/completions"),
        stream=Stream(),
    )
    started_at = time.monotonic()

    chunks = [
        chunk
        async for chunk in ai_proxy._stream_upstream_response(
            client=FakeClient(),
            upstream=upstream,
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


@pytest.mark.asyncio
async def test_cancelled_stream_still_records_partial_observation(monkeypatch) -> None:
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"model":"resolved","choices":[{"delta":{"content":"ok"}}]}\n\n'
            raise asyncio.CancelledError

    class FakeClient:
        async def aclose(self) -> None:
            return None

    class FakeSession:
        def __init__(self, _engine) -> None:
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *_args) -> None:
            return None

    recorded: dict[str, object] = {}
    monkeypatch.setattr(sqlmodel, "Session", FakeSession)
    monkeypatch.setattr(
        ai_proxy,
        "_record_usage_safely",
        lambda **kwargs: recorded.update(kwargs),
    )
    upstream = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        request=httpx.Request("POST", "http://upstream/v1/chat/completions"),
        stream=Stream(),
    )
    stream = ai_proxy._stream_upstream_response(
        client=FakeClient(),
        upstream=upstream,
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


def test_generation_relay_replaces_authorization_and_preserves_query(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

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

        async def aclose(self) -> None:
            return None

    async def no_redis():
        return None

    recorded: dict[str, object] = {}
    monkeypatch.setattr(ai_proxy, "get_redis", no_redis)
    monkeypatch.setattr(ai_proxy.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        ai_proxy.ai_gateway_service,
        "record_usage",
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
    credential = SimpleNamespace(id="credential-1", rate_limit=None)

    response = asyncio.run(
        ai_proxy._relay_generation(
            endpoint="responses",
            request=request,
            user_and_credential=(user, credential),
            session=object(),
        )
    )

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
