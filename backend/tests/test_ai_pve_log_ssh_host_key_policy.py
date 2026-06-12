"""SSH host key 驗證行為測試。

- _ssh_exec_sync 必須透過 infrastructure 的 create_key_client 建立連線
  （啟用 trust-on-first-use host key 驗證），不得自行 AutoAddPolicy。
- _TrustOnFirstUsePolicy 首次連線要把 host key 持久化到 known_hosts，
  之後的 client 載入後即認得該主機；forget_host_key 可移除紀錄。
"""

from __future__ import annotations

import io

import paramiko

from app.ai.pve_log import ssh_exec as ssh_exec_module
from app.infrastructure.ssh import client as ssh_client_module
from app.infrastructure.ssh import forget_host_key, generate_ed25519_keypair


class _FakeChannel:
    def recv_exit_status(self) -> int:
        return 0


class _FakeStream:
    def __init__(self, data: str = "") -> None:
        self._data = data
        self.channel = _FakeChannel()

    def read(self) -> bytes:
        return self._data.encode()


class _FakeSSHClient:
    def exec_command(self, *_args, **_kwargs):
        return None, _FakeStream("ok"), _FakeStream("")

    def close(self) -> None:
        return None


def test_ssh_exec_sync_uses_shared_key_client(monkeypatch) -> None:
    """_ssh_exec_sync 必須委派給 infrastructure 的 create_key_client。"""
    captured: dict[str, object] = {}

    def _fake_create_key_client(host, port, username, pem, *, timeout):
        captured.update(
            host=host, port=port, username=username, pem=pem, timeout=timeout
        )
        return _FakeSSHClient()

    monkeypatch.setattr(
        ssh_exec_module, "create_key_client", _fake_create_key_client
    )

    exit_code, stdout, stderr = ssh_exec_module._ssh_exec_sync(
        host="10.10.0.6",
        port=22,
        username="root",
        private_key_pem="dummy-key",
        command="echo ok",
        timeout=30,
    )

    assert exit_code == 0
    assert stdout == "ok"
    assert stderr == ""
    assert captured == {
        "host": "10.10.0.6",
        "port": 22,
        "username": "root",
        "pem": "dummy-key",
        "timeout": 30,
    }


def test_trust_on_first_use_persists_and_forgets_host_key(
    tmp_path, monkeypatch
) -> None:
    known_hosts = tmp_path / "known_hosts"
    monkeypatch.setenv("SSH_KNOWN_HOSTS_FILE", str(known_hosts))

    private_key_pem, _public = generate_ed25519_keypair()
    host_key = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key_pem))

    # 首次連線：policy 記錄 host key 並持久化
    client = paramiko.SSHClient()
    ssh_client_module._configure_host_key_verification(client)
    policy = ssh_client_module._TrustOnFirstUsePolicy(str(known_hosts))
    policy.missing_host_key(client, "10.10.0.6", host_key)

    saved = paramiko.HostKeys(str(known_hosts))
    assert saved.lookup("10.10.0.6") is not None

    # 新的 client 載入 known_hosts 後即認得該主機
    client2 = paramiko.SSHClient()
    ssh_client_module._configure_host_key_verification(client2)
    assert client2.get_host_keys().lookup("10.10.0.6") is not None

    # forget_host_key 移除紀錄（IP 回收情境）
    forget_host_key("10.10.0.6")
    assert paramiko.HostKeys(str(known_hosts)).lookup("10.10.0.6") is None
