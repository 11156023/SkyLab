"""app.infrastructure.proxmox.tls 的分支覆蓋：TCP 探測、CA pre-flight 錯誤與 WebSocket SSL context。"""

from __future__ import annotations

import socket
import ssl
from typing import Any

import pytest

from app.exceptions import ProxmoxError
from app.infrastructure.proxmox import tls
from app.infrastructure.proxmox.settings import ProxmoxSettings


def _cfg(*, ca_cert: str | None = None, verify_ssl: bool = False) -> ProxmoxSettings:
    return ProxmoxSettings(
        host="pve.example",
        user="root@pam",
        password="pw",
        verify_ssl=verify_ssl,
        iso_storage="local",
        data_storage="local-lvm",
        api_timeout=10,
        task_check_interval=1,
        pool_name="SkyLab",
        ca_cert=ca_cert,
    )


class _Sock:
    def __enter__(self) -> _Sock:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


def test_tcp_ping_true_when_port_open(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def _connect(address: tuple[str, int], timeout: float) -> _Sock:
        seen["address"], seen["timeout"] = address, timeout
        return _Sock()

    monkeypatch.setattr(socket, "create_connection", _connect)

    assert tls._tcp_ping("10.0.0.1", 8006, timeout=0.5) is True
    assert seen == {"address": ("10.0.0.1", 8006), "timeout": 0.5}


@pytest.mark.parametrize(
    "error", [TimeoutError("t"), ConnectionRefusedError("r"), OSError("o")]
)
def test_tcp_ping_false_on_connect_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def _connect(*_a: Any, **_k: Any) -> _Sock:
        raise error

    monkeypatch.setattr(socket, "create_connection", _connect)

    assert tls._tcp_ping("10.0.0.1") is False


class _Ctx:
    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.server_hostname: str | None = None

    def wrap_socket(self, _sock: Any, *, server_hostname: str) -> _Sock:
        self.server_hostname = server_hostname
        if self.error is not None:
            raise self.error
        return _Sock()


def _patch_preflight(
    monkeypatch: pytest.MonkeyPatch, ctx: _Ctx, connect_error: Exception | None = None
) -> None:
    monkeypatch.setattr(tls, "_pve_ca_ssl_context", lambda **_k: ctx)

    def _connect(*_a: Any, **_k: Any) -> _Sock:
        if connect_error is not None:
            raise connect_error
        return _Sock()

    monkeypatch.setattr(socket, "create_connection", _connect)


def test_verify_server_with_ca_passes_on_successful_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _Ctx(None)
    _patch_preflight(monkeypatch, ctx)

    tls._verify_server_with_ca("pve.example", "PEM", port=443)

    assert ctx.server_hostname == "pve.example"


def test_verify_server_with_ca_reports_certificate_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_preflight(monkeypatch, _Ctx(ssl.SSLCertVerificationError("bad chain")))

    with pytest.raises(ProxmoxError, match="CA certificate verification failed"):
        tls._verify_server_with_ca("pve.example", "PEM")


def test_verify_server_with_ca_reports_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_preflight(monkeypatch, _Ctx(None), connect_error=ConnectionRefusedError())

    with pytest.raises(ProxmoxError, match="Unable to connect to Proxmox host"):
        tls._verify_server_with_ca("pve.example", "PEM", port=8006)


def test_build_ws_ssl_context_verify_ssl_uses_default_certs() -> None:
    ctx = tls.build_ws_ssl_context(_cfg(verify_ssl=True))

    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_build_ws_ssl_context_without_verification() -> None:
    ctx = tls.build_ws_ssl_context(_cfg(verify_ssl=False))

    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_NONE
