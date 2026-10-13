"""以 OS 範本建立 LXC：先建成關機狀態 → 套預設防火牆 → 才開機；開機失敗整台回滾。

以前建立時直接帶 ``start=1``，容器在防火牆規則寫進去之前就已經開機上網。
"""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.exceptions import ProxmoxError
from app.services.proxmox import provisioning_service as svc


@pytest.fixture
def pve(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "events": [],
        "create_config": None,
        "start_error": None,
        "rolled_back": [],
    }

    def fake_create_lxc(node: str, **config: Any) -> str:
        state["create_config"] = config
        state["events"].append("create")
        return "UPID:create"

    def fake_control(node: str, vmid: int, rtype: str, action: str, **_: Any) -> None:
        state["events"].append(f"control:{action}")
        if state["start_error"] is not None:
            raise state["start_error"]

    monkeypatch.setattr(svc.proxmox_service, "create_lxc", fake_create_lxc)
    monkeypatch.setattr(svc.proxmox_service, "control", fake_control)
    monkeypatch.setattr(
        svc.firewall_service,
        "setup_default_rules",
        lambda node, vmid, rtype: state["events"].append("firewall"),
    )
    monkeypatch.setattr(
        svc,
        "_open_lxc_ssh_login",
        lambda node, vmid, key: state["events"].append("ssh_login"),
    )
    monkeypatch.setattr(
        svc,
        "_rollback_created_resource",
        lambda node, vmid, rtype: state["rolled_back"].append((node, vmid, rtype)),
    )
    monkeypatch.setattr(
        svc,
        "get_proxmox_settings_for_node",
        lambda node: SimpleNamespace(pool_name="skylab"),
    )
    return state


# ── execute_provision（申請單／排程走的路徑）──────────────────────────────


def _ostemplate_plan(**overrides: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "vmid": 400,
        "target_node": "pve1",
        "resource_type": "lxc",
        "hostname": "ct-01",
        "ostemplate": "local:vztmpl/debian-12.tar.zst",
        "target_storage": "local-lvm",
        "rootfs_size": 8,
        "cores": 1,
        "memory": 512,
        "unprivileged": True,
        "start_immediately": True,
        "ssh_public_key": "ssh-ed25519 AAAA platform",
        "password": "Student123",
        "allocated_ip": "10.0.0.70",
        "net_cfg": {"bridge_name": "vmbr1", "prefix_len": 24, "gateway": "10.0.0.1"},
    }
    plan.update(overrides)
    return plan


def test_execute_provision_creates_stopped_then_firewall_then_start(
    pve: dict[str, Any],
) -> None:
    assert svc.execute_provision(_ostemplate_plan()) == (400, "pve1")

    assert pve["create_config"]["start"] == 0
    assert pve["events"] == ["create", "firewall", "control:start", "ssh_login"]
    assert pve["rolled_back"] == []


def test_execute_provision_without_start_never_starts(pve: dict[str, Any]) -> None:
    svc.execute_provision(_ostemplate_plan(start_immediately=False))

    assert pve["create_config"]["start"] == 0
    assert pve["events"] == ["create", "firewall"]


def test_execute_provision_rolls_back_whole_machine_when_start_fails(
    pve: dict[str, Any],
) -> None:
    pve["start_error"] = ProxmoxError("CT 400 start failed")

    with pytest.raises(ProxmoxError):
        svc.execute_provision(_ostemplate_plan())

    assert pve["events"] == ["create", "firewall", "control:start"]
    assert pve["rolled_back"] == [("pve1", 400, "lxc")]


def test_execute_provision_never_starts_when_firewall_fails(
    pve: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_firewall(node: str, vmid: int, rtype: str) -> None:
        raise ProxmoxError("firewall API down")

    monkeypatch.setattr(svc.firewall_service, "setup_default_rules", failing_firewall)

    with pytest.raises(ProxmoxError):
        svc.execute_provision(_ostemplate_plan())

    assert "control:start" not in pve["events"]
    assert pve["rolled_back"] == [("pve1", 400, "lxc")]


# ── create_lxc（直接建立的路徑）───────────────────────────────────────────


@pytest.fixture
def direct_create(pve: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    pve["released"] = []
    monkeypatch.setattr(svc, "_resolve_managed_storage", lambda **_: "local-lvm")
    monkeypatch.setattr(
        svc.ip_management_service,
        "get_network_config_for_vm",
        lambda session: {"bridge_name": "vmbr1", "prefix_len": 24, "gateway": "10.0.0.1"},
    )
    monkeypatch.setattr(
        svc.proxmox_service, "vmid_allocation_lock", lambda **_: nullcontext()
    )
    monkeypatch.setattr(svc, "allocate_free_vmid", lambda session: 401)
    monkeypatch.setattr(
        svc.ip_management_service,
        "allocate_ip",
        lambda session, vmid, purpose, reservation_key=None: "10.0.0.71",
    )
    monkeypatch.setattr(
        svc, "generate_ed25519_keypair", lambda: ("PRIVATE", "ssh-ed25519 AAAA")
    )
    monkeypatch.setattr(svc, "encrypt_value", lambda value: f"enc:{value}")
    monkeypatch.setattr(svc, "get_connection_id_for_node", lambda node: 1)
    monkeypatch.setattr(
        svc.resource_repo, "create_resource", lambda **_: SimpleNamespace()
    )
    monkeypatch.setattr(svc, "initial_guest_os_identity", lambda **_: None)
    monkeypatch.setattr(
        svc.ip_management_service, "link_ip_to_resource", lambda *a, **k: True
    )
    monkeypatch.setattr(svc.audit_service, "log_action", lambda **_: None)
    monkeypatch.setattr(
        svc,
        "_release_ip_after_failure",
        lambda bind, vmid, key, *, label: pve["released"].append(vmid),
    )
    return pve


def _lxc_request(*, start: bool) -> SimpleNamespace:
    return SimpleNamespace(
        hostname="ct-02",
        ostemplate="local:vztmpl/debian-12.tar.zst",
        cores=1,
        memory=512,
        rootfs_size=8,
        storage=None,
        password="Student123",
        unprivileged=True,
        start=start,
        environment_type=None,
        os_info=None,
        expiry_date=None,
    )


def test_create_lxc_creates_stopped_then_firewall_then_start(
    direct_create: dict[str, Any],
) -> None:
    result = svc.create_lxc(
        session=MagicMock(),
        lxc_data=_lxc_request(start=True),  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        target_node="pve1",
    )

    assert result.vmid == 401
    assert direct_create["create_config"]["start"] == 0
    assert direct_create["events"] == ["create", "firewall", "control:start", "ssh_login"]


def test_create_lxc_without_start_stays_stopped(direct_create: dict[str, Any]) -> None:
    svc.create_lxc(
        session=MagicMock(),
        lxc_data=_lxc_request(start=False),  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        target_node="pve1",
    )

    assert direct_create["events"] == ["create", "firewall"]


def test_create_lxc_rolls_back_whole_machine_when_start_fails(
    direct_create: dict[str, Any],
) -> None:
    direct_create["start_error"] = ProxmoxError("CT 401 start failed")
    session = MagicMock()

    with pytest.raises(ProxmoxError):
        svc.create_lxc(
            session=session,
            lxc_data=_lxc_request(start=True),  # type: ignore[arg-type]
            user_id=uuid.uuid4(),
            target_node="pve1",
        )

    assert direct_create["rolled_back"] == [("pve1", 401, "lxc")]
    assert direct_create["released"] == [401]
    session.rollback.assert_called()
    session.commit.assert_not_called()
