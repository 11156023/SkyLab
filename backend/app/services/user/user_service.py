import uuid

from sqlmodel import Session, func, select

from app.core.authorizers import can_manage_users, require_user_manage
from app.core.config import settings
from app.core.security import get_password_hash, verify_password
from app.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.models import AuditLog, SpecChangeRequest, User, VMRequest
from app.repositories import resource as resource_repo
from app.repositories import user as user_repo
from app.repositories import vm_migration_job as vm_migration_job_repo
from app.schemas import (
    UserCreate,
    UserRegister,
    UsersPublic,
    UserUpdate,
    UserUpdateMe,
)
from app.services.user import audit_service
from app.utils import generate_new_account_email, send_email


def list_users(*, session: Session, skip: int = 0, limit: int = 100) -> UsersPublic:
    count = session.exec(select(func.count()).select_from(User)).one()
    users = session.exec(
        select(User).order_by(User.created_at.desc()).offset(skip).limit(limit)
    ).all()
    return UsersPublic(data=users, count=count)


def _commit_and_refresh(session: Session, user: User) -> User:
    session.commit()
    session.refresh(user)
    return user


def _prepare_user_delete(*, session: Session, user: User) -> None:
    if resource_repo.get_resources_by_user(session=session, user_id=user.id):
        raise BadRequestError(
            "Cannot delete a user who still owns provisioned resources"
        )

    vm_requests = session.exec(
        select(VMRequest).where(VMRequest.user_id == user.id)
    ).all()
    for request in vm_requests:
        vm_migration_job_repo.delete_jobs_for_request(
            session=session,
            request_id=request.id,
            commit=False,
        )
        session.delete(request)

    spec_change_requests = session.exec(
        select(SpecChangeRequest).where(SpecChangeRequest.user_id == user.id)
    ).all()
    for request in spec_change_requests:
        session.delete(request)

    reviewed_vm_requests = session.exec(
        select(VMRequest).where(VMRequest.reviewer_id == user.id)
    ).all()
    for request in reviewed_vm_requests:
        request.reviewer_id = None
        session.add(request)

    reviewed_spec_requests = session.exec(
        select(SpecChangeRequest).where(SpecChangeRequest.reviewer_id == user.id)
    ).all()
    for request in reviewed_spec_requests:
        request.reviewer_id = None
        session.add(request)

    audit_logs = session.exec(
        select(AuditLog).where(AuditLog.user_id == user.id)
    ).all()
    for log in audit_logs:
        log.user_id = None
        session.add(log)


def create_user(
    *, session: Session, user_in: UserCreate, current_user_id: uuid.UUID
) -> User:
    existing = user_repo.get_user_by_email(session=session, email=user_in.email)
    if existing:
        raise ConflictError("The user with this email already exists in the system.")

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
        raise BadRequestError("User registration is currently disabled")

    existing = user_repo.get_user_by_email(session=session, email=user_in.email)
    if existing:
        raise ConflictError("The user with this email already exists in the system")
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
    if not user:
        raise NotFoundError("User not found")
    return user


def update_user(
    *,
    session: Session,
    user_id: uuid.UUID,
    user_in: UserUpdate,
    current_user_id: uuid.UUID,
) -> User:
    db_user = session.get(User, user_id)
    if not db_user:
        raise NotFoundError("The user with this id does not exist in the system")
    if user_in.email:
        existing = user_repo.get_user_by_email(session=session, email=user_in.email)
        if existing and existing.id != user_id:
            raise ConflictError("User with this email already exists")

    db_user = user_repo.update_user(session=session, db_user=db_user, user_in=user_in)

    changes = ", ".join(
        f"{k}={v}" for k, v in user_in.model_dump(exclude_unset=True).items()
    )
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
    user = session.get(User, user_id)
    if not user:
        raise NotFoundError("User not found")
    if user == current_user:
        raise PermissionDeniedError(
            "Super users are not allowed to delete themselves"
        )

    try:
        _prepare_user_delete(session=session, user=user)
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            action="user_delete",
            details=f"Deleted user: {user.email}",
            commit=False,
        )
        session.delete(user)
        session.commit()
    except Exception:
        session.rollback()
        raise


def update_me(*, session: Session, user_in: UserUpdateMe, current_user: User) -> User:
    if user_in.email:
        existing = user_repo.get_user_by_email(session=session, email=user_in.email)
        if existing and existing.id != current_user.id:
            raise ConflictError("User with this email already exists")

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


def update_password(
    *, session: Session, current_password: str, new_password: str, current_user: User
) -> None:
    verified, _ = verify_password(current_password, current_user.hashed_password)
    if not verified:
        raise BadRequestError("Incorrect password")
    if current_password == new_password:
        raise BadRequestError(
            "New password cannot be the same as the current one"
        )
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
        raise PermissionDeniedError(
            "Super users are not allowed to delete themselves"
        )

    try:
        _prepare_user_delete(session=session, user=current_user)
        audit_service.log_action(
            session=session,
            user_id=None,
            action="user_delete",
            details=f"Deleted own account: {current_user.email}",
            commit=False,
        )
        session.delete(current_user)
        session.commit()
    except Exception:
        session.rollback()
        raise
