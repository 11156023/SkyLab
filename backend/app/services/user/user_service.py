import uuid

from sqlmodel import Session, col, func, select, update

from app.core.authorizers import can_manage_users, require_user_manage
from app.core.config import settings
from app.core.i18n import t
from app.core.security import get_password_hash, verify_password
from app.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.models import (
    AIAPICredential,
    TeachingClass,
    User,
    UserRole,
    get_datetime_utc,
)
from app.repositories import resource as resource_repo
from app.repositories import user as user_repo
from app.schemas import (
    UserCreate,
    UserRegister,
    UsersPublic,
    UserUpdate,
    UserUpdateMe,
)
from app.services.user import audit_service
from app.services.user.password_policy import ensure_password_complexity
from app.utils import generate_new_account_email, send_email


def list_users(*, session: Session, skip: int = 0, limit: int = 100) -> UsersPublic:
    count = session.exec(
        select(func.count()).select_from(User).where(col(User.deleted_at).is_(None))
    ).one()
    users = session.exec(
        select(User)
        .where(col(User.deleted_at).is_(None))
        .order_by(User.created_at.desc())
        .offset(skip)
        .limit(limit)
    ).all()
    return UsersPublic(data=users, count=count)


def _commit_and_refresh(session: Session, user: User) -> User:
    session.commit()
    session.refresh(user)
    return user


def _prepare_user_delete(*, session: Session, user: User) -> None:
    if resource_repo.get_resources_by_user(session=session, user_id=user.id):
        raise BadRequestError(t("user.deleteHasResources"))
    teaching_class = session.exec(
        select(TeachingClass).where(TeachingClass.owner_id == user.id).limit(1)
    ).first()
    if teaching_class is not None:
        raise ConflictError(t("user.deleteTeacherHasClasses"))

    # 保留 user／credential 主鍵與所有紀錄，供已接受的 AI 呼叫收尾入帳。
    deleted_at = get_datetime_utc()
    user.deleted_at = deleted_at
    user.is_active = False
    user.token_version += 1
    session.add(user)
    session.exec(
        update(AIAPICredential)
        .where(col(AIAPICredential.user_id) == user.id)
        .where(col(AIAPICredential.revoked_at).is_(None))
        .values(revoked_at=deleted_at)
    )


def create_user(
    *, session: Session, user_in: UserCreate, current_user_id: uuid.UUID
) -> User:
    ensure_password_complexity(user_in.password)
    existing = user_repo.get_user_by_email(session=session, email=user_in.email)
    if existing:
        raise ConflictError(t("user.emailExists"))

    user = user_repo.create_user(session=session, user_create=user_in)

    try:
        audit_service.log_action(
            session=session,
            user_id=current_user_id,
            action="user_create",
            details=f"Created user: {user_in.email}, role: {user.role.value}",
            commit=False,
        )
        user = _commit_and_refresh(session, user)
    except Exception:
        session.rollback()
        raise

    if settings.emails_enabled and user_in.email:
        email_data = generate_new_account_email(
            email_to=user_in.email, username=user_in.email, password=user_in.password
        )
        send_email(
            email_to=user_in.email,
            subject=email_data.subject,
            html_content=email_data.html_content,
        )
    return user


def register_user(*, session: Session, user_in: UserRegister) -> User:
    if not settings.ENABLE_SIGNUP:
        raise BadRequestError(t("user.registrationDisabled"))
    ensure_password_complexity(user_in.password)

    existing = user_repo.get_user_by_email(session=session, email=user_in.email)
    if existing:
        raise ConflictError(t("user.emailExists"))
    user_create = UserCreate.model_validate(user_in.model_dump())
    user = user_repo.create_user(session=session, user_create=user_create)
    return _commit_and_refresh(session, user)


def get_user_by_id(
    *, session: Session, user_id: uuid.UUID, current_user: User
) -> User:
    user = session.get(User, user_id)
    if user == current_user:
        return user
    require_user_manage(current_user)
    if not user or user.deleted_at is not None:
        raise NotFoundError(t("user.notFound"))
    return user


def _ensure_not_removing_last_admin(
    *, session: Session, db_user: User, deactivating: bool, role_changed: bool
) -> None:
    """拒絕拿掉「最後一位啟用中的管理員」的管理權限，避免平台失去管理者。"""
    if not (db_user.role == UserRole.admin and db_user.is_active):
        return
    if not (deactivating or role_changed):
        return
    other_active_admins = session.exec(
        select(func.count())
        .select_from(User)
        .where(
            User.role == UserRole.admin,
            User.is_active == True,  # noqa: E712
            User.id != db_user.id,
        )
    ).one()
    if not other_active_admins:
        raise BadRequestError(t("user.lastAdminLocked"))


