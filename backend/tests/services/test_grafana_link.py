"""資源監控頁的 Grafana 連結與管理員免密碼登入（auth.proxy）。

以 httpx.MockTransport 取代真的連線、以假 session 取代 DB，不需要 Grafana 容器。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps.auth import get_current_active_superuser
from app.api.deps.database import get_db
from app.api.routes import monitoring as monitoring_routes
from app.core import security
from app.core.config import settings
from app.models import UserRole
from app.services.monitoring import grafana_service


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grafana_service._ProbeCache, "expires_at", 0.0)
    monkeypatch.setattr(grafana_service._ProbeCache, "enabled", False)
    monkeypatch.setattr(grafana_service._AuthCache, "entries", {})
    monkeypatch.setattr(settings, "GRAFANA_INTERNAL_URL", "http://grafana:3000/grafana/")
    monkeypatch.setattr(settings, "GRAFANA_ROOT_URL", None)


def _mock_grafana(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> list[str]:
    calls: list[str] = []
    real_client = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return handler(request)

    def _factory(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(_handler), **kwargs)

    monkeypatch.setattr(grafana_service.httpx, "AsyncClient", _factory)
    return calls


async def test_enabled_when_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_grafana(monkeypatch, lambda _: httpx.Response(200, json={"database": "ok"}))

    assert await grafana_service.get_grafana_link() == {"enabled": True, "url": "/grafana/"}
    assert calls == ["http://grafana:3000/grafana/api/health"]


async def test_uses_root_url_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GRAFANA_ROOT_URL", " https://skylab.example.edu/grafana/ ")
    _mock_grafana(monkeypatch, lambda _: httpx.Response(200))

    link = await grafana_service.get_grafana_link()
    assert link == {"enabled": True, "url": "https://skylab.example.edu/grafana/"}


async def test_disabled_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Name or service not known", request=request)

    _mock_grafana(monkeypatch, _refuse)

    assert await grafana_service.get_grafana_link() == {"enabled": False, "url": None}


async def test_disabled_when_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_grafana(monkeypatch, lambda _: httpx.Response(503))

    assert await grafana_service.get_grafana_link() == {"enabled": False, "url": None}


async def test_result_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_grafana(monkeypatch, lambda _: httpx.Response(200))

    await grafana_service.get_grafana_link()
    await grafana_service.get_grafana_link()
    assert len(calls) == 1

    await grafana_service.get_grafana_link(use_cache=False)
    assert len(calls) == 2


# ─── 免密碼登入：cookie → Grafana auth.proxy 身分標頭 ─────────────────────


def _user(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "email": "admin@example.com",
        "full_name": "王小明",
        "is_active": True,
        "is_superuser": True,
        "role": UserRole.admin,
        "token_version": 3,
        "totp_required": False,
        "totp_enabled": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeSession:
    def __init__(self, *users: SimpleNamespace) -> None:
        self.users = {str(u.id): u for u in users}
        self.gets = 0

    def get(self, _model: Any, ident: Any) -> SimpleNamespace | None:
        self.gets += 1
        return self.users.get(str(ident))


def _token(user: SimpleNamespace, **claims: Any) -> str:
    payload = {
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "sub": str(user.id),
        "type": "grafana",
        "ver": user.token_version,
        **claims,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=security.ALGORITHM)


def test_quoted_printable_encodes_non_ascii_and_equals() -> None:
    assert grafana_service._quoted_printable("王 a=b@x.com") == "=E7=8E=8B=20a=3Db@x.com"
    # 換行也會被編碼，不可能拿姓名注入額外的 HTTP 標頭
    assert "\n" not in grafana_service._quoted_printable("evil\r\nX-Other: 1")


async def test_session_token_resolves_to_admin_headers() -> None:
    user = _user()
    token = grafana_service.create_session_token(user)  # type: ignore[arg-type]

    headers = await grafana_service.resolve_proxy_headers(_FakeSession(user), token)  # type: ignore[arg-type]

    assert headers == {
        "X-WEBAUTH-USER": "admin@example.com",
        "X-WEBAUTH-EMAIL": "admin@example.com",
        "X-WEBAUTH-NAME": "=E7=8E=8B=E5=B0=8F=E6=98=8E",
        "X-WEBAUTH-ROLE": "Admin",
    }


async def test_session_token_is_not_an_access_token() -> None:
    user = _user()
    payload = jwt.decode(
        grafana_service.create_session_token(user),  # type: ignore[arg-type]
        settings.SECRET_KEY,
        algorithms=[security.ALGORITHM],
    )
    assert payload["type"] == "grafana"
    assert payload["ver"] == 3


@pytest.mark.parametrize(
    "overrides",
    [
        {"is_active": False},
        {"token_version": 4},  # 改密碼／強制登出後 token_version 遞增
        {"is_superuser": False, "role": UserRole.teacher},
        {"totp_required": True, "totp_enabled": False},
    ],
)
async def test_rejects_users_who_lost_access(overrides: dict[str, Any]) -> None:
    user = _user()
    token = _token(user)
    user.__dict__.update(overrides)

    assert await grafana_service.resolve_proxy_headers(_FakeSession(user), token) == {}  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "make_token",
    [
        lambda u: None,
        lambda u: "not-a-jwt",
        lambda u: _token(u, type="access"),
        lambda u: _token(u, exp=datetime.now(timezone.utc) - timedelta(seconds=1)),
        lambda u: _token(u, sub="not-a-user"),
        lambda u: jwt.encode({"sub": str(u.id), "type": "grafana", "ver": 3}, "wrong-key", algorithm="HS256"),
    ],
)
async def test_rejects_invalid_tokens(make_token: Callable[[Any], str | None]) -> None:
    user = _user()
    assert await grafana_service.resolve_proxy_headers(_FakeSession(user), make_token(user)) == {}  # type: ignore[arg-type]


async def test_auth_result_is_cached_briefly(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _user()
    session = _FakeSession(user)
    token = _token(user)

    await grafana_service.resolve_proxy_headers(session, token)  # type: ignore[arg-type]
    await grafana_service.resolve_proxy_headers(session, token)  # type: ignore[arg-type]
    assert session.gets == 1

    monkeypatch.setattr(grafana_service, "AUTH_CACHE_TTL_SECONDS", 0.0)
    grafana_service._AuthCache.entries.clear()
    user.is_active = False
    assert await grafana_service.resolve_proxy_headers(session, token) == {}  # type: ignore[arg-type]


# ─── 路由：發 cookie、nginx auth_request 端點 ─────────────────────────────


def _client(user: SimpleNamespace, *, enabled: bool, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def _link(*, use_cache: bool = True) -> dict[str, Any]:
        return {"enabled": enabled, "url": "/grafana/" if enabled else None}

    monkeypatch.setattr(grafana_service, "get_grafana_link", _link)
    app = FastAPI()
    app.include_router(monitoring_routes.router)
    app.dependency_overrides[get_current_active_superuser] = lambda: user
    app.dependency_overrides[get_db] = lambda: _FakeSession(user)
    return TestClient(app)


def test_session_endpoint_sets_scoped_httponly_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(_user(), enabled=True, monkeypatch=monkeypatch)

    resp = client.post("/monitoring/grafana/session")

    assert resp.status_code == 200
    assert resp.json() == {"enabled": True, "url": "/grafana/"}
    cookie = resp.headers["set-cookie"]
    assert cookie.startswith("skylab_grafana=")
    assert "Path=/grafana/" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Secure" not in cookie
    assert f"Max-Age={settings.GRAFANA_SESSION_EXPIRE_MINUTES * 60}" in cookie


def test_session_cookie_is_secure_behind_https(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(_user(), enabled=True, monkeypatch=monkeypatch)

    resp = client.post("/monitoring/grafana/session", headers={"X-Forwarded-Proto": "https"})

    assert "Secure" in resp.headers["set-cookie"]


def test_session_endpoint_without_grafana_sets_no_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(_user(), enabled=False, monkeypatch=monkeypatch)

    resp = client.post("/monitoring/grafana/session")

    assert resp.json() == {"enabled": False, "url": None}
    assert "set-cookie" not in resp.headers


def test_auth_endpoint_returns_identity_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _user()
    client = _client(user, enabled=True, monkeypatch=monkeypatch)
    cookie = client.post("/monitoring/grafana/session").cookies["skylab_grafana"]

    resp = client.get(
        "/monitoring/grafana/auth", headers={"Cookie": f"skylab_grafana={cookie}"}
    )

    assert resp.status_code == 204
    assert resp.headers["x-webauth-user"] == "admin@example.com"
    assert resp.headers["x-webauth-role"] == "Admin"


def test_auth_endpoint_without_cookie_is_204_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(_user(), enabled=True, monkeypatch=monkeypatch)

    resp = client.get("/monitoring/grafana/auth")

    # 不能回 401：nginx 會整個擋掉 /grafana/，連 Grafana 自己的登入頁都進不去
    assert resp.status_code == 204
    assert "x-webauth-user" not in resp.headers
