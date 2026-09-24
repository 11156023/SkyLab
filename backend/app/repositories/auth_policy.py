"""登入安全政策 singleton 的 DB 存取（含短效快取，供每個請求的閘門查詢）。"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlmodel import Session

from app.models import AuthPolicy

AUTH_POLICY_ID = 1

# get_current_user 每個請求都要知道「是否強制 2FA」；為了不每次都打 DB，
# 在程序內快取幾秒。多 worker 部署時其他 worker 最多延遲這麼久才生效。
_CACHE_TTL_SECONDS = 15.0
_cache: tuple[bool, float] | None = None


def get_auth_policy(*, session: Session) -> AuthPolicy:
    """取得政策 singleton；不存在則以預設值（不強制）建立。"""
    policy = session.get(AuthPolicy, AUTH_POLICY_ID)
    if policy is None:
        policy = AuthPolicy(id=AUTH_POLICY_ID)
        session.add(policy)
        session.commit()
        session.refresh(policy)
    return policy


def update_auth_policy(*, session: Session, totp_required: bool) -> AuthPolicy:
    policy = get_auth_policy(session=session)
    policy.totp_required = totp_required
    policy.updated_at = datetime.now(timezone.utc)
    session.add(policy)
    session.commit()
    session.refresh(policy)
    invalidate_cache()
    return policy


def is_totp_required(*, session: Session) -> bool:
    """強制 2FA 是否開啟（程序內快取 ``_CACHE_TTL_SECONDS``）。"""
    global _cache
    now = time.monotonic()
    if _cache is not None and _cache[1] > now:
        return _cache[0]
    value = get_auth_policy(session=session).totp_required
    _cache = (value, now + _CACHE_TTL_SECONDS)
    return value


def invalidate_cache() -> None:
    global _cache
    _cache = None
