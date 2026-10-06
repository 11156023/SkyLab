from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.routes import ai_proxy
from app.core.config import settings
from app.core.db import engine
from app.features.ai.config import settings as ai_api_settings
from app.models import AIAPICredential, AIAPIUsage
from app.repositories import user as user_repo
from app.schemas import UserCreate
from app.services.llm_gateway import relay_service
from tests.utils.user import user_authentication_headers
from tests.utils.utils import random_lower_string


def _issue_ai_api_key(
    *,
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    rate_limit: int = 20,
) -> tuple[str, uuid.UUID]:
    email = f"ai-proxy-{uuid.uuid4().hex[:10]}@example.com"
    password = random_lower_string()
    user_repo.create_user(
        session=db,
        user_create=UserCreate(email=email, password=password),
    )
    db.commit()
    user_headers = user_authentication_headers(
        client=client,
        email=email,
        password=password,
    )

    request_response = client.post(
        f"{settings.API_V1_STR}/ai-api/requests",
        headers=user_headers,
        json={
            "purpose": "Exercise the public AI proxy route contract in CI.",
            "api_key_name": "ci-route-test",
            "duration": "30d",
        },
    )
    assert request_response.status_code == 200
    request_id = request_response.json()["id"]

    review_response = client.post(
        f"{settings.API_V1_STR}/ai-api/requests/{request_id}/review",
        headers=superuser_token_headers,
        json={"status": "approved"},
    )
    assert review_response.status_code == 200

    credentials_response = client.get(
        f"{settings.API_V1_STR}/ai-api/credentials/my",
        headers=user_headers,
    )
    assert credentials_response.status_code == 200
    credential_id = uuid.UUID(credentials_response.json()["data"][0]["id"])
    credential = db.get(AIAPICredential, credential_id)
    assert credential is not None
    credential.rate_limit = rate_limit
    db.add(credential)
    db.commit()

    detail_response = client.get(
        f"{settings.API_V1_STR}/ai-api/credentials/{credential_id}",
        headers=user_headers,
    )
    assert detail_response.status_code == 200
    api_key = detail_response.json()["api_key"]
    assert api_key.startswith("ccai_")
    return api_key, credential_id


def _fake_upstream_client(captured: dict[str, Any]) -> type:
    class FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def get(
            self,
            url: str,
            *,
            headers: dict[str, str],
            timeout: float | None = None,
        ) -> httpx.Response:
            request = httpx.Request("GET", url, headers=headers)
            captured["models_request"] = request
            return httpx.Response(
                200,
                json={"object": "list", "data": [{"id": "node-model-a"}]},
                headers={"content-type": "application/json"},
                request=request,
            )

        def build_request(self, method: str, url: str, **kwargs: Any) -> httpx.Request:
            request = httpx.Request(method, url, **kwargs)
            captured.setdefault("chat_requests", []).append(request)
            return request

        async def send(
            self, request: httpx.Request, *, stream: bool
        ) -> httpx.Response:
            # httpx stream=True 只表示 headers-first transport，與 public SSE 選項無關。
            assert stream is True
            payload = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-ci",
                    "object": "chat.completion",
                    "model": payload["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "OK"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 1,
                        "total_tokens": 3,
                    },
                },
                headers={
                    "content-type": "application/json",
                    "x-request-id": "upstream-ci",
                },
                request=request,
            )

        async def aclose(self) -> None:
            return None

    return FakeClient


def test_ai_proxy_rejects_an_invalid_user_key(client: TestClient) -> None:
    response = client.get(
        f"{settings.API_V1_STR}/ai-proxy/models",
        headers={"Authorization": "Bearer ccai_invalid"},
    )
    assert response.status_code == 401


