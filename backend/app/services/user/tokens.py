"""登入成功後發正式 access + refresh token 對的唯一實作。

密碼／Google（auth_service）、LDAP（ldap_auth_service）與兩步驟驗證第二階段
（totp_service）共用；獨立成葉模組，避免 auth_service ⇄ totp_service 循環 import。
"""

from datetime import timedelta

from app.core import security
from app.core.config import settings
from app.models import User
from app.schemas import Token


def create_token_pair(user: User) -> Token:
    """Create access + refresh token pair bound to the user's token_version."""
    access_token = security.create_access_token(
        user.id,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        token_version=user.token_version,
    )
    refresh_token = security.create_refresh_token(
        user.id,
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        token_version=user.token_version,
    )
    return Token(access_token=access_token, refresh_token=refresh_token)
