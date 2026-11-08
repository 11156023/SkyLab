"""Cloudflare「測試連線」要同時支援使用者層級與帳戶層級的 API Token。

2026-11-06 ntub 實測：zone／DNS 清單正常，但「測試連線」回「1000: Invalid API Token」——
帳戶層級（Account owned）的 token 不能打 /user/tokens/verify，要改打
/accounts/{account_id}/tokens/verify。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError
from app.infrastructure.cloudflare.client import CloudflareAPIClient
from app.services.network import cloudflare_service as cf

TOKEN = "t" * 40
USER_TOKEN_ERROR = "1000: Invalid API Token"


class _FakeClient:
    """verify_token 固定失敗（模擬帳戶層級 token），verify_account_token 可設定成功或失敗。"""

    account_error: Exception | None = None
    calls: list[tuple] = []

    def __init__(self, *, api_token: str) -> None:
        self.api_token = api_token

    def verify_token(self) -> dict:
        _FakeClient.calls.append(("user",))
        raise BadRequestError(USER_TOKEN_ERROR)

    def verify_account_token(self, account_id: str) -> dict:
        _FakeClient.calls.append(("account", account_id))
        if _FakeClient.account_error is not None:
            raise _FakeClient.account_error
        return {"status": "active"}


def _config(account_id: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        account_id=account_id,
        encrypted_api_token="enc",
        default_dns_target_type="A",
        default_dns_target_value="203.0.113.10",
        updated_at=None,
        last_verified_at=None,
    )


@pytest.fixture
def client_env(monkeypatch: pytest.MonkeyPatch):
    _FakeClient.account_error = None
    _FakeClient.calls = []
    env = SimpleNamespace(config=_config("acc-1"), verified=[])
    monkeypatch.setattr(cf, "CloudflareAPIClient", _FakeClient)
    monkeypatch.setattr(cf.config_repo, "get_cloudflare_config", lambda _s: env.config)
    monkeypatch.setattr(cf.config_repo, "get_decrypted_api_token", lambda _c: TOKEN)
    monkeypatch.setattr(
        cf.config_repo,
        "mark_cloudflare_config_verified",
        lambda _s, config: env.verified.append(config),
    )
    return env


def test_connection_falls_back_to_account_token_verify(client_env) -> None:
    result = cf.test_connection(object())

    assert result.success is True
    assert result.token_status == "active"
    assert _FakeClient.calls == [("user",), ("account", "acc-1")]
    assert client_env.verified == [client_env.config]


def test_connection_without_account_id_surfaces_user_verify_error(client_env) -> None:
    client_env.config = _config(None)

    with pytest.raises(BadRequestError, match="Invalid API Token"):
        cf.test_connection(object())

    assert _FakeClient.calls == [("user",)]
    assert client_env.verified == []


def test_connection_reports_both_errors_when_account_verify_also_fails(
    client_env,
) -> None:
    _FakeClient.account_error = BadRequestError(
        "9109: Unauthorized to access requested resource"
    )

    with pytest.raises(BadRequestError) as excinfo:
        cf.test_connection(object())

    message = str(excinfo.value)
    assert "1000" in message
    assert "9109" in message
    assert client_env.verified == []


def test_client_verify_account_token_hits_account_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = CloudflareAPIClient(api_token=TOKEN)
    requests: list[tuple[str, str]] = []

    def fake_request(method: str, path: str, **_kwargs) -> dict:
        requests.append((method, path))
        return {"success": True, "result": {"status": "active"}}

    monkeypatch.setattr(client, "_request", fake_request)

    assert client.verify_account_token(" acc-1 ") == {"status": "active"}
    assert requests == [("GET", "/accounts/acc-1/tokens/verify")]
