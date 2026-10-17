"""app.services.monitoring.system_health_service：探測、快取、系統告警 tick 與 /metrics 收集。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.core import metrics
from app.infrastructure.proxmox import client as proxmox_client
from app.models import AlertEvent, AlertMetric, AlertScope, TaskRecordStatus
from app.repositories import gateway_config as gw_repo
from app.repositories import proxmox_connection as connection_repo
from app.services.monitoring import (
    alert_service,
    heartbeat_service,
    system_health_service,
)

shs = system_health_service


def _comp(name: str, status: str, latency_ms: float | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "label": name,
        "status": status,
        "latency_ms": latency_ms,
        "detail": None,
    }


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def all(self) -> list[Any]:
        return self.rows


class _FakeSession:
    """取代 ``Session(engine)`` 的假 session：記錄 add／commit／refresh／expunge。"""

    def __init__(self, exec_results: list[list[Any]] | None = None) -> None:
        self.exec_results = list(exec_results or [])
        self.added: list[Any] = []
        self.refreshed: list[Any] = []
        self.expunged: list[Any] = []
        self.commits = 0

    def __call__(self, _engine: Any) -> _FakeSession:
        return self

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def exec(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self.exec_results.pop(0))

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, obj: Any) -> None:
        self.refreshed.append(obj)

    def expunge(self, obj: Any) -> None:
        self.expunged.append(obj)


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(shs._PveCache, "expires_at", 0.0)
    monkeypatch.setattr(shs._PveCache, "components", [])
    shs.reset_gateway_cache()
    shs.reset_ai_cache()
    shs.reset_alert_state()
    yield
    shs.reset_gateway_cache()
    shs.reset_ai_cache()
    shs.reset_alert_state()


# ─── 基本探測 ──────────────────────────────────────────────────────────────


def test_check_redis_disabled_ok_and_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shs, "get_sync_redis", lambda: None)
    assert shs.check_redis()["status"] == "disabled"

    monkeypatch.setattr(
        shs, "get_sync_redis", lambda: SimpleNamespace(ping=lambda: True)
    )
    ok = shs.check_redis()
    assert ok["status"] == "ok"
    assert ok["latency_ms"] is not None

    def broken_ping():
        raise TimeoutError

    monkeypatch.setattr(
        shs, "get_sync_redis", lambda: SimpleNamespace(ping=broken_ping)
    )
    down = shs.check_redis()
    assert down == _comp("redis", "down") | {"label": "Redis", "detail": "timeout"}


def test_check_worker_unknown_when_pipeline_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenRedis:
        def pipeline(self, transaction=False):
            raise ConnectionError("gone")

    monkeypatch.setattr(shs, "get_sync_redis", lambda: BrokenRedis())
    worker = shs.check_worker(redis_ok=True)
    assert worker["status"] == "unknown"
    assert "ConnectionError" in worker["detail"]


# ─── PVE ───────────────────────────────────────────────────────────────────


def test_list_pve_connections_skips_unsaved(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()
    monkeypatch.setattr(shs, "Session", session)
    seen: dict[str, Any] = {}

    def fake_get_all(sess, enabled_only=False):
        seen["session"] = sess
        seen["enabled_only"] = enabled_only
        return [SimpleNamespace(id=1, name="lab"), SimpleNamespace(id=None, name="new")]

    monkeypatch.setattr(connection_repo, "get_all_connections", fake_get_all)
    assert shs._list_pve_connections() == [(1, "lab")]
    assert seen == {"session": session, "enabled_only": True}


def test_probe_pve_connection_calls_version(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_api(connection_id: int):
        calls.append(connection_id)
        return SimpleNamespace(version=SimpleNamespace(get=lambda: {"version": "8"}))

    monkeypatch.setattr(proxmox_client, "get_proxmox_api", fake_api)
    shs._probe_pve_connection(5)
    assert calls == [5]


def test_check_pve_probes_each_connection_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        shs, "_list_pve_connections", lambda: [(1, "lab"), (2, "broken")]
    )

    def fake_probe(connection_id: int) -> None:
        if connection_id == 2:
            raise ConnectionError("refused")

    monkeypatch.setattr(shs, "_probe_pve_connection", fake_probe)
    components = shs.check_pve()
    assert [(c["name"], c["status"]) for c in components] == [
        ("pve:1", "ok"),
        ("pve:2", "down"),
    ]
    assert components[0]["label"] == "Proxmox VE · lab"
    assert components[1]["detail"] == "ConnectionError: refused"
    assert shs.cached_pve_components() == components

    # 快取期間不再探測
    monkeypatch.setattr(
        shs, "_list_pve_connections", lambda: pytest.fail("should use cache")
    )
    assert shs.check_pve() == components


def test_check_pve_disabled_and_listing_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shs, "_list_pve_connections", lambda: [])
    disabled = shs.check_pve(use_cache=False)
    assert [(c["name"], c["status"], c["detail"]) for c in disabled] == [
        ("pve", "disabled", "not configured")
    ]

    def broken() -> list[tuple[int, str]]:
        raise RuntimeError("db gone")

    monkeypatch.setattr(shs, "_list_pve_connections", broken)
    unknown = shs.check_pve(use_cache=False)
    assert [(c["name"], c["status"]) for c in unknown] == [("pve", "unknown")]
    assert unknown[0]["detail"] == "RuntimeError: db gone"


# ─── Gateway ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "config",
    [
        None,
        SimpleNamespace(host="", encrypted_private_key="k"),
        SimpleNamespace(host="gw", encrypted_private_key=""),
    ],
)
def test_load_gateway_config_returns_none_when_incomplete(
    monkeypatch: pytest.MonkeyPatch, config: Any
) -> None:
    session = _FakeSession()
    monkeypatch.setattr(shs, "Session", session)
    monkeypatch.setattr(gw_repo, "get_gateway_config", lambda _s: config)
    assert shs._load_gateway_config() is None
    assert session.expunged == []


def test_load_gateway_config_expunges_complete_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    config = SimpleNamespace(host="gw.example.com", encrypted_private_key="enc")
    monkeypatch.setattr(shs, "Session", session)
    monkeypatch.setattr(gw_repo, "get_gateway_config", lambda _s: config)
    assert shs._load_gateway_config() is config
    assert session.expunged == [config]


def test_exec_timeout_client_forwards_other_attributes() -> None:
    inner = SimpleNamespace(
        transport="t", exec_command=lambda cmd, timeout=None: (cmd, timeout)
    )
    wrapped = shs._ExecTimeoutClient(inner, 7)
    assert wrapped.transport == "t"
    assert wrapped.exec_command("ls") == ("ls", 7)
    assert wrapped.exec_command("ls", timeout=2) == ("ls", 2)


def test_check_gateway_unknown_when_config_load_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> Any:
        raise RuntimeError("db gone")

    monkeypatch.setattr(shs, "_load_gateway_config", broken)
    result = shs.check_gateway(use_cache=False)
    assert [(c["name"], c["status"], c["detail"]) for c in result] == [
        ("gateway", "unknown", "RuntimeError: db gone")
    ]
    # 失敗不寫快取
    assert shs.cached_gateway_components() == []


# ─── 彙總 ──────────────────────────────────────────────────────────────────


def test_collect_components_orders_every_check(monkeypatch: pytest.MonkeyPatch) -> None:
    worker_args: list[bool] = []
    monkeypatch.setattr(shs, "check_database", lambda: _comp("database", "ok"))
    monkeypatch.setattr(shs, "check_redis", lambda: _comp("redis", "down"))

    def fake_worker(*, redis_ok: bool) -> dict[str, Any]:
        worker_args.append(redis_ok)
        return _comp("worker", "unknown")

    monkeypatch.setattr(shs, "check_worker", fake_worker)
    monkeypatch.setattr(shs, "check_pve", lambda: [_comp("pve:1", "ok")])
    monkeypatch.setattr(shs, "check_gateway", lambda: [_comp("gateway", "disabled")])
    monkeypatch.setattr(shs, "check_ai", lambda: [_comp("ai_gateway", "ok")])
    names = [c["name"] for c in shs.collect_components()]
    assert names == ["database", "redis", "worker", "pve:1", "gateway", "ai_gateway"]
    assert worker_args == [False]


# ─── 系統告警 ──────────────────────────────────────────────────────────────


def test_notify_admins_sends_each_email_and_survives_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.utils as app_utils

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        alert_service,
        "list_active_admin_emails",
        lambda _s: ["a@example.com", "bad@example.com"],
    )

    def fake_send(*, email_to: str, subject: str, html_content: str) -> None:
        if email_to == "bad@example.com":
            raise RuntimeError("smtp down")
        sent.append((email_to, subject))
        assert "component:worker" in html_content

    monkeypatch.setattr(app_utils, "send_email", fake_send)
    alert = AlertEvent(
        scope=AlertScope.system,
        target="component:worker",
        metric=AlertMetric.health,
        value=0,
        threshold=1,
        message="worker down",
        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    shs._notify_admins(_FakeSession(), [alert])  # type: ignore[arg-type]
    assert sent == [("a@example.com", "[SkyLab 系統告警] component:worker")]


def _gov_config(**overrides: Any) -> SimpleNamespace:
    base = {
        "alerts_enabled": True,
        "alert_check_interval_seconds": 60,
        "alert_cooldown_minutes": 30,
        "alert_email_enabled": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _patch_alert_tick(
    monkeypatch: pytest.MonkeyPatch,
    *,
    config: SimpleNamespace,
    components: list[dict[str, Any]],
    open_alerts: list[Any],
    recent: list[Any],
) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(shs, "Session", session)
    monkeypatch.setattr(
        shs.governance_repo, "get_governance_config", lambda *, session: config
    )
    monkeypatch.setattr(
        shs.governance_repo, "get_open_system_alerts", lambda *, session: open_alerts
    )
    monkeypatch.setattr(
        shs.governance_repo, "get_system_alerts_since", lambda *, session, since: recent
    )
    monkeypatch.setattr(shs, "collect_components", lambda: components)
    monkeypatch.setattr(
        heartbeat_service,
        "snapshot",
        lambda: heartbeat_service.HeartbeatSnapshot(
            loops=[], tasks=[], source="memory"
        ),
    )
    return session


def test_process_alerts_disabled_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_alert_tick(
        monkeypatch,
        config=_gov_config(alerts_enabled=False),
        components=[],
        open_alerts=[],
        recent=[],
    )
    assert shs.process_system_health_alerts() == 0
    assert shs._alert_state.last_run_monotonic is None


def test_process_alerts_throttled_within_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_alert_tick(
        monkeypatch, config=_gov_config(), components=[], open_alerts=[], recent=[]
    )
    monkeypatch.setattr(shs.time, "monotonic", lambda: 1000.0)
    shs._alert_state.last_run_monotonic = 990.0
    monkeypatch.setattr(shs, "collect_components", lambda: pytest.fail("throttled"))
    assert shs.process_system_health_alerts() == 0


def test_process_alerts_creates_resolves_and_notifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    stale_open = SimpleNamespace(target="component:redis", resolved_at=None)
    still_open = SimpleNamespace(target="component:pve:9", resolved_at=None)
    recent = [
        SimpleNamespace(
            target="component:pve:9", created_at=now - timedelta(minutes=5)
        ),
        SimpleNamespace(
            target="component:pve:9", created_at=now - timedelta(minutes=10)
        ),
    ]
    session = _patch_alert_tick(
        monkeypatch,
        config=_gov_config(),
        components=[
            {
                "name": "worker",
                "label": "Worker",
                "status": "down",
                "detail": "no heartbeat",
            },
            {
                "name": "pve:9",
                "label": "Proxmox VE · x",
                "status": "down",
                "detail": "refused",
            },
        ],
        open_alerts=[stale_open, still_open],
        recent=recent,
    )
    # 前一輪已看過一次 → 這輪確認
    shs._alert_state.streaks = {"component:worker": 1, "component:pve:9": 1}
    notified: list[list[str]] = []
    monkeypatch.setattr(
        shs,
        "_notify_admins",
        lambda _s, created: notified.append([e.target for e in created]),
    )

    assert shs.process_system_health_alerts() == 1
    created = [obj for obj in session.added if isinstance(obj, AlertEvent)]
    assert [e.target for e in created] == ["component:worker"]
    assert created[0].scope == AlertScope.system
    assert session.refreshed == created
    assert stale_open.resolved_at is not None
    assert still_open.resolved_at is None
    assert session.commits == 1
    assert notified == [["component:worker"]]


# ─── /metrics ──────────────────────────────────────────────────────────────


def test_task_record_counts_maps_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession(
        exec_results=[
            [(TaskRecordStatus.queued, 3), ("running", 1)],
            [(TaskRecordStatus.failed, 2), (TaskRecordStatus.succeeded, 9)],
        ]
    )
    monkeypatch.setattr(shs, "Session", session)
    assert shs._task_record_counts() == {
        "queued": 3,
        "running": 1,
        "failed_24h": 2,
        "succeeded_24h": 9,
    }


def test_collect_metrics_updates_gauges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shs, "check_database", lambda: _comp("database", "ok", 12.0))
    monkeypatch.setattr(shs, "check_redis", lambda: _comp("redis", "ok"))
    monkeypatch.setattr(
        shs, "check_worker", lambda *, redis_ok: _comp("worker", "down")
    )
    monkeypatch.setattr(
        shs, "cached_pve_components", lambda: [_comp("pve:1", "unknown")]
    )
    monkeypatch.setattr(
        shs, "cached_gateway_components", lambda: [_comp("gateway", "attention")]
    )
    monkeypatch.setattr(shs, "cached_ai_components", lambda: [])
    monkeypatch.setattr(
        shs, "get_sync_redis", lambda: SimpleNamespace(zcard=lambda _q: 4)
    )
    monkeypatch.setattr(
        shs, "_task_record_counts", lambda: {"queued": 2, "running": 0, "failed_24h": 1}
    )
    shs.collect_metrics()

    def gauge(g: Any, **labels: str) -> float:
        return g.labels(**labels)._value.get()

    assert gauge(metrics.DEPENDENCY_UP, component="database") == 1
    assert gauge(metrics.DEPENDENCY_UP, component="worker") == 0
    assert gauge(metrics.DEPENDENCY_UP, component="gateway") == 1
    assert gauge(metrics.DEPENDENCY_LATENCY, component="database") == pytest.approx(
        0.012
    )
    assert gauge(metrics.QUEUE_JOBS, queue=shs.QUEUE_NAME) == 4
    assert gauge(metrics.TASK_RECORDS, status="queued") == 2
    assert gauge(metrics.TASK_RECORDS, status="failed_24h") == 1


def test_collect_metrics_survives_probe_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shs, "check_database", lambda: _comp("database", "ok"))
    monkeypatch.setattr(shs, "check_redis", lambda: _comp("redis", "ok"))
    monkeypatch.setattr(shs, "check_worker", lambda *, redis_ok: _comp("worker", "ok"))
    monkeypatch.setattr(shs, "cached_pve_components", lambda: [])
    monkeypatch.setattr(shs, "cached_gateway_components", lambda: [])
    monkeypatch.setattr(shs, "cached_ai_components", lambda: [])

    def broken_zcard(_q: str) -> int:
        raise ConnectionError("redis gone")

    def broken_counts() -> dict[str, int]:
        raise RuntimeError("db gone")

    monkeypatch.setattr(
        shs, "get_sync_redis", lambda: SimpleNamespace(zcard=broken_zcard)
    )
    monkeypatch.setattr(shs, "_task_record_counts", broken_counts)
    shs.collect_metrics()  # 不拋例外
