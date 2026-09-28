"""稽核修復的回歸測試：監控、治理、反挖礦、Web Push。

全部以 monkeypatch 替換 DB／PVE／SMTP，不需要真的連線。
"""

from __future__ import annotations

import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import AlertEvent, MiningIncidentStatus
from app.services.governance import lifecycle_policy, lifecycle_service
from app.services.governance import snapshot_cleanup_service as snap_svc
from app.services.monitoring import alert_service, health_policy, system_health_service
from app.services.notification import web_push_service
from app.services.security import mining_service


class _FakeSession:
    """只記錄 add／commit，供不碰 DB 的服務層測試使用。"""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        return None

    def refresh(self, obj: Any) -> None:
        return None

    def flush(self) -> None:
        return None


# ─── 挖礦存證快照的稽核與通知寫對保留天數 ─────────────────────────────


def test_evidence_snapshot_audit_uses_mining_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[dict[str, Any]] = []
    mails: list[dict[str, Any]] = []
    monkeypatch.setattr(
        snap_svc.audit_service, "log_action", lambda **kw: logged.append(kw)
    )
    monkeypatch.setattr(snap_svc, "send_email", lambda **kw: mails.append(kw))
    resource = SimpleNamespace(
        vmid=101, user=SimpleNamespace(email="s@campus.edu", full_name="學生")
    )

    snap_svc._audit_and_notify(
        _FakeSession(),  # type: ignore[arg-type]
        resource,  # type: ignore[arg-type]
        "mining-202607010000",
        snap_svc.MINING_EVIDENCE_RETENTION_DAYS,
        evidence=True,
    )

    assert logged[0]["details"] == (
        "Auto-cleaned mining evidence snapshot 'mining-202607010000' "
        f"(incident closed >{snap_svc.MINING_EVIDENCE_RETENTION_DAYS}d)"
    )
    assert "保留天數" not in mails[0]["html_content"]
    assert f"已結案超過 {snap_svc.MINING_EVIDENCE_RETENTION_DAYS} 天" in mails[0][
        "html_content"
    ]


def test_cleanup_loop_passes_evidence_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(snap_svc, "Session", lambda _engine: _FakeSession())
    monkeypatch.setattr(snap_svc, "_utc_now", lambda: now)
    monkeypatch.setattr(
        snap_svc,
        "_get_config",
        lambda session: SimpleNamespace(
            snapshot_cleanup_enabled=True, snapshot_retention_days=7
        ),
    )
    monkeypatch.setattr(
        snap_svc,
        "_list_scan_batch",
        lambda session, cursor, limit: [SimpleNamespace(vmid=101, user=None)],
    )
    monkeypatch.setattr(snap_svc, "_reset_cursor", lambda: None)
    monkeypatch.setattr(
        snap_svc.proxmox_service,
        "list_all_resources_by_vmid",
        lambda: {101: {"vmid": 101, "node": "pve1", "type": "qemu"}},
    )
    monkeypatch.setattr(
        snap_svc.proxmox_service,
        "list_snapshots",
        lambda node, vmid, rtype: [
            {
                "name": "mining-202605010000",
                "snaptime": int((now - timedelta(days=60)).timestamp()),
            }
        ],
    )
    monkeypatch.setattr(
        snap_svc, "_closed_mining_snapshots",
        lambda session, vmid: {"mining-202605010000": now - timedelta(days=40)},
    )
    monkeypatch.setattr(
        snap_svc.proxmox_service, "delete_snapshot", lambda *a, **k: None
    )
    monkeypatch.setattr(
        snap_svc, "_audit_and_notify", lambda *a, **k: calls.append((a, k))
    )

    assert snap_svc.process_snapshot_cleanup() == 1
    args, kwargs = calls[0]
    assert args[2] == "mining-202605010000"
    assert args[3] == snap_svc.MINING_EVIDENCE_RETENTION_DAYS
    assert kwargs == {"evidence": True}


# ─── 重啟後第一輪不可把仍在發生的系統告警收掉 ─────────────────────────


def test_unconfirmed_but_present_target_is_not_resolved() -> None:
    decision = health_policy.evaluate_system_alerts(
        [],
        open_targets=["component:gateway"],
        last_created={},
        cooldown_seconds=1800,
        now=1_800_000_000.0,
        current_targets=["component:gateway"],
    )
    assert decision.resolved_targets == []
    assert decision.new_findings == []

    # 真的恢復（這一輪沒看到）才收
    decision = health_policy.evaluate_system_alerts(
        [],
        open_targets=["component:gateway"],
        last_created={},
        cooldown_seconds=1800,
        now=1_800_000_000.0,
        current_targets=[],
    )
    assert decision.resolved_targets == ["component:gateway"]


