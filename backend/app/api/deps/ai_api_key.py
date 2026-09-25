"""
AI API Key 认证依赖
"""

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import col, select

from app.api.deps.database import SessionDep
from app.core.i18n import t
from app.core.security import decrypt_value
from app.models import (
    API_KEY_PREFIX_LENGTH,
    LEGACY_API_KEY_PREFIX_LENGTH,
    AIAPICredential,
    User,
    get_datetime_utc,
)


def get_current_user_by_ai_api_key(
    session: SessionDep,
    authorization: str = Header(..., description="Bearer ccai_xxx"),
) -> tuple[User, AIAPICredential]:
    """
    通过 AI API Key (ccai_xxx) 验证用户身份

    Returns:
        tuple[User, AIAPICredential]: 用户对象和凭证对象

    Raises:
        HTTPException: 401 如果认证失败
    """
    # 1. 提取 token
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=t("ai_api_key.invalid_auth_format"),
        )

    # removeprefix 只切掉開頭那一個 "Bearer "；replace 會把金鑰內部同樣的
    # 字串也一併刪掉，讓合法金鑰驗不過
    api_key = authorization.removeprefix("Bearer ").strip()

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=t("ai_api_key.api_key_required"),
        )

    # 2. 用 prefix 縮小查詢範圍。新金鑰存 16 字元前綴，2026-09 之前核發的只有
    #    8 字元，兩種長度都要查，舊金鑰才不會在換算法後突然驗不過。
    prefix_candidates = {
        api_key[: min(API_KEY_PREFIX_LENGTH, len(api_key))],
        api_key[: min(LEGACY_API_KEY_PREFIX_LENGTH, len(api_key))],
    }

    candidates = session.exec(
        select(AIAPICredential)
        .where(col(AIAPICredential.api_key_prefix).in_(prefix_candidates))
        .where(AIAPICredential.revoked_at.is_(None))
    ).all()

    # 3. 逐一解密比對，找到真正匹配的憑證（用 compare_digest 避免時序側通道）
    credential = None
    for cand in candidates:
        try:
            decrypted_key = decrypt_value(cand.api_key_encrypted)
        except Exception:
            continue
        if secrets.compare_digest(decrypted_key, api_key):
            credential = cand
            break

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=t("ai_api_key.invalid_or_revoked"),
        )

    # 4. 检查过期
    if credential.expires_at and credential.expires_at < get_datetime_utc():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=t("ai_api_key.api_key_expired"),
        )

    # 5. 获取用户并检查状态
    user = session.get(User, credential.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=t("ai_api_key.user_not_found"),
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=t("ai_api_key.user_inactive"),
        )

    return user, credential


# 类型标注（用于依赖注入）
AIAPIUserDep = Annotated[
    tuple[User, AIAPICredential], Depends(get_current_user_by_ai_api_key)
]


__all__ = ["get_current_user_by_ai_api_key", "AIAPIUserDep"]
