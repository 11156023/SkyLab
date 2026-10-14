"""Publishing must stay independent of VM outbound firewall isolation."""

from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError
from app.services.network import ip_management_service
from app.services.network.publish_target_policy import assert_publishable_vm_ip


@pytest.fixture
def publish_config(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    config = SimpleNamespace(
        cidr="10.10.0.0/16",
        gateway="10.10.0.1",
        gateway_vm_ip="10.10.0.2",
        blocked_subnet_list=["10.10.0.0/16"],
    )
    monkeypatch.setattr(ip_management_service, "get_subnet_config", lambda _: config)
    return config


def _session() -> SimpleNamespace:
    connections = [
        SimpleNamespace(host="10.10.0.3", gateway_ip="10.10.0.4"),
        SimpleNamespace(host="10.10.0.5", gateway_ip="10.10.0.6"),
    ]
    return SimpleNamespace(exec=lambda _: SimpleNamespace(all=lambda: connections))


@pytest.mark.parametrize("outbound_block", ["10.10.0.0/16", "10.0.0.0/8", "10.10.0.11"])
def test_vm_can_be_published_with_outbound_isolation(
    publish_config: SimpleNamespace, outbound_block: str,
) -> None:
    publish_config.blocked_subnet_list = [outbound_block]

    assert_publishable_vm_ip(_session(), "10.10.0.11")

    # Publishing does not remove or rewrite the outbound firewall configuration.
    assert ip_management_service.get_extra_blocked_subnets(publish_config) == [outbound_block]


@pytest.mark.parametrize("ip", [f"10.10.0.{i}" for i in range(1, 7)])
def test_infrastructure_in_vm_subnet_cannot_be_published(
    publish_config: SimpleNamespace, ip: str,
) -> None:
    with pytest.raises(BadRequestError):
        assert_publishable_vm_ip(_session(), ip)


@pytest.mark.parametrize("ip", ["192.168.100.125", "10.11.0.11"])
def test_targets_outside_vm_subnet_cannot_be_published(
    publish_config: SimpleNamespace, ip: str,
) -> None:
    with pytest.raises(BadRequestError):
        assert_publishable_vm_ip(_session(), ip)


# ── 沒有配發紀錄的機器：不可把規則指到配給別台或被預留的 IP ─────────────────


@pytest.fixture
def no_own_allocation(
    monkeypatch: pytest.MonkeyPatch, publish_config: SimpleNamespace
) -> dict[str, SimpleNamespace]:
    from app.repositories import resource as resource_repo

    holders: dict[str, SimpleNamespace] = {}
    monkeypatch.setattr(
        resource_repo, "get_allocated_ip_address", lambda *, session, vmid: None
    )
    monkeypatch.setattr(
        ip_management_service,
        "get_allocation_by_ip",
        lambda session, ip: holders.get(ip),
    )
    return holders


def test_unallocated_vm_cannot_target_ip_allocated_to_another_vmid(
    no_own_allocation: dict[str, SimpleNamespace],
) -> None:
    no_own_allocation["10.10.1.102"] = SimpleNamespace(vmid=102, purpose="vm")

    with pytest.raises(BadRequestError) as excinfo:
        assert_publishable_vm_ip(_session(), "10.10.1.102", vmid=999)

    assert "102" in str(excinfo.value)


@pytest.mark.parametrize("purpose", ["class_reserved", "quick_practice_reserved"])
def test_unallocated_vm_cannot_target_reserved_ip(
    no_own_allocation: dict[str, SimpleNamespace], purpose: str
) -> None:
    no_own_allocation["10.10.1.50"] = SimpleNamespace(vmid=None, purpose=purpose)

    with pytest.raises(BadRequestError):
        assert_publishable_vm_ip(_session(), "10.10.1.50", vmid=999)


def test_unallocated_vm_can_target_free_ip_in_subnet(
    no_own_allocation: dict[str, SimpleNamespace],
) -> None:
    no_own_allocation["10.10.1.102"] = SimpleNamespace(vmid=102, purpose="vm")

    assert_publishable_vm_ip(_session(), "10.10.9.9", vmid=999)


def test_allocated_vm_still_checked_against_its_own_allocation(
    monkeypatch: pytest.MonkeyPatch, publish_config: SimpleNamespace
) -> None:
    """有配發紀錄的機器維持原本的規則：只能是配給它的那個 IP。"""
    from app.repositories import resource as resource_repo

    monkeypatch.setattr(
        resource_repo,
        "get_allocated_ip_address",
        lambda *, session, vmid: "10.10.1.101",
    )

    def _must_not_be_called(session, ip):
        raise AssertionError("allocated VMs are checked against their own IP only")

    monkeypatch.setattr(ip_management_service, "get_allocation_by_ip", _must_not_be_called)

    assert_publishable_vm_ip(_session(), "10.10.1.101", vmid=101)
    with pytest.raises(BadRequestError):
        assert_publishable_vm_ip(_session(), "10.10.1.102", vmid=101)


def test_get_allocation_by_ip_reads_the_allocation_row() -> None:
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from app.models import IpAllocation

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add(IpAllocation(ip_address="10.10.1.102", purpose="vm", vmid=102))
            session.commit()

            row = ip_management_service.get_allocation_by_ip(session, "10.10.1.102")
            assert row is not None and row.vmid == 102
            assert ip_management_service.get_allocation_by_ip(session, "10.10.1.103") is None
    finally:
        engine.dispose()
