import uuid
from typing import Any

import jwt
from fastapi.concurrency import run_in_threadpool
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError
from sqlmodel import Session

from app.core import security
from app.core.config import settings
from app.core.i18n import t
from app.exceptions import AuthenticationError, BadRequestError
from app.infrastructure.google.tokeninfo import (
    GoogleTokenInfoNetworkError,
    GoogleTokenInfoRejected,
    fetch_id_token_info,
)
from app.models import AuditAction, User
from app.repositories import user as user_repo
from app.schemas import Token, TokenPayload, TotpChallenge, UserUpdate
from app.services.user import audit_service, totp_service
from app.services.user.tokens import create_token_pair
from app.utils import (
    decode_password_reset_token,
    generate_password_reset_token,
    generate_reset_password_email,
    send_email,
)


def login(
    *, session: Session, email: str, password: str
) -> Token | TotpChallenge:
    user = user_repo.authenticate(session=session, email=email, password=password)
    if not user:
        audit_service.log_action(
            session=session,
            user_id=None,
            action=AuditAction.login_failed,
            details=f"Failed login attempt for email: {email}",
        )
        raise BadRequestError(t("auth.incorrectCredentials"))
    if not user.is_active:
        audit_service.log_action(
            session=session,
            user_id=user.id,
            action=AuditAction.login_failed,
            details=f"Login blocked: inactive user {email}",
        )
        raise BadRequestError(t("auth.inactiveUser"))
    # 已綁定兩步驟驗證：密碼只算第一階段，成功稽核留到驗證碼通過後再寫
    if user.totp_enabled:
        return totp_service.issue_challenge(user, method="password")
    audit_service.log_action(
        session=session,
        user_id=user.id,
        action=AuditAction.login_success,
        details=f"User {user.email} logged in via password",
    )
    return create_token_pair(user)


def _log_google_login_failure(
    session: Session,
    reason: str,
    email: str | None = None,
    user_id: uuid.UUID | None = None,
) -> None:
    audit_service.log_action(
        session=session,
        user_id=user_id,
        action=AuditAction.login_google_failed,
        details=f"Google login failed ({reason})" + (f" for {email}" if email else ""),
    )


def _is_email_verified(data: dict[str, Any]) -> bool:
    raw = data.get("email_verified")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.lower() == "true"
    return False


def _complete_google_login(session: Session, email: str) -> Token | TotpChallenge:
    """Google ID token 已驗過之後的同步 DB 流程（查帳號、稽核、發 token）。"""
    user = user_repo.get_user_by_email(session=session, email=email)
    if not user:
        _log_google_login_failure(session, "user not found", email)
        raise BadRequestError(t("auth.googleAccountNotRegistered"))
    if not user.is_active:
        _log_google_login_failure(session, "inactive user", email, user.id)
        raise BadRequestError(t("auth.inactiveUser"))
    if user.totp_enabled:
        return totp_service.issue_challenge(user, method="google")
    audit_service.log_action(
        session=session,
        user_id=user.id,
        action=AuditAction.login_google_success,
        details=f"User {user.email} logged in via Google",
    )
    return create_token_pair(user)


async def google_login(
    *, session: Session, id_token: str
) -> Token | TotpChallenge:
    # 稽核寫入與帳號查詢都是同步 DB 操作（會 commit），一律丟到 worker thread，
    # 不佔住 event loop；event loop 上只留 Google tokeninfo 呼叫與純資料檢查。
    async def _fail(
        reason: str, email: str | None = None, user_id: uuid.UUID | None = None
    ) -> None:
        await run_in_threadpool(
            _log_google_login_failure, session, reason, email, user_id
        )

    # aud 必須永遠驗證：未設定 GOOGLE_CLIENT_ID 時不得接受任何 Google ID token，
    # 否則使用者交給其他 OAuth 應用的 ID token 也能登入本系統。
    if not settings.GOOGLE_CLIENT_ID:
        await _fail("google login not configured")
        raise BadRequestError(t("auth.googleNotConfigured"))

    try:
        data = await fetch_id_token_info(id_token)
    except GoogleTokenInfoNetworkError as exc:
        await _fail("network error")
        raise BadRequestError(t("auth.googleTokenVerifyFailed")) from exc
    except GoogleTokenInfoRejected:
        await _fail("invalid token")
        raise BadRequestError(t("auth.googleTokenInvalid"))
    if data.get("aud") != settings.GOOGLE_CLIENT_ID:
        await _fail("invalid audience")
        raise BadRequestError(t("auth.googleTokenAudienceInvalid"))
    if not _is_email_verified(data):
        await _fail("email not verified", data.get("email"))
        raise BadRequestError(t("auth.googleEmailNotVerified"))
    email = data.get("email")
    if not email:
        await _fail("missing email")
        raise BadRequestError(t("auth.googleEmailMissing"))
    return await run_in_threadpool(_complete_google_login, session, email)


def _decode_token_ignoring_expiry(raw: str) -> TokenPayload | None:
    """驗簽但不驗效期地解出 token；不合法回 None。"""
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
    except (InvalidTokenError, ValidationError):
        return None