def update_user(
    *,
    session: Session,
    user_id: uuid.UUID,
    user_in: UserUpdate,
    current_user_id: uuid.UUID,
) -> User:
    db_user = session.get(
        User, user_id, populate_existing=True, with_for_update={"key_share": True}
    )
    if not db_user or db_user.deleted_at is not None:
        raise NotFoundError(t("user.idNotFound"))
    # LDAP 帳號的密碼歸目錄管：設本地密碼登不進去，只會造成困惑（稽核 #9）
    if user_in.password and db_user.auth_source == "ldap":
        raise BadRequestError(t("user.ldapPasswordLocked"))
    if user_in.password:
        ensure_password_complexity(user_in.password)
    role_changed = user_in.role is not None and user_in.role != db_user.role
    # 管理員不可變更自己的角色或停用自己（與 delete_user 的 selfDeleteForbidden 對稱），
    # 否則一個按鍵就能把自己鎖在管理介面外。
    if db_user.id == current_user_id and (user_in.is_active is False or role_changed):
        raise PermissionDeniedError(t("user.selfEditLocked"))
    _ensure_not_removing_last_admin(
        session=session,
        db_user=db_user,
        deactivating=user_in.is_active is False,
        role_changed=role_changed,
    )
    if user_in.email:
        existing = user_repo.get_user_by_email(session=session, email=user_in.email)
        if existing and existing.id != user_id:
            raise ConflictError(t("user.emailExistsShort"))

    db_user = user_repo.update_user(session=session, db_user=db_user, user_in=user_in)

    # 稽核紀錄絕不可寫入明文密碼；只記錄「密碼已變更」
    changed_fields = user_in.model_dump(exclude_unset=True)
    if "password" in changed_fields:
        changed_fields["password"] = "<changed>"
    changes = ", ".join(f"{k}={v}" for k, v in changed_fields.items())
    try:
        audit_service.log_action(
            session=session,
            user_id=current_user_id,
            action="user_update",
            details=f"Updated user {db_user.email}: {changes}",
            commit=False,
        )
        db_user = _commit_and_refresh(session, db_user)
    except Exception:
        session.rollback()
        raise
    return db_user


def delete_user(*, session: Session, user_id: uuid.UUID, current_user: User) -> None:
    # NO KEY UPDATE 與更新／核發金鑰互斥，但不擋住晚到 usage 的外鍵檢查。
    user = session.get(
        User, user_id, populate_existing=True, with_for_update={"key_share": True}
    )
    if not user:
        raise NotFoundError(t("user.notFound"))
    if user.id == current_user.id:
        raise PermissionDeniedError(t("user.selfDeleteForbidden"))

    if user.deleted_at is not None:
        return

    try:
        _prepare_user_delete(session=session, user=user)
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            action="user_delete",
            details=f"Deleted user (records retained): {user.email}",
            commit=False,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise


def update_me(*, session: Session, user_in: UserUpdateMe, current_user: User) -> User:
    if user_in.email:
        existing = user_repo.get_user_by_email(session=session, email=user_in.email)
        if existing and existing.id != current_user.id:
            raise ConflictError(t("user.emailExistsShort"))

    user_data = user_in.model_dump(exclude_unset=True)
    current_user.sqlmodel_update(user_data)
    session.add(current_user)

    changes = ", ".join(f"{k}={v}" for k, v in user_data.items())
    try:
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            action="user_update",
            details=f"Updated own profile: {changes}",
            commit=False,
        )
        current_user = _commit_and_refresh(session, current_user)
    except Exception:
        session.rollback()
        raise
    return current_user


def complete_onboarding(*, session: Session, current_user: User) -> User:
    """標記首次登入引導精靈已完成（略過也算完成）；重複呼叫無副作用。"""
    if current_user.onboarding_completed:
        return current_user
    current_user.onboarding_completed = True
    session.add(current_user)
    return _commit_and_refresh(session, current_user)


def update_password(
    *, session: Session, current_password: str, new_password: str, current_user: User
) -> None:
    verified, _ = verify_password(current_password, current_user.hashed_password)
    if not verified:
        raise BadRequestError(t("user.incorrectPassword"))
    if current_password == new_password:
        raise BadRequestError(t("user.samePassword"))
    ensure_password_complexity(new_password)
    current_user.hashed_password = get_password_hash(new_password)
    current_user.token_version += 1  # Invalidate all existing tokens
    session.add(current_user)
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action="password_change",
        details=f"User {current_user.email} changed their password",
        commit=False,
    )
    session.commit()


def delete_me(*, session: Session, current_user: User) -> None:
    if can_manage_users(current_user):
        raise PermissionDeniedError(t("user.selfDeleteForbidden"))

    user = session.get(
        User, current_user.id, populate_existing=True, with_for_update={"key_share": True}
    )
    if user is None:
        raise NotFoundError(t("user.notFound"))
    if user.deleted_at is not None:
        return

    try:
        _prepare_user_delete(session=session, user=user)
        audit_service.log_action(
            session=session,
            user_id=user.id,
            action="user_delete",
            details=f"Deleted own account (records retained): {user.email}",
            commit=False,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
