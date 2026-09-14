"""Web Push API schemas。"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


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

    endpoint: str = Field(min_length=1, max_length=4096)
    keys: PushSubscriptionKeys
    user_agent: str | None = Field(default=None, max_length=512)
    language: str | None = Field(default=None, max_length=16)


class PushSubscriptionDelete(BaseModel):
    endpoint: str = Field(min_length=1, max_length=4096)


class PushSubscriptionPublic(BaseModel):
    id: uuid.UUID
    endpoint: str
    language: str


class PushSendResult(BaseModel):
    sent: int = Field(description="成功送出的訂閱數")
    removed: int = Field(default=0, description="因推播服務回報失效而移除的訂閱數")
