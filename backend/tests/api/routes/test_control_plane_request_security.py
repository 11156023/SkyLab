"""Security contracts for bounded control-plane JSON parsing."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.deps import get_current_user, get_db
from app.api.deps import rate_limit as rate_limit_deps
from app.api.request_body import parse_limited_json
from app.api.routes import ai_api as ai_api_routes
from app.api.routes import users as user_routes
from app.models import User, UserRole
from app.schemas import AIAPIRequestCreate


def _request(body: bytes, *, content_type: str = "application/json") -> Request:
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-type", content_type.encode())],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_chunked_body_over_limit_is_rejected_before_validation() -> None:
    request = _request(b'{}' + b" " * 32)
    with pytest.raises(HTTPException) as exc_info:
        await parse_limited_json(request, AIAPIRequestCreate, max_bytes=16)
    assert getattr(exc_info.value, "status_code", None) == 413


@pytest.mark.asyncio
async def test_invalid_json_keeps_fastapi_body_validation_location() -> None:
    with pytest.raises(RequestValidationError) as exc_info:
        await parse_limited_json(_request(b"{"), AIAPIRequestCreate)
    assert exc_info.value.errors()[0]["loc"][0] == "body"


def _no_db() -> object:
    return object()


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(ai_api_routes.router)
    return app


def test_unauthenticated_large_body_is_rejected_before_body_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = False

    async def should_not_parse(*_args: object, **_kwargs: object) -> None:
        nonlocal parsed
        parsed = True
        raise AssertionError("body parser ran before authentication")

    monkeypatch.setattr(ai_api_routes, "parse_limited_json", should_not_parse)
    client = TestClient(_app(), raise_server_exceptions=False)
    try:
        response = client.post("/ai-api/requests", content=b"x" * (2 * 1024 * 1024))
    finally:
        client.close()
    assert response.status_code == 401
    assert parsed is False


def test_authenticated_oversized_body_returns_413_before_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    user = User(
        email="bounded-body@example.com",
        hashed_password="unused",
        role=UserRole.student,
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _no_db
    app.dependency_overrides[
        ai_api_routes._AI_API_REQUEST_RATE_LIMIT.dependency
    ] = lambda: None
    called = False

    def should_not_create(**_kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("service ran for oversized request")

    monkeypatch.setattr(
        ai_api_routes.ai_gateway_service, "create_request", should_not_create
    )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.post(
            "/ai-api/requests",
            content=b"x" * (17 * 1024),
            headers={"content-type": "application/json"},
        )
    finally:
        client.close()
    assert response.status_code == 413
    assert called is False


def test_authenticated_non_utf8_json_returns_serializable_422() -> None:
    app = _app()
    user = User(
        email="invalid-json@example.com",
        hashed_password="unused",
        role=UserRole.student,
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _no_db
    app.dependency_overrides[
        ai_api_routes._AI_API_REQUEST_RATE_LIMIT.dependency
    ] = lambda: None
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.post(
            "/ai-api/requests",
            content=b"\xff",
            headers={"content-type": "application/json"},
        )
    finally:
        client.close()
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][0] == "body"


def test_manual_body_parser_preserves_openapi_request_schema() -> None:
    document = _app().openapi()
    operation = document["paths"]["/ai-api/requests"]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert schema["properties"]["purpose"]["type"] == "string"


def test_signup_limits_and_turnstile_run_in_safe_order() -> None:
    route = next(
        route
        for route in user_routes.router.routes
        if getattr(route, "path", None) == "/users/signup"
    )
    calls = [dependency.call for dependency in route.dependant.dependencies]
    assert calls[:3] == [
        user_routes._SIGNUP_RATE_LIMIT.dependency,
        user_routes._SIGNUP_TURNSTILE.dependency,
        user_routes._SIGNUP_CAPACITY_LIMIT.dependency,
    ]


@pytest.mark.asyncio
async def test_signup_capacity_uses_subnet_and_global_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys: list[str] = []

    async def no_redis() -> None:
        return None

    async def allow(
        _redis: object,
        *,
        key: str,
        limit: int,
        window_seconds: int,
        scope: str,
    ) -> tuple[bool, dict[str, int]]:
        keys.append(key)
        assert limit > 0
        assert window_seconds == 60
        assert scope == "signup"
        return True, {"window_seconds": window_seconds}

    monkeypatch.setattr(rate_limit_deps, "get_redis", no_redis)
    monkeypatch.setattr(rate_limit_deps, "check_rate_limit_by_key", allow)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/users/signup",
            "headers": [],
            "client": ("203.0.113.77", 12345),
        }
    )
    await user_routes._SIGNUP_CAPACITY_LIMIT.dependency(request)
    assert keys == ["network:signup:203.0.113.0/24", "global:signup"]


@pytest.mark.asyncio
async def test_signup_global_capacity_returns_429_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_redis() -> None:
        return None

    async def block_global(
        _redis: object,
        *,
        key: str,
        **_kwargs: object,
    ) -> tuple[bool, dict[str, int]]:
        return key != "global:signup", {"window_seconds": 60}

    monkeypatch.setattr(rate_limit_deps, "get_redis", no_redis)
    monkeypatch.setattr(rate_limit_deps, "check_rate_limit_by_key", block_global)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/users/signup",
            "headers": [],
            "client": ("2001:db8::1234", 12345),
        }
    )
    with pytest.raises(HTTPException) as exc_info:
        await user_routes._SIGNUP_CAPACITY_LIMIT.dependency(request)
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "60"}


def test_signup_oversized_body_returns_413_before_password_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(user_routes.router)
    app.dependency_overrides[get_db] = _no_db
    app.dependency_overrides[user_routes._SIGNUP_RATE_LIMIT.dependency] = lambda: None
    app.dependency_overrides[user_routes._SIGNUP_TURNSTILE.dependency] = lambda: None
    app.dependency_overrides[
        user_routes._SIGNUP_CAPACITY_LIMIT.dependency
    ] = lambda: None
    called = False

    def should_not_register(**_kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("password hashing ran for oversized request")

    monkeypatch.setattr(user_routes.user_service, "register_user", should_not_register)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.post(
            "/users/signup",
            content=b"x" * (17 * 1024),
            headers={"content-type": "application/json"},
        )
    finally:
        client.close()
    assert response.status_code == 413
    assert called is False


def test_signup_openapi_keeps_request_schema() -> None:
    app = FastAPI()
    app.include_router(user_routes.router)
    operation = app.openapi()["paths"]["/users/signup"]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert schema["properties"]["email"]["format"] == "email"
