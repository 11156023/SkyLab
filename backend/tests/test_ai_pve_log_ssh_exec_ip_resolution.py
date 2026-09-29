from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ai.pve_log import ssh_exec as ssh_exec_module


@pytest.mark.asyncio
async def test_resolve_vm_info_uses_cached_ip_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = SimpleNamespace(ssh_private_key_encrypted="encrypted-key")

    monkeypatch.setattr(
        ssh_exec_module.resource_repo,
        "get_resource_by_vmid",
        lambda **_kwargs: resource,
    )
    monkeypatch.setattr(
        ssh_exec_module.resource_repo,
        "get_cached_ip_address",
        lambda **_kwargs: "10.10.0.6",
    )
    monkeypatch.setattr(ssh_exec_module, "decrypt_value", lambda _v: "PRIVATE_KEY")

    host, private_key = await ssh_exec_module._resolve_vm_info_from_db(object(), 157)
    assert host == "10.10.0.6"
    assert private_key == "PRIVATE_KEY"


@pytest.mark.asyncio
async def test_resolve_vm_info_falls_back_to_proxmox_ip_and_updates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = SimpleNamespace(ssh_private_key_encrypted="encrypted-key")
    cache_updates: dict[str, object] = {}

    monkeypatch.setattr(
        ssh_exec_module.resource_repo,
        "get_resource_by_vmid",
        lambda **_kwargs: resource,
    )
    monkeypatch.setattr(
        ssh_exec_module.resource_repo,
        "get_cached_ip_address",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        ssh_exec_module.proxmox_service,
        "find_resource",
        lambda _vmid: {"node": "pve", "type": "qemu"},
    )
    monkeypatch.setattr(
        ssh_exec_module.proxmox_service,
        "get_ip_address",
        lambda _node, _vmid, _rtype: "10.10.0.6",
    )
    monkeypatch.setattr(
        ssh_exec_module.resource_repo,
        "update_ip_address",
        lambda **kwargs: cache_updates.update(kwargs),
    )
    monkeypatch.setattr(ssh_exec_module, "decrypt_value", lambda _v: "PRIVATE_KEY")

    host, private_key = await ssh_exec_module._resolve_vm_info_from_db(object(), 157)
    assert host == "10.10.0.6"
    assert private_key == "PRIVATE_KEY"
    assert cache_updates["vmid"] == 157
    assert cache_updates["ip_address"] == "10.10.0.6"


@pytest.mark.asyncio
async def test_resolve_vm_info_raises_when_no_cached_or_live_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = SimpleNamespace(ssh_private_key_encrypted="encrypted-key")

    monkeypatch.setattr(
        ssh_exec_module.resource_repo,
        "get_resource_by_vmid",
        lambda **_kwargs: resource,
    )
    monkeypatch.setattr(
        ssh_exec_module.resource_repo,
        "get_cached_ip_address",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        ssh_exec_module.proxmox_service,
        "find_resource",
        lambda _vmid: {"node": "pve", "type": "qemu"},
    )
    monkeypatch.setattr(
        ssh_exec_module.proxmox_service,
        "get_ip_address",
        lambda _node, _vmid, _rtype: None,
    )
    monkeypatch.setattr(ssh_exec_module, "decrypt_value", lambda _v: "PRIVATE_KEY")

    with pytest.raises(RuntimeError, match="沒有可用的 IP 位址"):
        await ssh_exec_module._resolve_vm_info_from_db(object(), 157)
