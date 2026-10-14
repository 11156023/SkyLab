"""app.services.user.totp_service 的挑戰 token 與狀態防呆分支補測（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.core import security
from app.core.config import settings
from app.exceptions import AuthenticationError, BadRequestError, NotFoundError
from app.models import User, UserRole
from app.services.user import totp_service as svc


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _user(db: Session, **overrides: Any) -> User:
    fields: dict[str, Any] = {
        "email": f"u-{uuid.uuid4().hex[:8]}@example.com",
        "hashed_password": "x",
    }
    fields.update(overrides)
    user = User(**fields)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _challenge(**claims: Any) -> str:
    claims.setdefault("exp", datetime.now(timezone.utc) + timedelta(minutes=5))
    claims.setdefault("type", "totp")
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=security.ALGORITHM)


@pytest.mark.parametrize(
    "token",
    [
        "garbage",
        _challenge(sub=str(uuid.uuid4()), type="access"),
        _challenge(sub="not-a-uuid"),
    ],
)
def test_complete_login_rejects_bad_challenges(db: Session, token: str) -> None:
    with pytest.raises(AuthenticationError):
        svc.complete_login(session=db, totp_token=token, code="123456")


def test_complete_login_rejects_inactive_user(db: Session) -> None:
    user = _user(db, is_active=False, totp_enabled=True)
    with pytest.raises(BadRequestError):
        svc.complete_login(
            session=db, totp_token=_challenge(sub=str(user.id)), code="123456"
        )


def test_complete_login_rejects_user_without_totp(db: Session) -> None:
    user = _user(db, totp_enabled=False)
    with pytest.raises(AuthenticationError):
        svc.complete_login(
            session=db, totp_token=_challenge(sub=str(user.id)), code="123456"
        )


def test_complete_login_fails_without_stored_secret(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db, totp_enabled=True, totp_secret_encrypted=None)
    failures: list[dict[str, Any]] = []
    monkeypatch.setattr(
        svc.audit_service, "log_action", lambda **kw: failures.append(kw)
    )
    with pytest.raises(BadRequestError):
        svc.complete_login(
            session=db,
            totp_token=_challenge(sub=str(user.id), method="ldap"),
            code="123456",
        )
    assert "ldap login" in failures[0]["details"]


def test_confirm_setup_guards(db: Session) -> None:
    enabled = _user(db, totp_enabled=True)
    with pytest.raises(BadRequestError):
        svc.confirm_setup(session=db, user=enabled, code="123456")
    not_started = _user(db)
    with pytest.raises(BadRequestError):
        svc.confirm_setup(session=db, user=not_started, code="123456")


def test_disable_requires_enabled(db: Session) -> None:
    user = _user(db)
    with pytest.raises(BadRequestError):
        svc.disable(session=db, user=user, code="123456")


def test_admin_reset_unknown_user(db: Session) -> None:
    actor = _user(db, role=UserRole.admin)
    with pytest.raises(NotFoundError):
        svc.admin_reset(session=db, user_id=uuid.uuid4(), actor=actor)
