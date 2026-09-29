"""Web Push API schemas。"""

from __future__ import annotations

import ipaddress
import re
import uuid
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator
from urllib3.exceptions import LocationParseError
from urllib3.util import parse_url

_NUMERIC_IPV4_LABEL = re.compile(r"[0-9]+|0x[0-9a-f]*")

# 各瀏覽器實際使用的推播服務：Chrome／Edge(Chromium) 走 FCM、Firefox 走
# Mozilla autopush、Safari 走 Apple、舊版 Edge 走 WNS。清單外的主機一律拒絕。
_PUSH_HOSTS_EXACT = frozenset({"fcm.googleapis.com", "web.push.apple.com"})
_PUSH_HOST_SUFFIXES = (
    ".push.apple.com",
    ".push.services.mozilla.com",
    ".notify.windows.com",
)


def is_allowed_push_host(host: str) -> bool:
    """主機（已小寫、去掉結尾點）是否為已知的瀏覽器推播服務。"""
    return host in _PUSH_HOSTS_EXACT or host.endswith(_PUSH_HOST_SUFFIXES)


def _sender_sees_same_host(value: str, host: str) -> bool:
    """用送出端（pywebpush → requests）實際使用的 urllib3 再解析一次，確認它連線的
    也是 ``host`` 的 443 埠且沒有 userinfo；兩種解析器結果不一致就不能放行。"""
    try:
        parsed = parse_url(value)
    except LocationParseError:
        return False
    return (
        parsed.auth is None
        and (parsed.host or "").lower().rstrip(".") == host
        and parsed.port in (None, 443)
    )


def _validate_push_endpoint(value: str) -> str:
    """推播 endpoint 必須是 https、443 埠，且主機是已知的瀏覽器推播服務。

    endpoint 是瀏覽器推播服務給的 URL，後端會對它發 POST；若不限制，
    任何登入者都能讓後端對任意位址發請求（SSRF）。因此只接受白名單內的
    推播服務網域（``is_allowed_push_host``）；私有／保留位址與非標準 IPv4
    寫法的檢查仍保留，作為白名單之外的第二道防線。
    """
    value = value.strip()
    # urlsplit 與送出端的 urllib3 對反斜線、空白、控制字元與 userinfo 的解析不同：
    # ``https://127.0.0.1\@fcm.googleapis.com/x`` 在 urlsplit 看來主機是
    # fcm.googleapis.com，urllib3 卻會連到 127.0.0.1。真正的推播 endpoint 不會
    # 有這些內容，一律拒絕，讓下面的主機判斷與實際連線對象一致。
    if any(c == "\\" or c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise ValueError("push endpoint contains invalid characters")
    parts = urlsplit(value)
    if parts.scheme != "https":
        raise ValueError("push endpoint must use https")
    if "@" in parts.netloc:
        raise ValueError("push endpoint must not contain credentials")
    # 結尾的點（FQDN 寫法）不影響解析，先拿掉再判斷，免得 "localhost." 繞過
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith((".localhost", ".local")):
        raise ValueError("push endpoint host is not allowed")
    if parts.port not in (None, 443):
        raise ValueError("push endpoint must use port 443")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # 最後一段是純數字或 0x 十六進位時，瀏覽器與 getaddrinfo 都把整個主機
        # 當 IPv4 解析（127.1、2130706433、0x7f.1 都是 127.0.0.1），但 ipaddress
        # 只認標準四段十進位；這類非標準寫法一律拒絕，不讓它繞過私有位址檢查。
        last_label = host.rsplit(".", 1)[-1]
        if _NUMERIC_IPV4_LABEL.fullmatch(last_label):
            raise ValueError("push endpoint host is not allowed") from None
        if not is_allowed_push_host(host):
            raise ValueError("push endpoint host is not a known push service") from None
        if not _sender_sees_same_host(value, host):
            raise ValueError("push endpoint host is ambiguous") from None
        return value
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped  # ::ffff:127.0.0.1 依內嵌的 IPv4 判斷
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        raise ValueError("push endpoint host is not allowed")
    # 推播服務一律用網域名稱，直接寫 IP 的 endpoint（即使是公網）不接受
    raise ValueError("push endpoint host is not a known push service")


def is_allowed_push_endpoint(endpoint: str) -> bool:
    """給送出端重用的布林版檢查：endpoint 通過 ``_validate_push_endpoint`` 才回 True。"""
    try:
        _validate_push_endpoint(endpoint)
    except ValueError:
        return False
    return True


class VapidPublicKeyResponse(BaseModel):
    enabled: bool = Field(description="後端是否啟用推播（有 VAPID 金鑰且套件可用）")
    public_key: str | None = Field(
        default=None,
        description="base64url 公鑰，前端 pushManager.subscribe 的 applicationServerKey",
    )


class PushSubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=255)
    auth: str = Field(min_length=1, max_length=255)


class PushSubscriptionCreate(BaseModel):
    """瀏覽器 ``PushSubscription.toJSON()`` 的內容加上使用者代理與介面語言。"""

    endpoint: str = Field(min_length=1, max_length=2048)
    keys: PushSubscriptionKeys
    user_agent: str | None = Field(default=None, max_length=512)
    language: str | None = Field(default=None, max_length=16)

    @field_validator("endpoint")
    @classmethod
    def _check_endpoint(cls, value: str) -> str:
        return _validate_push_endpoint(value)


class PushSubscriptionDelete(BaseModel):
    endpoint: str = Field(min_length=1, max_length=4096)


class PushSubscriptionPublic(BaseModel):
    id: uuid.UUID
    endpoint: str
    language: str


class PushSendResult(BaseModel):
    sent: int = Field(description="成功送出的訂閱數")
    removed: int = Field(default=0, description="因推播服務回報失效而移除的訂閱數")
