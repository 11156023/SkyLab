"""app.services.monitoring.grafana_service：session 查詢失敗與 auth 快取滿載時的淘汰。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import jwt
import pytest

from app.core import security
from app.core.config import settings
from app.models import UserRole
from app.services.monitoring import grafana_service


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grafana_service._AuthCache, "entries", {})


def _user() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        email="admin@example.com",
        full_name="Admin",
        is_active=True,
        is_superuser=True,
        role=UserRole.admin,
        token_version=1,
        totp_required=False,
        totp_enabled=False,
    )


class _Session:
    def __init__(
        self, user: SimpleNamespace | None = None, *, fail: bool = False
    ) -> None:
        self.user = user
        self.fail = fail

    def get(self, _model: Any, _ident: Any) -> SimpleNamespace | None:
        if self.fail:
            raise ValueError("badly formed hexadecimal UUID string")
        return self.user


def _token(user: SimpleNamespace, **claims: Any) -> str:
    payload = {
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "sub": str(user.id),
        "type": "grafana",
        "ver": user.token_version,
        **claims,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=security.ALGORITHM)


async def test_session_lookup_failure_returns_no_headers() -> None:
    user = _user()
    token = _token(user, sub="not-a-uuid")
    headers = await grafana_service.resolve_proxy_headers(_Session(fail=True), token)  # type: ignore[arg-type]
    assert headers == {}
    # 失敗不寫快取
    assert grafana_service._AuthCache.entries == {}


async def test_full_cache_of_live_entries_is_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(grafana_service, "_AUTH_CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(
        grafana_service._AuthCache,
        "entries",
        {"a": (float("inf"), {}), "b": (float("inf"), {})},
    )
    user = _user()
    token = _token(user)
    await grafana_service.resolve_proxy_headers(_Session(user), token)  # type: ignore[arg-type]
    assert set(grafana_service._AuthCache.entries) == {token}
