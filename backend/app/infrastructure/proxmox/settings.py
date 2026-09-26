"""Proxmox 設定載入。

多連線架構：每筆 proxmox_connections ＝ 一個獨立 PVE 入口（單台或叢集），
連線層欄位（host/user/password/SSL）與該叢集自身的資源設定（pool、
storage、gateway、預設節點）都存在該筆連線上；跨叢集共用的放置與排程
策略（placement、scheduler）仍來自 proxmox_config singleton。

尚未建立任何連線時視為 Proxmox 尚未設定。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_PROXMOX_POOL_NAME = "SkyLab"


@dataclass
class ProxmoxSettings:
    host: str
    user: str
    password: str
    verify_ssl: bool
    iso_storage: str
    data_storage: str
    api_timeout: int
    task_check_interval: int
    pool_name: str
    ca_cert: str | None = None
    gateway_ip: str | None = None
    local_subnet: str | None = None
    default_node: str | None = None
    connection_id: int | None = None
    connection_name: str | None = None
    port: int = 8006


def get_proxmox_settings(connection_id: int | None = None) -> ProxmoxSettings:
    """Load Proxmox settings from DB. Raises RuntimeError if not configured.

    ``connection_id`` 為 None 時使用預設連線。
    """
    from sqlmodel import Session

    from app.core.db import engine
    from app.repositories import proxmox_connection as connection_repo

    with Session(engine) as session:
        if connection_id is not None:
            connection = connection_repo.get_connection(session, connection_id)
            if connection is None:
                raise RuntimeError(f"Proxmox 連線 {connection_id} 不存在。")
        else:
            connection = connection_repo.get_default_connection(session)

        if connection is not None:
            try:
                conn_password = connection_repo.get_decrypted_password(connection)
            except Exception as exc:
                raise RuntimeError(
                    f"Proxmox 連線「{connection.name}」密碼解密失敗，"
                    "SECRET_KEY 可能已變更。請至管理員介面重新儲存該連線設定。"
                ) from exc

    if connection is None:
        raise RuntimeError("Proxmox 尚未設定，請至管理員介面完成 Proxmox 連線設定。")

    # 資源設定一律以該連線（叢集）自身的值為準
    return ProxmoxSettings(
        host=connection.host,
        user=connection.user,
        password=conn_password,
        verify_ssl=connection.verify_ssl,
        iso_storage=connection.iso_storage,
        data_storage=connection.data_storage,
        api_timeout=connection.api_timeout,
        task_check_interval=connection.task_check_interval,
        pool_name=connection.pool_name,
        ca_cert=connection.ca_cert,
        gateway_ip=connection.gateway_ip,
        local_subnet=connection.local_subnet,
        default_node=connection.default_node,
        connection_id=connection.id,
        connection_name=connection.name,
        port=connection.port,
    )


def get_proxmox_settings_for_node(node_name: str | None) -> ProxmoxSettings:
    """載入節點所屬連線的設定；查不到歸屬時退回預設連線。

    pool / storage / gateway 皆為該叢集自身的設定，凡是「操作某個節點」
    的呼叫端都應該走這個入口，而不是無參數的 get_proxmox_settings()。
    """
    if not node_name:
        return get_proxmox_settings()

    from app.infrastructure.proxmox.client import get_connection_id_for_node

    return get_proxmox_settings(get_connection_id_for_node(node_name))


def list_enabled_connection_ids() -> list[int]:
    """回傳所有啟用連線的 id（依預設優先排序）。

    尚未建立連線資料時回傳空清單。
    """
    from sqlmodel import Session

    from app.core.db import engine
    from app.repositories import proxmox_connection as connection_repo

    try:
        with Session(engine) as session:
            connections = connection_repo.get_all_connections(
                session, enabled_only=True
            )
            return [conn.id for conn in connections if conn.id is not None]
    except Exception as exc:
        logger.warning("Unable to list Proxmox connections: %s", exc)
        return []
