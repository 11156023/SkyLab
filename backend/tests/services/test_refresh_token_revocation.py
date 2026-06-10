"""Regression tests for refresh-token handling in ``auth_service``.

Covers the fix where a logged-out (jti-revoked) refresh token could still
mint new token pairs, plus password-reset token type isolation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import jwt
import pytest

import app.infrastructure.redis as redis_module
from app.core import security
from app.core.config import settings
from app.exceptions import AuthenticationError
from app.services.user import auth_service
from app.utils.token import generate_password_reset_token, verify_password_reset_token

_USER_ID = uuid.uuid4()


def _make_refresh_token(*, ver: int = 0, jti: str | None = "refresh-jti") -> str:
    payload: dict[str, Any] = {
        "exp": datetime.now(timezone.utc) + timedelta(days=1),
        "sub": str(_USER_ID),
        "type": "refresh",
        "ver": ver,
    }
    if jti is not None:
        payload["jti"] = jti
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=security.ALGORITHM)


class _FakeSession:
    def __init__(self, user: Any) -> None:
        self._user = user

    def get(self, model: Any, key: Any) -> Any:  # noqa: ARG002
        return self._user


def _patch_redis(monkeypatch: pytest.MonkeyPatch, *, revoked: bool) -> None:
    async def fake_get_redis() -> None:
        return None

    async def fake_is_jti_revoked(redis: Any, jti: str) -> bool:  # noqa: ARG001
        return revoked

    # auth_service imports these lazily from app.infrastructure.redis
    monkeypatch.setattr(redis_module, "get_redis", fake_get_redis)
    monkeypatch.setattr(redis_module, "is_jti_revoked", fake_is_jti_revoked)


def _fake_user(*, ver: int = 0, active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(id=_USER_ID, token_version=ver, is_active=active)


async def test_revoked_refresh_token_cannot_mint_new_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_redis(monkeypatch, revoked=True)
    session = _FakeSession(_fake_user())

    with pytest.raises(AuthenticationError):
        await auth_service.refresh_access_token(
            session=session, refresh_token=_make_refresh_token()
        )


async def test_valid_refresh_token_returns_new_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_redis(monkeypatch, revoked=False)
    session = _FakeSession(_fake_user())

    token = await auth_service.refresh_access_token(
        session=session, refresh_token=_make_refresh_token()
    )

    access_payload = jwt.decode(
        token.access_token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
    )
    refresh_payload = jwt.decode(
        token.refresh_token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
    )
    assert access_payload["type"] == "access"
    assert refresh_payload["type"] == "refresh"
    assert access_payload["sub"] == str(_USER_ID)


async def test_access_token_rejected_as_refresh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_redis(monkeypatch, revoked=False)
    session = _FakeSession(_fake_user())
    access_token = security.create_access_token(
        _USER_ID, expires_delta=timedelta(minutes=5)
    )

    with pytest.raises(AuthenticationError):
        await auth_service.refresh_access_token(
            session=session, refresh_token=access_token
        )


def test_password_reset_token_round_trips() -> None:
    token = generate_password_reset_token(email="user@example.com")
    assert verify_password_reset_token(token=token) == "user@example.com"


def test_access_token_not_valid_as_password_reset_token() -> None:
    """An access token (same key, no ``type: reset``) must not pass reset
    verification — otherwise any logged-in token could reset passwords."""
    access_token = security.create_access_token(
        "user@example.com", expires_delta=timedelta(minutes=5)
    )
    assert verify_password_reset_token(token=access_token) is None
