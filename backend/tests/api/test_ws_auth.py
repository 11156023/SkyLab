"""Unit tests for WebSocket authentication (``get_ws_current_user``).

Regression coverage for:
- refresh / password-reset tokens must NOT open WebSocket connections
  (only ``type == "access"`` is accepted);
- jti-revoked (blacklisted) tokens are rejected;
- token_version mismatch is rejected and the DB session is closed;
- a valid access token returns (user, session).

No real DB / Redis: ``Session`` and the redis helpers are monkeypatched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from fastapi import WebSocketException

from app.api.deps import auth as auth_module
from app.core import security
from app.core.config import settings


def _make_token(
    *,
    token_type: str | None = "access",
    sub: str = "00000000-0000-0000-0000-000000000001",
    ver: int = 0,
    jti: str | None = "test-jti",
) -> str:
    payload: dict[str, Any] = {
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "sub": sub,
        "ver": ver,
    }
    if token_type is not None:
        payload["type"] = token_type
    if jti is not None:
        payload["jti"] = jti
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=security.ALGORITHM)


class _FakeSession:
    def __init__(self, user: Any) -> None:
        self._user = user
        self.closed = False

    def get(self, model: Any, key: Any) -> Any:  # noqa: ARG002 — signature parity
        return self._user

    def close(self) -> None:
        self.closed = True


def _patch_redis(monkeypatch: pytest.MonkeyPatch, *, revoked: bool) -> None:
    async def fake_get_redis() -> None:
        return None

    async def fake_is_jti_revoked(redis: Any, jti: str) -> bool:  # noqa: ARG001
        return revoked

    monkeypatch.setattr(auth_module, "get_redis", fake_get_redis)
    monkeypatch.setattr(auth_module, "is_jti_revoked", fake_is_jti_revoked)


def _patch_session(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
    monkeypatch.setattr(auth_module, "Session", lambda engine: session)


_WS = SimpleNamespace()  # get_ws_current_user only reads the token


async def test_ws_rejects_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis(monkeypatch, revoked=False)
    token = _make_token(token_type="refresh")

    with pytest.raises(WebSocketException):
        await auth_module.get_ws_current_user(_WS, token=token)


async def test_ws_rejects_token_without_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """A JWT signed with the same key but lacking ``type`` (e.g. a
    password-reset token) must not authenticate a WebSocket."""
    _patch_redis(monkeypatch, revoked=False)
    token = _make_token(token_type=None)

    with pytest.raises(WebSocketException):
        await auth_module.get_ws_current_user(_WS, token=token)


async def test_ws_rejects_reset_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis(monkeypatch, revoked=False)
    token = _make_token(token_type="reset")

    with pytest.raises(WebSocketException):
        await auth_module.get_ws_current_user(_WS, token=token)


async def test_ws_rejects_revoked_jti(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis(monkeypatch, revoked=True)
    token = _make_token()

    with pytest.raises(WebSocketException):
        await auth_module.get_ws_current_user(_WS, token=token)


async def test_ws_accepts_valid_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_redis(monkeypatch, revoked=False)
    fake_user = SimpleNamespace(
        email="user@example.com", is_active=True, token_version=0
    )
    fake_session = _FakeSession(fake_user)
    _patch_session(monkeypatch, fake_session)

    user, session = await auth_module.get_ws_current_user(
        _WS, token=_make_token(ver=0)
    )

    assert user is fake_user
    assert session is fake_session
    assert not fake_session.closed  # caller owns the session lifecycle


async def test_ws_rejects_token_version_mismatch_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_redis(monkeypatch, revoked=False)
    fake_user = SimpleNamespace(
        email="user@example.com", is_active=True, token_version=5
    )
    fake_session = _FakeSession(fake_user)
    _patch_session(monkeypatch, fake_session)

    with pytest.raises(WebSocketException):
        await auth_module.get_ws_current_user(_WS, token=_make_token(ver=0))

    assert fake_session.closed


async def test_http_get_current_user_rejects_non_access_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The REST path must also accept only ``type == "access"``."""
    from app.exceptions import AuthenticationError

    _patch_redis(monkeypatch, revoked=False)
    for bad in (
        _make_token(token_type="refresh"),
        _make_token(token_type="reset"),
        _make_token(token_type=None),
    ):
        with pytest.raises(AuthenticationError):
            await auth_module.get_current_user(session=None, token=bad)
