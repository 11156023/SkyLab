from __future__ import annotations

import io
import os
import select
import threading
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

try:
    import paramiko
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    paramiko = SimpleNamespace(
        AuthenticationException=type(
            "MissingParamikoAuthenticationException",
            (Exception,),
            {},
        )
    )
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from app.exceptions import ProxmoxError

_PARAMIKO_AVAILABLE = not isinstance(paramiko, SimpleNamespace)
SSHAuthenticationError = paramiko.AuthenticationException

# 平台管理的 known_hosts：首次連線記錄 host key，之後 key 變更會拒絕連線
_KNOWN_HOSTS_ENV = "SSH_KNOWN_HOSTS_FILE"
_KNOWN_HOSTS_LOCK = threading.Lock()


def ensure_ssh_backend() -> None:
    if not _PARAMIKO_AVAILABLE:
        raise ProxmoxError(
            "SSH backend is unavailable because the 'paramiko' package is not installed"
        )


def generate_ed25519_keypair(*, comment: str = "SkyLab-gateway") -> tuple[str, str]:
    ensure_ssh_backend()
    private_key = Ed25519PrivateKey.generate()
    private_key_pem = private_key.private_bytes(
        Encoding.PEM,
        PrivateFormat.OpenSSH,
        NoEncryption(),
    ).decode()
    pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key_pem))
    public_key = f"ssh-ed25519 {pkey.get_base64()} {comment}"
    return private_key_pem, public_key


def _known_hosts_file() -> str:
    """回傳（必要時建立）平台管理的 known_hosts 檔案路徑。"""
    path = os.environ.get(_KNOWN_HOSTS_ENV) or os.path.join(
        os.path.expanduser("~"), ".ssh", "skylab_known_hosts"
    )
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    if not os.path.exists(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(fd)
    return path


class _TrustOnFirstUsePolicy:
    """未知主機首次連線時記錄 host key 並持久化（trust-on-first-use）。

    已記錄的主機若 key 不符，paramiko 會拋出 BadHostKeyException，
    藉此偵測中間人攻擊。VM 銷毀重建後請以 forget_host_key() 清除舊紀錄。
    """

    def __init__(self, known_hosts_path: str) -> None:
        self._path = known_hosts_path

    def missing_host_key(self, client: Any, hostname: str, key: Any) -> None:
        client.get_host_keys().add(hostname, key.get_name(), key)
        with _KNOWN_HOSTS_LOCK:
            client.save_host_keys(self._path)


def _configure_host_key_verification(client: Any) -> None:
    """載入持久化 known_hosts 並啟用 trust-on-first-use 驗證。"""
    try:
        client.load_system_host_keys()
    except Exception:  # pragma: no cover - 系統 known_hosts 不可讀時忽略
        pass
    path = _known_hosts_file()
    with _KNOWN_HOSTS_LOCK:
        client.load_host_keys(path)
    client.set_missing_host_key_policy(_TrustOnFirstUsePolicy(path))


def forget_host_key(host: str) -> None:
    """移除指定主機的 pinned host key。

    VM 銷毀或 IP 回收時呼叫，避免同一 IP 之後的新主機因 key 不符被拒連。
    """
    if not _PARAMIKO_AVAILABLE:
        return
    path = _known_hosts_file()
    with _KNOWN_HOSTS_LOCK:
        keys = paramiko.HostKeys(path)
        stale = [
            h for h in keys.keys()
            if h == host or h.startswith(f"[{host}]:")
        ]
        if not stale:
            return
        for h in stale:
            del keys[h]
        keys.save(path)


def create_key_client(
    host: str,
    port: int,
    username: str,
    private_key_pem: str,
    *,
    timeout: int = 10,
) -> Any:
    ensure_ssh_backend()
    client = paramiko.SSHClient()
    _configure_host_key_verification(client)
    pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key_pem))
    client.connect(
        hostname=host,
        port=port,
        username=username,
        pkey=pkey,
        timeout=timeout,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def create_password_client(
    host: str,
    port: int,
    username: str,
    password: str,
    *,
    timeout: int = 30,
) -> Any:
    ensure_ssh_backend()
    client = paramiko.SSHClient()
    _configure_host_key_verification(client)
    client.connect(
        hostname=host,
        port=port,
        username=username,
        password=password,
        timeout=timeout,
    )
    return client


def exec_command(
    client: Any,
    command: str,
    *,
    timeout: int | None = None,
    decode_errors: str = "replace",
) -> tuple[int, str, str]:
    _, stdout_ch, stderr_ch = client.exec_command(command, timeout=timeout)
    stdout_text = stdout_ch.read().decode(errors=decode_errors)
    stderr_text = stderr_ch.read().decode(errors=decode_errors)
    exit_code = stdout_ch.channel.recv_exit_status()
    return exit_code, stdout_text, stderr_text


def exec_command_streaming(
    client: Any,
    command: str,
    *,
    timeout: int = 900,
    on_stdout: Callable[[str], None] | None = None,
    decode_errors: str = "replace",
    cancel_event: Any = None,
    use_pty: bool = False,
    auto_responses: list[tuple[str, str]] | None = None,
) -> tuple[int, str, str]:
    """Stream a remote command, optionally with PTY and auto-response rules.

    auto_responses: list of (trigger_substring, response_text) pairs.
    When trigger appears in stdout, response_text is written to stdin once.
    Only effective when use_pty=True (PTY gives access to channel stdin).
    """
    _, stdout_ch, stderr_ch = client.exec_command(command, timeout=timeout, get_pty=use_pty)
    channel = stdout_ch.channel
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    start = time.monotonic()
    fired_responses: set[str] = set()

    while True:
        if cancel_event is not None and cancel_event.is_set():
            try:
                channel.close()
            except Exception:
                pass
            raise RuntimeError("SSH command cancelled by user")
        if time.monotonic() - start > timeout:
            channel.close()
            raise RuntimeError(f"SSH command timed out ({timeout}s)")

        readable, _, _ = select.select([channel], [], [], 1.0)

        if readable:
            if channel.recv_ready():
                chunk = channel.recv(4096).decode(errors=decode_errors)
                stdout_parts.append(chunk)
                if on_stdout is not None:
                    on_stdout(chunk)

                if use_pty and auto_responses:
                    accumulated = "".join(stdout_parts)
                    for trigger, response in auto_responses:
                        if trigger not in fired_responses and trigger in accumulated:
                            fired_responses.add(trigger)
                            try:
                                channel.send(response)
                            except Exception:
                                pass

            if channel.recv_stderr_ready():
                chunk = channel.recv_stderr(4096).decode(errors=decode_errors)
                stderr_parts.append(chunk)

        if (
            channel.exit_status_ready()
            and not channel.recv_ready()
            and not channel.recv_stderr_ready()
        ):
            break

    exit_code = channel.recv_exit_status()
    return exit_code, "".join(stdout_parts), "".join(stderr_parts)
