"""app.repositories.mining 的事件建立與查詢覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import NotFoundError
from app.models import MiningIncidentStatus, User
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


def test_list_incidents_with_owners_joins_owner(session: Session) -> None:
    owner = User(
        email="11156077@ntub.edu.tw", full_name="王小明", hashed_password="x"
    )
    session.add(owner)
    session.flush()
    owned = repo.create_incident(
        session=session,
        vmid=200,
        user_id=owner.id,
        node="pve1",
        resource_type="qemu",
        avg_cpu=96.8,
        window_hours=6,
        now=NOW,
    )
    # 擁有者不存在（資料異常）：事件仍要列出，擁有者為 None
    orphan = _incident(session, 201, minutes=-5)
    session.commit()

    rows = repo.list_incidents_with_owners(session=session)
    assert [(i.id, o.email if o else None) for i, o in rows] == [
        (owned.id, "11156077@ntub.edu.tw"),
        (orphan.id, None),
    ]
