"""Latency contract regressions using fake time, ASGI and isolated metrics."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from prometheus_client import CollectorRegistry, Counter, Histogram

from app.ai import monitoring
from app.ai.navigation import service as navigation
from app.ai.pve_log import chat as pve
from app.ai.role_contracts import AdherenceReason, AdherenceResult, AdherenceVerdict
from app.core import metrics
from app.models.user import UserRole
from app.services.llm_gateway import relay_service as relay
from app.services.monitoring import ai_metrics


class Clock:
    value = 0.0

    def __call__(self):
        return self.value


@pytest.fixture
def registry(monkeypatch):
    registry = CollectorRegistry()
    for attr in (
        "REQUEST_COUNT",
        "REQUEST_LATENCY",
        "AI_REQUESTS",
        "AI_REQUEST_DURATION",
        "AI_TIME_TO_FIRST_TOKEN",
        "AI_PROXY_QUEUE_WAIT",
        "AI_WORKFLOWS",
    ):
        original = getattr(metrics, attr)
        cls = Histogram if isinstance(original, Histogram) else Counter
        kwargs = {"buckets": original._upper_bounds[:-1]} if cls is Histogram else {}
        monkeypatch.setattr(
            metrics,
            attr,
            cls(
                original._name,
                original._documentation,
                original._labelnames,
                registry=registry,
                **kwargs,
            ),
        )
    ai_metrics.remember_models(["test-model", "A"])
    return registry


def sample(registry, name, **labels):
    return registry.get_sample_value(name, labels) or 0


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/ai/navigation/resolve", "ai_generation"),
        ("POST", "/ai/pve-log/chat", "ai_generation"),
        ("POST", "/ai-proxy/chat/completions", "ai_generation"),
        ("POST", "/ai-proxy/completions", "ai_generation"),
        ("POST", "/ai-proxy/responses", "ai_generation"),
        ("POST", "/ai/contextual-help/explain", "ai_generation"),
        ("POST", "/ai/template-recommendation/chat", "ai_generation"),
        ("POST", "/ai/template-recommendation/recommend", "ai_generation"),
        (
            "POST",
            "/teaching-classes/{teaching_class_id}/judge/sessions/{session_id}/messages",
            "ai_generation",
        ),
        (
            "GET",
            "/teaching-classes/{teaching_class_id}/judge/sessions/{session_id}/messages",
            "general_api",
        ),
        (
            "POST",
            "/teaching-classes/{teaching_class_id}/judge/sessions/{session_id}/script-sets",
            "general_api",
        ),
        (
            "POST",
            "/teaching-classes/{teaching_class_id}/judge/sessions/{session_id}/scripts/{artifact_id}/runs",
            "general_api",
        ),
        ("POST", "/ai/navigation/intake", "general_api"),
        ("GET", "/ai/contextual-help/surfaces", "general_api"),
        ("GET", "/ai-proxy/models", "general_api"),
        ("POST", "/ai-api/requests", "general_api"),
        ("POST", "/ai/pve-log/ssh/confirm", "general_api"),
    ],
)
def test_workload_classifies_method_and_template(method, path, expected):
    path = metrics.settings.API_V1_STR + path
    assert metrics._http_workload({"method": method}, path) == expected


@pytest.mark.parametrize(
    ("method", "template", "expected"),
    [
        ("POST", "/ai-proxy/chat/completions", "ai_generation"),
        (
            "POST",
            "/teaching-classes/{teaching_class_id}/judge/sessions/{session_id}/messages",
            "ai_generation",
        ),
        ("GET", "/utils/health-check/", "general_api"),
        ("GET", "/ai-proxy/models", "general_api"),
    ],
)
def test_workload_accepts_prefixless_route_template(method, template, expected):
    # Lazy include_router leaves the /api/v1 prefix out of scope["route"].path.
    scope = {"method": method, "path": metrics.settings.API_V1_STR + "/x"}
    assert metrics._http_workload(scope, template) == expected
    assert metrics._http_workload({"method": method, "path": "/x"}, template) == "other"


def test_real_app_routes_are_labelled_general_api(registry):
    from fastapi.testclient import TestClient

    from app.main import app

    TestClient(app).get(metrics.settings.API_V1_STR + "/utils/health-check/")
    workloads = {
        s.labels["workload"]
        for m in registry.collect()
        if m.name == "http_request_duration_seconds"
        for s in m.samples
        if s.name.endswith("_count")
        and s.labels["path"].endswith("/utils/health-check/")
    }
    assert workloads == {"general_api"}


@pytest.mark.parametrize("status", [401, 429, 503])
async def test_early_rejections_preserve_ai_intent_without_raw_path_labels(
    registry, status
):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status})

    async def send(message):
        pass

    await metrics.PrometheusMiddleware(app)(
        {
            "type": "http",
            "method": "POST",
            "path": metrics.settings.API_V1_STR + "/ai-proxy/chat/completions",
        },
        None,
        send,
    )
    assert (
        sample(
            registry,
            "http_requests_total",
            method="POST",
            path="<unmatched>",
            status=str(status),
            workload="ai_generation",
        )
        == 1
    )
    assert (
        metrics._http_workload(
            {"method": "POST", "path": "/api/v1/scanner/random-id"}, "<unmatched>"
        )
        == "other"
    )


async def test_long_ai_body_and_cleanup_do_not_pollute_general_api(
    registry, monkeypatch
):
    clock = Clock()
    monkeypatch.setattr(metrics.time, "perf_counter", clock)

    async def app(scope, receive, send):
        scope["route"] = SimpleNamespace(path=scope["path"])
        clock.value += 0.1
        await send({"type": "http.response.start", "status": 200})
        clock.value += scope["body_time"]
        await send({"type": "http.response.body", "body": b"ok"})
        clock.value += scope["cleanup_time"]

    async def send(message):
        pass

    prefix = metrics.settings.API_V1_STR
    await metrics.PrometheusMiddleware(app)(
        {
            "type": "http",
            "method": "GET",
            "path": prefix + "/users/me",
            "body_time": 0.2,
            "cleanup_time": 0,
        },
        None,
        send,
    )
    general = registry.get_sample_value(
        "http_request_duration_seconds_sum",
        {"method": "GET", "path": prefix + "/users/me", "workload": "general_api"},
    )
    await metrics.PrometheusMiddleware(app)(
        {
            "type": "http",
            "method": "POST",
            "path": prefix + "/ai-proxy/chat/completions",
            "body_time": 11.9,
            "cleanup_time": 2,
        },
        None,
        send,
    )
    labels = {
        "method": "POST",
        "path": prefix + "/ai-proxy/chat/completions",
        "workload": "ai_generation",
    }
    assert sample(
        registry, "http_request_duration_seconds_sum", **labels
    ) == pytest.approx(14)
    assert (
        sample(registry, "http_request_duration_seconds_bucket", **labels, le="10.0")
        == 0
    )
    assert (
        sample(registry, "http_request_duration_seconds_bucket", **labels, le="20.0")
        == 1
    )
    assert (
        sample(
            registry,
            "http_request_duration_seconds_sum",
            method="GET",
            path=prefix + "/users/me",
            workload="general_api",
        )
        == general
    )


@pytest.mark.parametrize(
    ("seconds", "upper"),
    [(12, 20), (70, 90), (110, 120), (320, 600), (570, 600), (650, float("inf"))],
)
def test_http_tail_buckets_preserve_long_samples(registry, seconds, upper):
    labels = {
        "method": "POST",
        "path": "/api/v1/ai/pve-log/chat",
        "workload": "ai_generation",
    }
    metrics.REQUEST_LATENCY.labels(**labels).observe(seconds)
    populated = [
        s
        for metric in registry.collect()
        for s in metric.samples
        if s.name == "http_request_duration_seconds_bucket" and s.value == 1
    ]
    assert min(float(s.labels["le"]) for s in populated) == upper


def test_duration_types_outcomes_and_cancellation_are_separate(registry):
    for kind in ["ai_nav", "pve_chat", "pve_chat_adherence", "arbitrary-user-input"]:
        ai_metrics.observe_call(
            source="platform",
            model="test-model",
            request_type=kind,
            record_status="success",
            duration_ms=1500,
        )
    labels = {
        "source": "platform",
        "model": "test-model",
        "stream": "false",
        "request_type": "ai_nav",
    }
    ai_metrics.observe_call(
        source="platform",
        model="test-model",
        request_type="ai_nav",
        record_status="error",
        duration_ms=2000,
    )
    ai_metrics.observe_call(
        source="platform",
        model="test-model",
        request_type="ai_nav",
        record_status="cancelled",
        duration_ms=3000,
    )
    assert (
        sample(
            registry,
            "skylab_ai_request_duration_seconds_count",
            **labels,
            outcome="success",
        )
        == 1
    )
    assert (
        sample(
            registry,
            "skylab_ai_request_duration_seconds_sum",
            **labels,
            outcome="error",
        )
        == 2
    )
    assert (
        sample(
            registry,
            "skylab_ai_request_duration_seconds_count",
            **labels,
            outcome="cancelled",
        )
        == 0
    )
    assert (
        sample(
            registry,
            "skylab_ai_requests_total",
            source="platform",
            model="test-model",
            request_type="other",
            outcome="success",
        )
        == 1
    )
    assert not any(
        m.samples
        for m in registry.collect()
        if m.name == "skylab_ai_time_to_first_token_seconds"
    )


async def test_retried_admission_is_observed_once_per_logical_request(
    registry, monkeypatch, caplog
):
    clock = Clock()
    admission = relay.AdmissionQueue(
        max_active=1, max_waiting=5, wait_timeout_seconds=70, clock=clock
    )
    ticket = admission.ticket("A")
    holder = await admission.acquire(admission.ticket("A"))
    pending = asyncio.create_task(admission.acquire(ticket))
    await asyncio.sleep(0)
    clock.value = 5
    holder.release()
    lease = await pending
    admission.rate_limited(lease, "10")
    pending = asyncio.create_task(admission.acquire(ticket))
    await asyncio.sleep(0)
    clock.value = 15
    admission.dispatch()
    lease = await pending
    assert ticket.waited_seconds == 15
    assert sample(registry, "skylab_ai_proxy_queue_wait_seconds_count", model="A") == 0

    async def record(**kwargs):
        pass

    monkeypatch.setattr(relay, "record_usage_in_threadpool", record)
    observation = relay.RelayObservation(
        SimpleNamespace(id="user"),
        SimpleNamespace(id="key"),
        "A",
        "chat_completion",
        "logical-id",
        relay.time.monotonic() - 15,
        relay.datetime.now(relay.timezone.utc),
        False,
        lease=lease,
        ticket=ticket,
        record_status="success",
        attempts=2,
    )
    with caplog.at_level("INFO"):
        await observation.finish()
        await observation.finish()
    assert sample(registry, "skylab_ai_proxy_queue_wait_seconds_count", model="A") == 1
    assert sample(registry, "skylab_ai_proxy_queue_wait_seconds_sum", model="A") == 15
    logs = [
        r for r in caplog.records if getattr(r, "event", None) == "ai_relay_finished"
    ]
    assert len(logs) == 1 and logs[0].queue_ms == 15000 and logs[0].attempts == 2
    await admission.close()


async def test_navigation_model_failure_keeps_fallback_and_records_reason(
    registry, monkeypatch, caplog
):
    async def failed(*args, **kwargs):
        raise httpx.ReadTimeout("private prompt must not enter summary")

    monkeypatch.setattr(navigation.system_ai_env, "vllm_model_name", "test-model")
    monkeypatch.setattr(navigation.navigation_client, "create_chat_completion", failed)
    with caplog.at_level("INFO"):
        result = await navigation.resolve_navigation(
            "Where are my API keys?",
            SimpleNamespace(id=None, role=UserRole.student, is_superuser=False),
        )
    assert result is not None
    logs = [
        r for r in caplog.records if getattr(r, "event", None) == "ai_workflow_finished"
    ]
    assert len(logs) == 1
    assert logs[0].outcome == "fallback" and logs[0].terminal_reason == "model_timeout"
    assert logs[0].model_calls == 1 and logs[0].tool_calls == 0
    assert "private prompt" not in json.dumps(
        {
            k: v
            for k, v in logs[0].__dict__.items()
            if k in {"outcome", "terminal_reason", "call_request_ids"}
        }
    )


async def test_pve_multiround_metrics_and_phase_summary(registry, monkeypatch, caplog):
    clock = Clock()
    monkeypatch.setattr(monitoring.time, "perf_counter", clock)
    monkeypatch.setattr(
        pve,
        "settings",
        SimpleNamespace(
            VLLM_MODEL_NAME="test-model",
            VLLM_BASE_URL="http://model.invalid/v1",
            VLLM_ENABLE_THINKING=False,
            VLLM_TIMEOUT=120,
            VLLM_CHAT_MAX_TOKENS=4096,
            VLLM_CHAT_TEMPERATURE=0.1,
            VLLM_TOP_P=0.95,
            VLLM_TOP_K=20,
            VLLM_MIN_P=0,
            VLLM_PRESENCE_PENALTY=0,
            VLLM_REPETITION_PENALTY=1,
        ),
    )
    monkeypatch.setattr(navigation.system_ai_env, "vllm_model_name", "test-model")
    monkeypatch.setattr(
        navigation.system_ai_env, "vllm_base_url", "http://model.invalid/v1"
    )
    replies = iter(
        [
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "t1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_nodes",
                                        "arguments": "{}",
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {"choices": [{"message": {"content": "nodes checked"}}]},
        ]
    )

    async def model(*args, **kwargs):
        clock.value += 2
        return next(replies)

    async def adherence(*args, **kwargs):
        clock.value += 0.5
        return AdherenceResult(
            AdherenceVerdict.ALLOW, AdherenceReason.NONE, elapsed_seconds=0.5
        )

    def tool(*args, **kwargs):
        clock.value += 3
        return {"nodes": []}

    monkeypatch.setattr(pve.vllm_client, "create_chat_completion", model)
    monkeypatch.setattr(pve, "check_adherence", adherence)
    monkeypatch.setattr(pve, "PveToolContext", object)
    monkeypatch.setattr(pve, "_execute_tool_sync", tool)
    path = metrics.settings.API_V1_STR + "/ai/pve-log/chat"
    result = None

    async def app(scope, receive, send):
        nonlocal result
        scope["route"] = SimpleNamespace(path=path)
        result = await pve.chat(message="Read nodes only")
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        pass

    with caplog.at_level("INFO"):
        await metrics.PrometheusMiddleware(app)(
            {"type": "http", "method": "POST", "path": path}, None, send
        )
    assert result.reply == "nodes checked"
    assert (
        sample(
            registry,
            "http_requests_total",
            method="POST",
            path=path,
            workload="ai_generation",
            status="200",
        )
        == 1
    )
    assert (
        sample(
            registry,
            "http_request_duration_seconds_count",
            method="POST",
            path=path,
            workload="ai_generation",
        )
        == 1
    )
    logs = [
        r for r in caplog.records if getattr(r, "event", None) == "ai_workflow_finished"
    ]
    assert len(logs) == 1
    record = logs[0]
    assert (record.model_calls, record.adherence_calls, record.tool_calls) == (2, 2, 1)
    assert (
        record.model_ms,
        record.adherence_ms,
        record.tool_ms,
        record.workflow_duration_ms,
    ) == (4000, 1000, 3000, 8000)
    assert len(set(record.call_request_ids)) == 4
    assert (
        sample(
            registry,
            "skylab_ai_request_duration_seconds_count",
            source="platform",
            model="test-model",
            stream="false",
            request_type="pve_chat",
            outcome="success",
        )
        == 2
    )
    assert (
        sample(
            registry,
            "skylab_ai_request_duration_seconds_count",
            source="platform",
            model="test-model",
            stream="false",
            request_type="pve_chat_adherence",
            outcome="success",
        )
        == 2
    )
    assert (
        sample(
            registry,
            "skylab_ai_workflows_total",
            route="/ai/pve-log/chat",
            outcome="success",
            terminal_reason="reply",
        )
        == 1
    )
    assert (
        sample(registry, "skylab_ai_proxy_queue_wait_seconds_count", model="test-model")
        == 0
    )


@pytest.mark.parametrize(
    "original", [None, ValueError("original"), asyncio.CancelledError()]
)
def test_observation_failure_preserves_response_exception_and_cancellation(
    monkeypatch, original
):
    def failed(*args, **kwargs):
        raise RuntimeError("observer unavailable")

    monkeypatch.setattr(ai_metrics, "model_label", failed)
    if original is None:
        with monitoring.AIWorkflowObservation("/ai/navigation/resolve", "test-model"):
            result = "original response"
        assert result == "original response"
    else:

        def observe_failure() -> None:
            with monitoring.AIWorkflowObservation(
                "/ai/navigation/resolve", "test-model"
            ):
                raise original

        with pytest.raises(type(original)) as caught:
            observe_failure()
        assert caught.value is original


async def test_navigation_invalid_output_is_fallback(registry, monkeypatch, caplog):
    async def invalid(*args, **kwargs):
        return {"choices": [{"message": {"content": "invalid decision"}}]}

    monkeypatch.setattr(navigation.system_ai_env, "vllm_model_name", "test-model")
    monkeypatch.setattr(navigation.navigation_client, "create_chat_completion", invalid)
    with caplog.at_level("INFO"):
        result = await navigation.resolve_navigation(
            "Where are my API keys?",
            SimpleNamespace(id=None, role=UserRole.student, is_superuser=False),
        )
    assert result is not None
    record = next(
        r for r in caplog.records if getattr(r, "event", None) == "ai_workflow_finished"
    )
    assert (record.outcome, record.terminal_reason) == (
        "fallback",
        "invalid_or_failed_model",
    )


@pytest.mark.parametrize("mode", ["act", "respond"])
@pytest.mark.parametrize(
    "reason", [AdherenceReason.CHECK_FAILED, AdherenceReason.ROLE_DRIFT]
)
async def test_pve_adherence_failure_and_policy_block_are_distinct(
    registry, monkeypatch, caplog, mode, reason
):
    _stub_pve_settings(monkeypatch)

    async def model(*args, **kwargs):
        message = (
            {
                "tool_calls": [
                    {
                        "id": "t1",
                        "type": "function",
                        "function": {"name": "get_nodes", "arguments": "{}"},
                    }
                ]
            }
            if mode == "act"
            else {"content": "reply"}
        )
        return {"choices": [{"message": message}], "usage": {"completion_tokens": 1}}

    async def adherence(*args, **kwargs):
        verdict = (
            AdherenceVerdict.INSUFFICIENT_CONTEXT
            if reason == AdherenceReason.CHECK_FAILED
            else AdherenceVerdict.BLOCK
        )
        return AdherenceResult(verdict, reason)

    monkeypatch.setattr(pve.vllm_client, "create_chat_completion", model)
    monkeypatch.setattr(pve, "check_adherence", adherence)
    with caplog.at_level("INFO"):
        result = await pve.chat(message="Read nodes only")
    assert result.reply == pve.PVE_ADHERENCE_FALLBACK and result.error is None
    record = next(
        r for r in caplog.records if getattr(r, "event", None) == "ai_workflow_finished"
    )
    expected = (
        ("error", "adherence_failed")
        if reason == AdherenceReason.CHECK_FAILED
        else ("blocked", "adherence_block")
    )
    assert (record.outcome, record.terminal_reason) == expected
    assert record.adherence_calls == 1 and record.tool_calls == 0


def _stub_pve_settings(monkeypatch):
    monkeypatch.setattr(
        pve,
        "settings",
        SimpleNamespace(
            VLLM_MODEL_NAME="test-model",
            VLLM_BASE_URL="http://model.invalid/v1",
            VLLM_ENABLE_THINKING=False,
            VLLM_TIMEOUT=120,
            VLLM_CHAT_MAX_TOKENS=4096,
            VLLM_CHAT_TEMPERATURE=0.1,
            VLLM_TOP_P=0.95,
            VLLM_TOP_K=20,
            VLLM_MIN_P=0,
            VLLM_PRESENCE_PENALTY=0,
            VLLM_REPETITION_PENALTY=1,
        ),
    )


@pytest.mark.parametrize(
    "terminal", ["tool_error", "round_limit", "needs_confirmation"]
)
async def test_pve_tool_terminal_summary(registry, monkeypatch, caplog, terminal):
    _stub_pve_settings(monkeypatch)
    calls = 0

    async def model(*args, **kwargs):
        nonlocal calls
        calls += 1
        message = (
            {"content": "reply"}
            if terminal == "tool_error" and calls > 1
            else {
                "tool_calls": [
                    {
                        "id": f"t{calls}",
                        "type": "function",
                        "function": {"name": "get_nodes", "arguments": "{}"},
                    }
                ]
            }
        )
        return {"choices": [{"message": message}], "usage": {"completion_tokens": 1}}

    async def adherence(*args, **kwargs):
        return AdherenceResult(AdherenceVerdict.ALLOW, AdherenceReason.NONE)

    monkeypatch.setattr(pve.vllm_client, "create_chat_completion", model)
    monkeypatch.setattr(pve, "check_adherence", adherence)
    monkeypatch.setattr(pve, "PveToolContext", object)
    monkeypatch.setattr(
        pve,
        "_execute_tool_sync",
        lambda *args, **kwargs: (
            {"error": "failed"}
            if terminal == "tool_error"
            else {"pending": terminal == "needs_confirmation"}
        ),
    )
    with caplog.at_level("INFO"):
        result = await pve.chat(message="Read nodes only")
    record = next(
        r for r in caplog.records if getattr(r, "event", None) == "ai_workflow_finished"
    )
    assert record.terminal_reason == terminal
    assert record.outcome == (
        "success" if terminal == "needs_confirmation" else "error"
    )
    assert result.needs_confirmation == (terminal == "needs_confirmation")
