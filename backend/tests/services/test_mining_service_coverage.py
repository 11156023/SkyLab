"""Coverage tests for app.services.security.mining_service."""

import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import NotFoundError
from app.models import (
    MiningIncidentStatus,
    TeachingClass,
    TeachingClassStudent,
    User,
)
from app.services.monitoring import alert_service
from app.services.security import mining_service as svc

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


def _incident(**overrides):
    values = {
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
        "reviewed_by": None,
        "reviewed_at": None,
        "review_note": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeSession:
    def __init__(self, user=None):
        self.user = user
        self.added: list = []

    def get(self, model, key):
        return self.user

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass


def test_fetch_cpu_stats_without_node_returns_none():
    assert (
        svc._fetch_cpu_stats(
            SimpleNamespace(vmid=1),  # type: ignore[arg-type]
            {"type": "lxc"},
            window_hours=6,
            now=NOW,
        )
        is None
    )


def test_fetch_cpu_stats_reads_rrd(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        svc.proxmox_service,
        "get_rrd_data",
        lambda node, vmid, rtype, timeframe: (
            calls.append((node, vmid, rtype)) or [{"cpu": 1}]
        ),
    )
    monkeypatch.setattr(svc, "cpu_stats", lambda rrd, *, window_hours, now: (95.0, 1.0))
    result = svc._fetch_cpu_stats(
        SimpleNamespace(vmid=7),  # type: ignore[arg-type]
        {"node": "pve2", "type": "lxc"},
        window_hours=6,
        now=NOW,
    )
    assert result == (95.0, 1.0)
    assert calls == [("pve2", 7, "lxc")]


# ── process_mining_detection ───────────────────────────────────────────────


@pytest.fixture
def scheduler_session(monkeypatch):
    fake = object()

    @contextmanager
    def fake_session(_engine):
        yield fake

    monkeypatch.setattr(svc, "Session", fake_session)
    monkeypatch.setattr(svc, "_utc_now", lambda: NOW)
    return fake


def test_process_detection_disabled(monkeypatch, scheduler_session):
    monkeypatch.setattr(
        svc.governance_repo,
        "get_governance_config",
        lambda *, session: SimpleNamespace(mining_detection_enabled=False),
    )
    assert svc.process_mining_detection() == 0


def test_process_detection_scans_running_candidates(monkeypatch, scheduler_session):
    config = SimpleNamespace(mining_detection_enabled=True, mining_scan_batch_size=5)
    monkeypatch.setattr(
        svc.governance_repo, "get_governance_config", lambda *, session: config
    )
    monkeypatch.setattr(
        svc.proxmox_service,
        "list_all_resources_by_vmid",
        lambda: {
            101: {"status": "running", "node": "pve1"},
            102: {"status": "stopped"},
            103: {"status": "running", "node": "pve1"},
            104: {},
        },
    )
    seen: dict = {}

    def candidates(*, session, vmids, checked_before, limit):
        seen.update(vmids=vmids, limit=limit, checked_before=checked_before)
        return [
            SimpleNamespace(vmid=101),
            SimpleNamespace(vmid=103),
            SimpleNamespace(vmid=999),
        ]

    monkeypatch.setattr(svc.resource_repo, "list_mining_scan_candidates", candidates)
    scanned: list[int] = []

    def scan(session, resource, pve_info, cfg, *, now):
        scanned.append(resource.vmid)
        return resource.vmid == 101

    monkeypatch.setattr(svc, "_scan_one", scan)
    assert svc.process_mining_detection() == 1
    assert seen["vmids"] == [101, 103]
    assert seen["limit"] == 5
    assert scanned == [101, 103]


def test_process_detection_swallows_errors(monkeypatch, scheduler_session):
    def boom(*, session):
        raise RuntimeError("db down")

    monkeypatch.setattr(svc.governance_repo, "get_governance_config", boom)
    assert svc.process_mining_detection() == 0


# ── notifications ──────────────────────────────────────────────────────────


def test_teacher_emails_lists_active_class_owners():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        teacher = User(email="teacher@example.com", hashed_password="x")
        inactive = User(email="old@example.com", hashed_password="x", is_active=False)
        student = User(email="student@example.com", hashed_password="x")
        session.add_all([teacher, inactive, student])
        session.flush()
        for owner in (teacher, inactive):
            klass = TeachingClass(
                owner_id=owner.id,
                name="c",
                code=f"C{owner.email[:3]}",
                term="115-1",
                start_date=date(2026, 9, 1),
                end_date=date(2026, 12, 31),
                weekday=1,
                start_time=time(9, 0),
                end_time=time(10, 0),
            )
            session.add(klass)
            session.flush()
            session.add(TeachingClassStudent(class_id=klass.id, user_id=student.id))
        session.commit()
        assert svc._teacher_emails(session, student.id) == ["teacher@example.com"]


def test_notify_incident_survives_email_failure(monkeypatch):
    monkeypatch.setattr(
        alert_service, "list_active_admin_emails", lambda _s: ["admin@example.com"]
    )
    monkeypatch.setattr(svc, "_teacher_emails", lambda _s, _u: ["t@example.com"])
    attempts: list[str] = []

    def failing_send(*, email_to, subject, html_content):
        attempts.append(email_to)
        raise RuntimeError("smtp down")

    monkeypatch.setattr(svc, "send_email", failing_send)
    svc._notify_incident(
        object(),  # type: ignore[arg-type]
        _incident(status=MiningIncidentStatus.suspended),  # type: ignore[arg-type]
        SimpleNamespace(user=None),  # type: ignore[arg-type]
    )
    assert sorted(attempts) == ["admin@example.com", "t@example.com"]


# ── review ─────────────────────────────────────────────────────────────────


def test_ban_requires_existing_owner(monkeypatch):
    incident = _incident()
    monkeypatch.setattr(
        svc.mining_repo, "get_incident", lambda *, session, incident_id: incident
    )
    with pytest.raises(NotFoundError):
        svc.ban_incident(
            session=_FakeSession(None),  # type: ignore[arg-type]
            incident_id=incident.id,
            admin=SimpleNamespace(id=uuid.uuid4()),  # type: ignore[arg-type]
        )


def test_dismiss_reports_snapshot_delete_failure(monkeypatch):
    incident = _incident(snapshot_name="mining-202607041200")
    monkeypatch.setattr(
        svc.mining_repo, "get_incident", lambda *, session, incident_id: incident
    )

    def fail_delete(node, vmid, rtype, snapname):
        raise RuntimeError("snapshot busy")

    monkeypatch.setattr(svc.proxmox_service, "delete_snapshot", fail_delete)
    audits: list = []
    monkeypatch.setattr(
        svc.audit_service, "log_action", lambda **kw: audits.append(kw["action"])
    )
    monkeypatch.setattr(
        svc.resource_repo, "get_resource_by_vmid", lambda *, session, vmid: None
    )
    result, warnings = svc.dismiss_incident(
        session=_FakeSession(),  # type: ignore[arg-type]
        incident_id=incident.id,
        admin=SimpleNamespace(id=uuid.uuid4()),  # type: ignore[arg-type]
        exempt=True,
        note="false positive",
    )
    assert warnings == ["存證快照刪除失敗：snapshot busy"]
    assert result.status is MiningIncidentStatus.dismissed
    assert "snapshot busy" in result.review_note
    assert "snapshot_delete" not in audits
