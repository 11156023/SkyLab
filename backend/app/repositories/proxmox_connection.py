"""Proxmox 連線（多入口）資料庫操作"""

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import InvalidToken
from sqlmodel import Session, select

from app.core.i18n import t
from app.core.security import decrypt_value, encrypt_value
from app.exceptions import AppError
from app.models.proxmox_connection import ProxmoxConnection


def get_all_connections(
    session: Session, *, enabled_only: bool = False
) -> list[ProxmoxConnection]:
    """取得所有連線，預設連線優先，其餘按名稱排序。"""
    stmt = select(ProxmoxConnection)
    if enabled_only:
        stmt = stmt.where(ProxmoxConnection.enabled == True)  # noqa: E712
    stmt = stmt.order_by(
        ProxmoxConnection.is_default.desc(),  # type: ignore[attr-defined]
        ProxmoxConnection.name,
    )
    return list(session.exec(stmt).all())


def get_connection(session: Session, connection_id: int) -> ProxmoxConnection | None:
    return session.get(ProxmoxConnection, connection_id)


def get_default_connection(session: Session) -> ProxmoxConnection | None:
    """取得預設連線；沒有標記時退回第一筆啟用的連線。"""
    stmt = (
        select(ProxmoxConnection)
        .where(ProxmoxConnection.is_default == True)  # noqa: E712
        .limit(1)
    )
    conn = session.exec(stmt).first()
    if conn is not None:
        return conn
    connections = get_all_connections(session, enabled_only=True)
    return connections[0] if connections else None


def create_connection(
    session: Session,
    *,
    name: str,
    host: str,
    port: int,
    user: str,
    password: str,
    verify_ssl: bool,
    ca_cert: str | None,
    api_timeout: int,
    pool_name: str,
    iso_storage: str,
    data_storage: str,
    task_check_interval: int,
    gateway_ip: str | None = None,
    local_subnet: str | None = None,
    default_node: str | None = None,
    enabled: bool = True,
    is_default: bool = False,
) -> ProxmoxConnection:
    if is_default:
        _clear_default(session)
    conn = ProxmoxConnection(
        name=name,
        host=host,
        port=port,
        user=user,
        encrypted_password=encrypt_value(password),
        verify_ssl=verify_ssl,
        ca_cert=ca_cert or None,
        api_timeout=api_timeout,
        pool_name=pool_name,
        iso_storage=iso_storage,
        data_storage=data_storage,
        task_check_interval=task_check_interval,
        gateway_ip=gateway_ip or None,
        local_subnet=local_subnet or None,
        default_node=default_node or None,
        enabled=enabled,
        is_default=is_default,
    )
    session.add(conn)
    session.commit()
    session.refresh(conn)
    return conn


# 可由 PUT /connections/{id} 更新的欄位；其餘欄位（id/時間戳）不接受外部指定
_UPDATABLE_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "host",
        "port",
        "user",
        "password",
        "verify_ssl",
        "ca_cert",
        "api_timeout",
        "pool_name",
        "iso_storage",
        "data_storage",
        "task_check_interval",
        "gateway_ip",
        "local_subnet",
        "default_node",
        "enabled",
        "is_default",
    }
)

# 這三個欄位允許清空：帶 None 或空字串都存成 None
_NULLABLE_TEXT_FIELDS: frozenset[str] = frozenset(
    {"gateway_ip", "local_subnet", "default_node"}
)


def update_connection(
    session: Session,
    connection_id: int,
    *,
    updates: Mapping[str, Any],
) -> ProxmoxConnection | None:
    """部分更新一筆連線：只寫入 ``updates`` 真的帶到的欄位。

    ``updates`` 由 route 以 ``model_dump(exclude_unset=True)`` 取得，所以
    「沒帶這個欄位」與「帶了 None」能分開處理，呼叫端不必回送整份設定：
    - ``password``：帶 None 表示不換密碼
    - ``ca_cert``：帶 None 表示不動，帶空字串才是清除
    - ``gateway_ip`` / ``local_subnet`` / ``default_node``：帶 None 或空字串是清空
    - 其餘欄位帶 None 一律忽略（它們沒有「設為 None」的語義）
    """
    conn = session.get(ProxmoxConnection, connection_id)
    if conn is None:
        return None

    if updates.get("is_default") and not conn.is_default:
        _clear_default(session)

    for field, value in updates.items():
        if field not in _UPDATABLE_FIELDS:
            continue
        if field == "password":
            if value is not None:
                conn.encrypted_password = encrypt_value(str(value))
            continue
        if field == "ca_cert":
            if value is not None:
                conn.ca_cert = str(value) or None
            continue
        if field in _NULLABLE_TEXT_FIELDS:
            setattr(conn, field, value or None)
            continue
        if value is None:
            continue
        setattr(conn, field, value)

    conn.updated_at = datetime.now(timezone.utc)
    session.add(conn)
    session.commit()
    session.refresh(conn)
    return conn


def delete_connection(session: Session, connection_id: int) -> bool:
    conn = session.get(ProxmoxConnection, connection_id)
    if conn is None:
        return False
    session.delete(conn)
    session.commit()
    return True


def get_decrypted_password(conn: ProxmoxConnection) -> str:
    try:
        return decrypt_value(conn.encrypted_password)
    except InvalidToken as e:
        raise AppError(
            t("proxmox.connection_decrypt_password_failed", name=conn.name),
            status_code=400,
        ) from e


def _clear_default(session: Session) -> None:
    stmt = select(ProxmoxConnection).where(
        ProxmoxConnection.is_default == True  # noqa: E712
    )
    for conn in session.exec(stmt).all():
        conn.is_default = False
        session.add(conn)


__all__ = [
    "get_all_connections",
    "get_connection",
    "get_default_connection",
    "create_connection",
    "update_connection",
    "delete_connection",
    "get_decrypted_password",
]
