"""Proxmox Storage 資料庫操作"""

from sqlmodel import Session, select

from app.models.proxmox_storage import ProxmoxStorage


def get_all_storages(session: Session) -> list[ProxmoxStorage]:
    """取得所有已儲存的 Storage，按節點名稱 + storage 名稱排序。"""
    stmt = select(ProxmoxStorage).order_by(
        ProxmoxStorage.node_name,
        ProxmoxStorage.storage,
    )
    return list(session.exec(stmt).all())


def get_storage(session: Session, storage_id: int) -> ProxmoxStorage | None:
    """依 id 取得單筆 Storage。"""
    return session.get(ProxmoxStorage, storage_id)


def get_shared_storage_peers(
    session: Session,
    storage_name: str,
    node_names: set[str],
) -> list[ProxmoxStorage]:
    """取得 ``node_names`` 範圍內同名的共享 Storage 記錄。

    共享 Storage 在叢集每個節點上都有一筆記錄（同一份實體儲存），
    使用者設定必須整組一致，故更新時需一併寫入這些記錄。
    """
    if not node_names:
        return []
    stmt = (
        select(ProxmoxStorage)
        .where(ProxmoxStorage.storage == storage_name)
        .where(ProxmoxStorage.is_shared == True)  # noqa: E712
        .where(ProxmoxStorage.node_name.in_(node_names))  # type: ignore[attr-defined]
        .order_by(ProxmoxStorage.node_name)
    )
    return list(session.exec(stmt).all())


def upsert_storages(
    session: Session,
    storages: list[dict],
    scope_node_names: set[str] | None = None,
) -> list[ProxmoxStorage]:
    """
    同步 Storage 清單到資料庫。
    以 (node_name, storage) 為 key：
    - 存在則只更新硬體資訊（total_gb/used_gb/avail_gb/type/flags/active）
    - 不存在則新建；保留既有的 enabled/speed_tier/user_priority 使用者設定。
    - 刪除本次同步中不再出現的舊 Storage。

    ``scope_node_names`` 有值時，只在這些節點的範圍內比對與刪除
    （單一連線同步時不可動到其他連線的 Storage）。
    """
    stmt = select(ProxmoxStorage)
    if scope_node_names is not None:
        stmt = stmt.where(ProxmoxStorage.node_name.in_(scope_node_names))  # type: ignore[attr-defined]
    existing_all = list(session.exec(stmt).all())
    existing_map: dict[tuple[str, str], ProxmoxStorage] = {
        (s.node_name, s.storage): s for s in existing_all
    }

    incoming_keys: set[tuple[str, str]] = set()
    result: list[ProxmoxStorage] = []

    for data in storages:
        key = (data["node_name"], data["storage"])
        incoming_keys.add(key)

        if key in existing_map:
            s = existing_map[key]
            s.storage_type = data.get("storage_type")
            s.total_gb = data.get("total_gb", 0.0)
            s.used_gb = data.get("used_gb", 0.0)
            s.avail_gb = data.get("avail_gb", 0.0)
            s.can_vm = data.get("can_vm", False)
            s.can_lxc = data.get("can_lxc", False)
            s.can_iso = data.get("can_iso", False)
            s.can_backup = data.get("can_backup", False)
            s.is_shared = data.get("is_shared", False)
            s.active = data.get("active", True)
            session.add(s)
            result.append(s)
        else:
            s = ProxmoxStorage(
                node_name=data["node_name"],
                storage=data["storage"],
                storage_type=data.get("storage_type"),
                total_gb=data.get("total_gb", 0.0),
                used_gb=data.get("used_gb", 0.0),
                avail_gb=data.get("avail_gb", 0.0),
                can_vm=data.get("can_vm", False),
                can_lxc=data.get("can_lxc", False),
                can_iso=data.get("can_iso", False),
                can_backup=data.get("can_backup", False),
                is_shared=data.get("is_shared", False),
                active=data.get("active", True),
                enabled=data.get("can_vm", False) or data.get("can_lxc", False),
                speed_tier="unknown",
                user_priority=5,
            )
            session.add(s)
            result.append(s)

    for key, s in existing_map.items():
        if key not in incoming_keys:
            session.delete(s)

    session.flush()   # push deletes before commit, consistent with proxmox_node.py

    session.commit()
    for s in result:
        session.refresh(s)
    return result


def update_storage_settings(
    session: Session,
    storage_id: int,
    enabled: bool,
    speed_tier: str,
    user_priority: int,
    peer_node_names: set[str] | None = None,
) -> tuple[ProxmoxStorage, list[str]] | None:
    """更新使用者可設定的欄位（enabled, speed_tier, user_priority）。

    ``peer_node_names`` 有值且該 Storage 為共享時，這些節點上同名的共享記錄
    會一併套用相同設定（同一份實體儲存不應在叢集內各節點設定分歧）。

    回傳 (目標 Storage, 實際套用的節點名稱清單)。
    """
    s = session.get(ProxmoxStorage, storage_id)
    if s is None:
        return None

    targets = [s]
    if s.is_shared and peer_node_names:
        peers = get_shared_storage_peers(session, s.storage, peer_node_names)
        targets = peers or targets
        if all(p.id != s.id for p in targets):
            targets.append(s)

    for target in targets:
        target.enabled = enabled
        target.speed_tier = speed_tier
        target.user_priority = user_priority
        session.add(target)
    session.commit()
    for target in targets:
        session.refresh(target)
    return s, sorted(str(t.node_name) for t in targets)


__all__ = [
    "get_all_storages",
    "get_shared_storage_peers",
    "get_storage",
    "upsert_storages",
    "update_storage_settings",
]
