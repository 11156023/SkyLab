import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.routes import desktop_client
from app.core.config import settings
from app.schemas.desktop_client import DeviceApproveRequest


@pytest.fixture
def device_codes(monkeypatch):
    class Codes(dict):
        def set(self, code, value, ttl_seconds=None):
            self[code] = value
            self.last_ttl = ttl_seconds

        def delete(self, code):
            self.pop(code, None)

    codes = Codes()
    codes["pending"] = {"token": None, "created_at": time.time() - 30}
    monkeypatch.setattr(desktop_client, "_device_codes", codes)
    return codes


def test_denied_device_is_reported_without_tokens_and_cannot_be_approved(device_codes):
    body = DeviceApproveRequest(device_code="pending")
    assert desktop_client.deny_device_code(body, None) == {"status": "denied"}
    assert 0 < device_codes.last_ttl <= desktop_client._DEVICE_CODE_TTL - 30
    for _ in range(2):
        result = desktop_client.poll_device_code("pending")
        assert result.status == "denied"
        assert result.access_token is None
        assert result.refresh_token is None
    with pytest.raises(HTTPException) as error:
        desktop_client.approve_device_code(body, None)
    assert error.value.status_code == 410


def test_denial_is_idempotent_and_does_not_extend_expiry(device_codes):
    body = DeviceApproveRequest(device_code="pending")
    desktop_client.deny_device_code(body, None)
    first_ttl = device_codes.last_ttl
    desktop_client.deny_device_code(body, None)
    assert device_codes.last_ttl <= first_ttl


def test_denial_cannot_overwrite_an_approved_device(device_codes):
    device_codes["pending"]["token"] = "approved-token"
    with pytest.raises(HTTPException) as error:
        desktop_client.deny_device_code(
            DeviceApproveRequest(device_code="pending"), None
        )
    assert error.value.status_code == 409
    assert desktop_client.poll_device_code("pending").access_token == "approved-token"


def test_deny_unknown_or_expired_device(device_codes):
    with pytest.raises(HTTPException) as error:
        desktop_client.deny_device_code(
            DeviceApproveRequest(device_code="unknown"), None
        )
    assert error.value.status_code == 404
    device_codes["pending"]["created_at"] = time.time() - 301
    with pytest.raises(HTTPException) as error:
        desktop_client.deny_device_code(
            DeviceApproveRequest(device_code="pending"), None
        )
    assert error.value.status_code == 410
    assert "pending" not in device_codes


def _write(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test")
    return path


def test_find_local_download_asset_follows_pattern_order(tmp_path, monkeypatch) -> None:
    static_dir = tmp_path / "static" / "downloads"
    static_zip = _write(static_dir / "campus-cloud-connect.zip")
    _write(static_dir / "Campus Cloud Connect Setup 0.1.0.exe")

    monkeypatch.setattr(desktop_client, "_STATIC_DIR", static_dir)

    assert desktop_client._find_local_download_asset() == static_zip


def test_find_local_download_asset_ignores_blockmap_files(
    tmp_path, monkeypatch
) -> None:
    static_dir = tmp_path / "static" / "downloads"
    _write(static_dir / "Campus Cloud Connect Setup 0.1.0.exe.blockmap")

    monkeypatch.setattr(desktop_client, "_STATIC_DIR", static_dir)

    assert desktop_client._find_local_download_asset() is None


def test_download_redirects_to_configured_public_release(monkeypatch) -> None:
    release_url = (
        "https://github.com/1Ray0/SkyLab-Connect-Releases/"
        "releases/latest/download/SkyLab-Connect-Setup.exe"
    )
    monkeypatch.setattr(settings, "DESKTOP_CLIENT_DOWNLOAD_URL", release_url)

    response = desktop_client.download_desktop_client()

    assert response.status_code == 302
    assert response.headers["location"] == release_url
