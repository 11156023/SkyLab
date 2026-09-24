"""兩步驟驗證（TOTP）業務邏輯：綁定／確認／停用／管理員重設，以及登入第二階段。

登入流程：
1. 密碼／Google／LDAP 任一種第一階段通過後，若 ``user.totp_enabled`` 為 True，
   由 ``issue_challenge`` 發一張 5 分鐘的挑戰 token（JWT ``type="totp"``），
   **不**發正式 access/refresh token。
2. 前端拿 Authenticator 的 6 位數驗證碼呼叫 ``POST /login/totp``，
   ``complete_login`` 驗證挑戰 token 與驗證碼後才發正式 token 對。

金鑰用 Fernet 加密存 DB（與 login_password_encrypted 同一把 key），
驗證碼成功後記下 time step，30 秒內同一組驗證碼不可重放。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError
from sqlmodel import Session

from app.core import security
from app.core.config import settings
from app.core.i18n import t
from app.exceptions import AuthenticationError, BadRequestError, NotFoundError
from app.models import AuditAction, User
from app.schemas import Token, TokenPayload, TotpChallenge, TotpSetupPublic
from app.services.user import audit_service
from app.utils import totp as totp_util

# 第一階段通過後給使用者輸入驗證碼的時間；過期就得重新登入。
TOTP_CHALLENGE_EXPIRE_MINUTES = 5

# 挑戰 token 記下的第一階段方式 → 完成後要寫的稽核動作
_SUCCESS_ACTIONS: dict[str, tuple[AuditAction, str]] = {
    "password": (AuditAction.login_success, "password"),
    "google": (AuditAction.login_google_success, "Google"),
    "ldap": (AuditAction.login_ldap_success, "LDAP"),
}


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


def _decrypt_secret(user: User) -> str | None:
    if not user.totp_secret_encrypted:
        return None
    return security.decrypt_value(user.totp_secret_encrypted)


# ---------------------------------------------------------------------------
# 登入第二階段
# ---------------------------------------------------------------------------


def issue_challenge(user: User, *, method: str) -> TotpChallenge:
    """第一階段通過、帳號已綁定 TOTP：發挑戰 token（不是正式 token）。

    ``ver`` 綁 token_version，改密碼／管理員重設後舊挑戰 token 立即失效。
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=TOTP_CHALLENGE_EXPIRE_MINUTES
    )
    payload = {
        "exp": expire,
        "sub": str(user.id),
        "type": "totp",
        "ver": user.token_version,
        "jti": uuid4().hex,
        "method": method,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=security.ALGORITHM)
    return TotpChallenge(totp_token=token)


