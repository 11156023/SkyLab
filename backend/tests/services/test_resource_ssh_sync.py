from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.services.resource import resource_service
from app.services.template import clone_service


def test_ensure_lxc_platform_key_uses_db_public_key(monkeypatch) -> None:
    public_key = "ssh-ed25519 AAAA platform"
    calls: list[tuple[str, int, str]] = []

    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resource_by_vmid",
        lambda **_kwargs: SimpleNamespace(ssh_public_key=public_key),
    )
    monkeypatch.setattr(
        clone_service,
        "inject_lxc_platform_key",
        lambda node, vmid, key: calls.append((node, vmid, key)) or True,
    )

    assert resource_service.ensure_lxc_platform_key(
        session=object(), node="pve1", vmid=203
    )
    assert calls == [("pve1", 203, public_key)]


def test_ensure_lxc_platform_key_fails_closed_when_db_key_missing(monkeypatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resource_by_vmid",
        lambda **_kwargs: SimpleNamespace(ssh_public_key=None),
    )
    monkeypatch.setattr(
        clone_service,
        "inject_lxc_platform_key",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    assert not resource_service.ensure_lxc_platform_key(
        session=object(), node="pve1", vmid=204
    )
    assert calls == []


def test_resource_start_syncs_lxc_platform_key(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        resource_service,
        "_enforce_start_window",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        resource_service.proxmox_service,
        "control",
        lambda *args, **_kwargs: None,
    )
    monkeypatch.setattr(
        resource_service,
        "ensure_lxc_platform_key",
        lambda *, node, vmid, **_kwargs: calls.append((node, vmid)) or True,
    )
    monkeypatch.setattr(
        resource_service.firewall_service,
        "ensure_firewall_enabled",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        resource_service,
        "_set_auto_stop_for_user_start",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        resource_service.audit_service,
        "log_action",
        lambda **kwargs: None,
    )

    result = resource_service.control(
        session=object(),
        vmid=205,
        action="start",
        resource_info={"node": "pve1", "type": "lxc", "name": "ct-205"},
        user_id=None,
    )

    assert result == {"message": "Resource 205 start"}
    assert calls == [("pve1", 205)]
