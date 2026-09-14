"""Token 相關工具函數"""

from datetime import datetime, timedelta, timezone

import jwt
from jwt.exceptions import InvalidTokenError

from app.core import security
from app.core.config import settings


def generate_password_reset_token(email: str, *, token_version: int = 0) -> str:
    """產生密碼重設 JWT token。

    ``token_version`` 綁定簽發當下使用者的 token_version：重設成功會把
    版本 +1，因此同一個重設連結只能成功使用一次，不會在有效期內重複可用。
    """
    delta = timedelta(hours=settings.EMAIL_RESET_TOKEN_EXPIRE_HOURS)
    now = datetime.now(timezone.utc)
    expires = now + delta
    exp = expires.timestamp()
    encoded_jwt = jwt.encode(
        {
            "exp": exp,
            "nbf": now,
            "sub": email,
            "type": "reset",
            "ver": int(token_version),
        },
        settings.SECRET_KEY,
        algorithm=security.ALGORITHM,
    )
    return encoded_jwt


def decode_password_reset_token(token: str) -> tuple[str, int] | None:
    """解碼密碼重設 token，回傳 (email, token_version)；無效時回傳 None。"""
    try:
        decoded_token = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
    except InvalidTokenError:
        return None
    # 同一把 SECRET_KEY 也用來簽 access/refresh token——必須驗證用途，
    # 避免其他類型的 token 被當成密碼重設 token 使用。
    if decoded_token.get("type") != "reset":
        return None
    try:
        version = int(decoded_token.get("ver", 0))
    except (TypeError, ValueError):
        return None
    return str(decoded_token["sub"]), version


def verify_password_reset_token(token: str) -> str | None:
    """驗證密碼重設 token，回傳 email（不檢查版本；完整檢查見 auth_service）"""
    decoded = decode_password_reset_token(token)
    return decoded[0] if decoded else None


__all__ = [
    "decode_password_reset_token",
    "generate_password_reset_token",
    "verify_password_reset_token",
]
