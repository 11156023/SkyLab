"""app.services.monitoring.ai_metrics：AI proxy 指標更新成功與失敗不外漏。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.core import metrics
from app.services.monitoring import ai_metrics


class _BrokenMetric:
    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"metric broken: {name}")


@pytest.fixture(autouse=True)
def _reset_models():
    ai_metrics.reset_known_models()
    yield
    ai_metrics.reset_known_models()


CASES: list[tuple[list[str], Callable[[], None]]] = [
    (
        ["AI_PROXY_INFLIGHT", "AI_PROXY_WAITING"],
        lambda: ai_metrics.update_proxy_admission(active=-1, waiting=3),
    ),
    (
        ["AI_PROXY_QUEUE_WAIT"],
        lambda: ai_metrics.observe_proxy_queue_wait(-2.0, model="m"),
    ),
    (
        ["AI_PROXY_ADMISSION_REJECTIONS"],
        lambda: ai_metrics.record_proxy_admission_rejection("full"),
    ),
    (
        ["AI_PROXY_MODEL_INFLIGHT", "AI_PROXY_MODEL_WAITING"],
        lambda: ai_metrics.update_proxy_model_admission("m", active=2, waiting=-1),
    ),
    (["AI_PROXY_EVENTS"], lambda: ai_metrics.record_proxy_event("m", "retry")),
    (["AI_PROXY_COOLDOWN"], lambda: ai_metrics.observe_proxy_cooldown("m", 1.5)),
    (["AI_PROXY_FINAL_STATUS"], lambda: ai_metrics.record_proxy_final_status("m", 503)),
]


@pytest.mark.parametrize(("names", "call"), CASES)
def test_proxy_metric_updates_succeed(
    names: list[str], call: Callable[[], None]
) -> None:
    call()  # 真實 Prometheus 指標：不拋例外


@pytest.mark.parametrize(("names", "call"), CASES)
def test_proxy_metric_failures_are_swallowed_and_logged(
    monkeypatch: pytest.MonkeyPatch, names: list[str], call: Callable[[], None]
) -> None:
    logged: list[str] = []
    monkeypatch.setattr(
        ai_metrics.logger, "debug", lambda msg, *a, **k: logged.append(msg)
    )
    for name in names:
        monkeypatch.setattr(metrics, name, _BrokenMetric())
    call()
    assert len(logged) == 1


def test_queue_wait_uses_other_label_for_unknown_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    labels: list[dict[str, str]] = []

    class Recorder:
        def labels(self, **kw: str) -> Recorder:
            labels.append(kw)
            return self

        def observe(self, value: float) -> None:
            assert value == 0.0

    monkeypatch.setattr(metrics, "AI_PROXY_QUEUE_WAIT", Recorder())
    ai_metrics.observe_proxy_queue_wait(-5.0, model="never-served")
    ai_metrics.remember_models(["known"])
    ai_metrics.observe_proxy_queue_wait(-5.0, model="known")
    assert labels == [{"model": "other"}, {"model": "known"}]
