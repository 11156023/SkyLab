"""Focused route contracts for AI API secret and async boundaries."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import ANY

import pytest
from fastapi import HTTPException, Response

from app.api.routes import ai_api, ai_proxy
from app.services.llm_gateway import ai_gateway_service


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """Pure unit tests; no external database is required."""


def test_rotate_secret_response_is_not_cacheable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = object()
    response = Response()
    monkeypatch.setattr(
        ai_gateway_service,
        "rotate_credential",
        lambda **_kwargs: expected,
    )

    result = ai_api.rotate_my_ai_api_credential(
        credential_id=uuid.uuid4(),
        response=response,
        session=object(),  # type: ignore[arg-type]
        current_user=object(),  # type: ignore[arg-type]
    )

    assert result is expected
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_public_usage_aggregation_runs_in_threadpool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    expected = {"total_requests": 3}
    calls: list[tuple[object, dict[str, object]]] = []

    def aggregate(**_kwargs: object) -> dict[str, int]:
        raise AssertionError("aggregation must be delegated to the threadpool")

    async def run_in_threadpool(function: object, **kwargs: object) -> dict[str, int]:
        calls.append((function, kwargs))
        return expected

    monkeypatch.setattr(ai_gateway_service, "get_user_usage_stats", aggregate)
    monkeypatch.setattr(
        ai_gateway_service,
        "default_usage_window",
        lambda start, end: (start, end),
    )
    monkeypatch.setattr(ai_proxy, "run_in_threadpool", run_in_threadpool)

    result = await ai_proxy.get_my_usage_stats(
        user_and_credential=cast(Any, (SimpleNamespace(id=user_id), object())),
        session=object(),  # type: ignore[arg-type]
    )

    assert result == expected
    assert calls == [
        (
            aggregate,
            {
                "session": ANY,
                "user_id": user_id,
                "start_date": None,
                "end_date": None,
            },
        )
    ]


@pytest.mark.asyncio
async def test_ai_proxy_rate_limit_sets_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def get_redis() -> None:
        return None

    async def reject(**_kwargs: object) -> tuple[bool, dict[str, object]]:
        return False, {
            "limit": 20,
            "current": 21,
            "window_seconds": 60,
            "reset_at": datetime.now(timezone.utc),
        }

    monkeypatch.setattr(ai_proxy, "get_redis", get_redis)
    monkeypatch.setattr(ai_proxy, "check_rate_limit_sliding_window", reject)
    credential = SimpleNamespace(
        request_id=uuid.uuid4(),
        rate_limit=20,
        _rate_limit_legacy_ids=(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await ai_proxy._enforce_rate_limit(credential=credential)

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "60"}
