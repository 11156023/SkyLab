"""Web Push 資料存取：VAPID 金鑰 singleton 與訂閱 CRUD。"""

from __future__ import annotations

import base64
import logging
import uuid
from collections.abc import Sequence

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.security import decrypt_value, encrypt_value
from app.models import PushSubscription, WebPushConfig, get_datetime_utc

logger = logging.getLogger(__name__)

WEB_PUSH_CONFIG_ID = 1


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_vapid_keys() -> tuple[str, str]:
    """產生一組 VAPID 金鑰：(私鑰 PEM, base64url 未壓縮公鑰點)。"""
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return pem, _b64url(public_raw)


def default_vapid_subject() -> str:
    """VAPID sub claim：優先用系統寄件信箱，沒有就用前端網址。"""
    if settings.EMAILS_FROM_EMAIL:
        return f"mailto:{settings.EMAILS_FROM_EMAIL}"
    host = settings.FRONTEND_HOST
    return host if host.startswith("https://") else "mailto:admin@skylab.local"


def get_web_push_config(*, session: Session) -> WebPushConfig:
    """取得 VAPID 金鑰 singleton；不存在則自動產生並持久化。

    ``vapid_private_key_pem`` 一律以 Fernet 加密後入庫，和 SSH 私鑰、AI 金鑰
    同一套處理；拿到 DB dump 的人不該連帶拿到可以冒名推播的簽章私鑰。
    """
    config = session.get(WebPushConfig, WEB_PUSH_CONFIG_ID)
    if config is None:
        pem, public_key = generate_vapid_keys()
        config = WebPushConfig(
            id=WEB_PUSH_CONFIG_ID,
            vapid_private_key_pem=encrypt_value(pem),
            vapid_public_key=public_key,
            subject=default_vapid_subject(),
        )
        session.add(config)
        session.commit()
        session.refresh(config)
    return config


def get_vapid_private_key(*, session: Session, config: WebPushConfig) -> str:
    """取出 VAPID 私鑰明文。

    舊資料是未加密的 PEM，解密會失敗；這時就地視為明文並重新加密寫回，
    不必為了這一列開 migration。換鑰會讓所有既有訂閱失效，所以絕不重新產生。
    """
    stored = config.vapid_private_key_pem
    try:
        return decrypt_value(stored)
    except Exception:
        if "-----BEGIN" not in stored:
            # 既不是本機金鑰能解的密文，也不是 PEM：換過 SECRET_KEY 才會這樣
            raise
        logger.info("VAPID private key stored in plaintext; re-encrypting in place")
        config.vapid_private_key_pem = encrypt_value(stored)
        session.add(config)
        session.commit()
        session.refresh(config)
        return stored


def get_subscription_by_endpoint(
    *, session: Session, endpoint: str
) -> PushSubscription | None:
    return session.exec(
        select(PushSubscription).where(PushSubscription.endpoint == endpoint)
    ).first()


def list_subscriptions_for_user(
    *, session: Session, user_id: uuid.UUID
) -> Sequence[PushSubscription]:
    return session.exec(
        select(PushSubscription).where(PushSubscription.user_id == user_id)
    ).all()


def list_subscribed_user_ids(*, session: Session) -> list[uuid.UUID]:
    """有至少一筆訂閱的使用者；排程只替這些人算快照。"""
    rows = session.exec(select(PushSubscription.user_id).distinct()).all()
    return list(rows)


def upsert_subscription(
    *,
    session: Session,
    user_id: uuid.UUID,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: str | None,
    language: str,
) -> PushSubscription:
    """以 endpoint 為鍵：已存在就更新金鑰／歸屬／語言並重置失敗計數。

    換帳號的情況只有在「同一個瀏覽器、同一組加密金鑰」時才改歸屬——endpoint
    是使用者自己送上來的字串，否則任何人都能拿別人的 endpoint 呼叫這支，把
    對方的推播訂閱搶到自己名下（等於接收對方的所有通知）。金鑰對不上就視為
    另一個訂閱：刪掉舊列、建一筆新的歸在自己名下。
    """
    existing = get_subscription_by_endpoint(session=session, endpoint=endpoint)
    now = get_datetime_utc()
    if existing is not None:
        same_browser_keys = existing.p256dh == p256dh and existing.auth == auth
        if existing.user_id == user_id or same_browser_keys:
            existing.user_id = user_id
            existing.p256dh = p256dh
            existing.auth = auth
            existing.user_agent = user_agent
            existing.language = language
            existing.failure_count = 0
            existing.last_seen_at = now
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing
        session.delete(existing)
        session.flush()

    subscription = PushSubscription(
        user_id=user_id,
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        user_agent=user_agent,
        language=language,
        last_seen_at=now,
    )
    session.add(subscription)
    session.commit()
    session.refresh(subscription)
    return subscription


def delete_subscription(*, session: Session, subscription: PushSubscription) -> None:
    session.delete(subscription)
    session.commit()


def delete_subscriptions_by_ids(*, session: Session, ids: Sequence[uuid.UUID]) -> int:
    if not ids:
        return 0
    rows = session.exec(
        select(PushSubscription).where(col(PushSubscription.id).in_(list(ids)))
    ).all()
    for row in rows:
        session.delete(row)
    session.commit()
    return len(rows)
