"""Coverage tests for app.services.network.ip_management_service."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.infrastructure.ssh as ssh_pkg
from app.exceptions import BadRequestError, ConflictError
from app.models import BatchProvisionJob, IpAllocation, Resource, SubnetConfig
from app.services.network import ip_management_service as svc

BASE = {
    "cidr": "10.20.0.0/24",
    "gateway": "10.20.0.1",
    "bridge_name": "vmbr1",
    "gateway_vm_ip": "10.20.0.2",
}


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


@pytest.fixture
def forgotten(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    hosts: list[str] = []
    monkeypatch.setattr(ssh_pkg, "forget_host_key", hosts.append)
    return hosts


# ---------------------------------------------------------------- holder labels


def test_quick_practice_holder_label_with_invalid_session_id(db: Session) -> None:
    label = svc._quick_practice_holder_label(db, "not-a-uuid")
    assert "not-a-uuid" in label


def test_reservation_holder_label_for_missing_class(db: Session) -> None:
    class_id = uuid.uuid4()
    alloc = IpAllocation(
        ip_address="10.20.0.9", purpose="class_reserved", teaching_class_id=class_id
    )
    assert str(class_id) in (svc._reservation_holder_label(db, alloc) or "")


def test_ip_holder_label_falls_back_to_description(db: Session) -> None:
    alloc = IpAllocation(
        ip_address="10.20.0.9",
        purpose="manual",
        reservation_key="other:key",
        description="手動保留",
    )
    assert svc._reservation_holder_label(db, alloc) is None
    assert svc._ip_holder_label(db, alloc) == "手動保留"


# ---------------------------------------------------------------- upsert / delete


def test_upsert_rejects_identical_gateway_ips(db: Session) -> None:
    with pytest.raises(BadRequestError):
        svc.upsert_subnet_config(db, **{**BASE, "gateway_vm_ip": BASE["gateway"]})


def test_upsert_rejects_blocked_subnet_overlapping_lab(db: Session) -> None:
    with pytest.raises(BadRequestError):
        svc.upsert_subnet_config(db, **BASE, extra_blocked_subnets=["10.20.0.0/16"])


def test_upsert_blocks_cidr_change_with_machine_allocations(db: Session) -> None:
    svc.upsert_subnet_config(db, **BASE)
    db.add(IpAllocation(ip_address="10.20.0.50", purpose="vm", vmid=100))
    db.commit()

    with pytest.raises(ConflictError):
        svc.upsert_subnet_config(
            db,
            cidr="10.30.0.0/24",
            gateway="10.30.0.1",
            bridge_name="vmbr1",
            gateway_vm_ip="10.30.0.2",
        )


def test_upsert_updates_forward_port_range(db: Session) -> None:
    svc.upsert_subnet_config(db, **BASE)

    config = svc.upsert_subnet_config(
        db, **BASE, forward_port_start=31000, forward_port_end=31999
    )

    assert (config.forward_port_start, config.forward_port_end) == (31000, 31999)


def test_delete_subnet_config_requires_config(db: Session) -> None:
    with pytest.raises(BadRequestError):
        svc.delete_subnet_config(db)


def test_delete_subnet_config_blocked_by_machine_allocations(db: Session) -> None:
    svc.upsert_subnet_config(db, **BASE)
    db.add(IpAllocation(ip_address="10.20.0.50", purpose="lxc", vmid=101))
    db.commit()

    with pytest.raises(ConflictError):
        svc.delete_subnet_config(db)


# ---------------------------------------------------------------- reserve / allocate


def test_reserve_ips_requires_subnet(db: Session) -> None:
    with pytest.raises(BadRequestError):
        svc.reserve_ips(db, teaching_class_id=None, reservation_keys=["k"])


def test_allocate_ip_requires_subnet(db: Session) -> None:
    with pytest.raises(BadRequestError):
        svc.allocate_ip(db, 100, "vm")


def test_allocate_ip_reserved_row_missing(db: Session) -> None:
    svc.upsert_subnet_config(db, **BASE)
    with pytest.raises(ConflictError):
        svc.allocate_ip(db, 200, "vm", reservation_key="quick:missing:web")


def test_allocate_ip_reserved_row_used_by_other_vmid(db: Session) -> None:
    svc.upsert_subnet_config(db, **BASE)
    db.add(
        IpAllocation(
            ip_address="10.20.0.60",
            purpose="vm",
            vmid=300,
            reservation_key="quick:b:web",
        )
    )
    db.commit()

    with pytest.raises(ConflictError):
        svc.allocate_ip(db, 301, "vm", reservation_key="quick:b:web")


def test_allocate_ip_pool_exhausted(db: Session) -> None:
    svc.upsert_subnet_config(
        db,
        cidr="10.40.0.0/29",
        gateway="10.40.0.1",
        bridge_name="vmbr1",
        gateway_vm_ip="10.40.0.2",
    )
    allocated = [svc.allocate_ip(db, 400 + index, "vm") for index in range(4)]
    assert allocated == ["10.40.0.3", "10.40.0.4", "10.40.0.5", "10.40.0.6"]

    with pytest.raises(ConflictError):
        svc.allocate_ip(db, 499, "vm")


# ---------------------------------------------------------------- stale reclaim


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, *, resource=None, job=None, node=None):
        self.resource = resource
        self.job = job
        self.node = node

    def get(self, model, _key):
        if model is Resource:
            return self.resource
        if model is BatchProvisionJob:
            return self.job
        return None

    def exec(self, _statement):
        return _FakeResult(self.node)

    def add(self, _value):
        pass

    def flush(self):
        pass


def _stale_row(class_id: uuid.UUID, user_id: uuid.UUID, **overrides) -> IpAllocation:
    values = {
        "ip_address": "10.20.0.70",
        "purpose": "lxc",
        "vmid": 487,
        "reservation_key": f"{class_id}:node-b:{user_id}",
        "teaching_class_id": class_id,
    }
    values.update(overrides)
    return IpAllocation(**values)


def test_reclaim_stale_rejects_unrelated_rows() -> None:
    class_id, user_id = uuid.uuid4(), uuid.uuid4()
    resource = SimpleNamespace(
        vmid=487, teaching_class_id=class_id, user_id=user_id, batch_job_id=uuid.uuid4()
    )
    reclaim = svc._reclaim_stale_class_reservation

    # 沒有預留鍵
    assert (
        reclaim(_FakeSession(), _stale_row(class_id, user_id, reservation_key=None))
        is False
    )
    # 鍵格式不對
    assert (
        reclaim(_FakeSession(), _stale_row(class_id, user_id, reservation_key="nokey"))
        is False
    )
    # 鍵裡的 UUID 不合法
    assert (
        reclaim(
            _FakeSession(), _stale_row(class_id, user_id, reservation_key="x:node:y")
        )
        is False
    )
    # 鍵裡的班級和列上的班級不同
    assert (
        reclaim(
            _FakeSession(),
            _stale_row(
                class_id, user_id, reservation_key=f"{uuid.uuid4()}:n:{user_id}"
            ),
        )
        is False
    )
    # 沒有資源
    assert reclaim(_FakeSession(), _stale_row(class_id, user_id)) is False
    # 資源屬於別人
    other = SimpleNamespace(**{**vars(resource), "user_id": uuid.uuid4()})
    assert reclaim(_FakeSession(resource=other), _stale_row(class_id, user_id)) is False
    # 找不到批次任務
    assert (
        reclaim(_FakeSession(resource=resource), _stale_row(class_id, user_id)) is False
    )
    # 資源就是這個邏輯節點
    job = SimpleNamespace(id=resource.batch_job_id, teaching_class_id=class_id)
    assert (
        reclaim(
            _FakeSession(resource=resource, job=job, node=object()),
            _stale_row(class_id, user_id),
        )
        is False
    )


# ---------------------------------------------------------------- link / release


def test_link_ip_to_resource(db: Session) -> None:
    db.add(
        IpAllocation(
            ip_address="10.20.0.80", purpose="vm", vmid=800, reservation_key="k:800"
        )
    )
    db.commit()

    assert svc.link_ip_to_resource(db, 800, reservation_key="k:800") is True
    assert svc.link_ip_to_resource(db, 801) is False


def test_release_ip_deletes_row_and_tolerates_host_key_errors(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.add(IpAllocation(ip_address="10.20.0.90", purpose="vm", vmid=900))
    db.commit()

    def broken(_host: str) -> None:
        raise OSError("known_hosts locked")

    monkeypatch.setattr(ssh_pkg, "forget_host_key", broken)

    assert svc.release_ip(db, 900) == "10.20.0.90"
    assert db.exec(select(IpAllocation)).all() == []


# ---------------------------------------------------------------- queries


def test_get_all_allocations_sorts_numerically(db: Session) -> None:
    db.add_all(
        [
            IpAllocation(ip_address="10.20.0.100", purpose="vm", vmid=1),
            IpAllocation(ip_address="10.20.0.9", purpose="vm", vmid=2),
        ]
    )
    db.commit()

    assert [a.ip_address for a in svc.get_all_allocations(db)] == [
        "10.20.0.9",
        "10.20.0.100",
    ]


def test_get_ip_stats_without_subnet_and_for_single_host(db: Session) -> None:
    assert svc.get_ip_stats(db) == {"total": 0, "used": 0, "available": 0}

    db.add(
        SubnetConfig(
            id=1,
            cidr="10.50.0.5/32",
            gateway="10.50.0.5",
            bridge_name="vmbr0",
            gateway_vm_ip="10.50.0.6",
        )
    )
    db.commit()

    assert svc.get_ip_stats(db) == {"total": 0, "used": 0, "available": 0}


def test_ensure_subnet_configured_raises_without_config(db: Session) -> None:
    with pytest.raises(BadRequestError):
        svc.ensure_subnet_configured(db)


def test_network_config_for_vm_includes_dns(db: Session) -> None:
    svc.upsert_subnet_config(db, **BASE, dns_servers="1.1.1.1,8.8.8.8")

    config = svc.get_network_config_for_vm(db)

    assert config["prefix_len"] == 24
    assert config["dns_servers"] == "1.1.1.1,8.8.8.8"


def test_quick_reservation_round_trip(db: Session, forgotten: list[str]) -> None:
    config = svc.upsert_subnet_config(
        db, **BASE, extra_blocked_subnets=["192.0.2.0/24"]
    )
    assert svc.get_extra_blocked_subnets(config) == ["192.0.2.0/24"]
    assert svc.get_forward_port_range(config) == (30000, 39999)
    keys = ["quick:s1:web", "quick:s1:db"]
    first = svc.reserve_ips(db, teaching_class_id=None, reservation_keys=keys)
    assert svc.reserve_ips(db, teaching_class_id=None, reservation_keys=keys) == first
    ip = svc.allocate_ip(db, 700, "vm", reservation_key="quick:s1:web")
    assert svc.get_allocation_by_ip(db, ip).vmid == 700

    assert svc.release_ip(db, 700, restore_reservation=True) == ip
    row = svc.get_allocation_by_ip(db, ip)
    assert row.purpose == "quick_practice_reserved" and row.vmid is None
    assert svc.release_ip(db, 700) is None
    assert svc.release_reservations_by_prefix(db, "quick:s1:") == 2
