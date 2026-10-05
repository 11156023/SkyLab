"""Tests for pure helpers in network services that don't need DB/SSH/Proxmox."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from app.schemas.ip_management import SubnetConfigCreate, split_dns_servers
from app.services.network import ip_management_service as ipm
from app.services.network import nat_service as nat

# ─── 子網設定的多值欄位（subnet_dns_servers／subnet_blocked_subnets）────────


@dataclass
class _ConfigStub:
    blocked_subnet_list: list[str]
    dns_server_list: list[str]


def test_extra_blocked_subnets_none_returns_empty() -> None:
    assert ipm.get_extra_blocked_subnets(None) == []
    assert ipm.get_extra_blocked_subnets(_ConfigStub([], [])) == []


def test_extra_blocked_subnets_reads_child_rows_in_order() -> None:
    cfg = _ConfigStub(["10.0.0.0/8", "192.168.0.0/16"], [])
    assert ipm.get_extra_blocked_subnets(cfg) == ["10.0.0.0/8", "192.168.0.0/16"]


def test_dns_servers_joined_with_commas_or_none() -> None:
    assert ipm.get_dns_servers(None) is None
    assert ipm.get_dns_servers(_ConfigStub([], [])) is None
    assert ipm.get_dns_servers(_ConfigStub([], ["8.8.8.8", "1.1.1.1"])) == "8.8.8.8,1.1.1.1"


def test_split_dns_servers_accepts_commas_semicolons_and_spaces() -> None:
    assert split_dns_servers(" 8.8.8.8, 1.1.1.1;8.8.8.8  9.9.9.9 ") == [
        "8.8.8.8",
        "1.1.1.1",
        "9.9.9.9",
    ]
    assert split_dns_servers(None) == []
    assert split_dns_servers("  ") == []


def _subnet_payload(**overrides: object) -> dict[str, object]:
    return {
        "cidr": "10.0.0.0/24",
        "gateway": "10.0.0.1",
        "bridge_name": "vmbr0",
        "gateway_vm_ip": "10.0.0.2",
        **overrides,
    }


def test_subnet_create_normalizes_dns_servers() -> None:
    body = SubnetConfigCreate(**_subnet_payload(dns_servers="8.8.8.8 1.1.1.1"))
    assert body.dns_servers == "8.8.8.8,1.1.1.1"
    assert SubnetConfigCreate(**_subnet_payload(dns_servers=" ")).dns_servers is None


def test_subnet_create_rejects_non_ip_dns_server() -> None:
    with pytest.raises(ValidationError):
        SubnetConfigCreate(**_subnet_payload(dns_servers="8.8.8.8,dns.example"))


def test_subnet_create_dedups_blocked_subnets_preserving_order() -> None:
    body = SubnetConfigCreate(
        **_subnet_payload(
            extra_blocked_subnets=" 10.0.0.0/8 ,, 192.168.0.0/16\n10.0.0.0/8 "
        )
    )
    assert body.extra_blocked_subnets == ["10.0.0.0/8", "192.168.0.0/16"]


# ─── nat_service.check_port_available / RESERVED_PORTS ──────────────────────


def test_reserved_ports_cover_pve_and_gateway_entrypoints() -> None:
    # 22/80/443 是 Gateway 自己在用（SSH、nginx http），8006 是 PVE Web UI
    assert {22, 80, 443, 8006}.issubset(nat.RESERVED_PORTS)


def test_check_port_available_rejects_reserved_port() -> None:
    from app.exceptions import BadRequestError

    try:
        nat.check_port_available(443, "tcp", object())
    except BadRequestError:
        return
    raise AssertionError("reserved port should be rejected")
