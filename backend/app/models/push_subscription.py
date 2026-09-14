"""Web Push：瀏覽器推播訂閱與 VAPID 金鑰。

- ``PushSubscription``：一筆＝一個瀏覽器（Service Worker）的訂閱，由前端
  ``pushManager.subscribe()`` 取得後回報。endpoint 全域唯一；同一台瀏覽器換帳號
  登入時以 endpoint upsert，把歸屬換到新使用者。
- ``WebPushConfig``：VAPID 金鑰對（單列 singleton，id 固定為 1）。第一次用到時
  自動產生並持久化，之後所有推播都以同一把私鑰簽章；換鑰會讓既有訂閱全部失效。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel

from .base import get_datetime_utc


class PushSubscription(SQLModel, table=True):
    __tablename__ = "push_subscriptions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    # 推播服務給的 URL，長度不定（FCM 約 200 字、部分服務更長），用 Text
    endpoint: str = Field(sa_column=Column(Text, nullable=False, unique=True))
    p256dh: str = Field(max_length=255)
    auth: str = Field(max_length=255)
    user_agent: str | None = Field(default=None, max_length=512)
    # 訂閱當下前端的介面語言；推播文案依此翻譯（排程情境沒有 request 語言可用）
    language: str = Field(default="zh-TW", max_length=16)
    # 連續送達失敗次數；推播服務回 404／410 會直接刪除，其他錯誤累計到上限才刪
    failure_count: int = Field(default=0)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    last_seen_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class WebPushConfig(SQLModel, table=True):
    """VAPID 金鑰（單列 singleton，id 固定為 1）"""

    __tablename__ = "web_push_config"

    id: int = Field(default=1, primary_key=True)
    # PEM 格式的 EC P-256 私鑰；pywebpush 以它簽 VAPID JWT
    vapid_private_key_pem: str = Field(sa_column=Column(Text, nullable=False))
    # base64url（無 padding）的未壓縮公鑰點，前端 applicationServerKey 直接用
    vapid_public_key: str = Field(max_length=255)
    # VAPID 的 sub claim，推播服務出問題時的聯絡方式（mailto: 或 https:）
    subject: str = Field(max_length=255)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
