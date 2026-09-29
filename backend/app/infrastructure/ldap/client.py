"""LDAP/Active Directory 原生連線層。

只做協議層工作：service bind → 搜尋使用者 → 以使用者密碼 rebind 驗證。
所有 ldap3 例外轉為使用者可讀的 AppError 家族；不含業務邏輯（建帳、
角色對映在 services 層）。
"""

from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from typing import Any

from ldap3 import Connection, Server, Tls
from ldap3.core.exceptions import LDAPBindError, LDAPException
from ldap3.utils.conv import escape_filter_chars

from app.core.config import settings
from app.core.i18n import t
from app.core.security import decrypt_value
from app.exceptions import (
    AppError,
    AuthenticationError,
    BadRequestError,
    UpstreamServiceError,
)
from app.models import LdapConfig

logger = logging.getLogger(__name__)


def _invalid_credentials() -> str:
    return t("ldap.invalidCredentials")


def _server_unavailable() -> str:
    return t("ldap.serverUnavailable")


@dataclass(frozen=True)
class LdapUserInfo:
    dn: str
    email: str
    full_name: str | None
    groups: list[str]  # memberOf DN 清單


class _VerifiedTls(Tls):
    """由標準庫在握手時驗證憑證鏈與主機名稱（DNS 或 IP SAN）。

    ldap3 2.9 在 Python 3.12+ 找不到 ``ssl.match_hostname``，會退回自帶的比對
    函式，只看 DNS SAN 與 CN，用 IP 連線（``ldaps://192.168.x.x``）一律失敗；
    ``create_default_context`` 在 Python 3.13+ 又預設開 ``VERIFY_X509_STRICT``，
    沒有 keyUsage 擴充的私有 CA 會被拒絕。這裡改由 OpenSSL 在同一條連線的握手中
    比對 ``server_hostname``，不再呼叫 ldap3 的 ``check_hostname``，並只拿掉
    strict（驗鏈與主機名稱照做）。ldaps:// 與 StartTLS 都經過 ``wrap_socket``。
    """

    def wrap_socket(self, connection: Any, do_handshake: bool = False) -> None:
        # 沒給 CA 時 create_default_context 會載入系統信任庫；
        # 預設即 CERT_REQUIRED + check_hostname=True。
        ctx = ssl.create_default_context(
            ssl.Purpose.SERVER_AUTH,
            cafile=self.ca_certs_file,
            capath=self.ca_certs_path,
            cadata=self.ca_certs_data,
        )
        # 登入密碼會經過這條連線，不接受 TLS 1.0／1.1
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        connection.socket = ctx.wrap_socket(
            connection.socket,
            server_side=False,
            do_handshake_on_connect=do_handshake,
            server_hostname=self.sni or connection.server.host,
        )


def _build_tls() -> Tls:
    """ldaps:// 與 StartTLS 共用的 TLS 設定：一律驗證憑證與主機名稱。

    ldap3 預設的 ``Tls()`` 是 ``CERT_NONE``，中間人可拿到 service bind 密碼與
    每位使用者登入時送出的密碼。憑證驗證交給 ``_VerifiedTls``（系統信任庫＋
    可選的私有 CA；URI 的主機名稱或 IP 必須出現在憑證的 SAN）。
    """
    try:
        return _VerifiedTls(
            validate=ssl.CERT_REQUIRED,
            ca_certs_file=settings.LDAP_CA_CERT_FILE or None,
        )
    except LDAPException as exc:
        logger.warning(
            "Invalid LDAP_CA_CERT_FILE %r: %s", settings.LDAP_CA_CERT_FILE, exc
        )
        raise UpstreamServiceError(_server_unavailable()) from exc


def _tls_hint(exc: BaseException) -> str:
    """憑證驗證失敗時在日誌補一句怎麼修（使用者端訊息維持通用）。"""
    text = str(exc).lower()
    if "mismatch" in text or "doesn't match" in text:
        return (
            " (TLS certificate does not match the server address; the host in "
            "the LDAP server URI must be a DNS name or IP address listed in "
            "the certificate's subjectAltName)"
        )
    if "certificate" in text or "hostname" in text:
        return (
            " (TLS certificate verification failed; if the directory server "
            "uses a private CA, set LDAP_CA_CERT_FILE to its PEM file and "
            "mount it into the backend and worker containers)"
        )
    return ""


