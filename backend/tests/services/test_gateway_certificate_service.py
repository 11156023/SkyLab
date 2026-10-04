"""Gateway 的 HTTPS 憑證（管理員自備，平台入口與 VM 網域共用）：路徑驗證、存檔檢查、狀態。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import BadRequestError, ProxmoxError
from app.repositories import gateway_config as gw_repo
from app.repositories import reverse_proxy as rp_repo
from app.schemas.gateway import GatewayCertificateUpdate
from app.services.network import (
    gateway_certificate_service as svc,
)
from app.services.network import (
    gateway_service,
    platform_entry_service,
    reverse_proxy_service,
)
from app.services.network import nginx_gateway_service as nginx

CERT = "/etc/ssl/skylab/fullchain.pem"
KEY = "/etc/ssl/skylab/privkey.pem"


def _inspect_output(*sans: str, end: str = "Jan  5 12:00:00 2099 GMT", key_match: str = "1") -> str:
    lines = [
        "cert_readable=1",
        "key_readable=1",
        "cert_valid=1",
        f"cert_end={end}",
        "key_valid=1",
        f"key_match={key_match}",
        *(f"san={name}" for name in sans),
    ]
    return "\n".join(lines) + "\n"


class _Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.config = SimpleNamespace(
            host="10.0.0.2",
            encrypted_private_key="x",
            ssl_certificate_path="",
            ssl_certificate_key_path="",
        )
        self.platform: nginx.PlatformEntry | None = None
        self.rules: list[Any] = []
        self.inspect_output = _inspect_output("example.com", "*.example.com")
        self.commands: list[str] = []
        self.saves: list[tuple[str, str]] = []
        self.syncs = 0
        self.sync_error: Exception | None = None

        def fake_save(_session: object, *, ssl_certificate_path: str, ssl_certificate_key_path: str) -> Any:
            self.saves.append((ssl_certificate_path, ssl_certificate_key_path))
            self.config.ssl_certificate_path = ssl_certificate_path
            self.config.ssl_certificate_key_path = ssl_certificate_key_path
            return self.config

        @contextmanager
        def fake_client(_session: object) -> Iterator[object]:
            yield object()

        def fake_exec(_client: Any, command: str, **_kwargs: Any) -> tuple[int, str, str]:
            self.commands.append(command)
            return 0, self.inspect_output, ""

        def fake_sync(_session: object) -> None:
            self.syncs += 1
            if self.sync_error is not None:
                raise self.sync_error

        monkeypatch.setattr(gw_repo, "get_gateway_config", lambda _s: self.config)
        monkeypatch.setattr(gw_repo, "save_certificate_paths", fake_save)
        monkeypatch.setattr(platform_entry_service, "load_entry", lambda _s: self.platform)
        monkeypatch.setattr(rp_repo, "list_rules", lambda _s: self.rules)
        monkeypatch.setattr(gateway_service, "gateway_client_or_502", fake_client)
        monkeypatch.setattr(nginx, "_exec", fake_exec)
        monkeypatch.setattr(reverse_proxy_service, "sync_to_gateway", fake_sync)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    return _Harness(monkeypatch)


def _update(cert: str = CERT, key: str = KEY) -> GatewayCertificateUpdate:
    return GatewayCertificateUpdate(ssl_certificate_path=cert, ssl_certificate_key_path=key)


def _https_platform(domain: str = "skylab.example.com") -> nginx.PlatformEntry:
    return nginx.PlatformEntry(domain, "192.168.100.20", 8082, True)


# ─── 路徑驗證 ────────────────────────────────────────────────────────────────


def test_normalize_cert_path_accepts_absolute_paths_only() -> None:
    assert svc.normalize_cert_path(f"  {CERT} ") == CERT
    assert svc.normalize_cert_path("") == ""
    assert svc.normalize_cert_path("/etc/letsencrypt/live/example.com/privkey.pem")
    # 這個值會寫進 nginx 設定與 Gateway 上的指令，不能夾帶其他字元
    for bad in (
        "etc/ssl/a.pem",
        "/etc/ssl/a b.pem",
        "/etc/ssl/a.pem;",
        "/etc/ssl/../shadow",
        "/etc/ssl/./a.pem",
        "/etc/ssl/*.pem",
        "/etc/ssl/$(id).pem",
        "/" + "a" * 600,
    ):
        with pytest.raises(BadRequestError):
            svc.normalize_cert_path(bad)


def test_load_paths_needs_both_paths(harness: _Harness) -> None:
    assert svc.load_paths(object()) is None
    harness.config.ssl_certificate_path = CERT
    assert svc.load_paths(object()) is None
    harness.config.ssl_certificate_key_path = KEY
    assert svc.load_paths(object()) == nginx.CertificatePaths(certificate=CERT, key=KEY)


# ─── 儲存 ────────────────────────────────────────────────────────────────────


def test_save_inspects_then_persists_and_syncs(harness: _Harness) -> None:
    result = svc.save_config(object(), _update())

    assert "Subject Alternative Name" in harness.commands[0]
    assert harness.saves == [(CERT, KEY)]
    assert harness.syncs == 1
    assert result.configured is True
    assert result.ssl_certificate_path == CERT


def test_save_requires_both_paths(harness: _Harness) -> None:
    with pytest.raises(BadRequestError):
        svc.save_config(object(), _update(key=""))
    assert harness.saves == []
    assert harness.commands == []


@pytest.mark.parametrize(
    ("output", "match"),
    [
        ("cert_readable=0\nkey_readable=1\n", "fullchain.pem"),
        ("cert_readable=1\nkey_readable=0\n", "privkey.pem"),
        ("cert_readable=1\nkey_readable=1\ncert_valid=0\nkey_valid=1\n", "PEM"),
        (_inspect_output("*.example.com", key_match="0"), None),
        (_inspect_output("*.example.com", end="Jan  5 12:00:00 2020 GMT"), "2020-01-05"),
    ],
)
def test_save_rejects_unusable_certificate(
    harness: _Harness, output: str, match: str | None
) -> None:
    harness.inspect_output = output

    with pytest.raises(BadRequestError, match=match):
        svc.save_config(object(), _update())
    assert harness.saves == []
    assert harness.syncs == 0


def test_save_requires_covering_active_https_platform_domain(harness: _Harness) -> None:
    harness.platform = _https_platform("skylab.other.org")

    with pytest.raises(BadRequestError, match="skylab.other.org"):
        svc.save_config(object(), _update())
    assert harness.saves == []


def test_save_does_not_block_on_uncovered_vm_domains(harness: _Harness) -> None:
    """VM 網域沒涵蓋只提示：站台仍可連，瀏覽器會警告。"""
    harness.rules = [SimpleNamespace(domain="app.other.org", enable_https=True)]

    svc.save_config(object(), _update())

    assert harness.saves == [(CERT, KEY)]


def test_clearing_is_blocked_while_platform_uses_https(harness: _Harness) -> None:
    harness.config.ssl_certificate_path = CERT
    harness.config.ssl_certificate_key_path = KEY
    harness.platform = _https_platform()

    with pytest.raises(BadRequestError):
        svc.save_config(object(), _update(cert="", key=""))
    assert harness.saves == []


def test_clearing_falls_back_to_self_signed_without_inspection(harness: _Harness) -> None:
    harness.config.ssl_certificate_path = CERT
    harness.config.ssl_certificate_key_path = KEY

    result = svc.save_config(object(), _update(cert="", key=""))

    assert harness.commands == []
    assert harness.saves == [("", "")]
    assert harness.syncs == 1
    assert result.configured is False


def test_save_restores_previous_paths_when_sync_fails(harness: _Harness) -> None:
    harness.config.ssl_certificate_path = "/etc/ssl/old.pem"
    harness.config.ssl_certificate_key_path = "/etc/ssl/old.key"
    harness.sync_error = ProxmoxError("nginx -t failed")

    with pytest.raises(ProxmoxError):
        svc.save_config(object(), _update())

    assert harness.saves == [(CERT, KEY), ("/etc/ssl/old.pem", "/etc/ssl/old.key")]


def test_save_is_rejected_when_gateway_cannot_be_reached(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextmanager
    def no_gateway(_session: object) -> Iterator[object]:
        raise BadRequestError("gateway not configured")
        yield  # pragma: no cover

    monkeypatch.setattr(gateway_service, "gateway_client_or_502", no_gateway)

    # 沒有 SSH 就檢查不了，直接拒絕（不會存一個沒驗證過的路徑）
    with pytest.raises(BadRequestError):
        svc.save_config(object(), _update())
    assert harness.saves == []


# ─── 狀態 ────────────────────────────────────────────────────────────────────


def test_status_without_certificate_lists_https_domains_as_uncovered(harness: _Harness) -> None:
    harness.platform = _https_platform()
    harness.rules = [
        SimpleNamespace(domain="app.example.com", enable_https=True),
        SimpleNamespace(domain="plain.example.com", enable_https=False),
    ]

    status = svc.get_status(object())

    assert status.configured is False
    assert status.uncovered_domains == ["skylab.example.com", "app.example.com"]
    assert harness.commands == []


def test_status_reports_coverage_and_expiry(harness: _Harness) -> None:
    harness.config.ssl_certificate_path = CERT
    harness.config.ssl_certificate_key_path = KEY
    harness.platform = _https_platform()
    harness.rules = [
        SimpleNamespace(domain="app.example.com", enable_https=True),
        SimpleNamespace(domain="deep.lab.example.com", enable_https=True),
    ]

    status = svc.get_status(object())

    assert status.configured is True
    assert status.key_matches is True
    assert status.expires_at is not None and status.expires_at.year == 2099
    assert status.dns_names == ["example.com", "*.example.com"]
    assert status.covered_domains == ["skylab.example.com", "app.example.com"]
    # 萬用憑證只涵蓋一層子網域
    assert status.uncovered_domains == ["deep.lab.example.com"]
