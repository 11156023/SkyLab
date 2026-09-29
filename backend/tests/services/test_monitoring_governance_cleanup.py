"""監控／治理整理後的共用 helper：視窗 CPU 取樣、管理員 email 清單、快照前綴、arq key。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from app.infrastructure.proxmox.rrd import window_cpu_percentages
from app.models import UserRole
from app.services.governance import snapshot_cleanup_policy
from app.services.monitoring import alert_service, system_health_service

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def test_window_cpu_percentages_filters_window_and_missing_points() -> None:
    start = NOW.timestamp()
    rrd: list[dict[str, Any]] = [
        {"time": start - 7200, "cpu": 0.9},  # 視窗外
        {"time": start - 1800, "cpu": 0.25},
        {"time": start - 600},  # 缺 cpu
        {"cpu": 0.5},  # 缺 time
        {"time": start, "cpu": 0.5},
    ]
    assert window_cpu_percentages(rrd, window_hours=1, now=NOW) == [25.0, 50.0]


def test_list_active_admin_emails_uses_is_admin() -> None:
    users = [
        SimpleNamespace(email="root@x.edu", is_superuser=True, role=UserRole.student),
        SimpleNamespace(email="admin@x.edu", is_superuser=False, role=UserRole.admin),
        SimpleNamespace(email="t@x.edu", is_superuser=False, role=UserRole.teacher),
        SimpleNamespace(email="s@x.edu", is_superuser=False, role=UserRole.student),
    ]

    class _Session:
        def exec(self, _stmt: Any) -> Any:
            return SimpleNamespace(all=lambda: users)

    emails = alert_service.list_active_admin_emails(_Session())  # type: ignore[arg-type]
    assert emails == ["root@x.edu", "admin@x.edu"]


def test_mining_snapshot_prefix_is_protected() -> None:
    assert snapshot_cleanup_policy.MINING_SNAPSHOT_PREFIX == "mining-"
    assert (
        snapshot_cleanup_policy.MINING_SNAPSHOT_PREFIX
        in snapshot_cleanup_policy.PROTECTED_PREFIXES
    )


def test_arq_health_key_follows_queue_name() -> None:
    from app.infrastructure.queue.arq_client import QUEUE_NAME

    assert system_health_service._ARQ_HEALTH_KEY == f"{QUEUE_NAME}:health-check"

def test_open_mining_incident_vmids_shared_by_lifecycle_and_alerts(
    monkeypatch: Any,
) -> None:
    from app.models import MiningIncidentStatus
    from app.services.governance import lifecycle_service
    from app.services.security import mining_service

    assert mining_service.OPEN_INCIDENT_STATUSES == (
        MiningIncidentStatus.detected,
        MiningIncidentStatus.suspended,
    )

    class _Session:
        def exec(self, _stmt: Any) -> Any:
            return SimpleNamespace(all=lambda: [101, None, 205])

    session: Any = _Session()
    assert mining_service.open_incident_vmids(session) == {101, 205}
    assert lifecycle_service._vmids_with_open_mining_incident(session) == {101, 205}
    assert alert_service._open_mining_targets(session) == {"101", "205"}