def _build_server(config: LdapConfig) -> Server:
    if not config.server_uri:
        raise BadRequestError(t("ldap.serverUriNotConfigured"))
    # start_tls() 沿用 server.tls，所以 StartTLS 也吃得到同一份驗證設定。
    return Server(
        config.server_uri,
        connect_timeout=config.connect_timeout_seconds,
        get_info="NO_INFO",
        tls=_build_tls(),
    )


def _service_connection(config: LdapConfig, server: Server) -> Connection:
    try:
        bind_password = decrypt_value(config.encrypted_bind_password)
    except Exception as exc:
        raise BadRequestError(t("ldap.bindPasswordDecryptFailed")) from exc
    try:
        conn = Connection(
            server,
            user=config.bind_dn,
            password=bind_password,
            receive_timeout=config.connect_timeout_seconds,
        )
        if config.use_starttls:
            conn.start_tls()
        if not conn.bind():
            raise UpstreamServiceError(t("ldap.serviceBindFailed"))
        return conn
    except AppError:
        raise
    except (LDAPException, ssl.SSLError) as exc:
        # 憑證驗證失敗也落在這裡（LDAPSocketOpenError／LDAPStartTLSError）
        logger.warning("LDAP service bind failed: %s%s", exc, _tls_hint(exc))
        raise UpstreamServiceError(_server_unavailable()) from exc


def test_bind(config: LdapConfig) -> None:
    """只驗證 service bind 是否成功（管理 UI「測試連線」用）。"""
    server = _build_server(config)
    conn = _service_connection(config, server)
    conn.unbind()  # type: ignore[no-untyped-call]


def authenticate_user(
    config: LdapConfig, username: str, password: str
) -> LdapUserInfo:
    """驗證使用者帳密並回傳目錄屬性。

    失敗一律拋 AuthenticationError（不洩漏帳號是否存在）；
    連線類問題拋 AppError(502)。
    """
    if not username or not password:
        raise AuthenticationError(_invalid_credentials())

    server = _build_server(config)
    conn = _service_connection(config, server)
    try:
        search_filter = config.user_filter_template.format(
            username=escape_filter_chars(username)
        )
        attributes = [config.email_attribute, config.name_attribute, "memberOf"]
        try:
            found = conn.search(
                search_base=config.user_search_base,
                search_filter=search_filter,
                attributes=attributes,
            )
        except LDAPException as exc:
            logger.warning("LDAP search failed: %s", exc)
            raise UpstreamServiceError(_server_unavailable()) from exc
        if not found or not conn.entries:
            raise AuthenticationError(_invalid_credentials())

        entry = conn.entries[0]
        user_dn = str(entry.entry_dn)
        raw = entry.entry_attributes_as_dict

        def _first(attr: str) -> str | None:
            values = raw.get(attr) or []
            return str(values[0]) if values else None

        email = _first(config.email_attribute)
        if not email:
            logger.warning("LDAP user %s has no %s attribute", user_dn, config.email_attribute)
            raise AuthenticationError(_invalid_credentials())
        full_name = _first(config.name_attribute)
        groups = [str(g) for g in (raw.get("memberOf") or [])]
    finally:
        conn.unbind()  # type: ignore[no-untyped-call]

    # 以使用者 DN + 密碼 rebind 驗證
    try:
        user_conn = Connection(
            server,
            user=user_dn,
            password=password,
            receive_timeout=config.connect_timeout_seconds,
        )
        if config.use_starttls:
            user_conn.start_tls()
        if not user_conn.bind():
            raise AuthenticationError(_invalid_credentials())
        user_conn.unbind()  # type: ignore[no-untyped-call]
    except AuthenticationError:
        raise
    except LDAPBindError as exc:
        raise AuthenticationError(_invalid_credentials()) from exc
    except (LDAPException, ssl.SSLError) as exc:
        logger.warning("LDAP user bind failed: %s%s", exc, _tls_hint(exc))
        raise UpstreamServiceError(_server_unavailable()) from exc

    return LdapUserInfo(dn=user_dn, email=email, full_name=full_name, groups=groups)


__all__ = [
    "LdapUserInfo",
    "authenticate_user",
    "test_bind",
]
