"""Proxmox 設定載入。

多連線架構：每筆 proxmox_connections ＝ 一個獨立 PVE 入口（單台或叢集），
連線層欄位（host/user/password/SSL）與該叢集自身的資源設定（pool、
storage、gateway、預設節點）都存在該筆連線上；跨叢集共用的放置與排程
策略（placement、scheduler）仍來自 proxmox_config singleton。

尚未建立任何連線時視為 Proxmox 尚未設定。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace

logger = logging.getLogger(__name__)

DEFAULT_PROXMOX_POOL_NAME = "SkyLab"

# 每次 PVE 查詢都要讀設定（pool 名稱、task 間隔…），原本每次都開一個 DB session
# 再解一次 Fernet 密碼；逐台迴圈裡會變成數百次。本行程內的設定變更由
# invalidate_proxmox_client() 立即清除，其他行程（其他 worker）最多延遲一個 TTL。
SETTINGS_CACHE_TTL = 30.0

_cache_lock = threading.Lock()
_settings_cache: dict[int | None, tuple[float, ProxmoxSettings]] = {}
_enabled_ids_cache: tuple[float, list[int]] | None = None


def invalidate_proxmox_settings_cache() -> None:
    global _enabled_ids_cache
    with _cache_lock:
        _settings_cache.clear()
        _enabled_ids_cache = None


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
    backup_storage: str | None = None


def get_proxmox_settings(connection_id: int | None = None) -> ProxmoxSettings:
    """Load Proxmox settings from DB. Raises RuntimeError if not configured.

    ``connection_id`` 為 None 時使用預設連線。
    """
    with _cache_lock:
        cached = _settings_cache.get(connection_id)
    if cached is not None and time.monotonic() - cached[0] < SETTINGS_CACHE_TTL:
        # 給複本：呼叫端改欄位不能汙染快取
        return replace(cached[1])
    settings = _load_proxmox_settings(connection_id)
    with _cache_lock:
        _settings_cache[connection_id] = (time.monotonic(), settings)
    return replace(settings)


def _load_proxmox_settings(connection_id: int | None) -> ProxmoxSettings:
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
        backup_storage=connection.backup_storage,
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
    global _enabled_ids_cache
    from sqlmodel import Session

    from app.core.db import engine
    from app.repositories import proxmox_connection as connection_repo

    with _cache_lock:
        cached = _enabled_ids_cache
    if cached is not None and time.monotonic() - cached[0] < SETTINGS_CACHE_TTL:
        return list(cached[1])

    try:
        with Session(engine) as session:
            connections = connection_repo.get_all_connections(
                session, enabled_only=True
            )
            ids = [conn.id for conn in connections if conn.id is not None]
    except Exception as exc:
        # 讀取失敗不快取，下一次呼叫再試
        logger.warning("Unable to list Proxmox connections: %s", exc)
        return []
    with _cache_lock:
        _enabled_ids_cache = (time.monotonic(), ids)
    return list(ids)
