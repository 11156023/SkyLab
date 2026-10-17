"""app.infrastructure.proxmox.settings 的分支覆蓋：密碼解密失敗與依節點選連線。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.infrastructure.proxmox import client as proxmox_client
from app.infrastructure.proxmox import settings as proxmox_settings
from app.repositories import proxmox_connection as connection_repo


def _conn(cid: int) -> Any:
    return SimpleNamespace(
        id=cid,
        name=f"lab{cid}",
        host=f"10.0.0.{cid}",
        port=8006,
        user="root@pam",
        verify_ssl=False,
        iso_storage="local",
        data_storage="local-lvm",
        api_timeout=15,
        task_check_interval=3,
        pool_name="SkyLab",
        ca_cert=None,
        gateway_ip=None,
        local_subnet=None,
        default_node=None,
        backup_storage=None,
    )


def test_password_decrypt_failure_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection_repo, "get_default_connection", lambda _s: _conn(1))

    def _boom(_c: Any) -> str:
        raise ValueError("InvalidToken")

    monkeypatch.setattr(connection_repo, "get_decrypted_password", _boom)

    with pytest.raises(RuntimeError, match="lab1") as excinfo:
        proxmox_settings.get_proxmox_settings()
    assert isinstance(excinfo.value.__cause__, ValueError)


def test_settings_for_empty_node_use_default_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection_repo, "get_default_connection", lambda _s: _conn(1))
    monkeypatch.setattr(connection_repo, "get_decrypted_password", lambda _c: "pw")

    assert proxmox_settings.get_proxmox_settings_for_node(None).connection_id == 1
    assert proxmox_settings.get_proxmox_settings_for_node("").connection_id == 1


def test_settings_for_node_route_to_owning_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        proxmox_client, "get_connection_id_for_node", lambda name: {"pve2": 2}[name]
    )
    monkeypatch.setattr(connection_repo, "get_connection", lambda _s, cid: _conn(cid))
    monkeypatch.setattr(connection_repo, "get_decrypted_password", lambda _c: "pw")

    result = proxmox_settings.get_proxmox_settings_for_node("pve2")

    assert (result.connection_id, result.host) == (2, "10.0.0.2")
