"""app.repositories.resource 的查詢、班級指派、IP 快取與排程欄位覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import IpAllocation, Resource, ResourceNetwork
from app.repositories import resource as repo

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _res(session: Session, vmid: int, **kw) -> Resource:
    kw.setdefault("user_id", uuid.uuid4())
    kw.setdefault("environment_type", "vm")
    return repo.create_resource(session=session, vmid=vmid, **kw)


def test_listing_queries(session: Session) -> None:
    uid = uuid.uuid4()
    class_a = uuid.uuid4()
    class_b = uuid.uuid4()
    _res(session, 110, user_id=uid)
    _res(session, 111, user_id=uid, teaching_class_id=class_a)
    _res(session, 112, teaching_class_id=class_b)

    assert {r.vmid for r in repo.get_all_resources(session=session)} == {110, 111, 112}
    assert {
        r.vmid for r in repo.get_resources_by_user(session=session, user_id=uid)
    } == {
        110,
        111,
    }
    assert [
        r.vmid
        for r in repo.get_resources_by_teaching_class(
            session=session, teaching_class_id=class_a
        )
    ] == [111]
    assert (
        repo.get_resources_by_teaching_classes(session=session, teaching_class_ids=[])
        == []
    )
    assert {
        r.vmid
        for r in repo.get_resources_by_teaching_classes(
            session=session, teaching_class_ids=iter([class_a, class_b])
        )
    } == {111, 112}


def test_get_resources_by_vmids(session: Session) -> None:
    _res(session, 120)
    _res(session, 121)
    assert repo.get_resources_by_vmids(session=session, vmids=[None]) == {}
    found = repo.get_resources_by_vmids(session=session, vmids=[120, 121, 999, None])
    assert set(found) == {120, 121}


@pytest.mark.parametrize("commit", [True, False])
def test_assign_to_teaching_class(session: Session, commit: bool) -> None:
    _res(session, 130)
    cls = uuid.uuid4()
    assert (
        repo.assign_to_teaching_class(
            session=session, vmid=999, teaching_class_id=cls, commit=commit
        )
        is None
    )
    r = repo.assign_to_teaching_class(
        session=session, vmid=130, teaching_class_id=cls, commit=commit
    )
    assert r is not None
    assert r.teaching_class_id == cls
    assert r.allocation_scope == "teaching_class"
    assert r.control_policy == "class_member"


def test_update_ip_address_creates_then_updates(session: Session) -> None:
    repo.update_ip_address(session=session, vmid=999, ip_address="10.0.0.1")
    assert repo.get_cached_ip_address(session=session, vmid=999) is None

    _res(session, 140)
    repo.update_ip_address(session=session, vmid=140, ip_address="10.0.0.2")
    assert repo.get_cached_ip_address(session=session, vmid=140) == "10.0.0.2"
    repo.update_ip_address(session=session, vmid=140, ip_address="10.0.0.3")
    network = repo.get_resource_network_by_vmid(session=session, vmid=140)
    assert network.ip_address == "10.0.0.3"
    assert network.source == "proxmox"


def test_network_and_allocation_lookups_without_exec() -> None:
    fake = object()
    assert repo.get_resource_network_by_vmid(session=fake, vmid=1) is None  # type: ignore[arg-type]
    assert repo.get_allocated_ip_address(session=fake, vmid=1) is None  # type: ignore[arg-type]
    assert repo.get_cached_ip_address(session=fake, vmid=1) is None  # type: ignore[arg-type]


def test_sync_ip_cache_many_empty(session: Session) -> None:
    assert repo.sync_ip_cache_many(session=session, live_ips={}) == {}


def test_sync_ip_cache_many_persists_changes(session: Session) -> None:
    _res(session, 150)  # has cache row that changes
    _res(session, 151)  # no cache row yet
    _res(session, 152)  # cache unchanged
    session.add(ResourceNetwork(resource_vmid=150, ip_address="10.1.0.1"))
    session.add(ResourceNetwork(resource_vmid=152, ip_address="10.1.0.3"))
    session.add(
        IpAllocation(ip_address="10.1.0.9", purpose="vm", vmid=153, resource_vmid=None)
    )
    session.commit()

    result = repo.sync_ip_cache_many(
        session=session,
        live_ips={
            150: "10.1.0.2",
            151: "10.1.0.4",
            152: "10.1.0.3",
            153: None,
            # vmid without a Resource row: skipped by the writer
            154: "10.1.0.5",
        },
    )

    assert result == {
        150: "10.1.0.2",
        151: "10.1.0.4",
        152: "10.1.0.3",
        153: "10.1.0.9",
        154: "10.1.0.5",
    }
    session.expire_all()
    rows = {
        r.resource_vmid: r.ip_address
        for r in session.exec(select(ResourceNetwork)).all()
    }
    assert rows == {150: "10.1.0.2", 151: "10.1.0.4", 152: "10.1.0.3"}
    assert all(
        r.source == "proxmox"
        for r in session.exec(select(ResourceNetwork)).all()
        if r.resource_vmid in (150, 151)
    )


@pytest.mark.parametrize("commit", [True, False])
def test_set_auto_stop_and_due_list(session: Session, commit: bool) -> None:
    _res(session, 180)
    _res(session, 181)
    assert (
        repo.set_auto_stop(
            session=session,
            vmid=999,
            auto_stop_at=NOW,
            auto_stop_reason="idle",
            commit=commit,
        )
        is None
    )
    r = repo.set_auto_stop(
        session=session,
        vmid=180,
        auto_stop_at=NOW - timedelta(minutes=1),
        auto_stop_reason="idle",
        commit=commit,
    )
    assert r.auto_stop_reason == "idle"
    repo.set_auto_stop(
        session=session,
        vmid=181,
        auto_stop_at=NOW + timedelta(hours=1),
        auto_stop_reason="ttl_expired",
        commit=commit,
    )
    due = repo.list_due_auto_stops(session=session, now=NOW)
    assert [x.vmid for x in due] == [180]


def test_list_idle_scan_candidates(session: Session) -> None:
    assert (
        repo.list_idle_scan_candidates(
            session=session, vmids=[], checked_before=NOW, limit=5
        )
        == []
    )
    _res(session, 200)
    old = _res(session, 201)
    recent = _res(session, 202)
    _res(session, 203, allocation_scope="teaching_class")
    old.idle_checked_at = NOW - timedelta(hours=2)
    recent.idle_checked_at = NOW + timedelta(hours=1)
    session.add(old)
    session.add(recent)
    session.commit()

    got = repo.list_idle_scan_candidates(
        session=session, vmids=[200, 201, 202, 203], checked_before=NOW, limit=5
    )
    assert [r.vmid for r in got] == [200, 201]
    got = repo.list_idle_scan_candidates(
        session=session, vmids=[200, 201, 202, 203], checked_before=NOW, limit=1
    )
    assert [r.vmid for r in got] == [200]


def test_list_mining_scan_candidates(session: Session) -> None:
    assert (
        repo.list_mining_scan_candidates(
            session=session, vmids=[], checked_before=NOW, limit=5
        )
        == []
    )
    _res(session, 210, allocation_scope="teaching_class")
    old = _res(session, 211)
    recent = _res(session, 212)
    old.mining_checked_at = NOW - timedelta(hours=2)
    recent.mining_checked_at = NOW + timedelta(hours=1)
    session.add(old)
    session.add(recent)
    session.commit()

    got = repo.list_mining_scan_candidates(
        session=session, vmids=[210, 211, 212], checked_before=NOW, limit=5
    )
    assert [r.vmid for r in got] == [210, 211]
