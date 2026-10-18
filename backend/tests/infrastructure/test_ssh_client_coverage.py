"""app.infrastructure.ssh.client 剩餘分支覆蓋：位元組格式、缺 paramiko、連線成功回傳與 stdin 逾時。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.exceptions import ProxmoxError
from app.infrastructure.ssh import client as ssh_client_module


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (2 * 1024 * 1024, "2 MB"),
        (3 * 1024, "3 KB"),
        (1500, "1500 B"),
        (10, "10 B"),
    ],
)
def test_format_bytes(value: int, expected: str) -> None:
    assert ssh_client_module._format_bytes(value) == expected


def test_ensure_ssh_backend_without_paramiko(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_client_module, "_PARAMIKO_AVAILABLE", False)
    with pytest.raises(ProxmoxError, match="paramiko"):
        ssh_client_module.ensure_ssh_backend()


def test_forget_host_key_without_paramiko_is_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    known = tmp_path / "kh"
    monkeypatch.setenv("SSH_KNOWN_HOSTS_FILE", str(known))
    monkeypatch.setattr(ssh_client_module, "_PARAMIKO_AVAILABLE", False)

    ssh_client_module.forget_host_key("10.0.0.1")

    assert not known.exists()


class _OkClient:
    instances: list[_OkClient] = []

    def __init__(self) -> None:
        self.connect_kwargs: dict[str, object] = {}
        self.closed = False
        _OkClient.instances.append(self)

    def load_host_keys(self, _path: str) -> None:
        return None

    def set_missing_host_key_policy(self, _policy: object) -> None:
        return None

    def connect(self, **kwargs: object) -> None:
        self.connect_kwargs = kwargs

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def ok_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> type[_OkClient]:
    monkeypatch.setenv("SSH_KNOWN_HOSTS_FILE", str(tmp_path / "known_hosts"))
    monkeypatch.setattr(ssh_client_module.paramiko, "SSHClient", _OkClient)
    _OkClient.instances = []
    return _OkClient


def test_create_password_client_returns_connected_client(ok_client) -> None:
    client = ssh_client_module.create_password_client("10.0.0.6", 22, "admin", "pw")

    assert client is ok_client.instances[0]
    assert client.connect_kwargs["password"] == "pw"
    assert client.connect_kwargs["timeout"] == 30


class _StdinTimeoutChannel:
    def __init__(self) -> None:
        self.channel = self
        self.shutdown_calls = 0
        self.close_calls = 0

    def write(self, _data: str) -> None:
        raise TimeoutError("write stalled")

    def flush(self) -> None:
        return None

    def shutdown_write(self) -> None:
        self.shutdown_calls += 1

    def close(self) -> None:
        self.close_calls += 1


def test_exec_command_stdin_write_timeout() -> None:
    channel = _StdinTimeoutChannel()
    client = SimpleNamespace(exec_command=lambda *_a, **_k: (channel, channel, channel))

    with pytest.raises(ssh_client_module.SSHCommandTimeoutError) as exc:
        ssh_client_module.exec_command(client, "chpasswd", stdin="u:p\n", timeout=5)

    assert exc.value.timeout == 5
    assert isinstance(exc.value.__cause__, TimeoutError)
    assert channel.shutdown_calls == 1
    assert channel.close_calls == 1


@pytest.mark.parametrize(
    ("recv_ready", "stderr_ready", "exit_ready", "expected"),
    [
        (True, False, True, False),
        (False, True, True, False),
        (False, False, False, False),
        (False, False, True, True),
    ],
)
def test_output_finished_after_eof(
    recv_ready: bool, stderr_ready: bool, exit_ready: bool, expected: bool
) -> None:
    channel = SimpleNamespace(
        eof_received=True,
        closed=False,
        recv_ready=lambda: recv_ready,
        recv_stderr_ready=lambda: stderr_ready,
        exit_status_ready=lambda: exit_ready,
    )
    assert ssh_client_module._output_finished(channel) is expected
