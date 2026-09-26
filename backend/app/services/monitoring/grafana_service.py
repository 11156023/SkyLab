"""監控 stack 的 Grafana：有沒有啟用、資源監控頁要連去的網址，以及管理員免密碼登入。

Grafana 屬於選用的 ``monitoring`` compose profile，沒開時容器根本不存在，
所以直接探測內網的 ``/api/health``：連得到才讓前端顯示「在 Grafana 查看詳細」。
探測結果快取一分鐘，避免每次開頁面都多一趟連線逾時。

免密碼登入（Grafana auth.proxy）：
1. 管理員開資源監控頁時，後端發一個 ``type=grafana`` 的 JWT，放在只對 ``/grafana/``
   送出的 httponly cookie（前端 JS 讀不到，也不會送到 ``/api``）。
2. 瀏覽器每次打 ``/grafana/``，nginx 先 ``auth_request`` 到後端，後端驗 cookie 並重新
   檢查帳號（停用、改密碼／強制登出、失去管理員權限都會立刻失效），回傳身分標頭；
   nginx 以這組標頭覆寫請求，瀏覽器自己帶的同名標頭一律被蓋掉。
3. Grafana 只信任 nginx 那個固定 IP 送來的標頭（``GF_AUTH_PROXY_WHITELIST``）。
cookie 無效時回空標頭，Grafana 顯示原本的帳密登入頁（備用入口）。
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx
import jwt
from fastapi.concurrency import run_in_threadpool
from jwt.exceptions import InvalidTokenError
from sqlmodel import Session

from app.core import security
from app.core.config import settings
from app.core.permissions import is_admin
from app.models import User

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 2.0
CACHE_TTL_SECONDS = 60.0
DEFAULT_PUBLIC_URL = "/grafana/"

SESSION_COOKIE = "skylab_grafana"
SESSION_COOKIE_PATH = "/grafana/"
SESSION_TOKEN_TYPE = "grafana"
# Grafana 一個頁面會打數十個請求，每個都經 auth_request；短暫快取避免逐一查 DB，
# 代價是帳號被停用後最多再通過這麼久。
AUTH_CACHE_TTL_SECONDS = 10.0
_AUTH_CACHE_MAX_ENTRIES = 1024
# 對應 compose 的 GF_AUTH_PROXY_HEADERS；Grafana 以 GF_AUTH_PROXY_HEADERS_ENCODED
# 解 quoted-printable，中文姓名才能放進 HTTP 標頭。
HEADER_USER = "X-WEBAUTH-USER"
HEADER_EMAIL = "X-WEBAUTH-EMAIL"
HEADER_NAME = "X-WEBAUTH-NAME"
HEADER_ROLE = "X-WEBAUTH-ROLE"
GRAFANA_ROLE = "Admin"


class _ProbeCache:
    lock = threading.Lock()
    expires_at = 0.0
    enabled = False


class _AuthCache:
    lock = threading.Lock()
    entries: dict[str, tuple[float, dict[str, str]]] = {}


def public_url() -> str:
    """瀏覽器要開的 Grafana 網址：GRAFANA_ROOT_URL，未設定時走同網域的 nginx /grafana/。"""
    return (settings.GRAFANA_ROOT_URL or "").strip() or DEFAULT_PUBLIC_URL


async def _probe() -> bool:
    url = f"{settings.GRAFANA_INTERNAL_URL.rstrip('/')}/api/health"
    try:
        # trust_env=False：內網主機名不能被 HTTP(S)_PROXY 環境變數導去外部 proxy
        async with httpx.AsyncClient(
            timeout=PROBE_TIMEOUT_SECONDS, trust_env=False
        ) as client:
            resp = await client.get(url)
    except httpx.HTTPError:
        logger.debug("Grafana probe failed: %s", url, exc_info=True)
        return False
    return resp.status_code == 200


async def get_grafana_link(*, use_cache: bool = True) -> dict[str, Any]:
    now = time.monotonic()
    with _ProbeCache.lock:
        cached = use_cache and now < _ProbeCache.expires_at
        enabled = _ProbeCache.enabled
    if not cached:
        enabled = await _probe()
        with _ProbeCache.lock:
            _ProbeCache.enabled = enabled
            _ProbeCache.expires_at = time.monotonic() + CACHE_TTL_SECONDS
    return {"enabled": enabled, "url": public_url() if enabled else None}


def session_max_age_seconds() -> int:
    return settings.GRAFANA_SESSION_EXPIRE_MINUTES * 60


def create_session_token(user: User) -> str:
    """給 /grafana/ cookie 用的 JWT；type 不是 access，不能拿來呼叫 SkyLab API。"""
    payload = {
        "exp": datetime.now(timezone.utc)
        + timedelta(seconds=session_max_age_seconds()),
        "sub": str(user.id),
        "type": SESSION_TOKEN_TYPE,
        "ver": user.token_version,
        "jti": uuid4().hex,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=security.ALGORITHM)


def _quoted_printable(value: str) -> str:
    """可見 ASCII（``=`` 除外）原樣保留，其餘位元組（含中文、空白、換行）轉 ``=XX``。"""
    return "".join(
        chr(b) if 0x21 <= b <= 0x7E and b != 0x3D else f"={b:02X}"
        for b in value.encode("utf-8")
    )


def _allowed(user: User, version: object) -> bool:
    return (
        user.is_active
        and user.token_version == version
        and is_admin(user)
        # 被要求綁定兩步驟驗證卻還沒綁的帳號，SkyLab 本身也只能走綁定流程
        and not (user.totp_required and not user.totp_enabled)
    )


def _proxy_headers(user: User) -> dict[str, str]:
    return {
        HEADER_USER: _quoted_printable(user.email),
        HEADER_EMAIL: _quoted_printable(user.email),
        HEADER_NAME: _quoted_printable(user.full_name or user.email),
        HEADER_ROLE: GRAFANA_ROLE,
    }


async def resolve_proxy_headers(session: Session, token: str | None) -> dict[str, str]:
    """cookie 換成 Grafana auth.proxy 的身分標頭；無效時回空 dict（Grafana 顯示登入頁）。"""
    if not token:
        return {}
    now = time.monotonic()
    with _AuthCache.lock:
        hit = _AuthCache.entries.get(token)
        if hit and now < hit[0]:
            return dict(hit[1])

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[security.ALGORITHM])
    except InvalidTokenError:
        return {}
    if payload.get("type") != SESSION_TOKEN_TYPE:
        return {}
    try:
        user = await run_in_threadpool(session.get, User, payload.get("sub"))
    except Exception:
        # sub 不是合法 UUID 等情況；簽章已驗過，實務上只會是舊格式或壞掉的 token
        logger.debug("Grafana session lookup failed", exc_info=True)
        return {}
    headers = (
        _proxy_headers(user)
        if user is not None and _allowed(user, payload.get("ver"))
        else {}
    )

    with _AuthCache.lock:
        if len(_AuthCache.entries) >= _AUTH_CACHE_MAX_ENTRIES:
            _AuthCache.entries = {
                k: v for k, v in _AuthCache.entries.items() if v[0] > now
            }
            if len(_AuthCache.entries) >= _AUTH_CACHE_MAX_ENTRIES:
                _AuthCache.entries.clear()
        _AuthCache.entries[token] = (now + AUTH_CACHE_TTL_SECONDS, headers)
    return dict(headers)
