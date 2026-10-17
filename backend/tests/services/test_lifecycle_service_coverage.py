"""lifecycle_service（TTL 漸進回收與閒置偵測 I/O 協調層）的分支覆蓋測試。"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.governance import lifecycle_service
from app.services.governance.lifecycle_policy import IdleAction, TtlAction
from app.services.resource import deletion_service

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def exec(self, stmt: Any) -> _Result:
        return _Result(self.rows)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _resource(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "vmid": 101,
        "user_id": uuid.uuid4(),
        "user": SimpleNamespace(email="s@campus.edu"),
        "expiry_date": date(2026, 7, 9),
        "expiry_notified_at": None,
        "scheduled_deletion_at": None,
        "auto_stop_at": None,
        "auto_stop_reason": None,
        "idle_since": None,
        "idle_notified_at": None,
        "idle_checked_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _config(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "ttl_enabled": True,
        "expiry_warn_days": 7,
        "expiry_grace_delete_days": 7,
        "idle_detection_enabled": True,
        "idle_window_hours": 48,
        "idle_cpu_threshold_percent": 5.0,
        "idle_notify_after_hours": 12,
        "idle_grace_hours": 24,
        "idle_scan_batch_size": 10,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "emails": [],
        "auto_stops": [],
        "deletions": [],
        "resources": [],
        "config": _config(),
        "pve_map": {
            101: {
                "vmid": 101,
                "node": "pve1",
                "type": "qemu",
                "status": "running",
                "uptime": 100 * 3600,
            }
        },
        "session": _FakeSession(),
    }
    monkeypatch.setattr(lifecycle_service, "Session", lambda _engine: state["session"])
    monkeypatch.setattr(lifecycle_service, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        lifecycle_service.governance_repo,
        "get_governance_config",
        lambda session: state["config"],
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
    monkeypatch.setattr(
        lifecycle_service.resource_repo,
        "set_auto_stop",
        lambda **kw: state["auto_stops"].append(kw["auto_stop_reason"]),
    )
    monkeypatch.setattr(
        lifecycle_service.proxmox_service,
        "list_all_resources_by_vmid",
        lambda: state["pve_map"],
    )
    monkeypatch.setattr(lifecycle_service, "_vmids_with_open_deletion", lambda s: set())
    monkeypatch.setattr(
        lifecycle_service, "_vmids_with_open_mining_incident", lambda s: set()
    )
    monkeypatch.setattr(
        lifecycle_service,
        "send_email",
        lambda **kw: state["emails"].append(kw["subject"]),
    )
    monkeypatch.setattr(
        deletion_service,
        "create_deletion_request",
        lambda **kw: state["deletions"].append(kw),
    )
    return state


# ─── 小工具函式 ────────────────────────────────────────────────────────────


def test_utc_now_is_timezone_aware() -> None:
    assert lifecycle_service._utc_now().tzinfo is not None


def test_send_owner_email_skips_without_email(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Any] = []
    monkeypatch.setattr(lifecycle_service, "send_email", lambda **kw: sent.append(kw))
    lifecycle_service._send_owner_email(_resource(user=None), "s", "h")  # type: ignore[arg-type]
    assert sent == []


def test_send_owner_email_swallows_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(**kw: Any) -> None:
        raise RuntimeError("smtp down")

    monkeypatch.setattr(lifecycle_service, "send_email", boom)
    lifecycle_service._send_owner_email(_resource(), "s", "h")  # type: ignore[arg-type]
    assert "Failed to send lifecycle email" in caplog.text


def test_vmids_with_open_deletion_skips_null() -> None:
    session = _FakeSession(rows=[101, None, "202"])
    assert lifecycle_service._vmids_with_open_deletion(session) == {101, 202}  # type: ignore[arg-type]


def test_uptime_seconds_variants() -> None:
    assert lifecycle_service._uptime_seconds({}) is None
    assert lifecycle_service._uptime_seconds({"uptime": "abc"}) is None
    assert lifecycle_service._uptime_seconds({"uptime": [1]}) is None
    assert lifecycle_service._uptime_seconds({"uptime": "42"}) == 42


def test_fetch_avg_cpu_without_node_returns_none() -> None:
    result = lifecycle_service._fetch_avg_cpu(
        _resource(),  # type: ignore[arg-type]
        {"type": "lxc"},
        window_hours=24,
        now=NOW,
    )
    assert result is None


def test_fetch_avg_cpu_rrd_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*a: Any) -> None:
        raise RuntimeError("pve down")

    monkeypatch.setattr(lifecycle_service.proxmox_service, "get_rrd_data", boom)
    result = lifecycle_service._fetch_avg_cpu(
        _resource(),  # type: ignore[arg-type]
        {"node": "pve1", "type": "qemu"},
        window_hours=24,
        now=NOW,
    )
    assert result is None
    assert "Failed to fetch RRD" in caplog.text


def test_fetch_avg_cpu_averages_rrd(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    def fake_rrd(node: str, vmid: int, rtype: str, timeframe: str) -> list[Any]:
        calls.append((node, vmid, rtype, timeframe))
        return [{"time": int(NOW.timestamp()) - 600, "cpu": 0.5}]

    monkeypatch.setattr(lifecycle_service.proxmox_service, "get_rrd_data", fake_rrd)
    monkeypatch.setattr(
        lifecycle_service, "average_cpu_percent", lambda rrd, **kw: 50.0
    )
    result = lifecycle_service._fetch_avg_cpu(
        _resource(),  # type: ignore[arg-type]
        {"node": "pve1", "type": "lxc"},
        window_hours=24,
        now=NOW,
    )
    assert result == 50.0
    assert calls[0][:3] == ("pve1", 101, "lxc")


# ─── _apply_ttl_* ──────────────────────────────────────────────────────────


def test_apply_ttl_stop_skips_when_already_scheduled(env: dict[str, Any]) -> None:
    resource = _resource(auto_stop_reason="ttl_expired", auto_stop_at=NOW)
    lifecycle_service._apply_ttl_stop(_FakeSession(), resource, NOW)  # type: ignore[arg-type]
    assert env["auto_stops"] == []
    assert env["emails"] == []


def test_apply_ttl_delete_missing_on_pve(env: dict[str, Any]) -> None:
    session = _FakeSession()
    resource = _resource()
    lifecycle_service._apply_ttl_delete(session, resource, NOW, None)  # type: ignore[arg-type]
    assert resource.scheduled_deletion_at == NOW
    assert env["deletions"] == []
    assert env["emails"] == []


# ─── process_ttl_lifecycle ─────────────────────────────────────────────────


def test_ttl_disabled_returns_zero(env: dict[str, Any]) -> None:
    env["config"] = _config(ttl_enabled=False)
    env["resources"] = [_resource()]
    assert lifecycle_service.process_ttl_lifecycle() == 0


def test_ttl_no_resources_returns_zero(env: dict[str, Any]) -> None:
    assert lifecycle_service.process_ttl_lifecycle() == 0


def test_ttl_dispatches_each_action(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    r_none = _resource(vmid=1)
    r_warn = _resource(vmid=2)
    r_delete = _resource(vmid=3)
    r_stop = _resource(vmid=4)
    r_mining = _resource(vmid=5)
    env["resources"] = [r_none, r_warn, r_delete, r_stop, r_mining]
    env["pve_map"] = {
        3: {"name": "x", "node": "pve1", "type": "lxc"},
        4: {"status": "running"},
    }
    monkeypatch.setattr(
        lifecycle_service, "_vmids_with_open_mining_incident", lambda s: {5}
    )
    plan = {
        1: TtlAction.none,
        2: TtlAction.warn,
        3: TtlAction.delete,
        4: TtlAction.stop,
        5: TtlAction.stop,
    }
    seen_deletion_pending: list[bool] = []

    def fake_decide(**kw: Any) -> TtlAction:
        seen_deletion_pending.append(kw["deletion_pending"])
        # 依呼叫順序對應資源
        return plan[len(seen_deletion_pending)]

    monkeypatch.setattr(lifecycle_service, "decide_ttl_action", fake_decide)

    assert lifecycle_service.process_ttl_lifecycle() == 3
    assert r_warn.expiry_notified_at == NOW
    assert r_delete.scheduled_deletion_at == NOW
    assert len(env["deletions"]) == 1
    assert env["auto_stops"] == ["ttl_expired"]
    assert r_mining.expiry_notified_at is None
    assert env["session"].commits == 3


def test_ttl_action_failure_rolls_back_and_continues(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    env["resources"] = [_resource()]
    monkeypatch.setattr(
        lifecycle_service, "decide_ttl_action", lambda **kw: TtlAction.warn
    )

    def boom(*a: Any) -> None:
        raise RuntimeError("db write failed")

    monkeypatch.setattr(lifecycle_service, "_apply_ttl_warn", boom)

    assert lifecycle_service.process_ttl_lifecycle() == 0
    assert env["session"].rollbacks == 1


def test_ttl_outer_failure_returns_zero(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(session: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(
        lifecycle_service.governance_repo, "get_governance_config", boom
    )
    assert lifecycle_service.process_ttl_lifecycle() == 0


# ─── _apply_idle_action ────────────────────────────────────────────────────


def test_apply_idle_notify_with_idle_since(env: dict[str, Any]) -> None:
    resource = _resource(idle_since=NOW - timedelta(hours=13))
    lifecycle_service._apply_idle_action(  # type: ignore[arg-type]
        _FakeSession(), resource, IdleAction.notify, NOW, grace_hours=24
    )
    assert resource.idle_notified_at == NOW
    assert "疑似閒置" in env["emails"][0]


def test_apply_idle_stop_without_prior_notice_emails(env: dict[str, Any]) -> None:
    # 沒有通知時間戳（idle_stop_email_due 的 None 分支）→ 一定寄信
    resource = _resource(idle_since=NOW - timedelta(hours=30), idle_notified_at=None)
    lifecycle_service._apply_idle_action(  # type: ignore[arg-type]
        _FakeSession(), resource, IdleAction.stop, NOW, grace_hours=24
    )
    assert env["auto_stops"] == ["idle"]
    assert resource.idle_notified_at == NOW
    assert "將自動關機" in env["emails"][0]


def test_apply_idle_clear_resets_marks(env: dict[str, Any]) -> None:
    resource = _resource(idle_since=NOW, idle_notified_at=NOW)
    session = _FakeSession()
    lifecycle_service._apply_idle_action(  # type: ignore[arg-type]
        session, resource, IdleAction.clear, NOW, grace_hours=24
    )
    assert resource.idle_since is None
    assert resource.idle_notified_at is None
    assert session.added == [resource]


# ─── process_idle_detection ────────────────────────────────────────────────


def test_idle_disabled_returns_zero(env: dict[str, Any]) -> None:
    env["config"] = _config(idle_detection_enabled=False)
    assert lifecycle_service.process_idle_detection() == 0


def test_idle_skips_candidate_missing_on_pve_and_counts_actions(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = _resource(vmid=999)
    active = _resource(vmid=101)
    env["resources"] = [missing, active]
    monkeypatch.setattr(lifecycle_service, "_fetch_avg_cpu", lambda *a, **k: 1.0)
    monkeypatch.setattr(
        lifecycle_service, "decide_idle_action", lambda **kw: IdleAction.mark
    )

    assert lifecycle_service.process_idle_detection() == 1
    assert missing.idle_checked_at is None
    assert active.idle_since == NOW
    assert active.idle_checked_at == NOW


def test_idle_per_resource_failure_still_advances_cursor(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    resource = _resource()
    env["resources"] = [resource]

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("rrd parse")

    monkeypatch.setattr(lifecycle_service, "_fetch_avg_cpu", boom)

    assert lifecycle_service.process_idle_detection() == 0
    assert env["session"].rollbacks == 1
    assert resource.idle_checked_at == NOW


def test_idle_outer_failure_returns_zero(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> None:
        raise RuntimeError("pve down")

    monkeypatch.setattr(
        lifecycle_service.proxmox_service, "list_all_resources_by_vmid", boom
    )
    assert lifecycle_service.process_idle_detection() == 0
