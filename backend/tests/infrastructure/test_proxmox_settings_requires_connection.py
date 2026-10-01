"""get_proxmox_settings 只從 proxmox_connections 讀取連線設定。

dbw02 已把 proxmox_config singleton 的連線欄位 drop 掉，沒有任何連線列時
不能再退回 singleton，一律視為「Proxmox 尚未設定」。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.infrastructure.proxmox import settings as proxmox_settings
from app.repositories import proxmox_connection as connection_repo


def test_no_connection_row_means_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection_repo, "get_default_connection", lambda _s: None)

    with pytest.raises(RuntimeError, match="Proxmox 尚未設定"):
        proxmox_settings.get_proxmox_settings()


def test_unknown_connection_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connection_repo, "get_connection", lambda _s, _cid: None)

    with pytest.raises(RuntimeError, match="42"):
        proxmox_settings.get_proxmox_settings(42)


def test_settings_come_from_connection_row(monkeypatch: pytest.MonkeyPatch) -> None:
    conn: Any = SimpleNamespace(
        id=3,
        name="lab",
        host="10.0.0.3",
        port=443,
        user="root@pam",
        verify_ssl=True,
        iso_storage="iso",
        data_storage="ssd",
        api_timeout=15,
        task_check_interval=3,
        pool_name="Lab",
        ca_cert=None,
        gateway_ip="10.0.0.254",
        local_subnet="10.0.0.0/24",
        default_node="pve1",
        backup_storage="pbs-main",
    )
    monkeypatch.setattr(connection_repo, "get_connection", lambda _s, _cid: conn)
    monkeypatch.setattr(connection_repo, "get_decrypted_password", lambda _c: "pw")

    result = proxmox_settings.get_proxmox_settings(3)

    assert (result.host, result.port, result.password) == ("10.0.0.3", 443, "pw")
    assert (result.pool_name, result.data_storage) == ("Lab", "ssd")
    assert result.backup_storage == "pbs-main"
    assert (result.connection_id, result.connection_name) == (3, "lab")
