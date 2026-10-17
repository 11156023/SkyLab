"""app.repositories.governance 的告警查詢與確認分支覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import NotFoundError
from app.models import AlertEvent, AlertScope
from app.models.alert_event import AlertMetric
from app.repositories import governance as repo


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


NOW = datetime(2099, 1, 1, tzinfo=UTC)


def _alert(scope: AlertScope, *, minutes: int, resolved: bool = False) -> AlertEvent:
    return AlertEvent(
        scope=scope,
        target="pve1",
        metric=AlertMetric.cpu,
        value=95.0,
        threshold=90.0,
        message="hot",
        created_at=NOW + timedelta(minutes=minutes),
        resolved_at=NOW + timedelta(minutes=minutes + 1) if resolved else None,
    )


def test_alert_queries(session: Session) -> None:
    system_open = _alert(AlertScope.system, minutes=0)
    system_resolved = _alert(AlertScope.system, minutes=10, resolved=True)
    other_open = _alert(AlertScope.node, minutes=20)
    for a in (system_open, system_resolved, other_open):
        session.add(a)
    session.commit()

    assert {a.id for a in repo.get_open_alerts(session=session)} == {
        system_open.id,
        other_open.id,
    }
    assert [a.id for a in repo.get_open_system_alerts(session=session)] == [
        system_open.id
    ]
    assert [
        a.id
        for a in repo.get_system_alerts_since(
            session=session, since=NOW + timedelta(minutes=5)
        )
    ] == [system_resolved.id]

    everything = repo.list_alerts(session=session)
    assert [a.id for a in everything] == [
        other_open.id,
        system_resolved.id,
        system_open.id,
    ]
    active = repo.list_alerts(session=session, active_only=True, limit=1)
    assert [a.id for a in active] == [other_open.id]


def test_acknowledge_alert(session: Session) -> None:
    alert = _alert(AlertScope.system, minutes=0)
    session.add(alert)
    session.commit()

    user_id = uuid.uuid4()
    acked = repo.acknowledge_alert(session=session, alert_id=alert.id, user_id=user_id)
    assert acked.acknowledged_by == user_id
    first_at = acked.acknowledged_at
    assert first_at is not None

    # 已確認過的不再覆寫
    again = repo.acknowledge_alert(
        session=session, alert_id=alert.id, user_id=uuid.uuid4()
    )
    assert again.acknowledged_by == user_id
    assert again.acknowledged_at == first_at

    with pytest.raises(NotFoundError):
        repo.acknowledge_alert(session=session, alert_id=uuid.uuid4(), user_id=user_id)
