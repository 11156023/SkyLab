"""LDAP service bind、StartTLS、使用者 bind 失敗時都要收掉連線（unbind／關 socket）。

登入結果與訊息維持原樣：只多了收尾，不改錯誤類型。
"""

from __future__ import annotations

import ssl
from typing import Any

import pytest
from ldap3.core.exceptions import LDAPBindError, LDAPSocketSendError, LDAPStartTLSError

from app.core.config import settings
from app.core.i18n import t
from app.exceptions import AuthenticationError, UpstreamServiceError
from app.infrastructure.ldap import client as ldap_client
from app.models import LdapConfig


@pytest.fixture(autouse=True)
def _no_ca_file_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LDAP_CA_CERT_FILE", None)
    monkeypatch.setattr(ldap_client, "decrypt_value", lambda _v: "svc-pw")


class _Strategy:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Entry:
    entry_dn = "uid=alice,dc=example,dc=org"
    entry_attributes_as_dict = {"mail": ["alice@example.org"], "displayName": ["Alice"]}


class _FakeConnection:
    """依使用者（service 或一般使用者）套用不同的失敗情境。"""

    instances: list[_FakeConnection] = []
    behaviour: dict[str, dict[str, Any]] = {}

    def __init__(self, _server: Any, *, user: str, **_kwargs: Any) -> None:
        self.user = user
        self.unbind_calls = 0
        self.strategy = _Strategy()
        self.entries = [_Entry()]
        _FakeConnection.instances.append(self)

    @property
    def _plan(self) -> dict[str, Any]:
        role = "service" if self.user.startswith("cn=svc") else "user"
        return _FakeConnection.behaviour.get(role, {})

    def start_tls(self) -> bool:
        error = self._plan.get("start_tls_error")
        if error is not None:
            raise error
        return True

    def bind(self) -> bool:
        error = self._plan.get("bind_error")
        if error is not None:
            raise error
        return self._plan.get("bind", True)

    def search(self, **_kwargs: Any) -> bool:
        return True

    def unbind(self) -> bool:
        self.unbind_calls += 1
        error = self._plan.get("unbind_error")
        if error is not None:
            raise error
        return True


@pytest.fixture
def connections(monkeypatch: pytest.MonkeyPatch) -> type[_FakeConnection]:
    _FakeConnection.instances = []
    _FakeConnection.behaviour = {}
    monkeypatch.setattr(ldap_client, "Connection", _FakeConnection)
    return _FakeConnection


def _config(*, starttls: bool = False) -> LdapConfig:
    return LdapConfig(
        server_uri="ldap://dc.example.org",
        use_starttls=starttls,
        bind_dn="cn=svc,dc=example,dc=org",
        encrypted_bind_password="encrypted",
        user_search_base="dc=example,dc=org",
    )


def _service_connection(config: LdapConfig) -> Any:
    return ldap_client._service_connection(config, ldap_client._build_server(config))


def test_rejected_service_bind_is_unbound(connections) -> None:
    connections.behaviour = {"service": {"bind": False}}

    with pytest.raises(UpstreamServiceError) as exc_info:
        _service_connection(_config())

    assert exc_info.value.message == t("ldap.serviceBindFailed")
    assert [c.unbind_calls for c in connections.instances] == [1]


@pytest.mark.parametrize(
    "error", [LDAPStartTLSError("startTLS failed"), ssl.SSLError("certificate verify failed")]
)
def test_failed_service_starttls_is_unbound(connections, error: Exception) -> None:
    connections.behaviour = {"service": {"start_tls_error": error}}

    with pytest.raises(UpstreamServiceError) as exc_info:
        _service_connection(_config(starttls=True))

    assert exc_info.value.message == t("ldap.serverUnavailable")
    assert [c.unbind_calls for c in connections.instances] == [1]


def test_cleanup_failure_closes_socket_and_keeps_original_error(connections) -> None:
    connections.behaviour = {
        "service": {
            "start_tls_error": LDAPStartTLSError("startTLS failed"),
            "unbind_error": LDAPSocketSendError("socket sending error"),
        }
    }

    with pytest.raises(UpstreamServiceError) as exc_info:
        _service_connection(_config(starttls=True))

    assert isinstance(exc_info.value.__cause__, LDAPStartTLSError)
    assert connections.instances[0].strategy.closed is True


def test_successful_service_bind_keeps_connection_open(connections) -> None:
    conn = _service_connection(_config())

    assert conn.unbind_calls == 0


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        ({"bind": False}, AuthenticationError),
        ({"bind_error": LDAPBindError("invalid credentials")}, AuthenticationError),
        ({"start_tls_error": ssl.SSLError("handshake failed")}, UpstreamServiceError),
    ],
)
def test_failed_user_bind_is_unbound(
    connections, plan: dict[str, Any], expected: type[Exception]
) -> None:
    connections.behaviour = {"user": plan}

    with pytest.raises(expected) as exc_info:
        ldap_client.authenticate_user(_config(starttls=True), "alice", "wrong")

    message = (
        t("ldap.invalidCredentials")
        if expected is AuthenticationError
        else t("ldap.serverUnavailable")
    )
    assert exc_info.value.message == message
    service_conn, user_conn = connections.instances
    assert service_conn.unbind_calls == 1
    assert user_conn.unbind_calls == 1


def test_successful_login_unbinds_once(connections) -> None:
    info = ldap_client.authenticate_user(_config(), "alice", "secret")

    assert info.email == "alice@example.org"
    assert [c.unbind_calls for c in connections.instances] == [1, 1]
