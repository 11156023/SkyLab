"""P1 security boundaries using real routes and isolated network substitutes."""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.api import request_body
from app.api.deps import get_current_user, get_db
from app.api.routes import ai_api, ai_proxy
from app.exceptions import AppError
from app.models import User, UserRole
from app.services.llm_gateway import relay_service as relay

_ID = str(uuid.uuid4())
_CONTROL_CASES = [
    (
        "POST",
        "/ai-api/requests/bulk-reject",
        {"request_ids": [_ID], "review_comment": "reason"},
        "bulk_reject_requests",
    ),
    (
        "POST",
        f"/ai-api/requests/{_ID}/review",
        {"status": "rejected", "review_comment": "reason"},
        "review_request",
    ),
    (
        "PATCH",
        f"/ai-api/credentials/{_ID}",
        {"api_key_name": "renamed"},
        "update_credential_name",
    ),
]


def _app(role: UserRole | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(ai_api.router)
    app.dependency_overrides[get_db] = lambda: object()
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)
    if role is not None:
        app.dependency_overrides[get_current_user] = lambda: User(
            email="p1@example.test",
            hashed_password="unused",
            role=role,
        )
    return app


@pytest.mark.parametrize(
    "method,path,payload,service_name,role,expected",
    [(*case, None, 401) for case in _CONTROL_CASES]
    + [(*case, UserRole.student, 403) for case in _CONTROL_CASES[:2]],
)
async def test_control_plane_rejects_before_asgi_receive(
    method, path, payload, service_name, role, expected
):
    app = _app(role)
    reads = []
    statuses = []

    async def receive():
        reads.append(1)
        return {
            "type": "http.request",
            "body": json.dumps({**payload, "padding": "x" * 32783}).encode(),
            "more_body": False,
        }

    async def send(message):
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    await app(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "scheme": "http",
            "server": ("test", 80),
        },
        receive,
        send,
    )
    assert statuses == [expected]
    assert reads == []


@pytest.mark.parametrize("method,path,payload,service_name", _CONTROL_CASES)
async def test_control_plane_limit_validation_and_legitimate_payload(
    monkeypatch, method, path, payload, service_name
):
    service = Mock(return_value=JSONResponse({"ok": True}))
    monkeypatch.setattr(ai_api.ai_gateway_service, service_name, service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(UserRole.admin)), base_url="http://test"
    ) as client:
        response = await client.request(
            method,
            path,
            content=b"x" * 17000,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 413
        service.assert_not_called()
        response = await client.request(method, path, json={})
        assert response.status_code == 422
        service.assert_not_called()
        response = await client.request(method, path, json=payload)
        assert response.status_code == 200
        service.assert_called_once()
    operation = _app().openapi()["paths"]
    schema_path = path.replace(
        _ID, "{request_id}" if "/requests/" in path else "{credential_id}"
    )
    schema = operation[schema_path][method.lower()]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert set(payload) <= set(schema["properties"])


@pytest.mark.parametrize("method,path,payload,service_name", _CONTROL_CASES)
@pytest.mark.parametrize("content_type,expected", [
    ("application/json ; charset=utf-8", 200),
    ("application/vnd.campus+json", 200),
    ("text/custom+json", 422),
    (None, 422),
])
async def test_control_plane_preserves_content_type_contract(
    monkeypatch, method, path, payload, service_name, content_type, expected,
):
    service = Mock(return_value=JSONResponse({"ok": True}))
    monkeypatch.setattr(ai_api.ai_gateway_service, service_name, service)
    headers = {"content-type": content_type} if content_type else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(UserRole.admin)), base_url="http://test") as client:
        response = await client.request(method, path, content=json.dumps(payload), headers=headers)
    assert response.status_code == expected
    assert service.call_count == int(expected == 200)


def test_manual_openapi_has_no_dangling_references():
    document = _app().openapi()

    def check(value):
        if isinstance(value, dict):
            reference = value.get("$ref")
            if reference and reference.startswith("#/"):
                target = document
                for segment in reference[2:].split("/"):
                    target = target[segment.replace("~1", "/").replace("~0", "~")]
                assert target is not None
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(document)
    review = document["paths"]["/ai-api/requests/{request_id}/review"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert "approved" in review["properties"]["status"]["enum"]


@pytest.mark.parametrize("content_length", [None, b"1", b"invalid"])
async def test_generation_stops_reading_at_first_overflow(monkeypatch, content_length):
    monkeypatch.setattr(ai_proxy.ai_api_settings, "ai_api_max_request_body_bytes", 10)
    reads = []

    async def receive():
        reads.append(1)
        assert len(reads) <= 2, "third chunk must not be read"
        return {"type": "http.request", "body": b"x" * 6, "more_body": True}

    headers = [(b"content-type", b"application/json")]
    if content_length is not None:
        headers.append((b"content-length", content_length))
    response = await ai_proxy._json_payload(
        Request({"type": "http", "headers": headers}, receive)
    )
    assert response.status_code == 413
    assert json.loads(response.body)["error"]["code"] == "request_too_large"
    assert len(reads) == 2


async def test_body_total_deadline_is_bounded(monkeypatch):
    monkeypatch.setattr(request_body, "REQUEST_BODY_TIMEOUT_SECONDS", 0.01)

    async def receive():
        await asyncio.Event().wait()

    response = await ai_proxy._json_payload(
        Request(
            {"type": "http", "headers": [(b"content-type", b"application/json")]},
            receive,
        )
    )
    assert response.status_code == 408
    assert json.loads(response.body)["error"]["code"] == "request_timeout"


def _models_request(query=b"", headers=()) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/models",
            "query_string": query,
            "headers": list(headers),
            "server": ("test", 80),
        }
    )


