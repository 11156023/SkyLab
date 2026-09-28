"""AI 模組監控：LiteLLM 健康判定與探測、平台健康元件、skylab_ai_* 指標。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.ai import monitoring as ai_feature_monitoring
from app.core import metrics as metrics_module
from app.services.llm_gateway import relay_service
from app.services.monitoring import ai_metrics, health_policy, system_health_service


def _sample(name: str, **labels: str) -> float:
    value = metrics_module.REGISTRY.get_sample_value(name, labels)
    return float(value or 0.0)


@pytest.fixture(autouse=True)
def _reset_state() -> Any:
    ai_metrics.reset_known_models()
    system_health_service.reset_ai_cache()
    yield
    ai_metrics.reset_known_models()
    system_health_service.reset_ai_cache()


def _by_name(components: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {component["name"]: component for component in components}


# ─── health_policy.ai_components ──────────────────────────────────────────


def test_unreachable_gateway_is_single_down_component_with_alert() -> None:
    components = health_policy.ai_components({"reachable": False, "error": "timeout"})
    assert len(components) == 1
    gateway = components[0]
    assert (gateway["name"], gateway["status"], gateway["detail"]) == ("ai_gateway", "down", "timeout")
    assert "AI API 與內建 AI 功能都無法使用" in gateway["alert_message"]


def test_disconnected_database_marks_gateway_down() -> None:
    gateway = _by_name(
        health_policy.ai_components({"reachable": True, "db": "Not connected", "models": []})
    )["ai_gateway"]
    assert gateway["status"] == "down"
    assert "Not connected" in gateway["detail"]
    assert "API 金鑰驗證與用量紀錄會失敗" in gateway["alert_message"]


def test_model_statuses_follow_background_health_checks() -> None:
    components = health_policy.ai_components(
        {
            "reachable": True,
            "db": "connected",
            "models": ["chat-a", "chat-b", "chat-c", "chat-d"],
            "deployments": {
                "chat-a": {"healthy": 1, "unhealthy": 0},
                "chat-b": {"healthy": 1, "unhealthy": 1},
                "chat-c": {"healthy": 0, "unhealthy": 2},
            },
        }
    )
    assert [c["name"] for c in components] == [
        "ai_gateway", "ai_model:chat-a", "ai_model:chat-b", "ai_model:chat-c", "ai_model:chat-d",
    ]
    by_name = _by_name(components)
    assert by_name["ai_gateway"]["status"] == "ok"
    assert by_name["ai_model:chat-a"]["status"] == "ok"
    assert by_name["ai_model:chat-b"]["status"] == "attention"
    assert by_name["ai_model:chat-b"]["detail"] == "1/2 個部署異常"
    assert by_name["ai_model:chat-c"]["status"] == "down"
    assert "chat-c" in by_name["ai_model:chat-c"]["alert_message"]
    assert by_name["ai_model:chat-d"]["status"] == "pending"
    assert by_name["ai_model:chat-a"]["label"] == "LLM · chat-a"


def test_models_are_pending_when_health_endpoint_is_unavailable() -> None:
    components = health_policy.ai_components(
        {"reachable": True, "db": "connected", "models": ["chat-a"], "deployments": None}
    )
    assert _by_name(components)["ai_model:chat-a"]["status"] == "pending"


def test_pending_model_neither_degrades_overall_nor_alerts() -> None:
    components = [
        {"name": "database", "status": "ok"},
        {"name": "ai_gateway", "status": "ok"},
        {"name": "ai_model:chat-a", "status": "pending", "label": "LLM · chat-a"},
    ]
    assert health_policy.overall_status(components, [], []) == "ok"
    assert health_policy.build_findings(components, [], []) == []


def test_down_model_degrades_overall_and_raises_its_own_finding() -> None:
    components = health_policy.ai_components(
        {
            "reachable": True,
            "db": "connected",
            "models": ["chat-a"],
            "deployments": {"chat-a": {"healthy": 0, "unhealthy": 1}},
        }
    )
    assert health_policy.overall_status(components, [], []) == "degraded"
    findings = health_policy.build_findings(components, [], [])
    assert [f.target for f in findings] == ["component:ai_model:chat-a"]
    assert findings[0].message.startswith("AI 模型 chat-a 無法使用")


# ─── system_health_service._probe_ai / check_ai ───────────────────────────


def _litellm_transport(*, live: bool = True, info_status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health/liveliness":
            return httpx.Response(200 if live else 503, json="I'm alive!")
        if path == "/health/readiness":
            return httpx.Response(200, json={"status": "healthy", "db": "connected"})
        assert request.headers["authorization"] == "Bearer sk-runtime"
        if path == "/model/info":
            return httpx.Response(
                info_status,
                json={
                    "data": [
                        {"model_name": "chat-a", "model_info": {"id": "id-a"}, "litellm_params": {"api_base": "http://10.0.0.5:8103/v1"}},
                        {"model_name": "chat-b", "model_info": {"id": "id-b"}},
                    ]
                },
            )
        if path == "/health":
            return httpx.Response(
                200,
                json={
                    "healthy_endpoints": [{"model": "hosted_vllm/a", "model_id": "id-a"}],
                    "unhealthy_endpoints": [
                        {"model": "hosted_vllm/b", "model_id": "id-b"},
                        {"model": "hosted_vllm/secret-upstream", "model_id": "id-unmapped"},
                    ],
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_probe_maps_deployment_ids_back_to_public_aliases() -> None:
    probe = system_health_service._probe_ai(
        "http://litellm:4000", "sk-runtime", transport=_litellm_transport()
    )
    assert probe["reachable"] is True
    assert probe["db"] == "connected"
    assert probe["models"] == ["chat-a", "chat-b"]
    # 對不回 alias 的部署直接略過，不讓上游名稱出現在健康頁
    assert probe["deployments"] == {
        "chat-a": {"healthy": 1, "unhealthy": 0},
        "chat-b": {"healthy": 0, "unhealthy": 1},
    }
    assert "10.0.0.5" not in repr(probe)


def test_probe_reports_unreachable_gateway() -> None:
    probe = system_health_service._probe_ai(
        "http://litellm:4000", "sk-runtime", transport=_litellm_transport(live=False)
    )
    assert probe["reachable"] is False
    assert probe["error"]


def test_probe_without_model_info_leaves_models_pending() -> None:
    probe = system_health_service._probe_ai(
        "http://litellm:4000", "sk-runtime", transport=_litellm_transport(info_status=401)
    )
    assert probe["models"] is None
    assert probe["deployments"] == {}


def test_check_ai_disabled_without_runtime_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.features.ai.config import settings as ai_settings

    monkeypatch.setattr(ai_settings, "litellm_runtime_api_key", None)
    monkeypatch.setattr(system_health_service, "_probe_ai", lambda *a, **k: pytest.fail("must not probe"))
    [component] = system_health_service.check_ai(use_cache=False)
    assert (component["name"], component["status"]) == ("ai_gateway", "disabled")


def test_check_ai_uses_policy_caches_and_teaches_model_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.features.ai.config import settings as ai_settings

    monkeypatch.setattr(ai_settings, "litellm_runtime_api_key", "sk-runtime")
    monkeypatch.setattr(ai_settings, "litellm_runtime_base_url", "http://litellm:4000/")
    calls: list[tuple[str, str]] = []

    def probe(base_url: str, api_key: str, **_: Any) -> dict[str, Any]:
        calls.append((base_url, api_key))
        return {
            "reachable": True,
            "db": "connected",
            "models": ["chat-a"],
            "deployments": {"chat-a": {"healthy": 0, "unhealthy": 1}},
        }

    monkeypatch.setattr(system_health_service, "_probe_ai", probe)
    components = system_health_service.check_ai()
    assert calls == [("http://litellm:4000", "sk-runtime")]
    by_name = _by_name(components)
    assert by_name["ai_gateway"]["latency_ms"] is not None
    assert by_name["ai_model:chat-a"]["status"] == "down"
    assert "alert_message" in by_name["ai_model:chat-a"]
    assert system_health_service.check_ai() == components  # cached
    assert len(calls) == 1
    assert system_health_service.cached_ai_components() == components
    # 探測查到的模型：之後即使呼叫失敗也用真名當 label
    assert ai_metrics.model_label("chat-a", served=False) == "chat-a"


def test_check_ai_probe_crash_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.features.ai.config import settings as ai_settings

    monkeypatch.setattr(ai_settings, "litellm_runtime_api_key", "sk-runtime")

    def boom(*_: Any, **__: Any) -> dict[str, Any]:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(system_health_service, "_probe_ai", boom)
    [component] = system_health_service.check_ai(use_cache=False)
    assert component["status"] == "down"
    assert "ConnectError" in component["detail"]


def test_collect_metrics_exports_ai_components_but_skips_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = {"latency_ms": None, "detail": None, "label": "x"}
    monkeypatch.setattr(system_health_service, "check_database", lambda: {**ok, "name": "database", "status": "down"})
    monkeypatch.setattr(system_health_service, "check_redis", lambda: {**ok, "name": "redis", "status": "disabled"})
    monkeypatch.setattr(system_health_service, "check_worker", lambda **_: {**ok, "name": "worker", "status": "disabled"})
    monkeypatch.setattr(system_health_service, "cached_pve_components", lambda: [])
    monkeypatch.setattr(system_health_service, "cached_gateway_components", lambda: [])
    monkeypatch.setattr(
        system_health_service,
        "cached_ai_components",
        lambda: [
            {**ok, "name": "ai_gateway", "status": "ok", "latency_ms": 12.0},
            {**ok, "name": "ai_model:up", "status": "ok"},
            {**ok, "name": "ai_model:down", "status": "down"},
            {**ok, "name": "ai_model:waiting", "status": "pending"},
        ],
    )
    metrics_module.DEPENDENCY_UP.clear()
    system_health_service.collect_metrics()
    assert _sample("skylab_dependency_up", component="ai_gateway") == 1.0
    assert _sample("skylab_dependency_latency_seconds", component="ai_gateway") == pytest.approx(0.012)
    assert _sample("skylab_dependency_up", component="ai_model:up") == 1.0
    assert _sample("skylab_dependency_up", component="ai_model:down") == 0.0
    assert metrics_module.REGISTRY.get_sample_value(
        "skylab_dependency_up", {"component": "ai_model:waiting"}
    ) is None


# ─── ai_metrics ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        ("success", None, "success"),
        ("cancelled", "client_disconnected", "cancelled"),
        ("error", "upstream_unavailable", "unavailable"),
        ("error", "upstream_stream_error", "stream_error"),
        ("error", "upstream_http_429", "rate_limited"),
        ("error", "upstream_http_403", "client_error"),
        ("error", "upstream_http_502", "upstream_error"),
        ("error", "vLLM said something odd", "error"),
        ("failed", None, "error"),
    ],
)
def test_outcome_classification(status: str, error: str | None, expected: str) -> None:
    assert ai_metrics.outcome(status, error) == expected


def test_model_label_is_bounded_to_models_litellm_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ai_metrics.model_label("typo-model", served=False) == "other"
    assert ai_metrics.model_label("chat-a", served=True) == "chat-a"
    assert ai_metrics.model_label("chat-a", served=False) == "chat-a"
    assert ai_metrics.model_label("  ", served=True) == "unknown"
    monkeypatch.setattr(ai_metrics, "MAX_MODEL_LABELS", 1)
    assert ai_metrics.model_label("chat-b", served=True) == "other"


def test_observe_call_updates_all_ai_metrics() -> None:
    labels = {"source": "api_key", "model": "chat-a"}
    before = _sample("skylab_ai_requests_total", **labels, request_type="chat_completion", outcome="success")
    tokens_in = _sample("skylab_ai_tokens_total", **labels, direction="input")
    ttft_count = _sample("skylab_ai_time_to_first_token_seconds_count", **labels)
    ai_metrics.observe_call(
        source="api_key",
        model="chat-a",
        request_type="chat_completion",
        record_status="success",
        duration_ms=1500,
        first_token_ms=200,
        input_tokens=12,
        output_tokens=30,
        stream=True,
    )
    assert _sample("skylab_ai_requests_total", **labels, request_type="chat_completion", outcome="success") == before + 1
    assert _sample("skylab_ai_tokens_total", **labels, direction="input") == tokens_in + 12
    assert _sample("skylab_ai_tokens_total", **labels, direction="output") >= 30
    assert _sample("skylab_ai_time_to_first_token_seconds_count", **labels) == ttft_count + 1
    assert _sample("skylab_ai_request_duration_seconds_count", **labels, stream="true") >= 1


def test_observe_call_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(**_: Any) -> Any:
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(metrics_module.AI_REQUESTS, "labels", broken)
    ai_metrics.observe_call(source="api_key", model="m", request_type="t", record_status="success")


def test_proxy_usage_recording_updates_metrics_even_if_accounting_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(**_: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(relay_service.ai_gateway_service, "record_usage", fail)
    before = _sample(
        "skylab_ai_requests_total", source="api_key", model="other", request_type="chat_completion", outcome="unavailable"
    )
    user = type("U", (), {"id": "u"})()
    credential = type("C", (), {"id": "c"})()
    relay_service.record_usage_safely(
        session=None,
        user=user,
        credential=credential,
        model_name="never-served",
        request_type="chat_completion",
        record_status="error",
        error_message="upstream_unavailable",
        duration_ms=30,
    )
    assert _sample(
        "skylab_ai_requests_total", source="api_key", model="other", request_type="chat_completion", outcome="unavailable"
    ) == before + 1


def test_platform_ai_calls_are_counted_without_a_session() -> None:
    before = _sample(
        "skylab_ai_requests_total", source="platform", model="chat-a", request_type="ai_nav", outcome="success"
    )
    ai_feature_monitoring.record_ai_template_call(
        session=None,
        user_id=None,
        call_type="ai_nav",
        model_name="chat-a",
        metrics={"prompt_tokens": 5, "completion_tokens": 7, "elapsed_seconds": 0.8},
    )
    assert _sample(
        "skylab_ai_requests_total", source="platform", model="chat-a", request_type="ai_nav", outcome="success"
    ) == before + 1
