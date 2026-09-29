"""LDAP over ldaps:// and StartTLS must verify the server certificate."""

from __future__ import annotations

import ssl

import pytest
from ldap3.core.exceptions import LDAPSocketOpenError

from app.core.config import settings
from app.exceptions import UpstreamServiceError
from app.infrastructure.ldap import client as ldap_client
from app.models import LdapConfig


@pytest.fixture(autouse=True)
def _no_ca_file_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The deployer's .env may set LDAP_CA_CERT_FILE for a private CA; these tests
    # assume the system trust store unless a test overrides it explicitly.
    monkeypatch.setattr(settings, "LDAP_CA_CERT_FILE", None)


def _config(uri: str, *, starttls: bool = False) -> LdapConfig:
    return LdapConfig(
        server_uri=uri,
        use_starttls=starttls,
        bind_dn="cn=svc,dc=example,dc=org",
        encrypted_bind_password="",
        user_search_base="dc=example,dc=org",
    )


@pytest.mark.parametrize(
    ("uri", "starttls"),
    [("ldaps://dc.example.org", False), ("ldap://dc.example.org", True)],
)
def test_server_tls_requires_certificate(uri: str, starttls: bool) -> None:
    server = ldap_client._build_server(_config(uri, starttls=starttls))

    # start_tls() reuses server.tls, so StartTLS is covered by the same object.
    assert server.tls is not None
    assert server.tls.validate == ssl.CERT_REQUIRED
    assert server.tls.ca_certs_file is None


def test_server_tls_uses_configured_ca_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca = tmp_path / "campus-ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr(settings, "LDAP_CA_CERT_FILE", str(ca))

    server = ldap_client._build_server(_config("ldaps://dc.example.org"))

    assert server.tls.validate == ssl.CERT_REQUIRED
    assert server.tls.ca_certs_file == str(ca)


def test_missing_ca_file_is_an_upstream_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "LDAP_CA_CERT_FILE", str(tmp_path / "nope.pem"))

    with pytest.raises(UpstreamServiceError):
        ldap_client._build_server(_config("ldaps://dc.example.org"))


@pytest.mark.parametrize(
    "exc", [ssl.SSLError("certificate verify failed"), LDAPSocketOpenError("tls")]
)
def test_tls_failures_map_to_upstream_error(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    class _FailingConnection:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start_tls(self) -> None:
            raise exc

        def bind(self) -> bool:
            raise exc

    monkeypatch.setattr(ldap_client, "Connection", _FailingConnection)
    monkeypatch.setattr(ldap_client, "decrypt_value", lambda _v: "pw")
    config = _config("ldap://dc.example.org", starttls=True)
    server = ldap_client._build_server(config)

    with pytest.raises(UpstreamServiceError):
        ldap_client._service_connection(config, server)


def test_tls_hint_mentions_ca_setting_only_for_certificate_errors() -> None:
    assert "LDAP_CA_CERT_FILE" in ldap_client._tls_hint(
        ssl.SSLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    )
    assert ldap_client._tls_hint(LDAPSocketOpenError("connection refused")) == ""
