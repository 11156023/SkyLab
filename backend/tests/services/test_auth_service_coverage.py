"""app.services.user.auth_service 的錯誤分支補測（假 Google／Redis、記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from typing import Any

import jwt
import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine

import app.infrastructure.redis as redis_module
from app.core import security
from app.core.config import settings
from app.exceptions import AuthenticationError, BadRequestError
from app.infrastructure.google.tokeninfo import (
    GoogleTokenInfoNetworkError,
    GoogleTokenInfoRejected,
)
from app.models import User
from app.services.user import auth_service as svc


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


# ---------------------------------------------------------------------------
# Google 帳號建立與登入
# ---------------------------------------------------------------------------


def test_create_google_user_returns_existing_on_race(db: Session) -> None:
    existing = _user(db, email="race@example.com")
    user = svc._create_google_user(session=db, email="race@example.com", data={})
    assert user.id == existing.id


def test_create_google_user_reraises_when_conflict_is_not_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingSession:
        def add(self, _obj: Any) -> None:
            pass

        def flush(self) -> None:
            raise IntegrityError("INSERT", {}, Exception("other constraint"))

        def rollback(self) -> None:
            pass

    monkeypatch.setattr(svc.user_repo, "get_user_by_email", lambda **_k: None)
    with pytest.raises(IntegrityError):
        svc._create_google_user(
            session=FailingSession(), email="x@example.com", data={}
        )


def test_is_email_verified_rejects_other_types() -> None:
    assert svc._is_email_verified({"email_verified": 1}) is False
    assert svc._is_email_verified({}) is False


def test_complete_google_login_with_totp_issues_challenge(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db, totp_enabled=True)
    monkeypatch.setattr(
        svc.totp_service,
        "issue_challenge",
        lambda u, *, method: ("challenge", u.id, method),
    )
    assert svc._complete_google_login(db, user.email, {}) == (
        "challenge",
        user.id,
        "google",
    )


@pytest.fixture()
def google_failures(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    reasons: list[str] = []
    monkeypatch.setattr(
        svc,
        "_log_google_login_failure",
        lambda _session, reason, *_a: reasons.append(reason),
    )
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "client-1")
    return reasons


async def test_google_login_not_configured(
    monkeypatch: pytest.MonkeyPatch, google_failures: list[str]
) -> None:
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", None)
    with pytest.raises(BadRequestError):
        await svc.google_login(session=None, id_token="t")
    assert google_failures == ["google login not configured"]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (GoogleTokenInfoNetworkError("timeout"), "network error"),
        (GoogleTokenInfoRejected(400), "invalid token"),
    ],
)
async def test_google_login_tokeninfo_failures(
    monkeypatch: pytest.MonkeyPatch,
    google_failures: list[str],
    error: Exception,
    reason: str,
) -> None:
    async def fetch(_token: str) -> dict[str, Any]:
        raise error

    monkeypatch.setattr(svc, "fetch_id_token_info", fetch)
    with pytest.raises(BadRequestError):
        await svc.google_login(session=None, id_token="t")
    assert google_failures == [reason]


async def test_google_login_requires_email(
    monkeypatch: pytest.MonkeyPatch, google_failures: list[str]
) -> None:
    async def fetch(_token: str) -> dict[str, Any]:
        return {"aud": "client-1", "email_verified": "true", "email": ""}

    monkeypatch.setattr(svc, "fetch_id_token_info", fetch)
    with pytest.raises(BadRequestError):
        await svc.google_login(session=None, id_token="t")
    assert google_failures == ["missing email"]


# ---------------------------------------------------------------------------
# refresh token
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    async def get_redis() -> None:
        return None

    async def is_jti_revoked(_redis: Any, _jti: str) -> bool:
        return False

    monkeypatch.setattr(redis_module, "get_redis", get_redis)
    monkeypatch.setattr(redis_module, "is_jti_revoked", is_jti_revoked)


def _refresh(**claims: Any) -> str:
    claims.setdefault("type", "refresh")
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=security.ALGORITHM)


async def test_refresh_rejects_garbage_token(db: Session) -> None:
    with pytest.raises(AuthenticationError):
        await svc.refresh_access_token(session=db, refresh_token="garbage")


class _StubSession:
    """session.get 只回固定的 user（PG 可用字串主鍵查詢，SQLite 不行）。"""

    def __init__(self, user: Any) -> None:
        self.user = user

    def get(self, _model: Any, _key: Any) -> Any:
        return self.user


async def test_refresh_rejects_unknown_user(fake_redis: None) -> None:
    with pytest.raises(AuthenticationError):
        await svc.refresh_access_token(
            session=_StubSession(None),
            refresh_token=_refresh(sub=str(uuid.uuid4()), jti="j1"),
        )


async def test_refresh_rejects_stale_token_version(fake_redis: None) -> None:
    user = User(email="stale@example.com", hashed_password="x", token_version=3)
    with pytest.raises(AuthenticationError):
        await svc.refresh_access_token(
            session=_StubSession(user), refresh_token=_refresh(sub=str(user.id), ver=2)
        )


# ---------------------------------------------------------------------------
# reset password
# ---------------------------------------------------------------------------


def test_reset_password_unknown_or_inactive_user(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    inactive = _user(db, is_active=False)
    decoded: list[Any] = [("missing@example.com", 0), (inactive.email, 0)]
    monkeypatch.setattr(
        svc, "decode_password_reset_token", lambda *, token: decoded.pop(0)
    )
    with pytest.raises(BadRequestError):
        svc.reset_password(session=db, token="t", new_password="Str0ng!Passw0rd")
    with pytest.raises(BadRequestError):
        svc.reset_password(session=db, token="t", new_password="Str0ng!Passw0rd")
    assert decoded == []
