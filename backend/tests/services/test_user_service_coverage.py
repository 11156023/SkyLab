"""app.services.user.user_service 的錯誤與回滾分支補測（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.core import security
from app.core.config import settings
from app.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models import TeachingClass, TeachingClassStatus, User, UserRole
from app.schemas import UserCreate, UserRegister, UserUpdate, UserUpdateMe
from app.services.user import user_service as svc

STRONG = "Str0ng!Passw0rd"


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def broken_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_k: Any) -> None:
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(svc.audit_service, "log_action", boom)


@pytest.fixture()
def emails(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "noreply@example.com")
    monkeypatch.setattr(svc, "send_email", lambda **kw: sent.append(kw))
    return sent


@pytest.fixture()
def no_emails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SMTP_HOST", None)


def _user(db: Session, **overrides: Any) -> User:
    fields: dict[str, Any] = {
        "email": f"u-{uuid.uuid4().hex[:8]}@example.com",
        "hashed_password": "x",
        "role": UserRole.student,
    }
    fields.update(overrides)
    user = User(**fields)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _token(**claims: Any) -> str:
    claims.setdefault("exp", datetime.now(timezone.utc) + timedelta(hours=1))
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=security.ALGORITHM)


# ---------------------------------------------------------------------------
# 註冊審核 / 註冊
# ---------------------------------------------------------------------------


def test_review_registration_loses_race(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = _user(db, role=UserRole.admin)
    pending = _user(db, registration_status="pending_review", is_active=False)
    monkeypatch.setattr(db, "execute", lambda *_a, **_k: SimpleNamespace(rowcount=0))
    with pytest.raises(BadRequestError):
        svc.review_registration(
            session=db, user_id=pending.id, actor=admin, approve=True
        )


def test_create_user_rolls_back_when_audit_fails(
    db: Session, broken_audit: None
) -> None:
    with pytest.raises(RuntimeError, match="audit write failed"):
        svc.create_user(
            session=db,
            user_in=UserCreate(email="new@example.com", password=STRONG),
            current_user_id=uuid.uuid4(),
        )


def test_register_user_requires_email_delivery(
    db: Session, monkeypatch: pytest.MonkeyPatch, no_emails: None
) -> None:
    monkeypatch.setattr(
        svc, "get_policy", lambda _s: SimpleNamespace(signup_enabled=True)
    )
    with pytest.raises(BadRequestError):
        svc.register_user(
            session=db,
            user_in=UserRegister(email="signup@example.com", password=STRONG),
        )


def test_confirm_signup_rejects_wrong_token_type(db: Session) -> None:
    token = _token(sub=str(uuid.uuid4()), type="email_change")
    with pytest.raises(BadRequestError):
        svc.confirm_signup(session=db, token=token, password=STRONG)


def test_confirm_signup_loses_race(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db, registration_status="pending_email", is_active=False)
    monkeypatch.setattr(
        svc, "get_policy", lambda _s: SimpleNamespace(approval_required=False)
    )
    monkeypatch.setattr(db, "execute", lambda *_a, **_k: SimpleNamespace(rowcount=0))
    token = _token(
        sub=str(user.id),
        type="signup_verify",
        email=user.email,
        ver=user.token_version,
    )
    with pytest.raises(BadRequestError):
        svc.confirm_signup(session=db, token=token, password=STRONG)


# ---------------------------------------------------------------------------
# update_user
# ---------------------------------------------------------------------------


def test_update_user_refuses_password_for_ldap_account(db: Session) -> None:
    target = _user(db, auth_source="ldap")
    with pytest.raises(BadRequestError):
        svc.update_user(
            session=db,
            user_id=target.id,
            user_in=UserUpdate(password=STRONG),
            current_user_id=uuid.uuid4(),
        )


def test_update_user_refuses_email_change_before_approval(db: Session) -> None:
    target = _user(db, registration_status="pending_review", is_active=False)
    with pytest.raises(BadRequestError):
        svc.update_user(
            session=db,
            user_id=target.id,
            user_in=UserUpdate(email="other@example.com"),
            current_user_id=uuid.uuid4(),
        )


def test_update_user_rolls_back_when_audit_fails(
    db: Session, broken_audit: None
) -> None:
    target = _user(db)
    with pytest.raises(RuntimeError):
        svc.update_user(
            session=db,
            user_id=target.id,
            user_in=UserUpdate(full_name="New Name"),
            current_user_id=uuid.uuid4(),
        )


def test_update_me_rolls_back_when_audit_fails(db: Session, broken_audit: None) -> None:
    me = _user(db)
    with pytest.raises(RuntimeError):
        svc.update_me(session=db, user_in=UserUpdateMe(full_name="Me"), current_user=me)


# ---------------------------------------------------------------------------
# email 變更
# ---------------------------------------------------------------------------


def test_request_email_change_guards(
    db: Session, monkeypatch: pytest.MonkeyPatch, no_emails: None
) -> None:
    ldap_user = _user(db, auth_source="ldap")
    with pytest.raises(BadRequestError):
        svc.request_email_change(
            session=db, current_user=ldap_user, email="x@example.com"
        )
    me = _user(db)
    with pytest.raises(BadRequestError):
        svc.request_email_change(session=db, current_user=me, email=me.email.upper())
    taken = _user(db)
    with pytest.raises(ConflictError):
        svc.request_email_change(session=db, current_user=me, email=taken.email)
    with pytest.raises(BadRequestError):
        svc.request_email_change(session=db, current_user=me, email="fresh@example.com")


def test_confirm_email_change_guards(db: Session, broken_audit: None) -> None:
    me = _user(db)
    with pytest.raises(BadRequestError):
        svc.confirm_email_change(session=db, current_user=me, token="not-a-jwt")

    taken = _user(db)
    conflict = _token(
        sub=str(me.id),
        type="email_change",
        old_email=me.email,
        new_email=taken.email,
        ver=me.token_version,
    )
    with pytest.raises(ConflictError):
        svc.confirm_email_change(session=db, current_user=me, token=conflict)

    ok = _token(
        sub=str(me.id),
        type="email_change",
        old_email=me.email,
        new_email="moved@example.com",
        ver=me.token_version,
    )
    with pytest.raises(RuntimeError):
        svc.confirm_email_change(session=db, current_user=me, token=ok)


def test_confirm_email_change_from_link_guards(db: Session) -> None:
    with pytest.raises(BadRequestError):
        svc.confirm_email_change_from_link(
            session=db, token=_token(sub=str(uuid.uuid4()), type="signup_verify")
        )
    with pytest.raises(BadRequestError):
        svc.confirm_email_change_from_link(session=db, token="garbage")
    inactive = _user(db, is_active=False)
    with pytest.raises(BadRequestError):
        svc.confirm_email_change_from_link(
            session=db, token=_token(sub=str(inactive.id), type="email_change")
        )


# ---------------------------------------------------------------------------
# 刪除
# ---------------------------------------------------------------------------


def test_delete_user_refuses_teacher_with_classes(db: Session) -> None:
    admin = _user(db, role=UserRole.admin)
    teacher = _user(db, role=UserRole.teacher)
    db.add(
        TeachingClass(
            owner_id=teacher.id,
            name="Linux",
            code="cls-1",
            term="115-1",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 31),
            weekday=1,
            start_time=time(13, 0),
            end_time=time(15, 0),
            status=TeachingClassStatus.active,
        )
    )
    db.commit()
    with pytest.raises(ConflictError):
        svc.delete_user(session=db, user_id=teacher.id, current_user=admin)


def test_delete_me_missing_or_already_deleted(db: Session) -> None:
    ghost = User(id=uuid.uuid4(), email="ghost@example.com", hashed_password="x")
    with pytest.raises(NotFoundError):
        svc.delete_me(session=db, current_user=ghost)

    gone = _user(db, is_active=False, deleted_at=datetime.now(timezone.utc))
    assert svc.delete_me(session=db, current_user=gone) is None


def test_delete_me_rolls_back_when_audit_fails(db: Session, broken_audit: None) -> None:
    me = _user(db)
    with pytest.raises(RuntimeError):
        svc.delete_me(session=db, current_user=me)
    db.expire_all()
    assert db.get(User, me.id).deleted_at is None
