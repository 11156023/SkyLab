from __future__ import annotations

import hashlib
import os
import socket
import ssl
import tempfile
import threading
from pathlib import Path
from typing import Any

from requests.adapters import HTTPAdapter

from app.exceptions import ProxmoxError
from app.infrastructure.proxmox.settings import ProxmoxSettings

_TCP_PING_TIMEOUT = 0.75
_CA_BUNDLE_DIR = Path(tempfile.gettempdir()) / "skylab-pve-ca"

# CA bundle 路徑 → 對應的 SSLContext（驗鏈、驗主機名，但不開 X509 strict）
_CA_BUNDLE_CONTEXTS: dict[str, ssl.SSLContext] = {}
_CA_BUNDLE_LOCK = threading.Lock()
_ADAPTER_HOOK_ATTR = "_skylab_pve_ca_hook"


def _pve_ca_ssl_context(cafile: str) -> ssl.SSLContext:
    """PVE 自簽 CA 專用的 client context。

    Python 3.13+ 的 urllib3 預設加 ``VERIFY_X509_STRICT``，PVE 產生的 root CA
    沒有 keyUsage 擴充，會被判成「CA cert does not include key usage extension」
    而整條連線失敗。這裡仍要求憑證鏈與主機名，只拿掉 strict（與 pre-flight、
    VNC/terminal WS 的 context 一致）。
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cafile=cafile)
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


def _install_adapter_hook() -> None:
    """讓 requests 對「本模組發出的 CA bundle」改用 ``_pve_ca_ssl_context``。

    proxmoxer 取 ticket 用的是模組層 ``requests.post``（每次新建 Session），
    沒有地方掛自訂 adapter，只能在 ``HTTPAdapter`` 組 pool 參數處介入。
    只有 ``verify`` 恰好是已登記的 bundle 路徑才換 context，其他 HTTPS 流量不受影響。
    """
    original = HTTPAdapter.build_connection_pool_key_attributes
    if getattr(original, _ADAPTER_HOOK_ATTR, False):
        return

    def build_connection_pool_key_attributes(
        self: HTTPAdapter, request: Any, verify: Any, cert: Any = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        host_params, pool_kwargs = original(self, request, verify, cert)
        if isinstance(verify, str):
            ctx = _CA_BUNDLE_CONTEXTS.get(verify)
            if ctx is not None:
                pool_kwargs["ssl_context"] = ctx
        return host_params, pool_kwargs

    setattr(build_connection_pool_key_attributes, _ADAPTER_HOOK_ATTR, True)
    HTTPAdapter.build_connection_pool_key_attributes = (  # type: ignore[method-assign]
        build_connection_pool_key_attributes
    )


def ca_bundle_path(ca_cert_pem: str) -> str:
    """把 CA PEM 落地成檔案並回傳路徑，供 requests/proxmoxer 的 ``verify`` 使用。

    requests 只接受 ``True``/``False``/CA bundle 路徑，沒有「傳 PEM 字串」的選項。
    以內容雜湊命名，同一把 CA 只寫一次；檔案權限 0600。
    """
    digest = hashlib.sha256(ca_cert_pem.encode("utf-8")).hexdigest()[:32]
    path = _CA_BUNDLE_DIR / f"{digest}.pem"
    if not path.exists():
        _CA_BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(ca_cert_pem)
        os.replace(tmp, path)
    key = str(path)
    if key not in _CA_BUNDLE_CONTEXTS:
        with _CA_BUNDLE_LOCK:
            if key not in _CA_BUNDLE_CONTEXTS:
                _install_adapter_hook()
                _CA_BUNDLE_CONTEXTS[key] = _pve_ca_ssl_context(key)
    return key


def resolve_verify(host: str, verify_ssl: bool, ca_cert: str | None) -> bool | str:
    """決定交給 proxmoxer/requests 的 ``verify_ssl`` 值。

    有 CA 時：先做一次 pre-flight 讓錯誤訊息友善，然後回傳 CA bundle 路徑，
    讓**實際承載帳密的每一個** HTTPS 請求都對這把 CA 驗證憑證鏈與主機名。
    以前這裡回傳 ``False``，等於「設了 CA 反而全程不驗 TLS」。
    """
    if ca_cert:
        _verify_server_with_ca(host, ca_cert)
        return ca_bundle_path(ca_cert)
    return verify_ssl


def _tcp_ping(host: str, port: int = 8006, timeout: float = _TCP_PING_TIMEOUT) -> bool:
    """Use TCP connect instead of ICMP to quickly verify host reachability."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (TimeoutError, ConnectionRefusedError, OSError):
        return False


def _verify_server_with_ca(host: str, ca_cert_pem: str, port: int = 8006) -> None:
    """Validate a Proxmox node certificate against the configured CA."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    # 與 requests 的行為一致：驗鏈也驗主機名（SAN 含 hostname 或 IP）。
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=ca_cert_pem)

    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT

    try:
        with socket.create_connection((host, port), timeout=10) as raw_sock:
            with ctx.wrap_socket(raw_sock, server_hostname=host):
                pass
    except ssl.SSLCertVerificationError as exc:
        raise ProxmoxError(f"CA certificate verification failed: {exc}") from exc
    except (TimeoutError, ConnectionRefusedError, OSError) as exc:
        raise ProxmoxError(
            f"Unable to connect to Proxmox host {host}:{port}: {exc}"
        ) from exc


def build_ws_ssl_context(cfg: ProxmoxSettings) -> ssl.SSLContext:
    """Create an SSL context suitable for VNC/terminal websocket handshakes."""
    if cfg.ca_cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cadata=cfg.ca_cert)
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        return ctx

    if cfg.verify_ssl:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_default_certs()
        return ctx

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
