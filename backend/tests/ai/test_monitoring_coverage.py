"""Coverage tests for app.ai.monitoring best-effort helpers."""

from types import SimpleNamespace

from app.ai import monitoring


def test_observation_swallows_metric_and_log_failures(monkeypatch) -> None:
    def broken_labels(**_kwargs):
        raise RuntimeError("metrics down")

    def broken_info(*_args, **_kwargs):
        raise RuntimeError("logger down")

    monkeypatch.setattr(
        monitoring.core_metrics,
        "AI_WORKFLOWS",
        SimpleNamespace(labels=broken_labels),
    )
    errors: list[str] = []
    monkeypatch.setattr(
        monitoring,
        "logger",
        SimpleNamespace(
            info=broken_info,
            exception=lambda message, *args: errors.append(message % args),
        ),
    )

    with monitoring.AIWorkflowObservation(route="/x", model="m") as observation:
        observation.outcome = "success"
    assert observation.outcome == "success"
    assert errors == ["Failed to record AI workflow metric: route=/x"]


def test_token_count_and_duration_fallbacks() -> None:
    assert monitoring._token_count({"prompt_tokens": "bad"}, "prompt_tokens") == 0
    assert monitoring._duration_ms(None) is None
    assert monitoring._duration_ms({}) is None
    assert monitoring._duration_ms({"elapsed_seconds": None}) is None
    assert monitoring._duration_ms({"elapsed_seconds": "oops"}) is None
    assert monitoring._duration_ms({"elapsed_seconds": 1.5}) == 1500
