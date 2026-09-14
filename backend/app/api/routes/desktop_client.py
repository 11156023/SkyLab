"""Desktop client download & device auth endpoints.

The desktop client authenticates via a "device auth" flow:
1. Client calls POST /auth/device-code  -> gets a device_code
2. Client opens browser to {frontend}/login?device_code={code}
3. User logs in on the web, frontend auto-calls POST /auth/approve
4. Client polls GET /auth/poll?code={code} -> gets access_token
"""

import logging
import mimetypes
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from app.api.deps import CurrentUser, SessionDep
from app.api.deps.rate_limit import rate_limit_by_ip
from app.core.config import settings
from app.core.i18n import t
from app.schemas.wireguard import (
    WireGuardConnectRequest,
    WireGuardConnectResponse,
    WireGuardDisconnectRequest,
    WireGuardDisconnectResponse,
    WireGuardRefreshRequest,
)
from app.services.network import wireguard_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/desktop-client", tags=["desktop-client"])

_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "downloads"
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DESKTOP_RELEASE_DIR = _REPO_ROOT / "desktop-client" / "release"
_DOWNLOAD_PATTERNS = (
    "campus-cloud-connect.zip",
    "Campus Cloud Connect Setup *.exe",
    "Campus Cloud Connect *.exe",
    "*.msi",
    "*.dmg",
    "*.AppImage",
    "*.zip",
)

# ─── Device auth in-memory store ─────────────────────────────────────────────

_DEVICE_CODE_TTL = 300  # 5 minutes
# 未認證端點會往記憶體寫入；限制同時存在的待核准碼數量，避免被灌爆
_DEVICE_CODE_MAX_PENDING = 1000
_device_codes: dict[str, dict] = {}  # code -> {token, created_at}
_DEVICE_CODE_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="device-code", limit=10, window_seconds=60)
)


def _cleanup_expired() -> None:
    """Remove expired device codes."""
    now = time.time()
    expired = [
        k for k, v in _device_codes.items() if now - v["created_at"] > _DEVICE_CODE_TTL
    ]
    for k in expired:
        del _device_codes[k]


class DeviceCodeResponse(BaseModel):
    device_code: str
    login_url: str
    expires_in: int


class DeviceApproveRequest(BaseModel):
    device_code: str


class DevicePollResponse(BaseModel):
    status: str  # "pending" | "approved"
    access_token: str | None = None


# ─── Device auth endpoints ───────────────────────────────────────────────────


@router.post("/auth/device-code", dependencies=[_DEVICE_CODE_RATE_LIMIT])
def create_device_code() -> DeviceCodeResponse:
    """Generate a new device code for desktop client login."""
    _cleanup_expired()
    if len(_device_codes) >= _DEVICE_CODE_MAX_PENDING:
        raise HTTPException(
            status_code=429, detail=t("desktop.device_code_too_many")
        )
    code = secrets.token_urlsafe(32)
    _device_codes[code] = {"token": None, "created_at": time.time()}
    frontend_url = str(settings.FRONTEND_HOST).rstrip("/")
    login_url = f"{frontend_url}/login?device_code={code}"
    return DeviceCodeResponse(
        device_code=code, login_url=login_url, expires_in=_DEVICE_CODE_TTL
    )


