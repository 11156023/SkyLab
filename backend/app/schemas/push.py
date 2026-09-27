"""Web Push API schemas。"""

from __future__ import annotations

import ipaddress
import re
import uuid
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

_NUMERIC_IPV4_LABEL = re.compile(r"[0-9]+|0x[0-9a-f]*")


def _validate_push_endpoint(value: str) -> str:
    """推播 endpoint 必須是 https 且指向公網主機。

    endpoint 是瀏覽器推播服務給的 URL，後端會對它發 POST；若不限制，
    任何登入者都能讓後端對內網任意位址發請求（SSRF）。這裡不綁死推播
    服務網域清單（各瀏覽器廠商會變），只擋 scheme 與私有／保留位址。
    """
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme != "https":
        raise ValueError("push endpoint must use https")
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
    return value


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
