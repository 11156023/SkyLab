"""單一 PVE 連線的節點／Storage 同步。

原本寫在 `api/routes/proxmox_config.py` 的私有函式，初始化精靈也需要同一套
邏輯（建立第一組連線後立刻同步），所以搬到 service 層讓兩個 route 共用。
"""

from __future__ import annotations

import logging

from sqlmodel import Session

from app.infrastructure.proxmox import fetch_cluster_nodes, resolve_verify
from app.models import ProxmoxConnection, ProxmoxNode
from app.repositories import proxmox_connection as proxmox_connection_repo
from app.repositories import proxmox_node as proxmox_node_repo
from app.repositories import proxmox_storage as proxmox_storage_repo

logger = logging.getLogger(__name__)


def storage_row_from_pve(node_name: str, st: dict) -> dict:
    """把 PVE `/nodes/{node}/storage` 的一筆回應整理成 `proxmox_storage` 欄位。"""
    content = st.get("content", "")
    total = st.get("total", 0)
    used = st.get("used", 0)
    avail = st.get("avail", 0)
    return {
        "node_name": node_name,
        "storage": st.get("storage", ""),
        "storage_type": st.get("type"),
        "total_gb": round(total / 1024**3, 2) if total else 0.0,
        "used_gb": round(used / 1024**3, 2) if used else 0.0,
        "avail_gb": round(avail / 1024**3, 2) if avail else 0.0,
        "can_vm": "images" in content,
        "can_lxc": "rootdir" in content,
        "can_iso": "iso" in content,
        "can_backup": "backup" in content,
        "is_shared": bool(st.get("shared", 0)),
        "active": st.get("active", 1) == 1,
    }


def is_usable_storage(st: dict) -> bool:
    """PVE 端已禁用、或在此節點不可用（node-restricted）的 storage 不同步。"""
    return bool(st.get("enabled", 1)) and bool(st.get("active", 1))


def sync_connection_inventory(
    session: Session, conn: ProxmoxConnection
) -> tuple[list[ProxmoxNode], int]:
    """同步單一連線的節點與 Storage，回傳 (nodes, storage_count)。

    節點名稱與其他連線衝突時拋 ValueError；連線失敗時拋原始例外。
    """
    from proxmoxer import ProxmoxAPI

    password = proxmox_connection_repo.get_decrypted_password(conn)
    verify_ssl = resolve_verify(conn.host, conn.verify_ssl, conn.ca_cert)

    raw_nodes = fetch_cluster_nodes(
        host=conn.host,
        user=conn.user,
        password=password,
        verify_ssl=verify_ssl,
        timeout=conn.api_timeout,
    )

    node_dicts = [
        {
            "name": n["name"],
            "host": n["host"],
            "port": n.get("port", 8006),
            "is_primary": n.get("is_primary", False),
        }
        for n in raw_nodes
    ]
    saved_nodes = proxmox_node_repo.upsert_nodes(
        session, node_dicts, connection_id=conn.id
    )

    client = ProxmoxAPI(
        conn.host,
        port=conn.port,
        user=conn.user,
        password=password,
        verify_ssl=verify_ssl,
        timeout=conn.api_timeout,
    )

    storage_dicts: list[dict] = []
    for node in saved_nodes:
        try:
            raw_storages = client.nodes(node.name).storage.get()
            storage_dicts.extend(
                storage_row_from_pve(node.name, st)
                for st in raw_storages
                if is_usable_storage(st)
            )
        except Exception as e:
            logger.warning(f"Failed to fetch storage for node {node.name}: {e}")

    saved_storages = proxmox_storage_repo.upsert_storages(
        session,
        storage_dicts,
        scope_node_names={node.name for node in saved_nodes},
    )
    return saved_nodes, len(saved_storages)
