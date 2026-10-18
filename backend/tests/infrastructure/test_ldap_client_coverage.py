"""app.infrastructure.ldap.client 的分支覆蓋：設定缺漏、解密失敗、測試連線與搜尋失敗路徑。"""

from __future__ import annotations

from typing import Any

import pytest
from ldap3.core.exceptions import LDAPSocketReceiveError

from app.core.config import settings
from app.exceptions import AuthenticationError, BadRequestError, UpstreamServiceError
from app.infrastructure.ldap import client as ldap_client
from app.models import LdapConfig


class _Strategy:
    def close(self) -> None:
        pass


class _Entry:
    entry_dn = "uid=alice,dc=example,dc=org"

    def __init__(self, attrs: dict[str, list[str]]) -> None:
        self.entry_attributes_as_dict = attrs


class _FakeConnection:
    """可設定搜尋結果的假 ldap3 Connection。"""

    instances: list[_FakeConnection] = []
    search_result: bool = True
    search_error: Exception | None = None
    entries_attrs: list[dict[str, list[str]]] = []

    def __init__(self, _server: Any, *, user: str, **_kwargs: Any) -> None:
        self.user = user
        self.unbind_calls = 0
        self.strategy = _Strategy()
        self.entries = [_Entry(a) for a in _FakeConnection.entries_attrs]
        _FakeConnection.instances.append(self)

    def start_tls(self) -> bool:
        return True

    def bind(self) -> bool:
        return True

    def search(self, **_kwargs: Any) -> bool:
        if _FakeConnection.search_error is not None:
            raise _FakeConnection.search_error
        return _FakeConnection.search_result

    def unbind(self) -> None:
        self.unbind_calls += 1


@pytest.fixture(autouse=True)
def _fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LDAP_CA_CERT_FILE", None)
    monkeypatch.setattr(ldap_client, "decrypt_value", lambda _v: "svc-pw")
    monkeypatch.setattr(ldap_client, "Connection", _FakeConnection)
    _FakeConnection.instances = []
    _FakeConnection.search_result = True
    _FakeConnection.search_error = None
    _FakeConnection.entries_attrs = [
        {"mail": ["alice@example.org"], "displayName": ["Alice"]}
    ]


def _config(**overrides: Any) -> LdapConfig:
    values: dict[str, Any] = {
        "server_uri": "ldap://ldap.example.org",
        "bind_dn": "cn=svc,dc=example,dc=org",
        "encrypted_bind_password": "enc",
        "user_search_base": "dc=example,dc=org",
        "user_filter_template": "(uid={username})",
        "email_attribute": "mail",
        "name_attribute": "displayName",
        "use_starttls": False,
        "connect_timeout_seconds": 5,
    }
    values.update(overrides)
    return LdapConfig(**values)


def test_build_server_requires_server_uri() -> None:
    with pytest.raises(BadRequestError):
        ldap_client._build_server(_config(server_uri=""))


def test_service_connection_decrypt_failure_is_bad_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_v: str) -> str:
        raise ValueError("bad token")

    monkeypatch.setattr(ldap_client, "decrypt_value", _boom)
    with pytest.raises(BadRequestError):
        ldap_client.test_bind(_config())


def test_test_bind_success_unbinds_service_connection() -> None:
    ldap_client.test_bind(_config())
    assert len(_FakeConnection.instances) == 1
    assert _FakeConnection.instances[0].unbind_calls == 1


def test_authenticate_user_rejects_empty_credentials() -> None:
    with pytest.raises(AuthenticationError):
        ldap_client.authenticate_user(_config(), "", "pw")
    with pytest.raises(AuthenticationError):
        ldap_client.authenticate_user(_config(), "alice", "")
    assert _FakeConnection.instances == []


def test_authenticate_user_search_error_is_upstream_error() -> None:
    _FakeConnection.search_error = LDAPSocketReceiveError("socket gone")
    with pytest.raises(UpstreamServiceError):
        ldap_client.authenticate_user(_config(), "alice", "pw")
    assert _FakeConnection.instances[0].unbind_calls == 1


def test_authenticate_user_found_but_no_entries_is_invalid_credentials() -> None:
    _FakeConnection.entries_attrs = []
    with pytest.raises(AuthenticationError):
        ldap_client.authenticate_user(_config(), "alice", "pw")


def test_authenticate_user_without_email_attribute_is_invalid_credentials() -> None:
    _FakeConnection.entries_attrs = [{"displayName": ["Alice"]}]
    with pytest.raises(AuthenticationError):
        ldap_client.authenticate_user(_config(), "alice", "pw")
    # 只有 service 連線，沒走到使用者 rebind
    assert len(_FakeConnection.instances) == 1
