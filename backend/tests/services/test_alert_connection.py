"""資源告警帶 PVE 連線：落 DB、解除、Email、API 與挖礦告警都以叢集區分。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.alembic.versions import alertconn01_alert_connection as migration
from app.core.config import settings
from app.core.db import engine as pg_engine
from app.models import (
    AlertEvent,
    AlertMetric,
    AlertScope,
    GovernanceConfig,
    MiningIncident,
    ProxmoxConnection,
    ProxmoxNode,
    Resource,
    User,
)
from app.services.monitoring import alert_service
from app.services.security import mining_service

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)


@pytest.fixture
def lite_engine() -> Iterator[sa.Engine]:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,  # type: ignore[attr-defined]
            ProxmoxConnection.__table__,  # type: ignore[attr-defined]
            ProxmoxNode.__table__,  # type: ignore[attr-defined]
            AlertEvent.__table__,  # type: ignore[attr-defined]
            GovernanceConfig.__table__,  # type: ignore[attr-defined]
            MiningIncident.__table__,  # type: ignore[attr-defined]
        ],
    )
    with Session(engine) as session:
        for cid, name in ((1, "機房A"), (2, "機房B")):
            session.add(
                ProxmoxConnection(
                    id=cid, name=name, host=f"10.0.{cid}.1", user="root@pam",
                    encrypted_password="x",
                )
            )
        session.add(ProxmoxNode(name="a1", host="10.0.1.11", connection_id=1))
        session.add(ProxmoxNode(name="b1", host="10.0.2.11", connection_id=2))
        session.commit()
    yield engine
    engine.dispose()


def _snapshot(resources: list[dict[str, Any]]) -> SimpleNamespace:
    return SimpleNamespace(
        nodes=[], resources=resources, failed_connections=0, total_connections=2
    )


def _run_tick(
    monkeypatch: pytest.MonkeyPatch, engine: sa.Engine, resources: list[dict[str, Any]]
) -> int:
    monkeypatch.setattr(alert_service, "engine", engine)
    monkeypatch.setattr(alert_service._tick_state, "last_run_monotonic", None)
    monkeypatch.setattr(
        alert_service.proxmox_service,
        "collect_monitoring_snapshot",
        lambda: _snapshot(resources),
    )
    return alert_service.process_resource_alerts()


def _open_alerts(engine: sa.Engine) -> set[tuple[int | None, str]]:
    with Session(engine) as session:
        rows = session.exec(
            select(AlertEvent).where(AlertEvent.resolved_at.is_(None))  # type: ignore[union-attr]
        ).all()
        return {(row.connection_id, row.target) for row in rows}


def test_tick_opens_and_resolves_alerts_per_connection(
    monkeypatch: pytest.MonkeyPatch, lite_engine: sa.Engine
) -> None:
    hot = {"status": "running", "cpu": 0.99, "mem": 1, "maxmem": 100}
    cool = {"status": "running", "cpu": 0.05, "mem": 1, "maxmem": 100}

    created = _run_tick(
        monkeypatch,
        lite_engine,
        [{"vmid": 100, "node": "a1", **hot}, {"vmid": 100, "node": "b1", **cool}],
    )
    assert created == 1
    assert _open_alerts(lite_engine) == {(1, "100")}

    # 叢集 B 的同號 VM 也爆了：另開一筆
    created = _run_tick(
        monkeypatch,
        lite_engine,
        [{"vmid": 100, "node": "a1", **hot}, {"vmid": 100, "node": "b1", **hot}],
    )
    assert created == 1
    assert _open_alerts(lite_engine) == {(1, "100"), (2, "100")}

    # 叢集 A 回落：只解除 A 的那筆
    _run_tick(
        monkeypatch,
        lite_engine,
        [{"vmid": 100, "node": "a1", **cool}, {"vmid": 100, "node": "b1", **hot}],
    )
    assert _open_alerts(lite_engine) == {(2, "100")}

    # 叢集 B 的 VM 關機：只收掉 B 的告警（A 的那台照常在跑）
    _run_tick(
        monkeypatch,
        lite_engine,
        [
            {"vmid": 100, "node": "a1", **cool},
            {"vmid": 100, "node": "b1", "status": "stopped"},
        ],
    )
    assert _open_alerts(lite_engine) == set()


def test_alert_email_names_the_cluster(
    monkeypatch: pytest.MonkeyPatch, lite_engine: sa.Engine
) -> None:
    sent: list[dict[str, str]] = []
    monkeypatch.setattr(alert_service, "list_active_admin_emails", lambda s: ["a@x.edu"])
    monkeypatch.setattr(alert_service, "send_email", lambda **kw: sent.append(kw))
    alert = AlertEvent(
        scope=AlertScope.vm,
        connection_id=2,
        target="100",
        metric=AlertMetric.cpu,
        value=97.0,
        threshold=90.0,
        message="vm 100 的 cpu 用量達 97.0%",
        created_at=NOW,
    )
    legacy = AlertEvent(
        scope=AlertScope.node,
        target="pve1",
        metric=AlertMetric.disk,
        value=95.0,
        threshold=90.0,
        message="node pve1 的 disk 用量達 95.0%",
        created_at=NOW,
    )
    with Session(lite_engine) as session:
        alert_service._notify_admins(session, [alert, legacy])

    assert sent[0]["subject"] == "[SkyLab 警告] 100 cpu 97%（機房B）"
    assert "叢集：機房B" in sent[0]["html_content"]
    # 無法判定叢集的告警主旨維持原樣
    assert sent[1]["subject"] == "[SkyLab 警告] pve1 disk 95%"
    assert "叢集" not in sent[1]["html_content"]


def test_to_public_includes_connection_name(lite_engine: sa.Engine) -> None:
    alerts = [
        AlertEvent(
            id=uuid.uuid4(), scope=AlertScope.node, connection_id=1, target="a1",
            metric=AlertMetric.cpu, value=95.0, threshold=90.0, message="m",
            created_at=NOW,
        ),
        AlertEvent(
            id=uuid.uuid4(), scope=AlertScope.system, target="component:redis",
            metric=AlertMetric.health, value=1.0, threshold=1.0, message="m",
            created_at=NOW,
        ),
    ]
    with Session(lite_engine) as session:
        public = alert_service.to_public(session, alerts)
    assert [(p.connection_id, p.connection_name) for p in public] == [
        (1, "機房A"),
        (None, None),
    ]


def test_alerts_api_returns_connection_name(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = SimpleNamespace(
        id=uuid.uuid4(), scope=AlertScope.vm, connection_id=7, target="100",
        metric=AlertMetric.memory, value=93.0, threshold=90.0, message="m",
        created_at=NOW, resolved_at=None, acknowledged_by=None, acknowledged_at=None,
    )
    monkeypatch.setattr(
        alert_service.governance_repo, "list_alerts", lambda **_: [row]
    )
    monkeypatch.setattr(alert_service, "_connection_names", lambda s: {7: "lab-cluster"})
    r = client.get(
        f"{settings.API_V1_STR}/monitoring/alerts?active=true",
        headers=superuser_token_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body[0]["connection_id"] == 7
    assert body[0]["connection_name"] == "lab-cluster"


def test_mining_alert_carries_vm_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    added: list[Any] = []
    session = SimpleNamespace(add=added.append)
    monkeypatch.setattr(
        alert_service.proxmox_node_repo,
        "get_node_by_name",
        lambda _session, name: SimpleNamespace(connection_id=2) if name == "b1" else None,
    )
    incident = SimpleNamespace(
        vmid=100, node="b1", avg_cpu=99.0, window_hours=6, detected_at=NOW
    )
    mining_service._create_alert_event(
        session,  # type: ignore[arg-type]
        incident,  # type: ignore[arg-type]
        SimpleNamespace(mining_cpu_threshold_percent=90.0),
    )
    assert added[0].connection_id == 2
    assert (added[0].scope, added[0].target) == (AlertScope.vm, "100")


# ─── migration：只回填開啟中的告警 ─────────────────────────────────────


def test_migration_backfills_open_alerts_only(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 直接在真的 PostgreSQL 上跑 migration（交易最後 rollback）：明確宣告 guarded
    # db fixture，DB guard 才不會把它當成純單元測試擋掉
    suffix = uuid.uuid4().hex[:8]
    vmid = 900_000 + int(suffix[:4], 16)
    with pg_engine.connect() as connection:
        transaction = connection.begin()
        try:
            session = Session(bind=connection)
            owner = session.exec(select(User)).first()
            assert owner is not None
            conn = ProxmoxConnection(
                name=f"alertconn-{suffix}", host="10.9.9.9", user="root@pam",
                encrypted_password="x",
            )
            session.add(conn)
            session.flush()
            session.add(
                ProxmoxNode(name=f"n-{suffix}", host="10.9.9.10", connection_id=conn.id)
            )
            session.add(
                Resource(
                    vmid=vmid, user_id=owner.id, environment_type="vm",
                    connection_id=conn.id, created_at=NOW,
                )
            )

            def _alert(scope: AlertScope, target: str, *, resolved: bool) -> AlertEvent:
                event = AlertEvent(
                    scope=scope, target=target, metric=AlertMetric.cpu, value=95.0,
                    threshold=90.0, message="m", created_at=NOW,
                    resolved_at=NOW + timedelta(minutes=5) if resolved else None,
                )
                session.add(event)
                return event

            node_open = _alert(AlertScope.node, f"n-{suffix}", resolved=False)
            vm_open = _alert(AlertScope.vm, str(vmid), resolved=False)
            vm_resolved = _alert(AlertScope.vm, str(vmid), resolved=True)
            unknown = _alert(AlertScope.vm, str(vmid + 1), resolved=False)
            session.flush()

            monkeypatch.setattr(
                migration, "op", Operations(MigrationContext.configure(connection))
            )
            migration.upgrade()

            def _conn(event: AlertEvent) -> int | None:
                return connection.execute(
                    sa.text("SELECT connection_id FROM alert_events WHERE id = :id"),
                    {"id": event.id},
                ).scalar_one()

            assert _conn(node_open) == conn.id
            assert _conn(vm_open) == conn.id
            assert _conn(vm_resolved) is None
            assert _conn(unknown) is None
            session.close()
        finally:
            transaction.rollback()
