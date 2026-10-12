"""connect 失敗（認證錯誤、host key 不符）時要關掉 SSHClient。

paramiko 不會自己收掉已啟動的 Transport；呼叫端拿不到 client，執行緒與
socket 會一直掛到遠端逾時。
"""

from __future__ import annotations

import paramiko
import pytest

from app.infrastructure.ssh import client as ssh_client_module
from app.infrastructure.ssh import generate_ed25519_keypair


class _FailingClient:
    instances: list[_FailingClient] = []

    def __init__(self) -> None:
        self.closed = False
        _FailingClient.instances.append(self)

    def load_host_keys(self, _path: str) -> None:
        return None

    def set_missing_host_key_policy(self, _policy: object) -> None:
        return None

    def connect(self, **_kwargs: object) -> None:
        raise paramiko.AuthenticationException("Authentication failed.")

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def failing_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> type[_FailingClient]:
    monkeypatch.setenv("SSH_KNOWN_HOSTS_FILE", str(tmp_path / "known_hosts"))
    monkeypatch.setattr(ssh_client_module.paramiko, "SSHClient", _FailingClient)
    _FailingClient.instances = []
    return _FailingClient


def test_key_client_is_closed_when_connect_fails(failing_client) -> None:
    pem, _ = generate_ed25519_keypair()

    with pytest.raises(paramiko.AuthenticationException):
        ssh_client_module.create_key_client("10.0.0.5", 22, "root", pem)

    assert [c.closed for c in failing_client.instances] == [True]


def test_password_client_is_closed_when_connect_fails(failing_client) -> None:
    with pytest.raises(paramiko.AuthenticationException):
        ssh_client_module.create_password_client("10.0.0.5", 22, "root", "pw")

    assert [c.closed for c in failing_client.instances] == [True]
