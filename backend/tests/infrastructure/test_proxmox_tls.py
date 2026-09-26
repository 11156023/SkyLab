"""PVE CA bundle：requests 要改用不開 X509 strict 的 context（PVE root CA 沒有 keyUsage）。"""

from __future__ import annotations

import ssl
from pathlib import Path

import certifi
import pytest
import requests
from requests.adapters import HTTPAdapter

from app.infrastructure.proxmox import tls


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


@pytest.fixture
def bundle_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(tls, "_CA_BUNDLE_DIR", tmp_path)
    monkeypatch.setattr(tls, "_CA_BUNDLE_CONTEXTS", {})
    return tmp_path


def _ca_pem() -> str:
    return Path(certifi.where()).read_text(encoding="utf-8")


def _pool_kwargs(verify: object) -> dict:
    request = requests.Request("GET", "https://pve.example:8006/api2/json").prepare()
    _host, pool_kwargs = HTTPAdapter().build_connection_pool_key_attributes(
        request, verify
    )
    return pool_kwargs


def test_ca_bundle_uses_non_strict_context(bundle_dir: Path) -> None:
    path = tls.ca_bundle_path(_ca_pem())

    assert Path(path).parent == bundle_dir
    ctx = _pool_kwargs(path).get("ssl_context")
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        assert not ctx.verify_flags & ssl.VERIFY_X509_STRICT


def test_other_verify_values_keep_default_context(bundle_dir: Path) -> None:
    tls.ca_bundle_path(_ca_pem())

    assert "ssl_context" not in _pool_kwargs(True)
    assert "ssl_context" not in _pool_kwargs(False)
    assert "ssl_context" not in _pool_kwargs(certifi.where())


def test_hook_installed_once(bundle_dir: Path) -> None:
    tls.ca_bundle_path(_ca_pem())
    hook = HTTPAdapter.build_connection_pool_key_attributes
    tls.ca_bundle_path(_ca_pem() + "\n")

    assert HTTPAdapter.build_connection_pool_key_attributes is hook
    assert len(tls._CA_BUNDLE_CONTEXTS) == 2
