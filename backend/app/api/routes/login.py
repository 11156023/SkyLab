from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from app.api.deps import (
    CurrentUser,
    SessionDep,
    TokenDep,
    get_current_active_superuser,
    rate_limit_by_ip,
)
from app.core import security
from app.core.config import settings
from app.infrastructure.redis import get_redis, revoke_jti
from app.schemas import Message, NewPassword, Token, TokenPayload, UserPublic
from app.schemas.ldap import LdapLoginRequest, LoginMethodsPublic
from app.services.user import auth_service, ldap_auth_service

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
) -> Token:
    return auth_service.login(
        session=session, email=form_data.username, password=form_data.password
    )


class GoogleLoginRequest(BaseModel):
    id_token: str


@router.post("/login/google", dependencies=[_LOGIN_RATE_LIMIT])
async def login_google(session: SessionDep, body: GoogleLoginRequest) -> Token:
    return await auth_service.google_login(session=session, id_token=body.id_token)


@router.post("/login/ldap", dependencies=[_LOGIN_RATE_LIMIT])
def login_ldap(session: SessionDep, body: LdapLoginRequest) -> Token:
    """以校園 LDAP/AD 帳號登入。"""
    return ldap_auth_service.login_ldap(
        session=session, username=body.username, password=body.password
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


@router.post("/login/test-token", response_model=UserPublic)
def test_token(current_user: CurrentUser) -> Any:
    return current_user


@router.post("/login/logout")
async def logout(
    token: TokenDep,
    current_user: CurrentUser,
    body: RefreshTokenRequest | None = None,
) -> Message:
    """Revoke the current access token (and optional refresh token) by JTI.

    The blacklist entry expires automatically once the token would have
    expired, so revocation incurs zero ongoing storage cost.
    """
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
        except Exception:  # noqa: BLE001
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


@router.post(
    "/password-recovery-html-content/{email}",
    dependencies=[Depends(get_current_active_superuser)],
    response_class=HTMLResponse,
)
def recover_password_html_content(email: str, session: SessionDep) -> Any:
    html_content, subject = auth_service.get_password_recovery_html(
        session=session, email=email
    )
    return HTMLResponse(
        content=html_content, headers={"subject:": subject}
    )
