"""Tests for pure helpers in network services that don't need DB/SSH/Proxmox."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.network import ip_management_service as ipm
from app.services.network import nat_service as nat

# ─── ip_management_service.get_extra_blocked_subnets ────────────────────────


@dataclass
class _ConfigStub:
    extra_blocked_subnets: str | None


def test_extra_blocked_subnets_none_returns_empty() -> None:
    assert ipm.get_extra_blocked_subnets(None) == []
    assert ipm.get_extra_blocked_subnets(_ConfigStub(None)) == []
    assert ipm.get_extra_blocked_subnets(_ConfigStub("")) == []


def test_extra_blocked_subnets_splits_comma_and_newline() -> None:
    cfg = _ConfigStub("10.0.0.0/8, 192.168.0.0/16\n172.16.0.0/12")
    assert ipm.get_extra_blocked_subnets(cfg) == [
        "10.0.0.0/8",
        "192.168.0.0/16",
        "172.16.0.0/12",
    ]


def test_extra_blocked_subnets_dedups_preserving_order() -> None:
    cfg = _ConfigStub("10.0.0.0/8,10.0.0.0/8,192.168.0.0/16,10.0.0.0/8")
    assert ipm.get_extra_blocked_subnets(cfg) == ["10.0.0.0/8", "192.168.0.0/16"]


def test_extra_blocked_subnets_strips_whitespace_and_skips_empty() -> None:
    cfg = _ConfigStub(" 10.0.0.0/8 ,, , 192.168.0.0/16 ")
    assert ipm.get_extra_blocked_subnets(cfg) == ["10.0.0.0/8", "192.168.0.0/16"]


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
