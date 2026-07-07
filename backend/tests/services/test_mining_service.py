"""反挖礦處置管線測試（mock PVE / DB / email）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError
from app.models import MiningIncidentStatus
from app.services.security import mining_service

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


def _config(**overrides: object) -> SimpleNamespace:
    values: dict = {
        "mining_detection_enabled": True,
        "mining_cpu_threshold_percent": 90.0,
        "mining_window_hours": 6,
        "mining_scan_batch_size": 20,
        "mining_auto_suspend": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _incident(**overrides: object) -> SimpleNamespace:
    values: dict = {
        "id": uuid.uuid4(),
        "vmid": 101,
        "user_id": uuid.uuid4(),
        "node": "pve1",
        "resource_type": "qemu",
        "avg_cpu": 97.5,
        "window_hours": 6,
        "snapshot_name": None,
        "status": MiningIncidentStatus.detected,
        "detected_at": NOW,
        "suspended_at": None,
        "reviewed_by": None,
        "reviewed_at": None,
        "review_note": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _resource(**overrides: object) -> SimpleNamespace:
    values: dict = {
        "vmid": 101,
        "user_id": uuid.uuid4(),
        "mining_exempt": False,
        "mining_checked_at": None,
        "user": SimpleNamespace(email="stu@campus.edu", full_name="學生"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeSession:
    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def flush(self) -> None:
        pass

    def get(self, model: type, key: object) -> None:
        return None


@pytest.fixture()
def pve_calls(monkeypatch: pytest.MonkeyPatch) -> dict:
    """樁掉 PVE 與通知，記錄呼叫。"""
    calls: dict = {"snapshot": [], "control": [], "emails": [], "alerts": []}
    monkeypatch.setattr(
        mining_service.proxmox_service,
        "create_snapshot",
        lambda node, vmid, rtype, wait_timeout_seconds=None, **p: calls[
            "snapshot"
        ].append((node, vmid, rtype, p.get("snapname"))),
    )
    monkeypatch.setattr(
        mining_service.proxmox_service,
        "control",
        lambda node, vmid, rtype, action: calls["control"].append(
            (node, vmid, rtype, action)
        ),
    )
    monkeypatch.setattr(
        mining_service,
        "_notify_incident",
        lambda session, incident, resource: calls["emails"].append(incident.vmid),
    )
    monkeypatch.setattr(
        mining_service,
        "_create_alert_event",
        lambda session, incident, config: calls["alerts"].append(incident.vmid),
    )
    monkeypatch.setattr(
        mining_service.audit_service,
        "log_action",
        lambda **kwargs: None,
    )
    return calls


# ── respond_to_incident ──────────────────────────────────────────────────────


def test_respond_snapshot_then_suspend_qemu(pve_calls: dict) -> None:
    incident = _incident()
    mining_service.respond_to_incident(
        _FakeSession(), incident, _resource(), _config(), now=NOW
    )
    assert len(pve_calls["snapshot"]) == 1
    assert pve_calls["control"] == [("pve1", 101, "qemu", "suspend")]
    assert incident.status is MiningIncidentStatus.suspended
    assert incident.snapshot_name is not None
    assert incident.suspended_at == NOW
    assert pve_calls["alerts"] == [101]
    assert pve_calls["emails"] == [101]


def test_respond_snapshot_timeout_still_suspends(
    pve_calls: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _timeout(*args: object, **kwargs: object) -> None:
        raise TimeoutError("task timed out")

    monkeypatch.setattr(
        mining_service.proxmox_service, "create_snapshot", _timeout
    )
    incident = _incident()
    mining_service.respond_to_incident(
        _FakeSession(), incident, _resource(), _config(), now=NOW
    )
    assert incident.snapshot_name is None
    assert pve_calls["control"] == [("pve1", 101, "qemu", "suspend")]
    assert incident.status is MiningIncidentStatus.suspended


def test_respond_suspend_failure_stays_detected(
    pve_calls: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*args: object) -> None:
        raise RuntimeError("PVE down")

    monkeypatch.setattr(mining_service.proxmox_service, "control", _fail)
    incident = _incident()
    mining_service.respond_to_incident(
        _FakeSession(), incident, _resource(), _config(), now=NOW
    )
    assert incident.status is MiningIncidentStatus.detected
    assert incident.suspended_at is None
    # 告警與通知仍送出
    assert pve_calls["alerts"] == [101]
    assert pve_calls["emails"] == [101]


def test_respond_lxc_uses_stop(pve_calls: dict) -> None:
    incident = _incident(resource_type="lxc")
    mining_service.respond_to_incident(
        _FakeSession(), incident, _resource(), _config(), now=NOW
    )
    assert pve_calls["control"] == [("pve1", 101, "lxc", "stop")]


def test_respond_auto_suspend_disabled_only_alerts(pve_calls: dict) -> None:
    incident = _incident()
    mining_service.respond_to_incident(
        _FakeSession(),
        incident,
        _resource(),
        _config(mining_auto_suspend=False),
        now=NOW,
    )
    assert pve_calls["snapshot"] == []
    assert pve_calls["control"] == []
    assert incident.status is MiningIncidentStatus.detected
    assert pve_calls["alerts"] == [101]
    assert pve_calls["emails"] == [101]


# ── ban / dismiss ────────────────────────────────────────────────────────────


def _admin() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), email="admin@campus.edu")


def _patch_review_deps(
    monkeypatch: pytest.MonkeyPatch,
    incident: SimpleNamespace,
    *,
    owner_active: bool = True,
) -> dict:
    calls: dict = {"control": [], "audit": []}
    owner = SimpleNamespace(id=incident.user_id, is_active=owner_active)
    resource = _resource(vmid=incident.vmid, user_id=incident.user_id)
    monkeypatch.setattr(
        mining_service.mining_repo,
        "get_incident",
        lambda *, session, incident_id: incident,
    )
    monkeypatch.setattr(
        mining_service, "_get_user", lambda session, user_id: owner
    )
    monkeypatch.setattr(
        mining_service.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: resource,
    )
    monkeypatch.setattr(
        mining_service.proxmox_service,
        "control",
        lambda node, vmid, rtype, action: calls["control"].append(action),
    )
    monkeypatch.setattr(
        mining_service.audit_service,
        "log_action",
        lambda **kwargs: calls["audit"].append(kwargs.get("action")),
    )
    calls["owner"] = owner
    calls["resource"] = resource
    return calls


def test_ban_deactivates_user(monkeypatch: pytest.MonkeyPatch) -> None:
    incident = _incident(status=MiningIncidentStatus.suspended)
    calls = _patch_review_deps(monkeypatch, incident)
    result = mining_service.ban_incident(
        session=_FakeSession(), incident_id=incident.id, admin=_admin()
    )
    assert result.status is MiningIncidentStatus.banned
    assert calls["owner"].is_active is False
    assert result.reviewed_at is not None
    assert "mining_ban" in calls["audit"]


def test_ban_closed_incident_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    incident = _incident(status=MiningIncidentStatus.dismissed)
    _patch_review_deps(monkeypatch, incident)
    with pytest.raises(BadRequestError):
        mining_service.ban_incident(
            session=_FakeSession(), incident_id=incident.id, admin=_admin()
        )


def test_dismiss_resumes_and_sets_exempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = _incident(status=MiningIncidentStatus.suspended)
    calls = _patch_review_deps(monkeypatch, incident)
    result = mining_service.dismiss_incident(
        session=_FakeSession(),
        incident_id=incident.id,
        admin=_admin(),
        exempt=True,
        note="教授的模型訓練",
    )
    assert result.status is MiningIncidentStatus.dismissed
    assert calls["control"] == ["resume"]          # qemu → resume
    assert calls["resource"].mining_exempt is True
    assert result.review_note == "教授的模型訓練"
    assert "mining_dismiss" in calls["audit"]


def test_dismiss_lxc_uses_start(monkeypatch: pytest.MonkeyPatch) -> None:
    incident = _incident(
        status=MiningIncidentStatus.suspended, resource_type="lxc"
    )
    calls = _patch_review_deps(monkeypatch, incident)
    mining_service.dismiss_incident(
        session=_FakeSession(),
        incident_id=incident.id,
        admin=_admin(),
        exempt=False,
        note=None,
    )
    assert calls["control"] == ["start"]


# ── 掃描游標推進 ─────────────────────────────────────────────────────────────


def test_scan_advances_cursor_even_on_rrd_failure(
    monkeypatch: pytest.MonkeyPatch, pve_calls: dict
) -> None:
    """抽 RRD 失敗的 VM，mining_checked_at 仍須推進（防輪替卡死）。"""
    session = _FakeSession()
    resource = _resource()
    config = _config()

    monkeypatch.setattr(
        mining_service,
        "_fetch_cpu_stats",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("RRD down")),
    )
    monkeypatch.setattr(
        mining_service.mining_repo,
        "has_open_incident",
        lambda *, session, vmid: False,
    )

    mining_service._scan_one(
        session, resource, {"node": "pve1", "type": "qemu"}, config, now=NOW
    )
    assert resource.mining_checked_at == NOW


def test_scan_advances_cursor_on_no_hit(
    monkeypatch: pytest.MonkeyPatch, pve_calls: dict
) -> None:
    """未命中閾值的 VM 也要推進游標，否則永遠佔住最舊清單。"""
    session = _FakeSession()
    resource = _resource()

    monkeypatch.setattr(
        mining_service, "_fetch_cpu_stats", lambda *a, **k: (30.0, 1.0)
    )
    monkeypatch.setattr(
        mining_service.mining_repo,
        "has_open_incident",
        lambda *, session, vmid: False,
    )

    flagged = mining_service._scan_one(
        session, resource, {"node": "pve1", "type": "qemu"}, _config(), now=NOW
    )
    assert flagged is False
    assert resource.mining_checked_at == NOW
    assert pve_calls["control"] == []