def test_first_tick_after_restart_keeps_open_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    open_alert = SimpleNamespace(
        target="component:gateway",
        resolved_at=None,
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    monkeypatch.setattr(system_health_service, "Session", lambda _engine: session)
    monkeypatch.setattr(
        system_health_service.governance_repo,
        "get_governance_config",
        lambda session: SimpleNamespace(
            alerts_enabled=True,
            alert_check_interval_seconds=60,
            alert_cooldown_minutes=30,
            alert_email_enabled=False,
        ),
    )
    monkeypatch.setattr(
        system_health_service,
        "collect_components",
        lambda **_: [
            {
                "name": "gateway",
                "label": "Gateway",
                "status": "down",
                "latency_ms": None,
                "detail": "SSH 失敗",
            }
        ],
    )
    monkeypatch.setattr(
        system_health_service, "_heartbeat_view", lambda now: ([], [], "memory")
    )
    monkeypatch.setattr(
        system_health_service.governance_repo,
        "get_open_system_alerts",
        lambda session: [open_alert],
    )
    monkeypatch.setattr(
        system_health_service.governance_repo,
        "get_system_alerts_since",
        lambda session, since: [],
    )

    system_health_service.reset_alert_state()
    try:
        created = system_health_service.process_system_health_alerts()
    finally:
        system_health_service.reset_alert_state()

    assert created == 0
    assert open_alert.resolved_at is None
    assert not [obj for obj in session.added if isinstance(obj, AlertEvent)]


# ─── 卡住的 Gateway 探測不能拖垮 DB／Redis 檢查 ─────────────────────


def test_gateway_probe_sets_ssh_read_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.repositories.gateway_config as gw_repo
    from app.services.network import gateway_service, nginx_gateway_service

    seen: list[Any] = []

    class _Client:
        closed = False

        def exec_command(self, command: str, timeout: float | None = None) -> Any:
            seen.append(timeout)
            raise TimeoutError("read timed out")

        def close(self) -> None:
            self.closed = True

    client = _Client()
    monkeypatch.setattr(gateway_service, "make_client", lambda *a, **k: client)
    monkeypatch.setattr(gw_repo, "get_decrypted_private_key", lambda config: "key")
    monkeypatch.setattr(
        nginx_gateway_service,
        "probe_health",
        lambda c, *, wireguard_unit: c.exec_command("probe"),
    )
    config = SimpleNamespace(host="gw", ssh_port=22, ssh_user="root")

    with pytest.raises(TimeoutError):
        system_health_service._probe_gateway(config)
    assert seen == [system_health_service.GATEWAY_EXEC_TIMEOUT_SECONDS]
    assert client.closed


def test_hung_external_probes_do_not_starve_database_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()

    def hung_probe(config: Any) -> dict[str, Any]:
        release.wait(30)
        return {}

    monkeypatch.setattr(system_health_service, "_probe_gateway", hung_probe)
    monkeypatch.setattr(
        system_health_service, "_load_gateway_config", lambda: SimpleNamespace(host="gw")
    )
    monkeypatch.setattr(system_health_service, "GATEWAY_PROBE_TIMEOUT_SECONDS", 0.05)

    class _Conn:
        def __enter__(self) -> _Conn:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def execute(self, *_a: Any) -> None:
            return None

    monkeypatch.setattr(
        system_health_service,
        "engine",
        SimpleNamespace(connect=lambda: _Conn()),
    )
    try:
        # 塞滿外部探測池的所有執行緒
        for _ in range(system_health_service._probe_pool._max_workers):
            assert (
                system_health_service.check_gateway(use_cache=False)[0]["status"]
                == "down"
            )
        assert system_health_service.check_database()["status"] == "ok"
    finally:
        release.set()
        system_health_service.reset_gateway_cache()


# ─── 生命週期（挖礦暫停中的 VM、重複寄信） ─────────────────────────


NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def _lifecycle_resource(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "vmid": 101,
        "user_id": uuid.uuid4(),
        "user": SimpleNamespace(email="s@campus.edu"),
        "expiry_date": date(2026, 7, 9),
        "expiry_notified_at": datetime(2026, 7, 5, tzinfo=timezone.utc),
        "scheduled_deletion_at": None,
        "auto_stop_at": None,
        "auto_stop_reason": None,
        "idle_since": None,
        "idle_notified_at": None,
        "idle_checked_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture()
def lifecycle(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "emails": [],
        "auto_stops": [],
        "open_mining": set(),
        "resources": [],
    }
    session = _FakeSession()
    monkeypatch.setattr(lifecycle_service, "Session", lambda _engine: session)
    monkeypatch.setattr(lifecycle_service, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        lifecycle_service.governance_repo,
        "get_governance_config",
        lambda session: SimpleNamespace(
            ttl_enabled=True,
            expiry_warn_days=7,
            expiry_grace_delete_days=7,
            idle_detection_enabled=True,
            idle_window_hours=48,
            idle_cpu_threshold_percent=5.0,
            idle_notify_after_hours=12,
            idle_grace_hours=24,
            idle_scan_batch_size=10,
        ),
    )
    monkeypatch.setattr(
        lifecycle_service.resource_repo,
        "list_resources_with_expiry",
        lambda session: state["resources"],
    )
    monkeypatch.setattr(
        lifecycle_service.resource_repo,
        "list_idle_scan_candidates",
        lambda **kw: state["resources"],
    )

    def set_auto_stop(**kw: Any) -> None:
        state["auto_stops"].append(kw["auto_stop_reason"])
        for resource in state["resources"]:
            if resource.vmid == kw["vmid"]:
                resource.auto_stop_at = kw["auto_stop_at"]
                resource.auto_stop_reason = kw["auto_stop_reason"]

    monkeypatch.setattr(lifecycle_service.resource_repo, "set_auto_stop", set_auto_stop)
    monkeypatch.setattr(
        lifecycle_service.proxmox_service,
        "list_all_resources_by_vmid",
        lambda: {
            101: {
                "vmid": 101,
                "node": "pve1",
                "type": "qemu",
                "status": "running",
                "uptime": 100 * 3600,
            }
        },
    )
    monkeypatch.setattr(lifecycle_service, "_vmids_with_open_deletion", lambda s: set())
    monkeypatch.setattr(
        lifecycle_service,
        "_vmids_with_open_mining_incident",
        lambda s: state["open_mining"],
    )
    monkeypatch.setattr(lifecycle_service, "_fetch_avg_cpu", lambda *a, **k: 0.5)
    monkeypatch.setattr(
        lifecycle_service,
        "send_email",
        lambda **kw: state["emails"].append(kw["subject"]),
    )
    return state


def _simulate_stop_one(resource: SimpleNamespace) -> None:
    """recurrence_scheduler._stop_one：送出 shutdown 後就清排程（VM 其實還在跑）。"""
    resource.auto_stop_at = None
    resource.auto_stop_reason = None


def test_ttl_stop_email_sent_once_while_guest_ignores_shutdown(
    lifecycle: dict[str, Any],
) -> None:
    resource = _lifecycle_resource()
    lifecycle["resources"] = [resource]

    lifecycle_service.process_ttl_lifecycle()
    _simulate_stop_one(resource)
    lifecycle_service.process_ttl_lifecycle()

    assert lifecycle["auto_stops"] == ["ttl_expired", "ttl_expired"]
    assert len(lifecycle["emails"]) == 1
    assert "已到期" in lifecycle["emails"][0]


def test_ttl_stop_email_again_after_extension() -> None:
    # 延期會把 expiry_notified_at 清掉；新的到期日到了要再通知
    assert lifecycle_policy.ttl_stop_email_due(
        expiry_date=date(2026, 8, 1), expiry_notified_at=None
    )
    # 舊的「已到期」戳記早於新的到期時刻 → 仍要寄
    assert lifecycle_policy.ttl_stop_email_due(
        expiry_date=date(2026, 8, 1),
        expiry_notified_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    assert not lifecycle_policy.ttl_stop_email_due(
        expiry_date=date(2026, 7, 9),
        expiry_notified_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )


def test_idle_stop_email_sent_once_while_guest_ignores_shutdown(
    lifecycle: dict[str, Any],
) -> None:
    resource = _lifecycle_resource(
        expiry_date=None,
        idle_since=NOW - timedelta(hours=30),
        idle_notified_at=NOW - timedelta(hours=18),
    )
    lifecycle["resources"] = [resource]

    lifecycle_service.process_idle_detection()
    _simulate_stop_one(resource)
    lifecycle_service.process_idle_detection()

    assert lifecycle["auto_stops"] == ["idle", "idle"]
    assert len(lifecycle["emails"]) == 1
    assert "將自動關機" in lifecycle["emails"][0]


def test_idle_detection_skips_vm_with_open_mining_incident(
    lifecycle: dict[str, Any],
) -> None:
    resource = _lifecycle_resource(
        expiry_date=None,
        idle_since=NOW - timedelta(hours=30),
        idle_notified_at=NOW - timedelta(hours=18),
    )
    lifecycle["resources"] = [resource]
    lifecycle["open_mining"] = {101}

    assert lifecycle_service.process_idle_detection() == 0

    assert lifecycle["auto_stops"] == []
    assert lifecycle["emails"] == []
    assert resource.auto_stop_at is None
    assert resource.idle_since is None
    assert resource.idle_notified_at is None
    assert resource.idle_checked_at == NOW


def test_ttl_does_not_stop_vm_with_open_mining_incident(
    lifecycle: dict[str, Any],
) -> None:
    resource = _lifecycle_resource()
    lifecycle["resources"] = [resource]
    lifecycle["open_mining"] = {101}

    assert lifecycle_service.process_ttl_lifecycle() == 0
    assert lifecycle["auto_stops"] == []
    assert lifecycle["emails"] == []


# ─── 挖礦通知信要跳脫使用者可改的姓名 ───────────────────────────────


def test_mining_email_escapes_owner_full_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    monkeypatch.setattr(
        alert_service, "list_active_admin_emails", lambda s: ["a@x.edu"]
    )
    monkeypatch.setattr(mining_service, "_teacher_emails", lambda s, uid: [])
    monkeypatch.setattr(
        mining_service, "send_email", lambda **kw: captured.append(kw["html_content"])
    )
    incident = SimpleNamespace(
        vmid=101,
        user_id=uuid.uuid4(),
        window_hours=6,
        avg_cpu=99.0,
        snapshot_name="mining-202607010000",
        status=MiningIncidentStatus.suspended,
    )
    resource = SimpleNamespace(
        user=SimpleNamespace(
            full_name='</p><a href="http://evil.example">請重新登入</a>',
            email="s@x.edu",
        )
    )

    mining_service._notify_incident(None, incident, resource)  # type: ignore[arg-type]

    assert captured
    assert "<a href" not in captured[0]
    assert "&lt;a href=&quot;http://evil.example&quot;&gt;" in captured[0]


# ─── VM 關機／刪除後告警要收掉 ─────────────────────────────────────


def _alert(target: str, metric: str, *, scope: str = "vm", open_: bool = True) -> Any:
    return SimpleNamespace(
        target=target,
        metric=metric,
        scope=scope,
        created_at=NOW - timedelta(hours=3),
        resolved_at=None if open_ else NOW - timedelta(hours=1),
    )


_CONFIG = SimpleNamespace(
    alert_cpu_threshold=90.0,
    alert_memory_threshold=90.0,
    alert_disk_threshold=90.0,
    alert_cooldown_minutes=30,
)


def test_stopped_vm_alert_resolves() -> None:
    decision = alert_service.evaluate(
        [],
        [_alert("101", "memory"), _alert("pve1", "cpu", scope="node")],
        _CONFIG,
        NOW,
        inactive_targets={"101"},
    )
    assert decision.resolved_targets == [("101", "memory")]


def test_inactive_vm_targets() -> None:
    resources = [
        {"vmid": 101, "status": "stopped"},
        {"vmid": 102, "status": "running"},
    ]
    open_targets = {"101", "102", "103", "104"}
    # 103 已刪除：所有連線都查成功才算；104 挖礦暫停中不收
    assert alert_service.inactive_vm_targets(
        resources, open_targets, complete=True, exclude={"104"}
    ) == {"101", "103"}
    assert alert_service.inactive_vm_targets(
        resources, open_targets, complete=False, exclude={"104"}
    ) == {"101"}


# ─── 長期未恢復的告警不能因為被擠出最新 1000 筆而重複建立 ─────────────


def test_old_open_alert_is_kept_in_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_open = SimpleNamespace(
        id=uuid.uuid4(),
        target="pve1",
        metric="disk",
        scope="node",
        created_at=NOW - timedelta(days=30),
        resolved_at=None,
    )
    recent = [
        SimpleNamespace(
            id=uuid.uuid4(),
            target=str(200 + i),
            metric="cpu",
            scope="vm",
            created_at=NOW - timedelta(minutes=5),
            resolved_at=NOW,
        )
        for i in range(3)
    ]

    class _Session:
        def exec(self, _stmt: Any) -> Any:
            return SimpleNamespace(all=lambda: recent)

    monkeypatch.setattr(
        alert_service.governance_repo, "get_open_alerts", lambda session: [old_open]
    )
    alerts = alert_service._alerts_for_evaluation(
        _Session(),  # type: ignore[arg-type]
        since=NOW - timedelta(minutes=30),
    )
    assert old_open in alerts
    assert len(alerts) == 4

    decision = alert_service.evaluate(
        [alert_service.MetricSample(scope="node", target="pve1", metric="disk", value=95.0)],
        alerts,
        _CONFIG,
        NOW,
    )
    assert decision.new_alerts == []


# ─── Web Push 只取本人任務 ─────────────────────────────────────────


def test_push_snapshot_is_owner_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.jobs import jobs_service

    seen: dict[str, Any] = {}

    def fake_list_recent(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return SimpleNamespace(items=[])

    monkeypatch.setattr(jobs_service, "list_recent_for_user", fake_list_recent)
    admin = SimpleNamespace(id=uuid.uuid4(), is_superuser=True, role="admin")

    web_push_service._process_user(
        session=None,  # type: ignore[arg-type]
        user=admin,  # type: ignore[arg-type]
        subscriptions=[],
        state=web_push_service._UserState(),
        include_reminders=False,
    )

    assert seen["own_only"] is True
