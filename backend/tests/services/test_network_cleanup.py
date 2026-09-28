"""services/network 整理後的共用 helper 測試（行為不變的重構）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import settings
from app.models import IpAllocation
from app.schemas.firewall import PortSpec
from app.services.network import (
    cloudflare_service,
    gateway_service,
    ip_management_service,
    wireguard_service,
)
from app.services.network import firewall_service as fw

# ─── firewall_service ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "target", "port", "protocol", "expected"),
    [
        ("gateway", 101, 80, "tcp", {"type": "internet_connection", "target_vmid": 101}),
        ("gateway", 101, 0, "icmp", {"type": "internet_connection", "target_vmid": 101}),
        (101, "gateway", 53, "udp", {"type": "gateway_connection", "source_vmid": 101}),
        (101, "gateway", 0, "esp", {"type": "gateway_connection", "source_vmid": 101}),
        (101, 102, 22, "tcp", {"type": "connection", "source_vmid": 101}),
    ],
)
def test_make_connection_comment_round_trips_gateway_endpoints(
    source: int | str, target: int | str, port: int, protocol: str, expected: dict
) -> None:
    parsed = fw._parse_connection_comment(
        fw._make_connection_comment(source, target, port, protocol)
    )
    assert parsed is not None
    for key, value in expected.items():
        assert parsed[key] == value
    assert parsed["port"] == port
    assert parsed["protocol"] == protocol


def test_matches_ports_none_means_any_port() -> None:
    parsed = {"port": 80, "protocol": "tcp"}
    assert fw._matches_ports(parsed, None) is True
    assert fw._matches_ports(parsed, [PortSpec(port=80, protocol="tcp")]) is True
    assert fw._matches_ports(parsed, [PortSpec(port=80, protocol="udp")]) is False
    assert fw._matches_ports(parsed, []) is False


def test_internet_node_is_gateway_node_at_given_position() -> None:
    node = fw._internet_node(12.5, 34.0)
    assert node.vmid is None
    assert node.name == "Internet"
    assert node.node_type == "gateway"
    assert (node.position_x, node.position_y) == (12.5, 34.0)


# ─── gateway / wireguard endpoint ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("vpn.example.com", "vpn.example.com:51820"),
        ("203.0.113.5", "203.0.113.5:51820"),
        ("2001:db8::1", "[2001:db8::1]:51820"),
    ],
)
def test_format_endpoint_brackets_ipv6(host: str, expected: str) -> None:
    assert gateway_service.format_endpoint(host, 51820) == expected


def test_wireguard_endpoint_host_prefers_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    config = SimpleNamespace(host="10.0.0.1")
    monkeypatch.setattr(settings, "WIREGUARD_ENDPOINT_HOST", "  vpn.example.com ")
    assert gateway_service.wireguard_endpoint_host(config) == "vpn.example.com"
    monkeypatch.setattr(settings, "WIREGUARD_ENDPOINT_HOST", "")
    assert gateway_service.wireguard_endpoint_host(config) == "10.0.0.1"
    assert gateway_service.wireguard_endpoint_host(None) == ""


def test_session_ttl_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "WIREGUARD_SESSION_TTL_SECONDS", 5)
    assert wireguard_service._session_ttl() == 60
    monkeypatch.setattr(settings, "WIREGUARD_SESSION_TTL_SECONDS", 10**6)
    assert wireguard_service._session_ttl() == 86400
    monkeypatch.setattr(settings, "WIREGUARD_SESSION_TTL_SECONDS", 3600)
    assert wireguard_service._session_ttl() == 3600


# ─── 共用的網域／資源 helper ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("example.com", True),
        ("a.b.example.com", True),
        ("", False),
        ("localhost", False),
        ("-bad.example.com", False),
        ("a" * 64 + ".com", False),
    ],
)
def test_is_valid_hostname(value: str, valid: bool) -> None:
    assert cloudflare_service.is_valid_hostname(value) is valid


# ─── ip_management_service ────────────────────────────────────────────────────


@pytest.fixture
def db_session():
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


def test_has_vm_allocations_ignores_reservations(db_session: Session) -> None:
    db_session.add(IpAllocation(ip_address="10.0.0.1", purpose="subnet_gateway"))
    db_session.commit()
    assert ip_management_service._has_vm_allocations(db_session) is False

    db_session.add(IpAllocation(ip_address="10.0.0.20", purpose="lxc", vmid=120))
    db_session.commit()
    assert ip_management_service._has_vm_allocations(db_session) is True


def test_release_ip_restores_quick_practice_reservation_purpose(
    db_session: Session,
) -> None:
    db_session.add(
        IpAllocation(
            ip_address="10.0.0.30",
            purpose="lxc",
            vmid=130,
            reservation_key="quick:session:node-a",
        )
    )
    db_session.commit()

    released = ip_management_service.release_ip(
        db_session, 130, restore_reservation=True
    )
    db_session.commit()

    assert released == "10.0.0.30"
    row = db_session.exec(select(IpAllocation)).one()
    assert row.vmid is None
    assert row.purpose == "quick_practice_reserved"
    assert row.description == "快速練習預留 quick:session:node-a"


def test_release_ip_restores_class_reservation_purpose(db_session: Session) -> None:
    db_session.add(
        IpAllocation(
            ip_address="10.0.0.31",
            purpose="vm",
            vmid=131,
            reservation_key="class:node-a:user",
            teaching_class_id=uuid.uuid4(),
        )
    )
    db_session.commit()

    ip_management_service.release_ip(db_session, 131, restore_reservation=True)
    db_session.commit()

    row = db_session.exec(select(IpAllocation)).one()
    assert row.vmid is None
    assert row.purpose == "class_reserved"
    assert row.description == "班級預留 class:node-a:user"