async def logout(access_token: str, refresh_token: str | None) -> None:
    """依 JTI 撤銷目前的 access token（以及選填的 refresh token）。

    黑名單項目會在 token 原本的到期時間自動過期，不佔長期儲存。
    """
    # 在呼叫時才從 app.infrastructure.redis 取（測試會 monkeypatch 該模組）
    from app.infrastructure.redis import get_redis, revoke_jti

    targets: list[TokenPayload] = []
    if (access := _decode_token_ignoring_expiry(access_token)) is not None:
        targets.append(access)
    if refresh_token and (refresh := _decode_token_ignoring_expiry(refresh_token)):
        targets.append(refresh)

    redis = await get_redis()
    for data in targets:
        if data.jti and data.exp:
            await revoke_jti(redis, data.jti, data.exp)


async def refresh_access_token(*, session: Session, refresh_token: str) -> Token:
    """Validate a refresh token and return a new access + refresh token pair."""
    # 在呼叫時才從 app.infrastructure.redis 取（測試會 monkeypatch 該模組）
    from app.infrastructure.redis import (
        get_redis,
        is_jti_revoked,
        mark_refresh_token_used,
    )

    # Refresh token failures must return 401 (not 400) so clients can treat
    # them uniformly as "session expired, please log in again".
    try:
        payload = jwt.decode(
            refresh_token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
    except (InvalidTokenError, ValidationError):
        raise AuthenticationError(t("auth.refreshTokenInvalid"))

    if token_data.type != "refresh":
        raise AuthenticationError(t("auth.tokenTypeInvalid"))

    # Logout revokes the refresh token's jti — honour that here, otherwise a
    # logged-out refresh token could still mint new token pairs.
    if token_data.jti:
        redis = await get_redis()
        if await is_jti_revoked(redis, token_data.jti):
            raise AuthenticationError(t("auth.tokenRevoked"))

    # 同步 DB 查詢丟到 worker thread，不佔住 event loop（同 deps.get_current_user）
    user = await run_in_threadpool(session.get, User, token_data.sub)
    if not user:
        raise AuthenticationError(t("auth.refreshTokenInvalid"))
    if not user.is_active:
        raise AuthenticationError(t("auth.inactiveUser"))
    if user.token_version != token_data.ver:
        raise AuthenticationError(t("auth.tokenRevoked"))

    # Refresh-token rotation: a refresh token may only be exchanged once
    # (plus a short grace window for concurrent tabs). Without this a leaked
    # refresh token stays usable for its full lifetime even after the
    # legitimate client has already rotated past it.
    if token_data.jti and token_data.exp:
        redis = await get_redis()
        if not await mark_refresh_token_used(
            redis, token_data.jti, token_data.exp
        ):
            raise AuthenticationError(t("auth.tokenRevoked"))

    return create_token_pair(user)


def recover_password(*, session: Session, email: str) -> None:
    user = user_repo.get_user_by_email(session=session, email=email)
    # LDAP 帳號的密碼歸目錄管：寄出重設信只會讓使用者設出一個永遠登不進來的
    # 本地密碼。一律不寄，但回應與稽核維持相同形狀，避免變成帳號枚舉管道。
    is_ldap = bool(user and user.auth_source == "ldap")
    audit_service.log_action(
        session=session,
        user_id=user.id if user else None,
        action=AuditAction.password_recovery_request,
        details=f"Password recovery requested for {email}"
        + (
            " (LDAP-managed account; no email sent)"
            if is_ldap
            else ("" if user else " (no matching account)")
        ),
    )
    if user and not is_ldap:
        token = generate_password_reset_token(
            email=email, token_version=user.token_version
        )
        email_data = generate_reset_password_email(
            email_to=user.email, email=email, token=token
        )
        send_email(
            email_to=user.email,
            subject=email_data.subject,
            html_content=email_data.html_content,
        )


def reset_password(*, session: Session, token: str, new_password: str) -> None:
    decoded = decode_password_reset_token(token=token)
    if not decoded:
        raise BadRequestError(t("auth.tokenInvalid"))
    email, token_version = decoded
    user = user_repo.get_user_by_email(session=session, email=email)
    if not user:
        raise BadRequestError(t("auth.tokenInvalid"))
    if not user.is_active:
        raise BadRequestError(t("auth.inactiveUser"))
    # LDAP 帳號不得用重設連結設本地密碼（正常流程不會寄出，但管理用的
    # 預覽端點仍能產生 token，這裡是最後防線）。
    if user.auth_source == "ldap":
        raise BadRequestError(t("user.ldapPasswordLocked"))
    # 重設連結綁定簽發當下的 token_version；成功重設會 +1（由
    # user_repo.update_user 負責），所以同一封信裡的連結只能用一次，
    # 之後（即使仍在 48 小時內）一律失效。
    if token_version != user.token_version:
        raise BadRequestError(t("auth.tokenInvalid"))
    user_repo.update_user(
        session=session, db_user=user, user_in=UserUpdate(password=new_password)
    )
    session.add(user)
    audit_service.log_action(
        session=session,
        user_id=user.id,
        action=AuditAction.password_reset,
        details=f"Password reset completed for {user.email}",
        commit=False,
    )
    session.commit()

