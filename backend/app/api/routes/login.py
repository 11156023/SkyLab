from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, Response
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from app.api.deps import (
    CurrentUser,
    SessionDep,
    TokenDep,
    rate_limit_by_ip,
)
from app.core import security
from app.core.config import settings
from app.infrastructure.redis import get_redis, revoke_jti
from app.schemas import (
    Message,
    NewPassword,
    Token,
    TokenPayload,
    TotpChallenge,
    TotpLoginRequest,
)
from app.schemas.ldap import LdapLoginRequest, LoginMethodsPublic
from app.services.monitoring import grafana_service
from app.services.user import auth_service, ldap_auth_service, totp_service

router = APIRouter(tags=["login"])

# Brute-force protection: 10 attempts/minute per IP for credential endpoints,
# 3/minute for password recovery to limit email-bombing.
_LOGIN_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="login", limit=10, window_seconds=60)
)
_PASSWORD_RECOVERY_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="pwd-recovery", limit=3, window_seconds=60)
)


@router.post("/login/access-token", dependencies=[_LOGIN_RATE_LIMIT])
def login_access_token(
    session: SessionDep, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
) -> Token | TotpChallenge:
    """密碼登入。帳號已綁定兩步驟驗證時回 ``TotpChallenge``（不含 token），
    前端須再呼叫 ``/login/totp``。"""
    return auth_service.login(
        session=session, email=form_data.username, password=form_data.password
    )


class GoogleLoginRequest(BaseModel):
    id_token: str


@router.post("/login/google", dependencies=[_LOGIN_RATE_LIMIT])
async def login_google(
    session: SessionDep, body: GoogleLoginRequest
) -> Token | TotpChallenge:
    return await auth_service.google_login(session=session, id_token=body.id_token)


@router.post("/login/ldap", dependencies=[_LOGIN_RATE_LIMIT])
def login_ldap(session: SessionDep, body: LdapLoginRequest) -> Token | TotpChallenge:
    """以校園 LDAP/AD 帳號登入。"""
    return ldap_auth_service.login_ldap(
        session=session, username=body.username, password=body.password
    )


@router.post("/login/totp", dependencies=[_LOGIN_RATE_LIMIT])
def login_totp(session: SessionDep, body: TotpLoginRequest) -> Token:
    """兩步驟驗證第二階段：挑戰 token + Authenticator 驗證碼 → 正式 token。"""
    return totp_service.complete_login(
        session=session, totp_token=body.totp_token, code=body.code
    )


@router.get("/login/methods", response_model=LoginMethodsPublic)
def login_methods(session: SessionDep) -> LoginMethodsPublic:
    """回報可用的登入方式（公開端點，登入頁據此顯示分頁）。"""
    return LoginMethodsPublic(**ldap_auth_service.get_login_methods(session=session))


class RefreshTokenRequest(BaseModel):
    refresh_token: str


@router.post("/login/refresh-token")
async def refresh_token(session: SessionDep, body: RefreshTokenRequest) -> Token:
    """Use a refresh token to get a new access + refresh token pair."""
    return await auth_service.refresh_access_token(
        session=session, refresh_token=body.refresh_token
    )


@router.post("/login/logout")
async def logout(
    token: TokenDep,
    current_user: CurrentUser,
    response: Response,
    body: RefreshTokenRequest | None = None,
) -> Message:
    """Revoke the current access token (and optional refresh token) by JTI.

    The blacklist entry expires automatically once the token would have
    expired, so revocation incurs zero ongoing storage cost.
    """
    # 資源監控頁發的 Grafana 免密碼 cookie 只在 /grafana/ 送出，這裡收不到值，
    # 但同路徑的過期 Set-Cookie 仍能讓瀏覽器刪掉它
    response.delete_cookie(
        grafana_service.SESSION_COOKIE, path=grafana_service.SESSION_COOKIE_PATH
    )
    redis = await get_redis()

    def _decode(raw: str) -> TokenPayload | None:
        try:
            payload = jwt.decode(
                raw,
                settings.SECRET_KEY,
                algorithms=[security.ALGORITHM],
                # Allow logging out an already-expired token (no-op effect,
                # but avoids confusing 401s during clock skew).
                options={"verify_exp": False},
            )
            return TokenPayload(**payload)
        except Exception:
            return None

    targets: list[TokenPayload] = []
    if (access := _decode(token)) is not None:
        targets.append(access)
    if body and body.refresh_token and (refresh := _decode(body.refresh_token)):
        targets.append(refresh)

    for data in targets:
        if data.jti and data.exp:
            await revoke_jti(redis, data.jti, data.exp)

    return Message(message="Logged out")


@router.post("/password-recovery/{email}", dependencies=[_PASSWORD_RECOVERY_RATE_LIMIT])
def recover_password(email: str, session: SessionDep) -> Message:
    auth_service.recover_password(session=session, email=email)
    return Message(
        message="If that email is registered, we sent a password recovery link"
    )


@router.post("/reset-password/", dependencies=[_PASSWORD_RECOVERY_RATE_LIMIT])
def reset_password(session: SessionDep, body: NewPassword) -> Message:
    auth_service.reset_password(
        session=session, token=body.token, new_password=body.new_password
    )
    return Message(message="Password updated successfully")
