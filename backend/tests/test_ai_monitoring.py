import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.api.routes import ai_monitoring
from app.models import AIAPIUsage, AITemplateCallLog
from app.services.llm_gateway import ai_gateway_service


def test_monitoring_summary_does_not_treat_empty_range_as_success() -> None:
    summary = ai_gateway_service._monitoring_summary(
        {
            "proxy_total_calls": 0,
            "template_total_calls": 0,
            "successful_calls": 0,
            "proxy_total_input_tokens": 0,
            "proxy_total_output_tokens": 0,
            "template_total_input_tokens": 0,
            "template_total_output_tokens": 0,
            "active_users": 0,
            "avg_latency_ms": 0,
        }
    )

    assert summary["total_calls"] == 0
    assert summary["failed_calls"] == 0
    assert summary["error_rate"] is None


def test_monitoring_overview_aggregates_total_tokens_per_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stats = {
        "proxy_total_calls": 2,
        "proxy_total_input_tokens": 10,
        "proxy_total_output_tokens": 20,
        "template_total_calls": 3,
        "template_total_input_tokens": 30,
        "template_total_output_tokens": 40,
        "successful_calls": 5,
        "active_users": 1,
        "avg_latency_ms": 100,
    }

    monkeypatch.setattr(
        ai_gateway_service,
        "get_monitoring_stats",
        lambda **_kwargs: stats,
    )
    monkeypatch.setattr(
        ai_gateway_service,
        "_monitoring_bucket_rows",
        lambda **_kwargs: [],
    )

    def model_rows(**kwargs):
        if kwargs["model"] is AIAPIUsage:
            return [("shared-model", 2, 2, 10, 20, 2, 200)]
        assert kwargs["model"] is AITemplateCallLog
        return [("shared-model", 3, 3, 30, 40, 3, 300)]

    monkeypatch.setattr(ai_gateway_service, "_monitoring_model_rows", model_rows)

    overview = ai_gateway_service.get_monitoring_overview(session=object(), compare=False)

    assert overview["model_breakdown"] == [
        {
            "model_name": "shared-model",
            "total_calls": 5,
            "total_tokens": 100,
            "failed_calls": 0,
            "error_rate": 0.0,
            "avg_latency_ms": 100,
        }
    ]


def test_monitoring_overview_aggregates_total_tokens_per_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stats = {
        "proxy_total_calls": 2,
        "template_total_calls": 1,
        "successful_calls": 2,
        "active_users": 1,
        "avg_latency_ms": 100,
    }
    bucket_start = "2026-09-11 10:00:00"

    monkeypatch.setattr(
        ai_gateway_service,
        "get_monitoring_stats",
        lambda **_kwargs: stats,
    )

    def bucket_rows(**kwargs):
        if kwargs["model"] is AIAPIUsage:
            return [(bucket_start, 2, 1, 10, 20, 2, 200)]
        assert kwargs["model"] is AITemplateCallLog
        return [(bucket_start, 1, 1, 30, 40, 1, 100)]

    monkeypatch.setattr(ai_gateway_service, "_monitoring_bucket_rows", bucket_rows)
    monkeypatch.setattr(ai_gateway_service, "_monitoring_model_rows", lambda **_kwargs: [])

    overview = ai_gateway_service.get_monitoring_overview(session=object(), compare=False)

    assert len(overview["series"]) == 1
    assert overview["series"][0]["total_tokens"] == 100
    assert overview["series"][0]["failed_calls"] == 1


@pytest.mark.asyncio
async def test_runtime_snapshot_normalizes_model_health_without_leaking_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "/health/liveliness": httpx.Response(200, json="I'm alive!"),
        "/health/readiness": httpx.Response(200, json={"status": "healthy"}),
        "/health": httpx.Response(
            200,
            json={
                "healthy_endpoints": [{"model_name": "public-model"}],
                "unhealthy_endpoints": [{"model_info": {"id": "broken-model"}}],
            },
        ),
        "/v1/models": httpx.Response(
            200,
            json={"data": [{"id": "public-model"}, {"id": "broken-model"}, {"id": "unknown-model"}]},
        ),
    }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str, **_kwargs):
            return responses[next(path for path in responses if url.endswith(path))]

    monkeypatch.setattr(
        ai_monitoring,
        "ai_api_settings",
        SimpleNamespace(
            litellm_runtime_api_key="test-observation-key",
            litellm_runtime_base_url="http://litellm.internal",
        ),
    )
    monkeypatch.setattr(ai_monitoring.httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    snapshot = await ai_monitoring.get_litellm_runtime_snapshot(object())

    assert snapshot["gateway"]["status"] == "available"
    assert snapshot["liveliness"] is True and snapshot["readiness"] is True
    assert snapshot["summary"] == {"online": 1, "degraded": 0, "offline": 1, "unknown": 1}
    assert [(model["name"], model["status"]) for model in snapshot["models"]] == [
        ("broken-model", "offline"),
        ("public-model", "online"),
        ("unknown-model", "unknown"),
    ]
    assert "test-observation-key" not in str(snapshot)


@pytest.mark.asyncio
async def test_runtime_snapshot_fails_closed_without_observation_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ai_monitoring,
        "ai_api_settings",
        SimpleNamespace(
            litellm_runtime_api_key="",
            litellm_runtime_base_url="http://litellm.internal",
        ),
    )

    with pytest.raises(HTTPException) as error:
        await ai_monitoring.get_litellm_runtime_snapshot(object())

    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_runtime_snapshot_runs_independent_probes_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    max_active = 0

    responses = {
        "/health/liveliness": httpx.Response(200, json="I'm alive!"),
        "/health/readiness": httpx.Response(200, json={"status": "healthy"}),
        "/health": httpx.Response(200, json={"healthy_endpoints": []}),
        "/v1/models": httpx.Response(200, json={"data": []}),
    }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str, **_kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(0.01)
                return responses[next(path for path in responses if url.endswith(path))]
            finally:
                active -= 1

    monkeypatch.setattr(
        ai_monitoring,
        "ai_api_settings",
        SimpleNamespace(
            litellm_runtime_api_key="test-observation-key",
            litellm_runtime_base_url="http://litellm.internal",
        ),
    )
    monkeypatch.setattr(ai_monitoring.httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    snapshot = await ai_monitoring.get_litellm_runtime_snapshot(object())

    assert snapshot["gateway"]["status"] == "available"
    assert max_active == 4
