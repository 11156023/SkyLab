from datetime import timedelta

import httpx
from sqlmodel import Session

from app.core import security
from app.core.config import settings
from app.core.i18n import t
from app.exceptions import AuthenticationError, BadRequestError, NotFoundError
from app.models import AuditAction
from app.repositories import user as user_repo
from app.schemas import Token, UserUpdate
from app.services.user import audit_service
from app.utils import (
    decode_password_reset_token,
    generate_password_reset_token,
    generate_reset_password_email,
    send_email,
)


def _create_token_pair(user) -> Token:
    """Create access + refresh token pair for a user."""
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


def login(*, session: Session, email: str, password: str) -> Token:
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
    audit_service.log_action(
        session=session,
        user_id=user.id,
        action=AuditAction.login_success,
        details=f"User {user.email} logged in via password",
    )
    return _create_token_pair(user)


async def google_login(*, session: Session, id_token: str) -> Token:
    def _fail(reason: str, email: str | None = None, user_id=None) -> None:
        audit_service.log_action(
            session=session,
            user_id=user_id,
            action=AuditAction.login_google_failed,
            details=f"Google login failed ({reason})"
            + (f" for {email}" if email else ""),
        )

    # aud 必須永遠驗證：未設定 GOOGLE_CLIENT_ID 時不得接受任何 Google ID token，
    # 否則使用者交給其他 OAuth 應用的 ID token 也能登入本系統。
    if not settings.GOOGLE_CLIENT_ID:
        _fail("google login not configured")
        raise BadRequestError("Google login is not configured")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": id_token},
            )
    except httpx.RequestError as exc:
        _fail("network error")
        raise BadRequestError(t("auth.googleTokenVerifyFailed")) from exc
    if r.status_code != 200:
        _fail("invalid token")
        raise BadRequestError(t("auth.googleTokenInvalid"))
    data = r.json()
    if data.get("aud") != settings.GOOGLE_CLIENT_ID:
        _fail("invalid audience")
        raise BadRequestError(t("auth.googleTokenAudienceInvalid"))
    email_verified_raw = data.get("email_verified")
    if isinstance(email_verified_raw, bool):
        email_verified = email_verified_raw
    elif isinstance(email_verified_raw, str):
        email_verified = email_verified_raw.lower() == "true"
    else:
        email_verified = False
    if not email_verified:
        _fail("email not verified", data.get("email"))
        raise BadRequestError(t("auth.googleEmailNotVerified"))
    email = data.get("email")
    if not email:
        _fail("missing email")
        raise BadRequestError(t("auth.googleEmailMissing"))
    user = user_repo.get_user_by_email(session=session, email=email)
    if not user:
        _fail("user not found", email)
        raise BadRequestError(t("auth.googleAccountNotRegistered"))
    if not user.is_active:
        _fail("inactive user", email, user.id)
        raise BadRequestError(t("auth.inactiveUser"))
    audit_service.log_action(
        session=session,
        user_id=user.id,
        action=AuditAction.login_google_success,
        details=f"User {user.email} logged in via Google",
    )
    return _create_token_pair(user)


async def refresh_access_token(*, session: Session, refresh_token: str) -> Token:
    """Validate a refresh token and return a new access + refresh token pair."""
    import jwt
    from jwt.exceptions import InvalidTokenError
    from pydantic import ValidationError

    from app.infrastructure.redis import (
        get_redis,
        is_jti_revoked,
        mark_refresh_token_used,
    )
    from app.models import User
    from app.schemas import TokenPayload

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

    user = session.get(User, token_data.sub)
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
            raise AuthenticationError("Token has been revoked")

    return _create_token_pair(user)


def recover_password(*, session: Session, email: str) -> None:
    user = user_repo.get_user_by_email(session=session, email=email)
    audit_service.log_action(
        session=session,
        user_id=user.id if user else None,
        action=AuditAction.password_recovery_request,
        details=f"Password recovery requested for {email}"
        + ("" if user else " (no matching account)"),
    )
    if user:
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
    # 重設連結綁定簽發當下的 token_version；成功重設會 +1，
    # 所以同一封信裡的連結只能用一次，之後（即使仍在 48 小時內）一律失效。
    if token_version != user.token_version:
        raise BadRequestError(t("auth.tokenInvalid"))
    user_repo.update_user(
        session=session, db_user=user, user_in=UserUpdate(password=new_password)
    )
    # Invalidate all existing tokens by incrementing version
    user.token_version += 1
    session.add(user)
    audit_service.log_action(
        session=session,
        user_id=user.id,
        action=AuditAction.password_reset,
        details=f"Password reset completed for {user.email}",
        commit=False,
    )
    session.commit()


def get_password_recovery_html(
    *, session: Session, email: str
) -> tuple[str, str]:
    """Returns (html_content, subject) for password recovery email."""
    user = user_repo.get_user_by_email(session=session, email=email)
    if not user:
        raise NotFoundError(t("auth.usernameNotFound"))
    token = generate_password_reset_token(
        email=email, token_version=user.token_version
    )
    email_data = generate_reset_password_email(
        email_to=user.email, email=email, token=token
    )
    return email_data.html_content, email_data.subject
