"""exec_command 的總時限、輸出上限，以及 stdout／stderr 同時讀取不互卡。

前半用假 channel 驗證邊界行為；後半在本機用 paramiko 的 server 模式跑一條
真的 SSH 連線，確認 stderr 超過 channel window（約 2 MB）時不會死鎖。
"""

from __future__ import annotations

import io
import socket
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import paramiko
import pytest

from app.infrastructure.ssh import (
    SSHCommandTimeoutError,
    SSHOutputTooLargeError,
    exec_command,
    generate_ed25519_keypair,
)
from app.infrastructure.ssh import client as ssh_client_module

# ── 假 channel ───────────────────────────────────────────────────────────


class _FakeChannel:
    """依序吐出 stdout／stderr 片段；``finish`` 為 False 時永遠不送 EOF。"""

    def __init__(
        self,
        *,
        stdout: list[bytes] | None = None,
        stderr: list[bytes] | None = None,
        exit_status: int = 0,
        finish: bool = True,
        endless_stdout: bytes | None = None,
    ) -> None:
        self._stdout = list(stdout or [])
        self._stderr = list(stderr or [])
        self._endless_stdout = endless_stdout
        self._exit_status = exit_status
        self._finish = finish
        self.closed = False
        self.close_calls = 0
        self.shutdown_calls = 0
        self.written = b""
        self.channel = self

    @property
    def eof_received(self) -> bool:
        return self._finish and not self._stdout and not self._stderr

    def recv_ready(self) -> bool:
        return bool(self._stdout) or self._endless_stdout is not None

    def recv(self, _size: int) -> bytes:
        if self._endless_stdout is not None:
            return self._endless_stdout
        return self._stdout.pop(0)

    def recv_stderr_ready(self) -> bool:
        return bool(self._stderr)

    def recv_stderr(self, _size: int) -> bytes:
        return self._stderr.pop(0)

    def exit_status_ready(self) -> bool:
        return self.closed or self.eof_received

    def recv_exit_status(self) -> int:
        return self._exit_status if self.eof_received else -1

    def close(self) -> None:
        self.closed = True
        self.close_calls += 1

    def write(self, data: str) -> None:
        self.written += data.encode()

    def flush(self) -> None:
        return None

    def shutdown_write(self) -> None:
        self.shutdown_calls += 1


class _FakeClient:
    def __init__(self, channel: _FakeChannel) -> None:
        self.channel = channel
        self.timeouts: list[Any] = []

    def exec_command(self, _command: str, timeout: Any = None) -> tuple[Any, Any, Any]:
        self.timeouts.append(timeout)
        return self.channel, self.channel, self.channel


def test_collects_interleaved_stdout_and_stderr() -> None:
    channel = _FakeChannel(
        stdout=[b"out-1 ", b"out-2"],
        stderr=[b"err-1 ", "錯誤".encode()],
        exit_status=7,
    )

    code, out, err = exec_command(_FakeClient(channel), "cmd", timeout=5)

    assert (code, out, err) == (7, "out-1 out-2", "err-1 錯誤")
    assert channel.close_calls == 0


def test_timeout_is_total_wall_clock_and_closes_channel() -> None:
    channel = _FakeChannel(finish=False)
    client = _FakeClient(channel)

    started = time.monotonic()
    with pytest.raises(SSHCommandTimeoutError) as exc_info:
        exec_command(client, "sleep forever", timeout=0.2)

    assert time.monotonic() - started < 2
    assert isinstance(exc_info.value, TimeoutError)
    assert channel.close_calls == 1
    assert client.timeouts == [0.2]


def test_missing_timeout_uses_bounded_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_client_module, "DEFAULT_EXEC_TIMEOUT_SECONDS", 0.2)
    channel = _FakeChannel(finish=False)
    client = _FakeClient(channel)

    with pytest.raises(SSHCommandTimeoutError):
        exec_command(client, "hang")

    assert client.timeouts == [0.2]


def test_output_over_limit_fails_and_closes_channel() -> None:
    channel = _FakeChannel(endless_stdout=b"y" * 1024)

    with pytest.raises(SSHOutputTooLargeError) as exc_info:
        exec_command(_FakeClient(channel), "yes", timeout=5, max_output_bytes=4096)

    assert exc_info.value.stream == "stdout"
    assert exc_info.value.limit == 4096
    assert "4 KB" in str(exc_info.value)
    assert channel.close_calls == 1


def test_stderr_over_limit_fails() -> None:
    channel = _FakeChannel(stderr=[b"e" * 3000, b"e" * 3000])

    with pytest.raises(SSHOutputTooLargeError) as exc_info:
        exec_command(_FakeClient(channel), "noisy", timeout=5, max_output_bytes=4096)

    assert exc_info.value.stream == "stderr"


def test_stdin_is_written_and_write_side_closed() -> None:
    channel = _FakeChannel(stdout=[b"ok"])

    code, out, _ = exec_command(
        _FakeClient(channel), "chpasswd", timeout=5, stdin="root:pw\n"
    )

    assert (code, out) == (0, "ok")
    assert channel.written == b"root:pw\n"
    assert channel.shutdown_calls == 1


