import logging
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import OAuth2PasswordRequestForm
from jwt.exceptions import InvalidTokenError
from pydantic import BaseModel, ValidationError

from app.api.deps import (
    CurrentUser,
    SessionDep,
    TokenDep,
    rate_limit_by_ip,
)
from app.core import security
from app.core.config import settings
from app.core.i18n import t
from app.exceptions import AuthenticationError, BadRequestError
from app.infrastructure.redis import (
    check_rate_limit_by_key,
    get_redis,
    is_jti_revoked,
    peek_rate_limit_by_key,
    rate_limiter,
    revoke_jti,
)
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

logger = logging.getLogger(__name__)

router = APIRouter(tags=["login"])

# Brute-force protection: 10 attempts/minute per IP for credential endpoints,
# 3/minute for password recovery to limit email-bombing.
_LOGIN_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="login", limit=10, window_seconds=60)
)
_PASSWORD_RECOVERY_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="pwd-recovery", limit=3, window_seconds=60)
)

# 兩步驟驗證碼的暴力破解防護（依 IP 的節流之外）：換來源 IP 也繞不過
_TOTP_USER_FAIL_LIMIT = 5
_TOTP_USER_FAIL_WINDOW_SECONDS = 15 * 60
_TOTP_CHALLENGE_FAIL_LIMIT = 3


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


@dataclass(frozen=True)
class _TotpChallengeClaims:
    user_id: str
    jti: str
    exp: int


def _totp_challenge_claims(totp_token: str) -> _TotpChallengeClaims | None:
    """解出挑戰 token 的 sub／jti／exp（簽章與效期照樣驗證）；不合法回 None。"""
    try:
        payload = jwt.decode(
            totp_token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        data = TokenPayload(**payload)
    except (InvalidTokenError, ValidationError):
        return None
    if data.type != "totp" or not data.sub or not data.jti or not data.exp:
        return None
    return _TotpChallengeClaims(user_id=data.sub, jti=data.jti, exp=data.exp)


def _totp_user_fail_key(user_id: str) -> str:
    return f"totp-fail:user:{user_id}"


def _totp_challenge_fail_key(jti: str) -> str:
    return f"totp-fail:jti:{jti}"


async def _record_totp_failure(redis: Any, claims: _TotpChallengeClaims) -> None:
    """記一次驗證碼錯誤：累計到帳號層級，同一張挑戰 token 錯滿次數即作廢。"""
    await check_rate_limit_by_key(
        redis,
        key=_totp_user_fail_key(claims.user_id),
        limit=_TOTP_USER_FAIL_LIMIT,
        window_seconds=_TOTP_USER_FAIL_WINDOW_SECONDS,
        scope="login",
    )
    allowed, info = await check_rate_limit_by_key(
        redis,
        key=_totp_challenge_fail_key(claims.jti),
        limit=_TOTP_CHALLENGE_FAIL_LIMIT,
        window_seconds=_TOTP_USER_FAIL_WINDOW_SECONDS,
        scope="login",
    )
    if not allowed or int(info.get("current") or 0) >= _TOTP_CHALLENGE_FAIL_LIMIT:
        await revoke_jti(redis, claims.jti, claims.exp)


async def _clear_totp_failures(redis: Any, user_id: str) -> None:
    """驗證成功後歸零帳號的失敗計數（失敗計數沿用 rate limiter 的 key 命名）。"""
    if redis is None:
        return
    try:
        await redis.delete(f"{rate_limiter._KEY_PREFIX}{_totp_user_fail_key(user_id)}")
    except Exception:
        logger.warning("Failed to reset TOTP failure counter", exc_info=True)


@router.post("/login/totp", dependencies=[_LOGIN_RATE_LIMIT])
async def login_totp(session: SessionDep, body: TotpLoginRequest) -> Token:
    """兩步驟驗證第二階段：挑戰 token + Authenticator 驗證碼 → 正式 token。

    除了依 IP 節流，另外依帳號與挑戰 token 計算失敗次數：換 IP 也無法無限
    猜 6 位數驗證碼（帳號 15 分鐘內錯 5 次即暫時鎖住；一張挑戰 token 錯 3 次
    即作廢，須重新輸入密碼）。
    """
    claims = _totp_challenge_claims(body.totp_token)
    if claims is None:
        raise AuthenticationError(t("auth.totpChallengeInvalid"))
    redis = await get_redis()
    if await is_jti_revoked(redis, claims.jti):
        raise AuthenticationError(t("auth.totpChallengeInvalid"))
    failures = await peek_rate_limit_by_key(
        redis,
        key=_totp_user_fail_key(claims.user_id),
        window_seconds=_TOTP_USER_FAIL_WINDOW_SECONDS,
    )
    if failures is not None and failures >= _TOTP_USER_FAIL_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=t(
                "auth.totpTooManyAttempts",
                minutes=_TOTP_USER_FAIL_WINDOW_SECONDS // 60,
            ),
            headers={"Retry-After": str(_TOTP_USER_FAIL_WINDOW_SECONDS)},
        )
    try:
        # 同步 DB 查詢丟到 worker thread，不佔住 event loop
        token = await run_in_threadpool(
            totp_service.complete_login,
            session=session,
            totp_token=body.totp_token,
            code=body.code,
        )
    except BadRequestError:
        await _record_totp_failure(redis, claims)
        raise
    await _clear_totp_failures(redis, claims.user_id)
    return token


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
