"""infrastructure/ai/litellm_runtime.probe：LiteLLM 健康探測（MockTransport，無外部依賴）。"""

from __future__ import annotations

import httpx

from app.infrastructure.ai import litellm_runtime


def _transport(*, live: bool = True, health_status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health/liveliness":
            return httpx.Response(200 if live else 503, json="I'm alive!")
        if path == "/health/readiness":
            return httpx.Response(200, content=b"not json")
        assert request.headers["authorization"] == "Bearer sk-runtime"
        if path == "/model/info":
            return httpx.Response(
                200,
                json={"data": [{"model_name": "chat-a", "model_info": {"id": "id-a"}}, {"model_name": ""}]},
            )
        if path == "/health":
            return httpx.Response(
                health_status,
                json={
                    "healthy_endpoints": [{"model_id": "id-a"}, {"model_id": "id-a"}],
                    "unhealthy_endpoints": "unexpected",
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_probe_counts_healthy_deployments_and_tolerates_bad_payloads() -> None:
    result = litellm_runtime.probe("http://litellm:4000", "sk-runtime", transport=_transport())
    assert result["reachable"] is True
    assert result["db"] is None  # readiness 不是 JSON 時不當成已連線
    assert result["models"] == ["chat-a"]
    assert result["deployments"] == {"chat-a": {"healthy": 2, "unhealthy": 0}}


def test_probe_leaves_deployments_pending_when_health_fails() -> None:
    result = litellm_runtime.probe(
        "http://litellm:4000", "sk-runtime", transport=_transport(health_status=500)
    )
    assert result["reachable"] is True
    assert result["deployments"] is None


def test_probe_reports_error_when_gateway_is_down() -> None:
    result = litellm_runtime.probe(
        "http://litellm:4000", "sk-runtime", transport=_transport(live=False)
    )
    assert result == {
        "reachable": False,
        "db": None,
        "models": None,
        "deployments": None,
        "error": result["error"],
    }
    assert result["error"].startswith("HTTPStatusError")
