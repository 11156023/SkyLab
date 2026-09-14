"""Proxmox 節點資料庫操作"""

from datetime import datetime, timezone

from sqlmodel import Session, col, select

from app.models.proxmox_connection import ProxmoxConnection
from app.models.proxmox_node import ProxmoxNode


def get_all_nodes(
    session: Session, connection_id: int | None = None
) -> list[ProxmoxNode]:
    """取得節點清單，主節點優先，其餘按名稱排序。

    ``connection_id`` 有值時只回傳該連線的節點。
    """
    stmt = select(ProxmoxNode)
    if connection_id is not None:
        stmt = stmt.where(ProxmoxNode.connection_id == connection_id)
    stmt = stmt.order_by(
        ProxmoxNode.is_primary.desc(),  # type: ignore[attr-defined]
        ProxmoxNode.name,
    )
    return list(session.exec(stmt).all())


def get_node_by_name(session: Session, name: str) -> ProxmoxNode | None:
    stmt = select(ProxmoxNode).where(ProxmoxNode.name == name).limit(1)
    return session.exec(stmt).first()


def get_disabled_node_names(session: Session) -> set[str]:
    """回傳被管理員停用的節點名稱集合（停用＝不參與放置）。"""
    stmt = select(ProxmoxNode.name).where(
        ProxmoxNode.enabled == False  # noqa: E712
    )
    return set(session.exec(stmt).all())


def get_node_connection_names(session: Session) -> dict[str, str]:
    """節點名稱 → 所屬連線名稱的對應（未歸屬的節點不列入）。

    節點名稱在多連線架構下全域唯一，因此可直接以名稱為 key。
    """
    stmt = select(ProxmoxNode.name, ProxmoxConnection.name).join(
        ProxmoxConnection,
        ProxmoxNode.connection_id == ProxmoxConnection.id,  # type: ignore[arg-type]
    )
    return dict(session.exec(stmt).all())


def get_node_connection_map(session: Session) -> dict[str, tuple[int | None, str | None]]:
    """節點名稱 → (連線 id, 連線名稱)，包含所有節點。

    與 :func:`get_node_connection_names` 的差別：保留連線 id，且未歸屬連線的
    節點（舊版單連線資料）也會列入、值為 ``(None, None)``，因此可直接用
    連線 id 對節點分群（例如共享 Storage 以叢集為單位去重）。
    """
    stmt = select(ProxmoxNode.name, ProxmoxConnection.id, ProxmoxConnection.name).join(
        ProxmoxConnection,
        ProxmoxNode.connection_id == ProxmoxConnection.id,  # type: ignore[arg-type]
        isouter=True,
    )
    return {
        node_name: (conn_id, conn_name)
        for node_name, conn_id, conn_name in session.exec(stmt).all()
    }


def upsert_nodes(
    session: Session,
    nodes: list[dict],
    connection_id: int | None = None,
) -> list[ProxmoxNode]:
    """
    同步節點清單到資料庫（限定在 ``connection_id`` 的範圍內）。
    以 name 為 key：存在則更新 host/port/is_primary/is_online/last_checked，保留既有 priority；
    不存在則新建（priority 預設 5）。
    刪除該連線中本次同步不再出現的舊節點。

    節點名稱為全域唯一鍵（operations/placement/storage 都以名稱定位節點），
    因此若名稱已被其他連線使用則拋 ValueError。
    """
    stmt = select(ProxmoxNode)
    if connection_id is not None:
        stmt = stmt.where(
            (ProxmoxNode.connection_id == connection_id)
            | col(ProxmoxNode.connection_id).is_(None)  # 舊資料歸屬預設連線
        )
    existing_all = list(session.exec(stmt).all())
    existing_map: dict[str, ProxmoxNode] = {n.name: n for n in existing_all}

    incoming_names: set[str] = set()
    result: list[ProxmoxNode] = []

    for node_data in nodes:
        name = node_data["name"]
        incoming_names.add(name)

        conflict = get_node_by_name(session, name)
        if (
            conflict is not None
            and name not in existing_map
            and conflict.connection_id is not None
            and conflict.connection_id != connection_id
        ):
            raise ValueError(
                f"節點名稱「{name}」已被其他連線使用；"
                "多連線架構要求節點名稱全域唯一，請先調整 PVE 節點名稱"
            )

        if name in existing_map:
            node = existing_map[name]
            node.connection_id = connection_id
            node.host = node_data["host"]
            node.port = node_data.get("port", 8006)
            node.is_primary = node_data.get("is_primary", False)
            node.is_online = True
            node.last_checked = datetime.now(timezone.utc)
            # 保留既有 priority，不覆寫
        else:
            node = ProxmoxNode(
                connection_id=connection_id,
                name=name,
                host=node_data["host"],
                port=node_data.get("port", 8006),
                is_primary=node_data.get("is_primary", False),
                is_online=True,
                last_checked=datetime.now(timezone.utc),
                priority=5,
            )
        session.add(node)
        result.append(node)

    # 刪除該連線範圍內消失的節點
    for name, node in existing_map.items():
        if name not in incoming_names:
            session.delete(node)

    session.flush()
    session.commit()
    for node in result:
        session.refresh(node)
    return result


def update_node(
    session: Session,
    node_id: int,
    host: str,
    port: int,
    priority: int,
    enabled: bool | None = None,
) -> ProxmoxNode | None:
    """更新單一節點的連線設定、優先級與啟用狀態。"""
    node = session.get(ProxmoxNode, node_id)
    if node is None:
        return None
    node.host = host
    node.port = port
    node.priority = priority
    if enabled is not None:
        node.enabled = enabled
    session.add(node)
    session.commit()
    session.refresh(node)
    return node


def update_node_status(session: Session, node_id: int, is_online: bool) -> None:
    """更新節點的連線狀態。"""
    node = session.get(ProxmoxNode, node_id)
    if node:
        node.is_online = is_online
        node.last_checked = datetime.now(timezone.utc)
        session.add(node)
        session.commit()


__all__ = [
    "get_all_nodes",
    "get_disabled_node_names",
    "get_node_by_name",
    "get_node_connection_map",
    "get_node_connection_names",
    "upsert_nodes",
    "update_node",
    "update_node_status",
]
