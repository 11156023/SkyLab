"""登入節流改為「依帳號」為主、「依 IP」放寬。

真實使用者測試時整班學生從同一個出口 IP（校園 NAT，或 rootless Docker 的
builtin port driver 讓所有連線都變成 bridge gateway）登入，舊的每 IP 每分鐘
10 次讓整站每分鐘只能登入 10 次。現在：
- 同一 IP 上不同帳號各自計次，不互相吃掉額度；
- 同一帳號（大小寫、前後空白視為同一個）連續嘗試仍在上限後回 429；
- Redis key 不留明文帳號。

把 Redis 限流換成記憶體版、直接呼叫路由函式，不需要 DB 或 Redis。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.deps import rate_limit as rate_limit_deps
from app.api.routes import login as login_routes
from app.core.config import settings
from app.schemas.ldap import LdapLoginRequest


class _FakeLimiter:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def check(
        self,
        _redis: Any,
        *,
        key: str,
        limit: int,
        window_seconds: int,
        scope: str = "",
    ) -> tuple[bool, dict[str, Any]]:
        current = self.counts.get(key, 0)
        if current >= limit:
            return False, {"current": current, "window_seconds": window_seconds}
        self.counts[key] = current + 1
        return True, {"current": current + 1, "window_seconds": window_seconds}


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    limiter = _FakeLimiter()
    logins: list[str] = []

    async def get_redis() -> object:
        return object()

    def fake_login(*, session: Any, email: str, password: str) -> str:
        logins.append(email)
        return "token"

    def fake_ldap_login(*, session: Any, username: str, password: str) -> str:
        logins.append(f"ldap:{username}")
        return "token"

    def fake_recover(*, session: Any, email: str) -> None:
        logins.append(f"recover:{email}")

    monkeypatch.setattr(rate_limit_deps, "get_redis", get_redis)
    monkeypatch.setattr(rate_limit_deps, "check_rate_limit_by_key", limiter.check)
    monkeypatch.setattr(login_routes.auth_service, "login", fake_login)
    monkeypatch.setattr(login_routes.auth_service, "recover_password", fake_recover)
    monkeypatch.setattr(login_routes.ldap_auth_service, "login_ldap", fake_ldap_login)
    return {"limiter": limiter, "logins": logins}


def _password_login(username: str) -> Any:
    form = SimpleNamespace(username=username, password="pw")
    return asyncio.run(
        login_routes.login_access_token(session=None, form_data=form)  # type: ignore[arg-type]
    )


def test_many_accounts_behind_one_ip_are_not_throttled_by_each_other(
    harness: dict[str, Any],
) -> None:
    # 一整班 60 人各自登入：依帳號計次，誰都不會被別人的嘗試擋下
    for i in range(60):
        assert _password_login(f"student{i}@example.com") == "token"
    assert len(harness["logins"]) == 60


def test_same_account_is_limited_after_configured_attempts(
    harness: dict[str, Any],
) -> None:
    limit = settings.LOGIN_RATE_LIMIT_PER_ACCOUNT
    for _ in range(limit):
        _password_login("victim@example.com")
    # 大小寫與前後空白不同仍算同一個帳號，繞不過上限
    with pytest.raises(HTTPException) as exc:
        _password_login("  Victim@Example.com ")
    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "60"}
    # 被擋下的那次沒有走到真正的驗證
    assert len(harness["logins"]) == limit


def test_account_key_does_not_store_plaintext(harness: dict[str, Any]) -> None:
    _password_login("someone@example.com")
    (key,) = harness["limiter"].counts
    assert key.startswith("account:login:")
    assert "someone" not in key


def test_ldap_login_is_limited_per_account(harness: dict[str, Any]) -> None:
    body = LdapLoginRequest(username="s1234", password="pw")
    for _ in range(settings.LOGIN_RATE_LIMIT_PER_ACCOUNT):
        asyncio.run(login_routes.login_ldap(session=None, body=body))  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as exc:
        asyncio.run(login_routes.login_ldap(session=None, body=body))  # type: ignore[arg-type]
    assert exc.value.status_code == 429
    # 另一個 LDAP 帳號不受影響
    other = LdapLoginRequest(username="s5678", password="pw")
    assert asyncio.run(login_routes.login_ldap(session=None, body=other)) == "token"  # type: ignore[arg-type]


def test_password_recovery_limited_per_email(harness: dict[str, Any]) -> None:
    for _ in range(3):
        asyncio.run(login_routes.recover_password(email="a@example.com", session=None))  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as exc:
        asyncio.run(login_routes.recover_password(email="a@example.com", session=None))  # type: ignore[arg-type]
    assert exc.value.status_code == 429
    # 其他信箱照常
    asyncio.run(login_routes.recover_password(email="b@example.com", session=None))  # type: ignore[arg-type]
    assert harness["logins"].count("recover:a@example.com") == 3
    assert "recover:b@example.com" in harness["logins"]


def test_per_ip_limit_fits_a_whole_class_behind_nat() -> None:
    # 依 IP 的上限只擋單一來源大量撞帳號，要容得下整班（含 TOTP 第二步）同時登入
    assert settings.LOGIN_RATE_LIMIT_PER_IP >= 200
