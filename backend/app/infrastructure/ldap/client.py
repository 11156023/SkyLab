"""LDAP/Active Directory 原生連線層。

只做協議層工作：service bind → 搜尋使用者 → 以使用者密碼 rebind 驗證。
所有 ldap3 例外轉為使用者可讀的 AppError 家族；不含業務邏輯（建帳、
角色對映在 services 層）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ldap3 import Connection, Server
from ldap3.core.exceptions import LDAPBindError, LDAPException
from ldap3.utils.conv import escape_filter_chars

from app.core.security import decrypt_value
from app.exceptions import (
    AppError,
    AuthenticationError,
    BadRequestError,
    UpstreamServiceError,
)
from app.models import LdapConfig

logger = logging.getLogger(__name__)

_INVALID_CREDENTIALS = "帳號或密碼錯誤"
_SERVER_UNAVAILABLE = "無法連線 LDAP 伺服器，請聯絡管理員"


@dataclass(frozen=True)
class LdapUserInfo:
    dn: str
    email: str
    full_name: str | None
    groups: list[str]  # memberOf DN 清單


def _build_server(config: LdapConfig) -> Server:
    if not config.server_uri:
        raise BadRequestError("LDAP server URI is not configured")
    return Server(
        config.server_uri,
        connect_timeout=config.connect_timeout_seconds,
        get_info="NO_INFO",
    )


def _service_connection(config: LdapConfig, server: Server) -> Connection:
    try:
        bind_password = decrypt_value(config.encrypted_bind_password)
    except Exception as exc:
        raise BadRequestError("LDAP bind password cannot be decrypted") from exc
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
            raise UpstreamServiceError(
                "LDAP service bind 失敗，請檢查 bind DN 與密碼"
            )
        return conn
    except AppError:
        raise
    except LDAPException as exc:
        logger.warning("LDAP service bind failed: %s", exc)
        raise UpstreamServiceError(_SERVER_UNAVAILABLE) from exc


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
        raise AuthenticationError(_INVALID_CREDENTIALS)

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
            raise UpstreamServiceError(_SERVER_UNAVAILABLE) from exc
        if not found or not conn.entries:
            raise AuthenticationError(_INVALID_CREDENTIALS)

        entry = conn.entries[0]
        user_dn = str(entry.entry_dn)
        raw = entry.entry_attributes_as_dict

        def _first(attr: str) -> str | None:
            values = raw.get(attr) or []
            return str(values[0]) if values else None

        email = _first(config.email_attribute)
        if not email:
            logger.warning("LDAP user %s has no %s attribute", user_dn, config.email_attribute)
            raise AuthenticationError(_INVALID_CREDENTIALS)
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
            raise AuthenticationError(_INVALID_CREDENTIALS)
        user_conn.unbind()  # type: ignore[no-untyped-call]
    except AuthenticationError:
        raise
    except LDAPBindError as exc:
        raise AuthenticationError(_INVALID_CREDENTIALS) from exc
    except LDAPException as exc:
        logger.warning("LDAP user bind failed: %s", exc)
        raise UpstreamServiceError(_SERVER_UNAVAILABLE) from exc

    return LdapUserInfo(dn=user_dn, email=email, full_name=full_name, groups=groups)


__all__ = [
    "LdapUserInfo",
    "authenticate_user",
    "test_bind",
]
