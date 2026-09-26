"""監控 stack 的 Grafana：有沒有啟用，以及資源監控頁要連去的網址。

Grafana 屬於選用的 ``monitoring`` compose profile，沒開時容器根本不存在，
所以直接探測內網的 ``/api/health``：連得到才讓前端顯示「在 Grafana 查看詳細」。
探測結果快取一分鐘，避免每次開頁面都多一趟連線逾時。
"""

import logging
import threading
import time
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 2.0
CACHE_TTL_SECONDS = 60.0
DEFAULT_PUBLIC_URL = "/grafana/"


class _ProbeCache:
    lock = threading.Lock()
    expires_at = 0.0
    enabled = False


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