@router.post("/auth/approve")
def approve_device_code(
    body: DeviceApproveRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> dict:
    """Approve a device code (called by the frontend after the user explicitly
    confirms the "authorize this device" prompt).

    The current user's access token is associated with the device code.
    We generate a fresh token for the desktop client using the same user identity.
    """
    from datetime import timedelta  # noqa: PLC0415

    from app.core.security import create_access_token  # noqa: PLC0415

    _cleanup_expired()
    entry = _device_codes.get(body.device_code)
    if entry is None:
        raise HTTPException(
            status_code=404, detail=t("desktop.device_code_not_found")
        )

    if time.time() - entry["created_at"] > _DEVICE_CODE_TTL:
        del _device_codes[body.device_code]
        raise HTTPException(status_code=410, detail=t("desktop.device_code_expired"))

    if entry["token"] is not None:
        # 一組 code 只能被核准一次，避免第二個人（或釣魚頁）覆寫成自己的身份
        raise HTTPException(
            status_code=409, detail=t("desktop.device_code_already_approved")
        )

    # Generate a long-lived access token for the desktop client (8 hours).
    # token_version 必須帶入，否則改過密碼的使用者拿到的 token 會立刻被拒，
    # 且無法透過 token_version 一次撤銷。
    token = create_access_token(
        subject=str(current_user.id),
        expires_delta=timedelta(hours=8),
        token_version=current_user.token_version,
    )
    entry["token"] = token
    return {"status": "approved"}


@router.get("/auth/poll")
def poll_device_code(code: str) -> DevicePollResponse:
    """Poll for device code approval (called by the desktop client)."""
    _cleanup_expired()
    entry = _device_codes.get(code)
    if entry is None:
        raise HTTPException(
            status_code=404, detail=t("desktop.device_code_not_found")
        )

    if entry["token"] is not None:
        token = entry["token"]
        # One-time use: delete after retrieval
        del _device_codes[code]
        return DevicePollResponse(status="approved", access_token=token)

    return DevicePollResponse(status="pending")


# ─── WireGuard tunnel lifecycle ─────────────────────────────────────────────


@router.post(
    "/wireguard/connect",
    response_model=WireGuardConnectResponse,
)
def connect_wireguard(
    body: WireGuardConnectRequest,
    session: SessionDep,
    current_user: CurrentUser,
) -> WireGuardConnectResponse:
    """Authorize this desktop device and return its split-tunnel config.

    The client generates and retains the private key. Only the public key is
    sent to the control plane.
    """
    return wireguard_service.connect(
        session=session,
        user_id=current_user.id,
        device_id=body.device_id,
        public_key=body.public_key,
    )


@router.post(
    "/wireguard/refresh",
    response_model=WireGuardConnectResponse,
)
def refresh_wireguard(
    body: WireGuardRefreshRequest,
    session: SessionDep,
    current_user: CurrentUser,
) -> WireGuardConnectResponse:
    """Renew this device lease and rebuild its current resource ACLs."""
    return wireguard_service.refresh(
        session=session,
        user_id=current_user.id,
        device_id=body.device_id,
    )


@router.post(
    "/wireguard/disconnect",
    response_model=WireGuardDisconnectResponse,
)
def disconnect_wireguard(
    body: WireGuardDisconnectRequest,
    session: SessionDep,
    current_user: CurrentUser,
) -> WireGuardDisconnectResponse:
    """Revoke this device's Gateway peer and all associated VM ACLs."""
    disconnected = wireguard_service.disconnect(
        session=session,
        user_id=current_user.id,
        device_id=body.device_id,
    )
    return WireGuardDisconnectResponse(disconnected=disconnected)


# ─── Download endpoint ───────────────────────────────────────────────────────


def _newest_matching_file(directory: Path, pattern: str) -> Path | None:
    if not directory.exists():
        return None

    matches = [
        path
        for path in directory.glob(pattern)
        if path.is_file() and not path.name.endswith(".blockmap")
    ]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def _find_local_download_asset() -> Path | None:
    for pattern in _DOWNLOAD_PATTERNS:
        asset = _newest_matching_file(_STATIC_DIR, pattern)
        if asset:
            return asset

    if not _DESKTOP_RELEASE_DIR.exists():
        return None

    release_dirs = [path for path in _DESKTOP_RELEASE_DIR.iterdir() if path.is_dir()]
    release_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for release_dir in release_dirs:
        for pattern in _DOWNLOAD_PATTERNS:
            asset = _newest_matching_file(release_dir, pattern)
            if asset:
                return asset

    return None


@router.get("/download")
def download_desktop_client():
    """Return the desktop client installer or archive.

    The installer is intentionally public so a normal browser download can
    follow the redirect without exposing an API access token.

    If DESKTOP_CLIENT_DOWNLOAD_URL is set, redirects to that URL (e.g. a
    GitHub Releases asset). Otherwise serves a local file from static/downloads/
    or the latest desktop-client/release build.
    """
    if settings.DESKTOP_CLIENT_DOWNLOAD_URL:
        return RedirectResponse(settings.DESKTOP_CLIENT_DOWNLOAD_URL, status_code=302)

    download_path = _find_local_download_asset()
    if not download_path:
        logger.warning(
            "Desktop client download asset not found in %s or %s",
            _STATIC_DIR,
            _DESKTOP_RELEASE_DIR,
        )
        raise HTTPException(
            status_code=404,
            detail=t("desktop.installer_not_found"),
        )

    media_type = (
        mimetypes.guess_type(download_path.name)[0] or "application/octet-stream"
    )

    return FileResponse(
        download_path,
        media_type=media_type,
        filename=download_path.name,
    )
