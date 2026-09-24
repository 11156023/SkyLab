"""LDAP/AD 登入業務邏輯：目錄驗證 → 本地帳號對應/建立 → JWT。"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from sqlmodel import Session

from app.core import security
from app.core.config import settings
from app.core.i18n import t
from app.exceptions import AppError, AuthenticationError, BadRequestError
from app.infrastructure import ldap as ldap_client
from app.models import AuditAction, User, UserRole
from app.repositories import user as user_repo
from app.repositories.ldap_config import get_ldap_config
from app.schemas import Token, UserUpdate
from app.services.user import audit_service
from app.services.user.auth_service import create_token_pair

logger = logging.getLogger(__name__)


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


def _sync_role_from_directory(
    *, session: Session, user: User, config: Any, info: Any
) -> None:
    """既有 LDAP 帳號每次登入都依目錄群組重算角色。

    目錄端把老師移出群組後，本地角色若不跟著降回學生，權限就會永遠留著。
    只處理 ``auth_source == "ldap"`` 的帳號；``user_repo.update_user`` 會把
    ``is_superuser=True`` 的帳號拉回 admin，所以手動指定的超級使用者不會被
    目錄群組降級。
    """
    if user.auth_source != "ldap":
        return
    new_role = _role_from_groups(
        info.groups,
        teacher_group_dn=config.teacher_group_dn,
        admin_group_dn=config.admin_group_dn,
    )
    if new_role == user.role:
        return
    previous_role = user.role
    user_repo.update_user(
        session=session, db_user=user, user_in=UserUpdate(role=new_role)
    )
    session.commit()
    session.refresh(user)
    if user.role == previous_role:
        logger.info(
            "LDAP role sync kept %s as %s (superuser override)",
            user.email,
            previous_role.value,
        )
    else:
        logger.info(
            "LDAP role sync updated %s: %s -> %s",
            user.email,
            previous_role.value,
            user.role.value,
        )


def login_ldap(*, session: Session, username: str, password: str) -> Token:
    config = get_ldap_config(session=session)
    if not config.enabled:
        raise BadRequestError(t("ldapAuth.notEnabled"))

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
            raise BadRequestError(t("ldapAuth.accountNotRegistered"))
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
            auth_source="ldap",
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
    else:
        if user.auth_source != "ldap":
            # 標記欄位晚於帳號出現（或帳號先由管理員手動建立）：
            # 能用 LDAP 登入成功就代表密碼歸 LDAP 目錄管，自癒標記。
            user.auth_source = "ldap"
            session.add(user)
            session.commit()
            session.refresh(user)

    if not user.is_active:
        _fail(f"inactive user {info.email}")
        raise BadRequestError(t("auth.inactiveUser"))

    # 確定登入會成功才重算角色：被停用的帳號沒必要留下角色異動。
    _sync_role_from_directory(session=session, user=user, config=config, info=info)

    audit_service.log_action(
        session=session,
        user_id=user.id,
        action=AuditAction.login_ldap_success,
        details=f"User {user.email} logged in via LDAP ({info.dn})",
    )
    return create_token_pair(user)


def get_login_methods(*, session: Session) -> dict[str, bool]:
    """登入頁可用的認證方式（公開資訊）。"""
    config = get_ldap_config(session=session)
    return {
        "password": True,
        "google": bool(settings.GOOGLE_CLIENT_ID),
        "ldap": bool(config.enabled),
    }
