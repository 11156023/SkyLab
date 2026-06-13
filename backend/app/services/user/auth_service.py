from datetime import timedelta

import httpx
from sqlmodel import Session

from app.core import security
from app.core.config import settings
from app.exceptions import AuthenticationError, BadRequestError, NotFoundError
from app.models import AuditAction
from app.repositories import user as user_repo
from app.schemas import Token, UserUpdate
from app.services.user import audit_service
from app.utils import (
    generate_password_reset_token,
    generate_reset_password_email,
    send_email,
    verify_password_reset_token,
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
        raise BadRequestError("Incorrect email or password")
    if not user.is_active:
        audit_service.log_action(
            session=session,
            user_id=user.id,
            action=AuditAction.login_failed,
            details=f"Login blocked: inactive user {email}",
        )
        raise BadRequestError("Inactive user")
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

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": id_token},
            )
    except httpx.RequestError as exc:
        _fail("network error")
        raise BadRequestError("Unable to verify Google token") from exc
    if r.status_code != 200:
        _fail("invalid token")
        raise BadRequestError("Invalid Google token")
    data = r.json()
    if settings.GOOGLE_CLIENT_ID and data.get("aud") != settings.GOOGLE_CLIENT_ID:
        _fail("invalid audience")
        raise BadRequestError("Invalid Google token audience")
    email_verified_raw = data.get("email_verified")
    if isinstance(email_verified_raw, bool):
        email_verified = email_verified_raw
    elif isinstance(email_verified_raw, str):
        email_verified = email_verified_raw.lower() == "true"
    else:
        email_verified = False
    if not email_verified:
        _fail("email not verified", data.get("email"))
        raise BadRequestError("Google email not verified")
    email = data.get("email")
    if not email:
        _fail("missing email")
        raise BadRequestError("Could not retrieve email from Google token")
    user = user_repo.get_user_by_email(session=session, email=email)
    if not user:
        _fail("user not found", email)
        raise BadRequestError("Invalid Google token")
    if not user.is_active:
        _fail("inactive user", email, user.id)
        raise BadRequestError("Inactive user")
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

    from app.infrastructure.redis import get_redis, is_jti_revoked
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
        raise AuthenticationError("Invalid refresh token")

    if token_data.type != "refresh":
        raise AuthenticationError("Invalid token type")

    # Logout revokes the refresh token's jti — honour that here, otherwise a
    # logged-out refresh token could still mint new token pairs.
    if token_data.jti:
        redis = await get_redis()
        if await is_jti_revoked(redis, token_data.jti):
            raise AuthenticationError("Token has been revoked")

    user = session.get(User, token_data.sub)
    if not user:
        raise AuthenticationError("Invalid refresh token")
    if not user.is_active:
        raise AuthenticationError("Inactive user")
    if user.token_version != token_data.ver:
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
        token = generate_password_reset_token(email=email)
        email_data = generate_reset_password_email(
            email_to=user.email, email=email, token=token
        )
        send_email(
            email_to=user.email,
            subject=email_data.subject,
            html_content=email_data.html_content,
        )


def reset_password(*, session: Session, token: str, new_password: str) -> None:
    email = verify_password_reset_token(token=token)
    if not email:
        raise BadRequestError("Invalid token")
    user = user_repo.get_user_by_email(session=session, email=email)
    if not user:
        raise BadRequestError("Invalid token")
    if not user.is_active:
        raise BadRequestError("Inactive user")
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
        raise NotFoundError(
            "The user with this username does not exist in the system."
        )
    token = generate_password_reset_token(email=email)
    email_data = generate_reset_password_email(
        email_to=user.email, email=email, token=token
    )
    return email_data.html_content, email_data.subject
