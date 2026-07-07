"""LDAP/AD 登入業務邏輯：目錄驗證 → 本地帳號對應/建立 → JWT。"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from sqlmodel import Session

from app.core import security
from app.core.config import settings
from app.exceptions import AppError, AuthenticationError, BadRequestError
from app.infrastructure import ldap as ldap_client
from app.models import AuditAction, User, UserRole
from app.repositories import user as user_repo
from app.repositories.ldap_config import get_ldap_config
from app.schemas import Token
from app.services.user import audit_service

logger = logging.getLogger(__name__)


def _create_token_pair(user: User) -> Token:
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


def _role_from_groups(
    groups: list[str],
    *,
    teacher_group_dn: str | None,
    admin_group_dn: str | None,
) -> UserRole:
    """LDAP 群組 → 角色（完整 DN 比對，不分大小寫）。預設 student。"""
    lowered = {g.casefold() for g in groups}
    if admin_group_dn and admin_group_dn.casefold() in lowered:
        return UserRole.admin
    if teacher_group_dn and teacher_group_dn.casefold() in lowered:
        return UserRole.teacher
    return UserRole.student


def login_ldap(*, session: Session, username: str, password: str) -> Token:
    config = get_ldap_config(session=session)
    if not config.enabled:
        raise BadRequestError("LDAP login is not enabled")

    def _fail(reason: str) -> None:
        audit_service.log_action(
            session=session,
            user_id=None,
            action=AuditAction.login_ldap_failed,
            details=f"LDAP login failed ({reason}) for username: {username}",
        )

    try:
        info = ldap_client.authenticate_user(config, username, password)
    except AuthenticationError:
        _fail("invalid credentials")
        raise
    except AppError:
        _fail("server error")
        raise

    user = user_repo.get_user_by_email(session=session, email=info.email)
    if user is None:
        if not config.auto_create_users:
            _fail(f"no local account for {info.email}")
            raise BadRequestError("Account is not registered")
        role = _role_from_groups(
            info.groups,
            teacher_group_dn=config.teacher_group_dn,
            admin_group_dn=config.admin_group_dn,
        )
        user = User(
            email=info.email,
            full_name=info.full_name,
            role=role,
            is_active=True,
            # LDAP 帳號不允許本地密碼登入 — 設不可猜的隨機雜湊。
            hashed_password=security.get_password_hash(
                secrets.token_urlsafe(32)
            ),
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        logger.info(
            "Auto-created LDAP user %s with role %s", info.email, role.value
        )

    if not user.is_active:
        _fail(f"inactive user {info.email}")
        raise BadRequestError("Inactive user")

    audit_service.log_action(
        session=session,
        user_id=user.id,
        action=AuditAction.login_ldap_success,
        details=f"User {user.email} logged in via LDAP ({info.dn})",
    )
    return _create_token_pair(user)


def get_login_methods(*, session: Session) -> dict[str, bool]:
    """登入頁可用的認證方式（公開資訊）。"""
    config = get_ldap_config(session=session)
    return {
        "password": True,
        "google": bool(settings.GOOGLE_CLIENT_ID),
        "ldap": bool(config.enabled),
    }
