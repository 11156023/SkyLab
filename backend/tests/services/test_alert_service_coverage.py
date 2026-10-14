"""app.services.monitoring.alert_service：取樣邊界、重複樣本、Email 失敗、確認告警與 tick 早退路徑。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import AlertEvent, AlertMetric, AlertScope
from app.services.monitoring import alert_service
from app.services.monitoring.alert_service import (
    MetricSample,
    collect_samples,
    evaluate,
    inactive_vm_targets,
)

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


class FakeConfig:
    alerts_enabled = True
    alert_cpu_threshold = 90.0
    alert_memory_threshold = 90.0
    alert_disk_threshold = 90.0
    alert_cooldown_minutes = 30
    alert_check_interval_seconds = 60


class _FakeSession:
    def __call__(self, _engine: Any) -> _FakeSession:
        return self

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset_tick(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(alert_service._tick_state, "last_run_monotonic", None)


def test_collect_samples_skips_nameless_node_and_vmidless_resource() -> None:
    samples = collect_samples(
        [{"node": "", "cpu": 0.5}],
        [{"vmid": None, "status": "running", "cpu": 0.5}],
    )
    assert samples == []


def test_inactive_vm_targets_ignores_resources_without_vmid() -> None:
    result = inactive_vm_targets(
        [{"vmid": None, "status": "running"}],
        {(None, "100")},
        complete=True,
    )
    assert result == {(None, "100")}


def test_evaluate_duplicate_samples_open_one_alert() -> None:
    sample = MetricSample(scope="node", target="pve1", metric="cpu", value=95.0)
    decision = evaluate([sample, sample], [], FakeConfig(), NOW)
    assert decision.new_alerts == [sample]


def _alert(connection_id: int | None = None) -> AlertEvent:
    return AlertEvent(
        scope=AlertScope.vm,
        connection_id=connection_id,
        target="100",
        metric=AlertMetric.cpu,
        value=95.0,
        threshold=90.0,
        message="vm 100 cpu",
        created_at=NOW,
    )


def test_notify_admins_logs_failed_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        alert_service,
        "list_active_admin_emails",
        lambda _s: ["ok@example.com", "bad@example.com"],
    )
    monkeypatch.setattr(alert_service, "_connection_names", lambda _s: {1: "lab"})
    sent: list[tuple[str, str]] = []

    def fake_send(*, email_to: str, subject: str, html_content: str) -> None:
        if email_to == "bad@example.com":
            raise RuntimeError("smtp down")
        sent.append((email_to, subject))

    warnings: list[str] = []
    monkeypatch.setattr(alert_service, "send_email", fake_send)
    monkeypatch.setattr(
        alert_service.logger, "warning", lambda msg, *a, **k: warnings.append(msg)
    )
    alert_service._notify_admins(object(), [_alert(1)])  # type: ignore[arg-type]
    assert sent == [("ok@example.com", "[SkyLab 警告] 100 cpu 95%（lab）")]
    assert warnings == ["Failed to send alert email to %s for %s/%s"]


def test_acknowledge_alert_returns_public(monkeypatch: pytest.MonkeyPatch) -> None:
    alert = _alert()
    user_id = uuid.uuid4()
    seen: dict[str, Any] = {}

    def fake_ack(
        *, session: Any, alert_id: uuid.UUID, user_id: uuid.UUID
    ) -> AlertEvent:
        seen.update(alert_id=alert_id, user_id=user_id)
        return alert

    monkeypatch.setattr(alert_service.governance_repo, "acknowledge_alert", fake_ack)
    public = alert_service.acknowledge_alert(
        object(), alert_id=alert.id, user_id=user_id
    )  # type: ignore[arg-type]
    assert public.id == alert.id
    assert public.connection_name is None
    assert seen == {"alert_id": alert.id, "user_id": user_id}


def test_process_resource_alerts_returns_zero_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(alert_service, "Session", _FakeSession())
    config = FakeConfig()
    config.alerts_enabled = False
    monkeypatch.setattr(
        alert_service.governance_repo,
        "get_governance_config",
        lambda *, session: config,
    )
    assert alert_service.process_resource_alerts() == 0
    assert alert_service._tick_state.last_run_monotonic is None


def test_process_resource_alerts_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alert_service, "Session", _FakeSession())
    monkeypatch.setattr(
        alert_service.governance_repo,
        "get_governance_config",
        lambda *, session: FakeConfig(),
    )
    monkeypatch.setattr(
        alert_service, "time", SimpleNamespace(monotonic=lambda: 1000.0)
    )
    monkeypatch.setattr(alert_service._tick_state, "last_run_monotonic", 990.0)
    monkeypatch.setattr(
        alert_service.proxmox_service,
        "collect_monitoring_snapshot",
        lambda: pytest.fail("throttled"),
    )
    assert alert_service.process_resource_alerts() == 0


def test_process_resource_alerts_logs_and_swallows_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(alert_service, "Session", _FakeSession())

    def broken(*, session: Any) -> Any:
        raise RuntimeError("db gone")

    logged: list[str] = []
    monkeypatch.setattr(alert_service.governance_repo, "get_governance_config", broken)
    monkeypatch.setattr(
        alert_service.logger, "exception", lambda msg, *a, **k: logged.append(msg)
    )
    assert alert_service.process_resource_alerts() == 0
    assert logged == ["process_resource_alerts failed"]
