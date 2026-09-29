from __future__ import annotations

import io
import os
import threading
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
        # 不能用 client.save_host_keys()：它把連線開始時載入的整份快照寫回，
        # 期間被 forget_host_key() 刪掉的舊 key 會被復活。只在檔案目前內容上
        # 追加這一筆。
        with _KNOWN_HOSTS_LOCK:
            keys = paramiko.HostKeys(self._path)
            keys.add(hostname, key.get_name(), key)
            keys.save(self._path)


def _configure_host_key_verification(client: Any) -> None:
    """載入平台自管 known_hosts 並啟用 trust-on-first-use 驗證。

    刻意不載入系統 ~/.ssh/known_hosts：paramiko 比對時系統檔優先於
    client 自載的 host keys，但 forget_host_key() 只清平台自管檔，
    若載入系統檔，殘留其中的舊 key 會讓「重設 host key」失效。
    """
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
    stdin: str | None = None,
) -> tuple[int, str, str]:
    """執行遠端指令；``stdin`` 有值時寫入後關閉寫入端再讀輸出。

    敏感資料（密碼等）要走 stdin：指令列會出現在遠端的 ps 與 shell 記錄裡，
    同機的其他使用者看得到。
    """
    stdin_ch, stdout_ch, stderr_ch = client.exec_command(command, timeout=timeout)
    if stdin is not None:
        try:
            stdin_ch.write(stdin)
            stdin_ch.flush()
        finally:
            # 不關寫入端，讀取 stdin 的指令（chpasswd 等）會一直等下去
            stdin_ch.channel.shutdown_write()
    stdout_text = stdout_ch.read().decode(errors=decode_errors)
    stderr_text = stderr_ch.read().decode(errors=decode_errors)
    exit_code = stdout_ch.channel.recv_exit_status()
    return exit_code, stdout_text, stderr_text
