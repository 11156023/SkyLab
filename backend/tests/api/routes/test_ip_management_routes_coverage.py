"""app/api/routes/ip_management.py 路由層的單元測試（直接呼叫路由函式，service 全部替換）。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.routes import ip_management as ipm
from app.services.network import firewall_service

SESSION = object()
NOW = datetime(2030, 1, 1, tzinfo=UTC)
STATS = {"total": 254, "used": 4, "available": 250}


def _config() -> Any:
    return SimpleNamespace(
        cidr="10.0.0.0/24",
        gateway="10.0.0.1",
        bridge_name="vmbr1",
        vlan_tag=None,
        gateway_vm_ip="10.0.0.2",
        platform_ip=None,
        forward_port_start=30000,
        forward_port_end=39999,
        forward_public_host=None,
        updated_at=NOW,
    )


@pytest.fixture()
def svc(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"config": _config(), "upserts": [], "deleted": 0}
    s = ipm.ip_management_service
    monkeypatch.setattr(s, "get_subnet_config", lambda session: state["config"])
    monkeypatch.setattr(s, "get_ip_stats", lambda session: STATS)
    monkeypatch.setattr(s, "get_dns_servers", lambda config: "1.1.1.1")
    monkeypatch.setattr(
        s, "get_extra_blocked_subnets", lambda config: ["192.168.0.0/16"]
    )

    def _upsert(session: Any, **kwargs: Any) -> Any:
        state["upserts"].append(kwargs)
        return state["config"]

    def _delete(session: Any) -> None:
        state["deleted"] += 1

    monkeypatch.setattr(s, "upsert_subnet_config", _upsert)
    monkeypatch.setattr(s, "delete_subnet_config", _delete)
    return state


def test_get_subnet_config_none(svc: dict[str, Any]) -> None:
    svc["config"] = None
    assert ipm.get_subnet_config(session=SESSION, _=None) is None


def test_get_subnet_config_present(svc: dict[str, Any]) -> None:
    result = ipm.get_subnet_config(session=SESSION, _=None)
    assert result.cidr == "10.0.0.0/24"
    assert result.dns_servers == "1.1.1.1"
    assert result.extra_blocked_subnets == ["192.168.0.0/16"]
    assert result.available_ips == 250
    assert result.block_sync is None


def _body() -> Any:
    return SimpleNamespace(
        cidr="10.0.0.0/24",
        gateway="10.0.0.1",
        bridge_name="vmbr1",
        vlan_tag=None,
        gateway_vm_ip="10.0.0.2",
        platform_ip="10.0.0.3",
        dns_servers="1.1.1.1",
        extra_blocked_subnets=["192.168.0.0/16"],
        forward_port_start=30000,
        forward_port_end=39999,
        forward_public_host=None,
    )


def test_upsert_subnet_config_sync_partial_errors(
    monkeypatch: pytest.MonkeyPatch, svc: dict[str, Any]
) -> None:
    monkeypatch.setattr(
        firewall_service,
        "sync_extra_block_rules",
        lambda: {"extra_blocks": {"errors": [{"vmid": 101, "error": "pve down"}]}},
    )
    result = ipm.upsert_subnet_config(session=SESSION, _=None, body=_body())
    assert result.block_sync.errors[0].vmid == 101


def test_upsert_subnet_config_sync_raises(
    monkeypatch: pytest.MonkeyPatch, svc: dict[str, Any]
) -> None:
    def _boom() -> dict:
        raise RuntimeError("sync failed")

    monkeypatch.setattr(firewall_service, "sync_extra_block_rules", _boom)
    result = ipm.upsert_subnet_config(session=SESSION, _=None, body=_body())
    assert result.block_sync.targets == ["192.168.0.0/16"]
    assert result.block_sync.errors[0].vmid is None
    assert result.block_sync.errors[0].error == "sync failed"


def test_delete_subnet_config(svc: dict[str, Any]) -> None:
    result = ipm.delete_subnet_config(session=SESSION, _=None)
    assert result.message
    assert svc["deleted"] == 1


def test_list_allocations(monkeypatch: pytest.MonkeyPatch) -> None:
    allocs = [
        SimpleNamespace(
            ip_address="10.0.0.10",
            purpose="vm",
            vmid=101,
            description=None,
            allocated_at=NOW,
        )
    ]
    monkeypatch.setattr(
        ipm.ip_management_service, "get_all_allocations", lambda session: allocs
    )
    result = ipm.list_allocations(session=SESSION, _=None)
    assert result.total == 1
    assert result.allocations[0].ip_address == "10.0.0.10"


def test_get_subnet_status(svc: dict[str, Any]) -> None:
    status = ipm.get_subnet_status(session=SESSION, _=None)
    assert status.configured is True
    assert status.used_ips == 4
    svc["config"] = None
    assert ipm.get_subnet_status(session=SESSION, _=None).configured is False
