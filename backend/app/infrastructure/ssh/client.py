from __future__ import annotations

import io
import select
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, Literal

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
HostKeyPolicy = Literal["auto_add", "warning"]


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


def _resolve_host_key_policy(policy: HostKeyPolicy):
    if policy == "warning":
        return paramiko.WarningPolicy()
    return paramiko.AutoAddPolicy()


def create_key_client(
    host: str,
    port: int,
    username: str,
    private_key_pem: str,
    *,
    timeout: int = 10,
    host_key_policy: HostKeyPolicy = "auto_add",
) -> Any:
    ensure_ssh_backend()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_resolve_host_key_policy(host_key_policy))
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
    host_key_policy: HostKeyPolicy = "warning",
) -> Any:
    ensure_ssh_backend()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_resolve_host_key_policy(host_key_policy))
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
