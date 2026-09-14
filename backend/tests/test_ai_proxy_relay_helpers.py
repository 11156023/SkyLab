"""Unit tests for the P5 AI relay helpers without a database or live model."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
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
    too_large = _request(
        body=b"{}", headers=[(b"content-type", b"application/json")]
    )
    response = asyncio.run(ai_proxy._json_payload(too_large))
    assert response.status_code == 413


def test_model_is_forwarded_without_a_campus_allowlist() -> None:
    assert ai_proxy._request_model({"model": "gpt-oss-20B"}) == "gpt-oss-20B"
    assert ai_proxy._request_model({"model": "not-public"}) == "not-public"
    assert ai_proxy._usage_tokens(
        {"usage": {"input_tokens": 11, "output_tokens": 7}}
    ) == (11, 7)


def test_stream_usage_is_injected_without_mutating_the_original_payload() -> None:
    payload = {"model": "gpt-oss-20B", "stream": True, "stream_options": {}}
    updated = ai_proxy._stream_payload(payload, "chat/completions")

    assert updated["stream_options"] == {"include_usage": True}
    assert payload["stream_options"] == {}

    usage = {"input_tokens": 0, "output_tokens": 0}
    ai_proxy._update_stream_usage(
        'data: {"usage":{"prompt_tokens":3,"completion_tokens":2}}', usage
    )
    assert usage == {"input_tokens": 3, "output_tokens": 2}


def test_generation_relay_replaces_authorization_and_preserves_query(monkeypatch) -> None:
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
                json={"object": "response", "usage": {"input_tokens": 5, "output_tokens": 3}},
                headers={"content-type": "application/json", "x-request-id": "upstream-1"},
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
    monkeypatch.setattr(ai_api_settings, "ai_api_base_url", "http://litellm.internal:4000")
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
    assert str(outbound.url) == "http://litellm.internal:4000/v1/responses?include=usage"
    assert outbound.headers["authorization"] == "Bearer restricted-service-key"
    assert b"ccai_user_key" not in outbound.content
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "upstream-1"
    assert recorded["request_type"] == "response"
    assert recorded["input_tokens"] == 5
    assert recorded["output_tokens"] == 3
