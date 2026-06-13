"""Token 相關工具函數"""

from datetime import datetime, timedelta, timezone

import jwt
from jwt.exceptions import InvalidTokenError

from app.core import security
from app.core.config import settings


def generate_password_reset_token(email: str) -> str:
    """產生密碼重設 JWT token"""
    delta = timedelta(hours=settings.EMAIL_RESET_TOKEN_EXPIRE_HOURS)
    now = datetime.now(timezone.utc)
    expires = now + delta
    exp = expires.timestamp()
    encoded_jwt = jwt.encode(
        {"exp": exp, "nbf": now, "sub": email, "type": "reset"},
        settings.SECRET_KEY,
        algorithm=security.ALGORITHM,
    )
    return encoded_jwt


def verify_password_reset_token(token: str) -> str | None:
    """驗證密碼重設 token"""
    try:
        decoded_token = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        # 同一把 SECRET_KEY 也用來簽 access/refresh token——必須驗證用途，
        # 避免其他類型的 token 被當成密碼重設 token 使用。
        if decoded_token.get("type") != "reset":
            return None
        return str(decoded_token["sub"])
    except InvalidTokenError:
        return None


__all__ = [
    "generate_password_reset_token",
    "verify_password_reset_token",
]
