"""被要求強制 2FA 但尚未綁定的帳號不可開 WebSocket（與 HTTP API 同一道閘）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from fastapi import WebSocketException, status

from app.api.deps import auth as auth_module
from app.core import security
from app.core.config import settings


def _access_token(ver: int = 0) -> str:
    payload = {
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "sub": "00000000-0000-0000-0000-000000000001",
        "ver": ver,
        "type": "access",
        "jti": "b3-jti",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=security.ALGORITHM)


class _FakeSession:
    def __init__(self, user: Any) -> None:
        self._user = user
        self.closed = False

    def get(self, model: Any, key: Any) -> Any:
        return self._user

    def close(self) -> None:
        self.closed = True


def _patch(monkeypatch: pytest.MonkeyPatch, user: Any) -> _FakeSession:
    async def fake_get_redis() -> None:
        return None

    async def fake_is_jti_revoked(redis: Any, jti: str) -> bool:
        return False

    session = _FakeSession(user)
    monkeypatch.setattr(auth_module, "get_redis", fake_get_redis)
    monkeypatch.setattr(auth_module, "is_jti_revoked", fake_is_jti_revoked)
    monkeypatch.setattr(auth_module, "Session", lambda engine: session)
    return session


def _user(*, totp_required: bool, totp_enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        email="b3@example.com",
        is_active=True,
        token_version=0,
        totp_required=totp_required,
        totp_enabled=totp_enabled,
    )


async def test_ws_rejects_user_who_must_enroll_totp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _patch(monkeypatch, _user(totp_required=True, totp_enabled=False))

    with pytest.raises(WebSocketException) as exc_info:
        await auth_module.get_ws_current_user(SimpleNamespace(), token=_access_token())

    assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION
    assert session.closed


@pytest.mark.parametrize(
    ("totp_required", "totp_enabled"),
    [(True, True), (False, False), (False, True)],
)
async def test_ws_accepts_users_who_satisfy_the_policy(
    monkeypatch: pytest.MonkeyPatch, totp_required: bool, totp_enabled: bool
) -> None:
    user = _user(totp_required=totp_required, totp_enabled=totp_enabled)
    session = _patch(monkeypatch, user)

    got_user, got_session = await auth_module.get_ws_current_user(
        SimpleNamespace(), token=_access_token()
    )

    assert got_user is user
    assert got_session is session
    assert not session.closed