# ── 本機 paramiko SSH server ──────────────────────────────────────────────


class _ExecServer(paramiko.ServerInterface):
    def __init__(self, handler: Callable[[paramiko.Channel, str], None]) -> None:
        self._handler = handler

    def check_channel_request(self, kind: str, chanid: int) -> int:
        return paramiko.OPEN_SUCCEEDED

    def get_allowed_auths(self, username: str) -> str:
        return "none"

    def check_auth_none(self, username: str) -> int:
        return paramiko.AUTH_SUCCESSFUL

    def check_channel_exec_request(self, channel: paramiko.Channel, command: bytes) -> bool:
        threading.Thread(
            target=self._handler, args=(channel, command.decode()), daemon=True
        ).start()
        return True


class _TransportClient:
    """與 paramiko.SSHClient.exec_command 相同的開 channel 方式。"""

    def __init__(self, transport: paramiko.Transport) -> None:
        self._transport = transport

    def exec_command(self, command: str, timeout: float | None = None) -> tuple[Any, Any, Any]:
        channel = self._transport.open_session(timeout=timeout)
        channel.settimeout(timeout)
        channel.exec_command(command)
        return (
            channel.makefile_stdin("wb"),
            channel.makefile("r"),
            channel.makefile_stderr("r"),
        )


@pytest.fixture
def ssh_server() -> Iterator[Callable[[Callable[[paramiko.Channel, str], None]], Any]]:
    transports: list[paramiko.Transport] = []

    def connect(handler: Callable[[paramiko.Channel, str], None]) -> Any:
        server_sock, client_sock = socket.socketpair()
        server = paramiko.Transport(server_sock)
        transports.append(server)
        pem, _ = generate_ed25519_keypair()
        server.add_server_key(paramiko.Ed25519Key.from_private_key(io.StringIO(pem)))
        server.start_server(event=threading.Event(), server=_ExecServer(handler))
        client = paramiko.Transport(client_sock)
        transports.append(client)
        client.start_client(timeout=10)
        client.auth_none("tester")
        return _TransportClient(client)

    yield connect
    for transport in transports:
        transport.close()


def _call_with_deadline(func: Callable[[], Any], seconds: float) -> Any:
    """在 daemon thread 裡跑；卡住時讓測試失敗而不是讓整個 pytest 掛住。"""
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["value"] = func()
        except Exception as exc:  # noqa: BLE001 - 原樣轉交給測試
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        pytest.fail(f"exec_command did not return within {seconds}s (deadlock?)")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def test_large_stderr_does_not_deadlock(ssh_server) -> None:
    stderr_size = 4 * 1024 * 1024  # 大於 paramiko 預設 2 MB 的 channel window

    def noisy(channel: paramiko.Channel, _command: str) -> None:
        channel.sendall_stderr(b"e" * stderr_size)
        channel.sendall(b"done\n")
        channel.send_exit_status(3)
        channel.close()

    client = ssh_server(noisy)

    code, out, err = _call_with_deadline(
        lambda: exec_command(client, "noisy", timeout=20), 30
    )

    assert code == 3
    assert out == "done\n"
    assert len(err) == stderr_size


def test_trickling_command_hits_total_timeout(ssh_server) -> None:
    server_channels: list[paramiko.Channel] = []

    def trickle(channel: paramiko.Channel, _command: str) -> None:
        server_channels.append(channel)
        try:
            while not channel.closed:
                channel.send(b".")
                time.sleep(0.05)
        except Exception:
            return

    client = ssh_server(trickle)

    started = time.monotonic()
    with pytest.raises(SSHCommandTimeoutError):
        _call_with_deadline(lambda: exec_command(client, "trickle", timeout=0.5), 10)
    assert time.monotonic() - started < 5

    deadline = time.monotonic() + 5
    while not server_channels[0].closed and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server_channels[0].closed


def test_endless_output_hits_size_limit(ssh_server) -> None:
    def flood(channel: paramiko.Channel, _command: str) -> None:
        try:
            while True:
                channel.sendall(b"x" * 65536)
        except Exception:
            return

    client = ssh_server(flood)

    with pytest.raises(SSHOutputTooLargeError):
        _call_with_deadline(
            lambda: exec_command(
                client, "flood", timeout=20, max_output_bytes=1024 * 1024
            ),
            30,
        )


def test_stdin_reaches_remote_command(ssh_server) -> None:
    def upper(channel: paramiko.Channel, _command: str) -> None:
        data = b""
        while chunk := channel.recv(1024):
            data += chunk
        channel.sendall(data.upper())
        channel.send_exit_status(0)
        channel.close()

    client = ssh_server(upper)

    code, out, err = _call_with_deadline(
        lambda: exec_command(client, "upper", timeout=10, stdin="root:pw\n"), 20
    )

    assert (code, out, err) == (0, "ROOT:PW\n", "")