def complete_login(*, session: Session, totp_token: str, code: str) -> Token:
    """驗證挑戰 token 與驗證碼，成功才發正式 token 對。"""
    try:
        payload = jwt.decode(
            totp_token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        data = TokenPayload(**payload)
    except (InvalidTokenError, ValidationError):
        raise AuthenticationError(t("auth.totpChallengeInvalid"))
    if data.type != "totp" or not data.sub:
        raise AuthenticationError(t("auth.totpChallengeInvalid"))

    try:
        user_id = uuid.UUID(data.sub)
    except ValueError:
        raise AuthenticationError(t("auth.totpChallengeInvalid"))
    user = session.get(User, user_id)
    if not user or user.token_version != data.ver:
        raise AuthenticationError(t("auth.totpChallengeInvalid"))
    if not user.is_active:
        raise BadRequestError(t("auth.inactiveUser"))
    # 挑戰 token 發出後被停用／重設 2FA：要求重新走第一階段，避免用舊 token 繞過
    if not user.totp_enabled:
        raise AuthenticationError(t("auth.totpChallengeInvalid"))

    method = data.method if data.method in _SUCCESS_ACTIONS else "password"
    if not _consume_code(session=session, user=user, code=code):
        audit_service.log_action(
            session=session,
            user_id=user.id,
            action=AuditAction.login_totp_failed,
            details=f"Invalid two-factor code for {user.email} ({method} login)",
        )
        raise BadRequestError(t("auth.totpCodeInvalid"))

    action, label = _SUCCESS_ACTIONS[method]
    audit_service.log_action(
        session=session,
        user_id=user.id,
        action=action,
        details=f"User {user.email} logged in via {label} + two-factor code",
    )
    return _create_token_pair(user)


def _consume_code(*, session: Session, user: User, code: str) -> bool:
    """驗證並「消耗」一組驗證碼：成功即記錄 step，防止重放。"""
    secret = _decrypt_secret(user)
    if not secret:
        return False
    matched = totp_util.verify_totp(
        secret, code, last_used_step=user.totp_last_used_step
    )
    if matched is None:
        return False
    user.totp_last_used_step = matched
    session.add(user)
    session.commit()
    return True


# ---------------------------------------------------------------------------
# 使用者自助：綁定 / 確認 / 停用
# ---------------------------------------------------------------------------


def begin_setup(*, session: Session, user: User) -> TotpSetupPublic:
    """產生新金鑰並存成「待確認」；回傳金鑰與 otpauth URI 供前端畫 QR code。

    已啟用時不可重新綁定（要先停用），否則有人拿到已登入的分頁就能換掉金鑰。
    重複呼叫（還沒確認）會換一把新金鑰，舊 QR code 作廢。
    """
    if user.totp_enabled:
        raise BadRequestError(t("auth.totpAlreadyEnabled"))
    secret = totp_util.generate_secret()
    user.totp_secret_encrypted = security.encrypt_value(secret)
    user.totp_last_used_step = None
    session.add(user)
    session.commit()
    issuer = settings.PROJECT_NAME or "SkyLab"
    return TotpSetupPublic(
        secret=secret,
        otpauth_uri=totp_util.build_otpauth_uri(
            secret, account=user.email, issuer=issuer
        ),
        issuer=issuer,
        account=user.email,
    )


def confirm_setup(*, session: Session, user: User, code: str) -> None:
    """用 App 產生的第一組驗證碼確認金鑰輸入正確，才正式啟用。"""
    if user.totp_enabled:
        raise BadRequestError(t("auth.totpAlreadyEnabled"))
    if not user.totp_secret_encrypted:
        raise BadRequestError(t("auth.totpSetupNotStarted"))
    if not _consume_code(session=session, user=user, code=code):
        raise BadRequestError(t("auth.totpCodeInvalid"))
    user.totp_enabled = True
    session.add(user)
    audit_service.log_action(
        session=session,
        user_id=user.id,
        action=AuditAction.totp_enable,
        details=f"User {user.email} enabled two-factor authentication",
        commit=False,
    )
    session.commit()


def disable(*, session: Session, user: User, code: str) -> None:
    """停用需要一組目前有效的驗證碼（LDAP／Google 帳號沒有可用的本地密碼，
    所以不用密碼當第二道確認）。手機遺失請管理員重設。"""
    if not user.totp_enabled:
        raise BadRequestError(t("auth.totpNotEnabled"))
    # 管理員要求此帳號啟用 2FA 時不可自行停用（只能由管理員重設）
    if user.totp_required:
        raise BadRequestError(t("auth.totpEnforced"))
    if not _consume_code(session=session, user=user, code=code):
        raise BadRequestError(t("auth.totpCodeInvalid"))
    _clear(user)
    session.add(user)
    audit_service.log_action(
        session=session,
        user_id=user.id,
        action=AuditAction.totp_disable,
        details=f"User {user.email} disabled two-factor authentication",
        commit=False,
    )
    session.commit()


def admin_reset(*, session: Session, user_id: uuid.UUID, actor: User) -> None:
    """管理員替使用者解除兩步驟驗證（手機遺失等救援用途）。

    同時把 token_version +1：讓對方已發出的挑戰 token 與既有登入全部失效，
    重設後必須重新登入。
    """
    target = session.get(User, user_id)
    if not target:
        raise NotFoundError(t("user.notFound"))
    if not target.totp_enabled and not target.totp_secret_encrypted:
        raise BadRequestError(t("auth.totpNotEnabled"))
    _clear(target)
    target.token_version += 1
    session.add(target)
    audit_service.log_action(
        session=session,
        user_id=actor.id,
        action=AuditAction.totp_admin_reset,
        details=(
            f"Admin {actor.email} reset two-factor authentication for {target.email}"
        ),
        commit=False,
    )
    session.commit()


def _clear(user: User) -> None:
    user.totp_enabled = False
    user.totp_secret_encrypted = None
    user.totp_last_used_step = None


__all__ = [
    "TOTP_CHALLENGE_EXPIRE_MINUTES",
    "admin_reset",
    "begin_setup",
    "complete_login",
    "confirm_setup",
    "disable",
    "issue_challenge",
]
