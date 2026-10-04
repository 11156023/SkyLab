"""Cloudflare Turnstile 機器人驗證：服務規則與登入／註冊路由的接線。"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import TURNSTILE_HEADER
from app.core.config import settings
from app.exceptions import AppError, BadRequestError
from app.infrastructure.cloudflare import TurnstileNetworkError
from app.services.user import turnstile_service

API = settings.API_V1_STR


class _FakeSiteverify:
    """取代 Cloudflare siteverify：記錄呼叫並回傳指定結果（或丟出指定例外）。"""

    def __init__(
        self, result: dict[str, Any] | None = None, exc: Exception | None = None
    ) -> None:
        self.result = result or {"success": True, "action": "login"}
        self.exc = exc
        self.calls: list[dict[str, str]] = []

    async def __call__(self, *, secret: str, token: str) -> dict[str, Any]:
        self.calls.append({"secret": secret, "token": token})
        if self.exc is not None:
            raise self.exc
        return self.result


@pytest.fixture
def enable_turnstile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TURNSTILE_SITE_KEY", "site-key")
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "secret-key")


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeSiteverify) -> None:
    monkeypatch.setattr(turnstile_service, "siteverify", fake)


# ── 服務規則 ────────────────────────────────────────────────────────────────


async def test_disabled_skips_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TURNSTILE_SITE_KEY", None)
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", None)
    fake = _FakeSiteverify()
    _install(monkeypatch, fake)

    await turnstile_service.verify(None, action="login")

    assert fake.calls == []
    assert turnstile_service.public_site_key() is None


async def test_only_site_key_is_not_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    """只填 site key、沒填 secret 時視為未啟用：前端不顯示驗證框、後端也不擋。"""
    monkeypatch.setattr(settings, "TURNSTILE_SITE_KEY", "site-key")
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", None)
    fake = _FakeSiteverify()
    _install(monkeypatch, fake)

    await turnstile_service.verify(None, action="login")

    assert fake.calls == []
    assert turnstile_service.public_site_key() is None


@pytest.mark.usefixtures("enable_turnstile")
@pytest.mark.parametrize("token", [None, "", "   ", "x" * 2049])
async def test_missing_or_oversized_token_rejected_without_calling_cloudflare(
    monkeypatch: pytest.MonkeyPatch, token: str | None
) -> None:
    fake = _FakeSiteverify()
    _install(monkeypatch, fake)

    with pytest.raises(BadRequestError):
        await turnstile_service.verify(token, action="login")
    assert fake.calls == []


@pytest.mark.usefixtures("enable_turnstile")
async def test_valid_token_passes_with_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeSiteverify({"success": True, "action": "signup"})
    _install(monkeypatch, fake)

    await turnstile_service.verify(" tok ", action="signup")

    assert fake.calls == [{"secret": "secret-key", "token": "tok"}]
    assert turnstile_service.public_site_key() == "site-key"


@pytest.mark.usefixtures("enable_turnstile")
@pytest.mark.parametrize(
    "result",
    [
        {"success": False, "error-codes": ["invalid-input-response"]},
        {"success": False, "error-codes": ["timeout-or-duplicate"]},
        # 拿登入頁的 token 來註冊：action 不符
        {"success": True, "action": "login"},
        {"success": True},
        # 測試金鑰的旗標只放寬 action 比對，驗證失敗照樣拒絕
        {"success": False, "metadata": {"result_with_testing_key": True}},
    ],
)
async def test_rejected_result_raises_bad_request(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]
) -> None:
    _install(monkeypatch, _FakeSiteverify(result))

    with pytest.raises(BadRequestError):
        await turnstile_service.verify("tok", action="signup")


@pytest.mark.usefixtures("enable_turnstile")
async def test_cloudflare_testing_key_result_skips_action_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloudflare 測試金鑰（本機開發）的 siteverify 回應不含 action。"""
    _install(
        monkeypatch,
        _FakeSiteverify(
            {
                "success": True,
                "error-codes": [],
                "hostname": "example.com",
                "metadata": {"result_with_testing_key": True},
            }
        ),
    )

    await turnstile_service.verify("XXXX.DUMMY.TOKEN.XXXX", action="signup")


@pytest.mark.usefixtures("enable_turnstile")
async def test_cloudflare_unreachable_fails_closed_with_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _FakeSiteverify(exc=TurnstileNetworkError("timeout")))

    with pytest.raises(AppError) as exc_info:
        await turnstile_service.verify("tok", action="login")
    assert exc_info.value.status_code == 503


# ── 路由接線 ────────────────────────────────────────────────────────────────


def _superuser_form() -> dict[str, str]:
    return {
        "username": settings.FIRST_SUPERUSER,
        "password": settings.FIRST_SUPERUSER_PASSWORD,
    }


@pytest.mark.usefixtures("enable_turnstile")
def test_password_login_requires_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeSiteverify()
    _install(monkeypatch, fake)

    r = client.post(f"{API}/login/access-token", data=_superuser_form())
    assert r.status_code == 400
    assert fake.calls == []

    r = client.post(
        f"{API}/login/access-token",
        data=_superuser_form(),
        headers={TURNSTILE_HEADER: "tok"},
    )
    assert r.status_code == 200
    assert r.json().get("access_token") or r.json().get("totp_required")
    assert fake.calls == [{"secret": "secret-key", "token": "tok"}]


@pytest.mark.usefixtures("enable_turnstile")
def test_password_login_rejected_token_blocks_login(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, _FakeSiteverify({"success": False}))

    r = client.post(
        f"{API}/login/access-token",
        data=_superuser_form(),
        headers={TURNSTILE_HEADER: "tok"},
    )
    assert r.status_code == 400
    assert "access_token" not in r.json()


@pytest.mark.usefixtures("enable_turnstile")
def test_ldap_login_requires_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeSiteverify()
    _install(monkeypatch, fake)

    r = client.post(
        f"{API}/login/ldap", json={"username": "someone", "password": "secret"}
    )
    assert r.status_code == 400
    assert fake.calls == []


@pytest.mark.usefixtures("enable_turnstile")
def test_signup_requires_signup_action_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {
        "email": "turnstile-signup@example.com",
        "password": "a-long-password",
        "full_name": "Bot",
    }
    fake = _FakeSiteverify({"success": True, "action": "login"})
    _install(monkeypatch, fake)

    r = client.post(f"{API}/users/signup", json=body)
    assert r.status_code == 400
    assert fake.calls == []

    # 登入頁的 token（action=login）不能拿來註冊
    r = client.post(f"{API}/users/signup", json=body, headers={TURNSTILE_HEADER: "tok"})
    assert r.status_code == 400
    assert len(fake.calls) == 1


def test_login_methods_exposes_site_key_only_when_enabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "TURNSTILE_SITE_KEY", None)
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", None)
    r = client.get(f"{API}/login/methods")
    assert r.status_code == 200
    assert r.json()["turnstile_site_key"] is None

    monkeypatch.setattr(settings, "TURNSTILE_SITE_KEY", "site-key")
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "secret-key")
    r = client.get(f"{API}/login/methods")
    assert r.json()["turnstile_site_key"] == "site-key"
    assert "secret-key" not in r.text