def test_ai_proxy_models_uses_the_restricted_upstream_identity(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key, _credential_id = _issue_ai_api_key(
        client=client,
        db=db,
        superuser_token_headers=superuser_token_headers,
    )
    captured: dict[str, Any] = {}
    fake_client = _fake_upstream_client(captured)()
    monkeypatch.setattr(
        relay_service, "_get_relay_http_client", lambda: fake_client
    )
    monkeypatch.setattr(ai_api_settings, "ai_api_base_url", "http://litellm.test:4000")
    monkeypatch.setattr(ai_api_settings, "ai_api_api_key", "restricted-service-key")

    response = client.get(
        f"{settings.API_V1_STR}/ai-proxy/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "node-model-a"
    assert isinstance(response.json()["data"][0]["created"], int)
    request = captured["models_request"]
    assert isinstance(request, httpx.Request)
    assert str(request.url) == "http://litellm.test:4000/v1/models"
    assert request.headers["authorization"] == "Bearer restricted-service-key"
    assert api_key not in request.headers.values()


def test_ai_proxy_chat_relays_and_records_usage(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key, credential_id = _issue_ai_api_key(
        client=client,
        db=db,
        superuser_token_headers=superuser_token_headers,
    )
    captured: dict[str, Any] = {}
    fake_client = _fake_upstream_client(captured)()
    monkeypatch.setattr(
        relay_service, "_get_relay_http_client", lambda: fake_client
    )
    monkeypatch.setattr(ai_api_settings, "ai_api_base_url", "http://litellm.test:4000")
    monkeypatch.setattr(ai_api_settings, "ai_api_api_key", "restricted-service-key")

    response = client.post(
        f"{settings.API_V1_STR}/ai-proxy/chat/completions?include=usage",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "x-request-id": "campus-ci",
        },
        json={
            "model": "node-model-a",
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "max_tokens": 8,
        },
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "OK"
    outbound = captured["chat_requests"][0]
    assert isinstance(outbound, httpx.Request)
    assert str(outbound.url) == (
        "http://litellm.test:4000/v1/chat/completions?include=usage"
    )
    assert outbound.headers["authorization"] == "Bearer restricted-service-key"
    assert outbound.headers["x-request-id"] == "campus-ci"
    assert api_key.encode() not in outbound.content

    with Session(engine) as usage_session:
        usage_rows = usage_session.exec(
            select(AIAPIUsage).where(AIAPIUsage.credential_id == credential_id)
        ).all()
    assert len(usage_rows) == 1
    assert usage_rows[0].model_name == "node-model-a"
    assert usage_rows[0].call_type == "chat_completion"
    assert usage_rows[0].input_tokens == 2
    assert usage_rows[0].output_tokens == 1


def test_ai_proxy_chat_enforces_the_credential_rate_limit(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key, credential_id = _issue_ai_api_key(
        client=client,
        db=db,
        superuser_token_headers=superuser_token_headers,
        rate_limit=1,
    )
    captured: dict[str, Any] = {}
    fake_client = _fake_upstream_client(captured)()
    monkeypatch.setattr(
        relay_service, "_get_relay_http_client", lambda: fake_client
    )
    counts: dict[str, int] = {}
    checked_credentials: list[tuple[str, int]] = []

    async def fake_get_redis() -> object:
        return object()

    async def fake_check_rate_limit(
        *,
        redis: object,
        request_id: str,
        legacy_credential_ids: tuple[str, ...],
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, dict[str, Any]]:
        del redis
        checked_credentials.append((request_id, limit))
        current = counts.get(request_id, 0)
        allowed = current < limit
        if allowed:
            current += 1
            counts[request_id] = current
        return allowed, {
            "limit": limit,
            "current": current,
            "remaining": max(0, limit - current),
            "reset_at": datetime.now(timezone.utc),
            "window_seconds": window_seconds,
        }

    monkeypatch.setattr(ai_proxy, "get_redis", fake_get_redis)
    monkeypatch.setattr(
        ai_proxy,
        "check_rate_limit_sliding_window",
        fake_check_rate_limit,
    )

    request_kwargs: dict[str, Any] = {
        "headers": {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        "json": {
            "model": "node-model-a",
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "max_tokens": 8,
        },
    }
    first = client.post(
        f"{settings.API_V1_STR}/ai-proxy/chat/completions",
        **request_kwargs,
    )
    second = client.post(
        f"{settings.API_V1_STR}/ai-proxy/chat/completions",
        **request_kwargs,
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"]["error"] == "rate_limit_exceeded"
    assert len(captured["chat_requests"]) == 1
    request_id = db.get(AIAPICredential, credential_id).request_id
    assert checked_credentials == [(str(request_id), 1), (str(request_id), 1)]
