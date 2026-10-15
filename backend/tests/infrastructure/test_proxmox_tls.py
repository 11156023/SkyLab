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


def test_concurrent_first_write_of_same_ca_does_not_fail(
    bundle_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """兩個執行緒同時首次落地同一把 CA：不能因共用暫存檔而 FileNotFoundError。"""
    import threading

    barrier = threading.Barrier(2)
    real_replace = tls.os.replace

    def replace_after_both_wrote(src: object, dst: object) -> None:
        # 讓兩個寫入者都寫完暫存檔後才 replace，重現共用 .tmp 的競態
        barrier.wait(timeout=5)
        real_replace(src, dst)

    monkeypatch.setattr(tls.os, "replace", replace_after_both_wrote)
    pem = _ca_pem()
    errors: list[Exception] = []
    paths: list[str] = []

    def write() -> None:
        try:
            paths.append(tls.ca_bundle_path(pem))
        except Exception as exc:  # pragma: no cover - 失敗時才會進來
            errors.append(exc)

    threads = [threading.Thread(target=write) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(set(paths)) == 1
    assert Path(paths[0]).read_text(encoding="utf-8") == pem
    assert sorted(p.name for p in bundle_dir.iterdir()) == [Path(paths[0]).name]
