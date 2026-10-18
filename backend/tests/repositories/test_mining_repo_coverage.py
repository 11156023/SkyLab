"""app.repositories.mining 的事件建立與查詢覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import NotFoundError
from app.models import MiningIncidentStatus
from app.repositories import mining as repo


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


NOW = datetime(2099, 1, 1, tzinfo=UTC)


def _incident(session: Session, vmid: int, minutes: int = 0):
    return repo.create_incident(
        session=session,
        vmid=vmid,
        user_id=uuid.uuid4(),
        node="pve1",
        resource_type="qemu",
        avg_cpu=97.5,
        window_hours=6,
        now=NOW + timedelta(minutes=minutes),
    )


def test_create_get_and_open_checks(session: Session) -> None:
    incident = _incident(session, 100)
    assert incident.status == MiningIncidentStatus.detected
    assert repo.get_incident(session=session, incident_id=incident.id) is incident
    with pytest.raises(NotFoundError):
        repo.get_incident(session=session, incident_id=uuid.uuid4())

    assert repo.has_open_incident(session=session, vmid=100) is True
    assert repo.has_open_incident(session=session, vmid=101) is False

    closed = _incident(session, 102, minutes=5)
    closed.status = MiningIncidentStatus.dismissed
    session.add(closed)
    session.flush()
    assert repo.list_open_incident_vmids(session=session) == {100}


def test_list_incidents_with_status_filter(session: Session) -> None:
    first = _incident(session, 100, minutes=0)
    second = _incident(session, 101, minutes=10)
    second.status = MiningIncidentStatus.suspended
    session.add(second)
    session.commit()

    assert [
        i.id for i, _owner in repo.list_incidents_with_owners(session=session)
    ] == [second.id, first.id]
    assert [
        i.id
        for i, _owner in repo.list_incidents_with_owners(
            session=session, status=MiningIncidentStatus.detected
        )
    ] == [first.id]
