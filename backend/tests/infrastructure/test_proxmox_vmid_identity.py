"""VMID 在多連線下的識別：找機器不能猜、配發不能撞到 DB 已登記的 VMID。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.exceptions import NotFoundError, ProxmoxError
from app.infrastructure.proxmox import operations


def _vm(vmid: int, node: str, kind: str = "qemu") -> dict:
    return {"vmid": vmid, "node": node, "type": kind}


def test_find_resource_returns_single_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        operations, "_pool_vms", lambda: [_vm(101, "pve-a"), _vm(102, "pve-b")]
    )
    assert operations.find_resource(102)["node"] == "pve-b"


def test_find_resource_refuses_vmid_on_multiple_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        operations, "_pool_vms", lambda: [_vm(101, "pve-a"), _vm(101, "pve-b")]
    )
    with pytest.raises(ProxmoxError):
        operations.find_resource(101)


def test_find_lxc_ignores_same_vmid_of_other_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        operations,
        "_pool_vms",
        lambda: [_vm(101, "pve-a", "qemu"), _vm(101, "pve-b", "lxc")],
    )
    assert operations.find_lxc(101)["node"] == "pve-b"
    with pytest.raises(NotFoundError):
        operations.find_lxc(999)


def _fake_api(nextid: int) -> SimpleNamespace:
    return SimpleNamespace(
        cluster=SimpleNamespace(nextid=SimpleNamespace(get=lambda: str(nextid)))
    )


def test_next_vmid_skips_db_claimed_single_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(operations, "_connection_keys", lambda: [None])
    monkeypatch.setattr(operations, "get_proxmox_api", lambda _key: _fake_api(200))
    monkeypatch.setattr(operations, "_raw_vms", lambda: [])
    monkeypatch.setattr(operations, "_db_claimed_vmids", lambda: {200, 201})
    assert operations.next_vmid() == 202


def test_next_vmid_single_connection_does_not_step_onto_pve_vmid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """nextid 回空號 102（DB 還殘留 102 的資源列），往上遞增不可踩到 PVE 的 103。"""
    monkeypatch.setattr(operations, "_connection_keys", lambda: [None])
    monkeypatch.setattr(operations, "get_proxmox_api", lambda _key: _fake_api(102))
    monkeypatch.setattr(
        operations, "_raw_vms", lambda: [_vm(100, "a"), _vm(101, "a"), _vm(103, "a")]
    )
    monkeypatch.setattr(operations, "_db_claimed_vmids", lambda: {102})
    assert operations.next_vmid() == 104


def test_next_vmid_skips_pve_and_db_claimed_multi_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apis = {1: _fake_api(300), 2: _fake_api(305)}
    monkeypatch.setattr(operations, "_connection_keys", lambda: [1, 2])
    monkeypatch.setattr(operations, "get_proxmox_api", lambda key: apis[key])
    monkeypatch.setattr(operations, "_raw_vms", lambda: [_vm(305, "pve-a")])
    monkeypatch.setattr(operations, "_db_claimed_vmids", lambda: {306})
    assert operations.next_vmid() == 307
