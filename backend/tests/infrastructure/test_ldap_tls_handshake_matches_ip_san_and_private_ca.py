"""LDAP TLS handshake: IP SANs and private CAs without keyUsage verify; wrong hosts fail.

These run a real TLS handshake against a local server, so they cover the
hostname check that ldap3 does on its own (DNS-only on Python 3.12+) and the
X509 strict flag that Python 3.13+ turns on by default.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import socket
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from ldap3 import Connection
from ldap3.core.exceptions import LDAPSocketOpenError

from app.core.config import settings
from app.infrastructure.ldap import client as ldap_client
from app.models import LdapConfig

_Leaf = tuple[Path, Path]  # (certificate PEM, private key PEM)


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """Pure unit tests; no test database needed."""


@dataclass(frozen=True)
class _Pki:
    trust_bundle: Path  # both campus CAs below
    other_ca_file: Path  # a CA that signed none of the leaves
    leaf: _Leaf  # CA with keyUsage; SAN DNS:localhost, IP:127.0.0.1
    leaf_from_ca_without_key_usage: _Leaf  # same SANs
    dns_only_leaf: _Leaf  # CA with keyUsage; SAN DNS:localhost


def _key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _make_ca(
    name: str, *, key_usage: bool
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = _key()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
    )
    if key_usage:
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    return builder.sign(key, hashes.SHA256()), key


def _make_leaf(
    tmp: Path,
    stem: str,
    ca: tuple[x509.Certificate, ec.EllipticCurvePrivateKey],
    sans: list[x509.GeneralName],
) -> _Leaf:
    ca_cert, ca_key = ca
    key = _key()
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dc")]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    cert_file = tmp / f"{stem}.crt"
    cert_file.write_bytes(_pem(cert))
    key_file = tmp / f"{stem}.key"
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


@pytest.fixture(scope="module")
def pki(tmp_path_factory: pytest.TempPathFactory) -> _Pki:
    tmp = tmp_path_factory.mktemp("ldap-pki")
    ca = _make_ca("Campus Test CA", key_usage=True)
    ca_without_key_usage = _make_ca("Campus Legacy CA", key_usage=False)
    other_ca = _make_ca("Unrelated CA", key_usage=True)

    trust_bundle = tmp / "campus-ca.pem"
    trust_bundle.write_bytes(_pem(ca[0]) + _pem(ca_without_key_usage[0]))
    other_ca_file = tmp / "other-ca.pem"
    other_ca_file.write_bytes(_pem(other_ca[0]))

    localhost = x509.DNSName("localhost")
    loopback = x509.IPAddress(ipaddress.ip_address("127.0.0.1"))
    return _Pki(
        trust_bundle=trust_bundle,
        other_ca_file=other_ca_file,
        leaf=_make_leaf(tmp, "leaf", ca, [localhost, loopback]),
        leaf_from_ca_without_key_usage=_make_leaf(
            tmp, "legacy", ca_without_key_usage, [localhost, loopback]
        ),
        dns_only_leaf=_make_leaf(tmp, "dns-only", ca, [localhost]),
    )


@contextmanager
def _tls_server(leaf: _Leaf) -> Iterator[int]:
    """Accept TLS handshakes on 127.0.0.1 and yield the port."""
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(leaf[0]), str(leaf[1]))
    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(0.2)
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            try:
                raw, _addr = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            raw.settimeout(5)
            try:
                with server_ctx.wrap_socket(raw, server_side=True):
                    pass
            except OSError:  # the client rejected our certificate
                pass
            finally:
                raw.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        stop.set()
        thread.join(timeout=5)
        listener.close()


def _handshake(port: int, host: str) -> None:
    """Run the LDAP TLS wrap on a socket to 127.0.0.1 while ldap3 believes it talks to ``host``."""
    tls = ldap_client._build_tls()
    raw = socket.create_connection(("127.0.0.1", port), timeout=5)
    conn = SimpleNamespace(socket=raw, server=SimpleNamespace(host=host))
    try:
        tls.wrap_socket(conn, do_handshake=True)
    finally:
        conn.socket.close()


def _config(uri: str) -> LdapConfig:
    return LdapConfig(
        server_uri=uri,
        use_starttls=False,
        bind_dn="cn=svc,dc=example,dc=org",
        encrypted_bind_password="",
        user_search_base="dc=example,dc=org",
    )


@pytest.fixture
def trust_campus_ca(pki: _Pki, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LDAP_CA_CERT_FILE", str(pki.trust_bundle))


@pytest.mark.usefixtures("trust_campus_ca")
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_ip_and_dns_san_match_the_server_host(pki: _Pki, host: str) -> None:
    with _tls_server(pki.leaf) as port:
        _handshake(port, host)


@pytest.mark.usefixtures("trust_campus_ca")
def test_private_ca_without_key_usage_is_accepted(pki: _Pki) -> None:
    with _tls_server(pki.leaf_from_ca_without_key_usage) as port:
        _handshake(port, "localhost")


@pytest.mark.usefixtures("trust_campus_ca")
@pytest.mark.parametrize("host", ["127.0.0.2", "evil.example"])
def test_host_not_in_san_is_rejected(pki: _Pki, host: str) -> None:
    with _tls_server(pki.leaf) as port:
        with pytest.raises(ssl.SSLCertVerificationError, match="mismatch"):
            _handshake(port, host)


def test_certificate_from_untrusted_ca_is_rejected(
    pki: _Pki, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "LDAP_CA_CERT_FILE", str(pki.other_ca_file))
    with _tls_server(pki.leaf) as port:
        with pytest.raises(ssl.SSLCertVerificationError):
            _handshake(port, "127.0.0.1")


def test_private_ca_is_not_trusted_without_ca_file(
    pki: _Pki, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "LDAP_CA_CERT_FILE", None)
    with _tls_server(pki.leaf) as port:
        with pytest.raises(ssl.SSLCertVerificationError):
            _handshake(port, "127.0.0.1")


@pytest.mark.usefixtures("trust_campus_ca")
def test_ldaps_uri_with_ip_opens_through_ldap3(pki: _Pki) -> None:
    with _tls_server(pki.leaf) as port:
        server = ldap_client._build_server(_config(f"ldaps://127.0.0.1:{port}"))
        conn = Connection(server)
        conn.open()
        try:
            assert isinstance(conn.socket, ssl.SSLSocket)
        finally:
            conn.strategy.close()


@pytest.mark.usefixtures("trust_campus_ca")
def test_ldaps_uri_with_ip_missing_from_san_fails_through_ldap3(pki: _Pki) -> None:
    with _tls_server(pki.dns_only_leaf) as port:
        server = ldap_client._build_server(_config(f"ldaps://127.0.0.1:{port}"))
        with pytest.raises(LDAPSocketOpenError, match="mismatch"):
            Connection(server).open()


def test_tls_hint_points_at_san_for_host_mismatch() -> None:
    hint = ldap_client._tls_hint(
        ssl.SSLCertVerificationError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "IP address mismatch, certificate is not valid for '10.0.0.5'."
        )
    )
    assert "subjectAltName" in hint
    assert "LDAP_CA_CERT_FILE" not in hint
