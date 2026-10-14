from __future__ import annotations

import io
import os
import threading
import time
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

from app.core.i18n import t
from app.exceptions import ProxmoxError

_PARAMIKO_AVAILABLE = not isinstance(paramiko, SimpleNamespace)
SSHAuthenticationError = paramiko.AuthenticationException

# 平台管理的 known_hosts：首次連線記錄 host key，之後 key 變更會拒絕連線
_KNOWN_HOSTS_ENV = "SSH_KNOWN_HOSTS_FILE"
_KNOWN_HOSTS_LOCK = threading.Lock()

# exec_command 的上限：呼叫端沒給 timeout 時整支指令（寫 stdin、讀輸出、等結束碼）
# 的總秒數，以及 stdout、stderr 各自最多收多少位元組。超過就關掉 channel 並丟出
# 下面兩個例外，不回傳截斷的輸出。
DEFAULT_EXEC_TIMEOUT_SECONDS = 300
DEFAULT_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_READ_CHUNK_BYTES = 32 * 1024
# 沒有資料時的輪詢間隔：從很短開始，持續沒資料就逐步拉長到上限
_POLL_MIN_SECONDS = 0.005
_POLL_MAX_SECONDS = 0.05


class SSHCommandTimeoutError(TimeoutError):
    """遠端指令超過總時限仍未結束；channel 已關閉，實際結果不明。"""

    def __init__(self, timeout: float) -> None:
        super().__init__(t("ssh.commandTimeout", timeout=f"{timeout:g}"))
        self.timeout = timeout


class SSHOutputTooLargeError(RuntimeError):
    """遠端指令的 stdout 或 stderr 超過上限；channel 已關閉。"""

    def __init__(self, stream: str, limit: int) -> None:
        super().__init__(
            t("ssh.outputTooLarge", stream=stream, limit=_format_bytes(limit))
        )
        self.stream = stream
        self.limit = limit


def _format_bytes(value: int) -> str:
    if value >= 1024 * 1024 and value % (1024 * 1024) == 0:
        return f"{value // (1024 * 1024)} MB"
    if value >= 1024 and value % 1024 == 0:
        return f"{value // 1024} KB"
    return f"{value} B"


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


def _connect_or_close(client: Any, **kwargs: Any) -> None:
    """connect 失敗時關掉 client。

    paramiko 在認證失敗、host key 不符時不會自己收掉已啟動的 Transport，
    背景執行緒與 socket 會掛到遠端 LoginGraceTime 逾時才斷；呼叫端拿不到
    client 物件，沒有機會 close。
    """
    try:
        client.connect(**kwargs)
    except BaseException:
        client.close()
        raise


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
    try:
        _configure_host_key_verification(client)
        pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key_pem))
    except BaseException:
        client.close()
        raise
    _connect_or_close(
        client,
        hostname=host,
        port=port,
        username=username,
        pkey=pkey,
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
        channel_timeout=timeout,
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
    _connect_or_close(
        client,
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
    timeout: float | None = None,
    decode_errors: str = "replace",
    stdin: str | None = None,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> tuple[int, str, str]:
    """執行遠端指令；``stdin`` 有值時寫入後關閉寫入端再讀輸出。

    ``timeout`` 是整支指令的總時限（不是單次讀取的閒置時間），沒給時用
    ``DEFAULT_EXEC_TIMEOUT_SECONDS``；stdout、stderr 各自最多收
    ``max_output_bytes``。超過時關掉 channel，丟 ``SSHCommandTimeoutError``
    （``TimeoutError`` 子類別）或 ``SSHOutputTooLargeError``。

    stdout 與 stderr 輪流讀：paramiko 只在應用端讀取時才放大 channel window，
    若先把 stdout 讀到 EOF，stderr 一多就塞滿 window，遠端卡在寫 stderr、
    這端卡在等 stdout，兩邊互相等待、永遠不會結束。

    敏感資料（密碼等）要走 stdin：指令列會出現在遠端的 ps 與 shell 記錄裡，
    同機的其他使用者看得到。
    """
    limit = DEFAULT_EXEC_TIMEOUT_SECONDS if timeout is None else timeout
    deadline = time.monotonic() + limit
    stdin_ch, stdout_ch, _ = client.exec_command(command, timeout=limit)
    channel = stdout_ch.channel
    try:
        if stdin is not None:
            try:
                stdin_ch.write(stdin)
                stdin_ch.flush()
            except TimeoutError as exc:
                raise SSHCommandTimeoutError(limit) from exc
            finally:
                # 不關寫入端，讀取 stdin 的指令（chpasswd 等）會一直等下去
                channel.shutdown_write()
        stdout, stderr = _collect_output(
            channel, deadline=deadline, limit=limit, max_output_bytes=max_output_bytes
        )
        exit_code = channel.recv_exit_status()
    except BaseException:
        # 逾時或輸出過大時遠端可能還在跑；關掉 channel，別讓它繼續佔著連線
        channel.close()
        raise
    return (
        exit_code,
        stdout.decode(errors=decode_errors),
        stderr.decode(errors=decode_errors),
    )


def _collect_output(
    channel: Any, *, deadline: float, limit: float, max_output_bytes: int
) -> tuple[bytes, bytes]:
    """同時收 stdout／stderr，直到遠端送出 EOF 與結束碼（或 channel 已關）。"""
    stdout = bytearray()
    stderr = bytearray()
    delay = _POLL_MIN_SECONDS
    while True:
        received = False
        if channel.recv_ready():
            _append_limited(
                stdout, channel.recv(_READ_CHUNK_BYTES), max_output_bytes, "stdout"
            )
            received = True
        if channel.recv_stderr_ready():
            _append_limited(
                stderr,
                channel.recv_stderr(_READ_CHUNK_BYTES),
                max_output_bytes,
                "stderr",
            )
            received = True
        # EOF 之前的資料一定已進緩衝區（transport 依序處理），先清空緩衝再收尾
        if not received and _output_finished(channel):
            return bytes(stdout), bytes(stderr)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SSHCommandTimeoutError(limit)
        if received:
            delay = _POLL_MIN_SECONDS
            continue
        time.sleep(min(delay, remaining))
        delay = min(delay * 2, _POLL_MAX_SECONDS)


def _output_finished(channel: Any) -> bool:
    if not (channel.eof_received or channel.closed):
        return False
    if channel.recv_ready() or channel.recv_stderr_ready():
        return False
    return bool(channel.exit_status_ready())


def _append_limited(
    buffer: bytearray, data: bytes, max_bytes: int, stream: str
) -> None:
    if len(buffer) + len(data) > max_bytes:
        raise SSHOutputTooLargeError(stream, max_bytes)
    buffer.extend(data)