@pytest.fixture
def models_cache(monkeypatch):
    monkeypatch.setattr(relay, "_models_cache_loop", None)
    monkeypatch.setattr(relay, "_relay_stopping", False)


async def test_models_coalesces_bounds_waiters_and_preserves_metadata(
    monkeypatch, models_cache
):
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handle(request):
        calls.append(request)
        started.set()
        await release.wait()
        return httpx.Response(
            200,
            json={"object": "list", "data": [{"id": "m", "owned_by": "campus"}]},
            headers={"x-request-id": "fetch-only"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        requests = [
            asyncio.create_task(
                relay.list_models(_models_request(), user=SimpleNamespace(id="owner"))
            )
            for _ in range(50)
        ]
        await started.wait()
        await asyncio.sleep(0)
        assert len(calls) == 1
        variant = await relay.list_models(
            _models_request(b"nonce=1"), user=SimpleNamespace(id="owner")
        )
        assert variant.status_code == 503
        requests[0].cancel()
        release.set()
        responses = await asyncio.gather(*requests, return_exceptions=True)
        assert (
            sum(
                isinstance(item, JSONResponse) and item.status_code == 503
                for item in responses
            )
            == 10
        )
        hit = await relay.list_models(
            _models_request(), user=SimpleNamespace(id="another")
        )
        assert len(calls) == 1
        assert "x-request-id" not in hit.headers
        data = json.loads(hit.body)
        assert data["object"] == "list"
        assert data["data"][0]["owned_by"] == "campus"
        assert isinstance(data["data"][0]["created"], int)
        assert relay._get_models_cache().waiters == 0


async def test_models_context_is_not_mixed_and_ttl_refreshes(monkeypatch, models_cache):
    calls = []

    async def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={"data": [{"id": request.headers.get("openai-project", "default")}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        first = await relay.list_models(
            _models_request(), user=SimpleNamespace(id="owner")
        )
        other = await relay.list_models(
            _models_request(b"filter=x", [(b"openai-project", b"project")]),
            user=SimpleNamespace(id="owner"),
        )
        assert json.loads(first.body)["data"][0]["id"] == "default"
        assert json.loads(other.body)["data"][0]["id"] == "project"
        assert calls[-1].url.query == b"filter=x"
        assert (
            calls[-1].headers["authorization"]
            == f"Bearer {relay.ai_api_settings.ai_api_api_key}"
        )
        cache = relay._get_models_cache()
        cache.until = 0
        await relay.list_models(
            _models_request(b"filter=x", [(b"openai-project", b"project")]),
            user=SimpleNamespace(id="owner"),
        )
        assert len(calls) == 3


async def test_models_errors_are_sanitized_and_expire(monkeypatch, models_cache):
    calls = []

    async def handle(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "synthetic-secret-marker"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        request = _models_request()
        first = await relay.list_models(request, user=SimpleNamespace(id="owner"))
        second = await relay.list_models(request, user=SimpleNamespace(id="owner"))
        assert first.status_code == second.status_code == 401 and len(calls) == 1
        assert b"synthetic-secret-marker" not in first.body
        assert 0 < relay._get_models_cache().until - relay.time.monotonic() <= 5
        relay._get_models_cache().until = 0
        await relay.list_models(request, user=SimpleNamespace(id="owner"))
        assert len(calls) == 2


async def test_models_route_has_separate_stable_budget(monkeypatch):
    seen = []

    async def no_redis():
        return None

    async def check(redis, **kwargs):
        seen.append(kwargs)
        return True, {}

    async def list_models(request, *, user):
        return JSONResponse({"data": []})

    monkeypatch.setattr(ai_proxy, "get_redis", no_redis)
    monkeypatch.setattr(ai_proxy, "check_rate_limit_by_key", check)
    monkeypatch.setattr(relay, "list_models", list_models)
    await ai_proxy.list_models(
        _models_request(),
        (SimpleNamespace(id="owner"), SimpleNamespace(request_id="approved")),
    )
    assert seen == [
        {
            "key": "ai-models:approved",
            "limit": 30,
            "window_seconds": 60,
            "scope": "ai-proxy-models",
        }
    ]


async def test_models_fetch_deadline_releases_singleflight(monkeypatch, models_cache):
    original_await_before = relay.await_before

    async def short_deadline(awaitable, deadline, reason):
        return await original_await_before(awaitable, relay.time.monotonic() + 0.01, reason)

    async def handle(request):
        await asyncio.Event().wait()

    monkeypatch.setattr(relay, "await_before", short_deadline)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        response = await relay.list_models(_models_request(), user=SimpleNamespace(id="owner"))
    assert response.status_code == 503
    assert relay._get_models_cache().task.done()
    assert relay._get_models_cache().waiters == 0


async def test_shutdown_cancels_models_fetch_and_clears_cache(monkeypatch, models_cache):
    started = asyncio.Event()

    async def handle(request):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(relay, "_admission_queue", None)
    monkeypatch.setattr(relay, "_catalogue_task", None)
    monkeypatch.setattr(relay, "_relay_http_client", None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        waiting = asyncio.create_task(relay.list_models(_models_request(), user=SimpleNamespace(id="owner")))
        await started.wait()
        cache = relay._get_models_cache()
        await relay.close_relay_runtime()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert cache.task is None and cache.response is None and cache.waiters == 0
        response = await relay.list_models(_models_request(), user=SimpleNamespace(id="owner"))
        assert response.status_code == 503
