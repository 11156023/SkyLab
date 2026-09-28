"""Proxmox 連線、節點、Storage 與放置／排程策略管理 API（僅管理員）"""

import logging
from typing import Any

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from fastapi import APIRouter, Body, HTTPException
from sqlmodel import col, select

from app.api.deps import AdminUser, SessionDep
from app.core.i18n import t
from app.exceptions import AppError, BadRequestError
from app.infrastructure.proxmox import (
    invalidate_proxmox_client,
    resolve_verify,
)
from app.infrastructure.proxmox.operations import list_connection_vms
from app.models import AuditAction, Resource
from app.models.proxmox_storage import ProxmoxStorage
from app.repositories import proxmox_config as proxmox_config_repo
from app.repositories import proxmox_connection as proxmox_connection_repo
from app.repositories import proxmox_node as proxmox_node_repo
from app.repositories import proxmox_storage as proxmox_storage_repo
from app.schemas.proxmox_config import (
    CertParseResult,
    ConnectionSyncResult,
    ProxmoxConfigPublic,
    ProxmoxConfigUpdate,
    ProxmoxConnectionCreate,
    ProxmoxConnectionPublic,
    ProxmoxConnectionTestResult,
    ProxmoxConnectionUpdateIn,
    ProxmoxNodePublic,
    ProxmoxNodeUpdate,
    ProxmoxStoragePublic,
    ProxmoxStorageUpdate,
    SyncNowResult,
)
from app.services.proxmox import connection_sync_service
from app.services.proxmox.tls_helpers import fingerprint_of, validate_ca_cert_pem
from app.services.user import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proxmox-config", tags=["proxmox-config"])


# ── 內部工具 ────────────────────────────────────────────────────────────────


def _to_public(config, *, is_configured: bool) -> ProxmoxConfigPublic:
    fields = {
        name: getattr(config, name)
        for name in ProxmoxConfigPublic.model_fields
        if name != "is_configured"
    }
    return ProxmoxConfigPublic(**fields, is_configured=is_configured)


def _connection_to_public(session, conn) -> ProxmoxConnectionPublic:
    node_count = len(
        proxmox_node_repo.get_all_nodes(session, connection_id=conn.id)
    )
    return ProxmoxConnectionPublic(
        id=conn.id,
        name=conn.name,
        host=conn.host,
        port=conn.port,
        user=conn.user,
        verify_ssl=conn.verify_ssl,
        api_timeout=conn.api_timeout,
        pool_name=conn.pool_name,
        iso_storage=conn.iso_storage,
        data_storage=conn.data_storage,
        task_check_interval=conn.task_check_interval,
        gateway_ip=conn.gateway_ip,
        local_subnet=conn.local_subnet,
        default_node=conn.default_node,
        enabled=conn.enabled,
        is_default=conn.is_default,
        has_ca_cert=bool(conn.ca_cert),
        node_count=node_count,
        updated_at=conn.updated_at,
    )


class _ResourceCheckUnavailable(AppError):
    """刪除連線前問不到該 PVE 底下有哪些機器（503）。

    與「確認仍有機器」的 400 分開，前端才能只在這種情況提供「強制刪除」
    （``DELETE /connections/{id}?force=true``）。
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, 503)


def _resource_vmids_on_connection(
    session, connection_id: int, node_names: set[str]
) -> list[int]:
    """回傳仍屬於這個連線的 SkyLab Resource vmid。

    新資源建立時會記下 connection_id，直接查 DB；舊資料沒有這個欄位，
    仍要問這組連線的 PVE「現在叢集上有哪些機器」再與 resources 表取交集。
    PVE 問不到時：DB 已確認有機器就直接回傳（照樣 400 擋下），否則視為
    不安全（丟 ``_ResourceCheckUnavailable``，回 503），以免把底下還有機器
    的連線刪掉、讓那些機器再也路由不到。
    """
    recorded = {
        int(v)
        for v in session.exec(
            select(Resource.vmid).where(Resource.connection_id == connection_id)
        ).all()
    }
    try:
        on_pve = _resource_vmids_on_pve(session, connection_id, node_names)
    except _ResourceCheckUnavailable:
        if recorded:
            return sorted(recorded)
        raise
    return sorted(recorded | set(on_pve))


def _resource_vmids_on_pve(
    session, connection_id: int, node_names: set[str]
) -> list[int]:
    """問這組連線的 PVE 現在有哪些機器，回傳其中有 SkyLab Resource 記錄的 vmid。

    直接詢問要刪除的這組連線本身（不論是否停用、不套 pool 篩選——手動搬出
    pool 的機器仍是這個連線的）；跨連線彙總的 list_all_resources() 不能用：
    它略過停用與連不上的連線。問不到時丟 ``_ResourceCheckUnavailable``。
    """
    if not node_names:
        return []

    try:
        cluster_resources = list_connection_vms(connection_id)
    except Exception as e:
        logger.warning(f"刪除連線前無法列出 PVE 機器: {e}")
        raise _ResourceCheckUnavailable(
            t("proxmoxConfig.connectionResourceCheckFailed")
        ) from e

    candidate_vmids: set[int] = set()
    for r in cluster_resources:
        try:
            candidate_vmids.add(int(r["vmid"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not candidate_vmids:
        return []

    rows = session.exec(
        select(Resource.vmid).where(col(Resource.vmid).in_(candidate_vmids))
    ).all()
    return sorted(int(vmid) for vmid in rows)


def _node_to_public(node) -> ProxmoxNodePublic:
    return ProxmoxNodePublic(
        id=node.id,
        name=node.name,
        host=node.host,
        port=node.port,
        is_primary=node.is_primary,
        is_online=node.is_online,
        last_checked=node.last_checked,
        priority=node.priority,
        enabled=getattr(node, "enabled", True),
    )


ConnMap = dict[str, tuple[int | None, str | None]]


def _cluster_node_names(node_name: str, conn_map: ConnMap) -> set[str]:
    """回傳與 ``node_name`` 屬於同一 PVE 連線（叢集）的所有節點名稱。

    未歸屬連線的節點（舊版單連線資料）彼此視為同一叢集。
    """
    conn_id = conn_map.get(node_name, (None, None))[0]
    return {name for name, (cid, _) in conn_map.items() if cid == conn_id} | {node_name}


def _dedupe_shared_storages(
    storages: list[ProxmoxStorage], conn_map: ConnMap
) -> list[ProxmoxStoragePublic]:
    """共享 Storage 每個叢集只保留一筆代表，其餘節點併入 ``node_names``。

    共享 Storage 是整個叢集共用同一份實體儲存，PVE 會在每個節點各回報一次；
    非共享（local / local-lvm 之類）則是各節點獨立的實體儲存，仍逐一列出。
    """
    result: list[ProxmoxStoragePublic] = []
    shared_index: dict[tuple[int | None, str], ProxmoxStoragePublic] = {}

    for s in storages:
        node_name = str(s.node_name)
        if not s.is_shared:
            result.append(_storage_to_public(s, conn_map))
            continue

        conn_id = conn_map.get(node_name, (None, None))[0]
        key = (conn_id, str(s.storage))
        existing = shared_index.get(key)
        if existing is None:
            public = _storage_to_public(s, conn_map)
            shared_index[key] = public
            result.append(public)
        else:
            existing.node_names.append(node_name)

    # 同連線內：叢集級共享排在各節點的本機儲存之前
    result.sort(
        key=lambda p: (
            p.connection_name or "",
            not p.is_shared,
            "" if p.is_shared else p.node_name,
            p.storage,
        )
    )
    return result


def _storage_to_public(
    s: ProxmoxStorage,
    conn_map: ConnMap | None = None,
    node_names: list[str] | None = None,
) -> ProxmoxStoragePublic:
    conn = (conn_map or {}).get(str(s.node_name))
    assert s.id is not None, "已持久化的 Storage 記錄必有主鍵"
    return ProxmoxStoragePublic(
        id=s.id,
        node_name=s.node_name,
        node_names=node_names or [s.node_name],
        connection_id=conn[0] if conn else None,
        connection_name=conn[1] if conn else None,
        storage=s.storage,
        storage_type=s.storage_type,
        total_gb=s.total_gb,
        used_gb=s.used_gb,
        avail_gb=s.avail_gb,
        can_vm=s.can_vm,
        can_lxc=s.can_lxc,
        can_iso=s.can_iso,
        can_backup=s.can_backup,
        is_shared=s.is_shared,
        active=s.active,
        enabled=s.enabled,
        speed_tier=s.speed_tier,
        user_priority=s.user_priority,
    )


def _validate_threshold_ordering(
    *,
    loadavg_warn: float,
    loadavg_max: float,
    disk_warn: float,
    disk_high: float,
) -> None:
    """成對的警戒／上限閾值必須嚴格遞增。

    PUT / 要檢查：評分函式雖然會用 max(high, warn + 0.01) 兜底，
    但存進 DB 的順序若顛倒，管理員看到的數字就與實際生效的不一致。
    PUT 是部分更新，呼叫端要傳入「合併後」的生效值再驗，
    只送一半的成對欄位也擋得住順序顛倒。
    """
    if loadavg_max <= loadavg_warn:
        raise BadRequestError(
            "Loadavg max per core must be greater than the warning threshold"
        )
    if disk_high <= disk_warn:
        raise BadRequestError(
            "Disk contention high share must be greater than the warning threshold"
        )


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/", response_model=ProxmoxConfigPublic)
def get_proxmox_config(session: SessionDep, current_user: AdminUser) -> Any:
    """取得放置／排程策略（PVE 連線本身見 /connections）"""
    config = proxmox_config_repo.get_proxmox_config(session)
    if config is None:
        return ProxmoxConfigPublic(is_configured=False)
    return _to_public(config, is_configured=True)


@router.put("/", response_model=ProxmoxConfigPublic)
def update_proxmox_config(
    session: SessionDep, current_user: AdminUser, config_in: ProxmoxConfigUpdate
) -> Any:
    """新增或更新放置／排程策略。

    **部分更新**：payload 沒帶的欄位維持 DB 現值；尚無設定列時以預設值建立。
    """
    provided = config_in.model_dump(exclude_unset=True)
    existing = proxmox_config_repo.get_proxmox_config(session)

    def merged(name: str):
        """生效值：payload 有帶用帶的，否則沿用現值，初次建立退回 schema 預設"""
        if name in provided:
            return provided[name]
        if existing is not None:
            return getattr(existing, name)
        return getattr(config_in, name)

    _validate_threshold_ordering(
        loadavg_warn=merged("placement_loadavg_warn_per_core"),
        loadavg_max=merged("placement_loadavg_max_per_core"),
        disk_warn=merged("placement_disk_contention_warn_share"),
        disk_high=merged("placement_disk_contention_high_share"),
    )

    config = proxmox_config_repo.upsert_proxmox_config(session, **provided)

    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.proxmox_config_update,
        details=f"Updated Proxmox config fields: {', '.join(sorted(provided)) or '(none)'}",
    )

    return _to_public(config, is_configured=True)


@router.get("/nodes", response_model=list[ProxmoxNodePublic])
def get_nodes(session: SessionDep, current_user: AdminUser) -> list[ProxmoxNodePublic]:
    """取得所有已儲存的叢集節點清單。"""
    nodes = proxmox_node_repo.get_all_nodes(session)
    return [_node_to_public(n) for n in nodes]


@router.put("/nodes/{node_id}", response_model=ProxmoxNodePublic)
def update_node(
    node_id: int,
    session: SessionDep,
    current_user: AdminUser,
    node_in: ProxmoxNodeUpdate,
) -> ProxmoxNodePublic:
    """更新單一節點的連線設定與優先級。"""
    node = proxmox_node_repo.update_node(
        session,
        node_id=node_id,
        host=node_in.host,
        port=node_in.port,
        priority=node_in.priority,
        enabled=node_in.enabled,
    )
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.proxmox_node_update,
        details=(
            f"Updated node {node.name}: host={node_in.host} "
            f"port={node_in.port} priority={node_in.priority} "
            f"enabled={node_in.enabled}"
        ),
    )
    return _node_to_public(node)


@router.get("/storages", response_model=list[ProxmoxStoragePublic])
def get_storages(
    session: SessionDep, current_user: AdminUser
) -> list[ProxmoxStoragePublic]:
    """取得 Storage 清單（共享 Storage 每個叢集只列一筆）。"""
    storages = proxmox_storage_repo.get_all_storages(session)
    conn_map = proxmox_node_repo.get_node_connection_map(session)
    return _dedupe_shared_storages(storages, conn_map)


@router.put("/storages/{storage_id}", response_model=ProxmoxStoragePublic)
def update_storage(
    storage_id: int,
    session: SessionDep,
    current_user: AdminUser,
    storage_in: ProxmoxStorageUpdate,
) -> ProxmoxStoragePublic:
    """更新 Storage 的使用者設定（enabled, speed_tier, user_priority）。

    共享 Storage 會把設定套用到同叢集所有節點上的同名記錄。
    """
    conn_map = proxmox_node_repo.get_node_connection_map(session)
    target = proxmox_storage_repo.get_storage(session, storage_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Storage not found")

    peer_node_names = _cluster_node_names(str(target.node_name), conn_map)
    result = proxmox_storage_repo.update_storage_settings(
        session,
        storage_id=storage_id,
        enabled=storage_in.enabled,
        speed_tier=storage_in.speed_tier,
        user_priority=storage_in.user_priority,
        peer_node_names=peer_node_names,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Storage not found")
    s, applied_nodes = result
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.proxmox_storage_update,
        details=(
            f"Updated storage {s.storage} on {len(applied_nodes)} node(s) "
            f"[{', '.join(applied_nodes)}]: enabled={storage_in.enabled} "
            f"speed_tier={storage_in.speed_tier} priority={storage_in.user_priority}"
        ),
    )
    return _storage_to_public(s, conn_map, node_names=applied_nodes)


# ── PVE 連線管理（多入口） ───────────────────────────────────────────────────


@router.get("/connections", response_model=list[ProxmoxConnectionPublic])
def list_connections(
    session: SessionDep, current_user: AdminUser
) -> list[ProxmoxConnectionPublic]:
    """取得所有 PVE 連線（預設連線優先）。"""
    connections = proxmox_connection_repo.get_all_connections(session)
    return [_connection_to_public(session, c) for c in connections]


@router.post("/connections", response_model=ProxmoxConnectionPublic)
def create_connection(
    session: SessionDep, current_user: AdminUser, conn_in: ProxmoxConnectionCreate
) -> ProxmoxConnectionPublic:
    """新增一組 PVE 連線（單台主機或叢集入口）。"""
    validate_ca_cert_pem(conn_in.ca_cert)

    # 第一筆連線自動成為預設
    is_default = conn_in.is_default or not proxmox_connection_repo.get_all_connections(
        session
    )
    conn = proxmox_connection_repo.create_connection(
        session,
        name=conn_in.name,
        host=conn_in.host,
        port=conn_in.port,
        user=conn_in.user,
        password=conn_in.password,
        verify_ssl=conn_in.verify_ssl,
        ca_cert=conn_in.ca_cert,
        api_timeout=conn_in.api_timeout,
        pool_name=conn_in.pool_name,
        iso_storage=conn_in.iso_storage,
        data_storage=conn_in.data_storage,
        task_check_interval=conn_in.task_check_interval,
        gateway_ip=conn_in.gateway_ip,
        local_subnet=conn_in.local_subnet,
        default_node=conn_in.default_node,
        enabled=conn_in.enabled,
        is_default=is_default,
    )
    invalidate_proxmox_client()
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.proxmox_config_update,
        details=f"Created Proxmox connection: {conn.name} ({conn.host})",
    )
    return _connection_to_public(session, conn)


@router.put("/connections/{connection_id}", response_model=ProxmoxConnectionPublic)
def update_connection(
    connection_id: int,
    session: SessionDep,
    current_user: AdminUser,
    conn_in: ProxmoxConnectionUpdateIn,
) -> ProxmoxConnectionPublic:
    """更新一組 PVE 連線設定（**部分更新**，payload 沒帶的欄位維持現值）。"""
    validate_ca_cert_pem(conn_in.ca_cert)

    updates = conn_in.model_dump(exclude_unset=True)
    conn = proxmox_connection_repo.update_connection(
        session, connection_id, updates=updates
    )
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    invalidate_proxmox_client()
    changed_fields = sorted(f for f in updates if f != "password")
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.proxmox_config_update,
        details=(
            f"Updated Proxmox connection: {conn.name} ({conn.host}) "
            f"fields: {', '.join(changed_fields) or '(none)'}"
            + (", password" if "password" in updates and conn_in.password else "")
        ),
    )
    return _connection_to_public(session, conn)


@router.delete("/connections/{connection_id}")
def delete_connection(
    connection_id: int,
    session: SessionDep,
    current_user: AdminUser,
    force: bool = False,
) -> dict:
    """刪除一組 PVE 連線（其節點與 Storage 記錄一併移除）。

    ``force=true`` 只用在 PVE 已永久無法連線、無從確認底下機器的情況：
    略過「連不上」這一關（503，會寫入稽核），確認到仍有機器時（400）一樣
    會擋下。前端在收到 503 時才提供二次確認的強制刪除。
    """
    conn = proxmox_connection_repo.get_connection(session, connection_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    others = [
        c for c in proxmox_connection_repo.get_all_connections(session)
        if c.id != connection_id
    ]
    if conn.is_default and others:
        raise BadRequestError(t("proxmoxConfig.cannotDeleteDefault"))

    node_names = {
        n.name for n in proxmox_node_repo.get_all_nodes(
            session, connection_id=connection_id
        )
    }
    # 連線一刪，節點記錄跟著 CASCADE 消失，還掛在上面的機器就再也路由不到
    # （get_proxmox_api_for_node 找不到節點）。先擋下來，要管理員自己決定
    # 是先搬機器還是先刪機器。
    check_skipped = False
    try:
        in_use_vmids = _resource_vmids_on_connection(
            session, connection_id, node_names
        )
    except _ResourceCheckUnavailable:
        if not force:
            raise
        logger.warning(
            "Force-deleting Proxmox connection %s without resource check",
            connection_id,
        )
        in_use_vmids = []
        check_skipped = True
    if in_use_vmids:
        raise BadRequestError(
            t(
                "proxmoxConfig.connectionHasResources",
                name=conn.name,
                count=len(in_use_vmids),
                vmids=", ".join(str(v) for v in in_use_vmids[:10]),
            )
        )

    # 先清掉該連線的節點對應 Storage 記錄，再刪節點與連線
    if node_names:
        proxmox_storage_repo.upsert_storages(
            session, [], scope_node_names=node_names
        )
    proxmox_connection_repo.delete_connection(session, connection_id)
    invalidate_proxmox_client()
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.proxmox_config_update,
        details=(
            f"Deleted Proxmox connection: {conn.name} ({conn.host})"
            + (
                " [forced: PVE unreachable, resource check skipped]"
                if check_skipped
                else ""
            )
        ),
    )
    return {"success": True}


@router.post(
    "/connections/{connection_id}/test", response_model=ProxmoxConnectionTestResult
)
def test_connection_by_id(
    connection_id: int, session: SessionDep, current_user: AdminUser
) -> ProxmoxConnectionTestResult:
    """測試指定連線。"""
    conn = proxmox_connection_repo.get_connection(session, connection_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    try:
        password = proxmox_connection_repo.get_decrypted_password(conn)
        verify_ssl = resolve_verify(
            conn.host, conn.verify_ssl, conn.ca_cert, port=conn.port
        )
        node_names = connection_sync_service.probe_node_names(
            conn.host,
            port=conn.port,
            user=conn.user,
            password=password,
            verify_ssl=verify_ssl,
            timeout=conn.api_timeout,
        )
        return ProxmoxConnectionTestResult(
            success=True,
            message=t("proxmoxConfig.connectionSuccess", nodes=", ".join(node_names)),
        )
    except Exception as e:
        logger.warning(f"Proxmox connection test failed for {connection_id}: {e}")
        return ProxmoxConnectionTestResult(
            success=False, message=t("proxmoxConfig.connectionFailed")
        )


@router.post(
    "/connections/{connection_id}/sync", response_model=ConnectionSyncResult
)
def sync_connection(
    connection_id: int, session: SessionDep, current_user: AdminUser
) -> ConnectionSyncResult:
    """同步指定連線的節點與 Storage。"""
    conn = proxmox_connection_repo.get_connection(session, connection_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    try:
        saved_nodes, storage_count = connection_sync_service.sync_connection_inventory(session, conn)
    except ValueError as e:
        return ConnectionSyncResult(
            success=False, connection_id=connection_id, nodes=[],
            storage_count=0, error=str(e),
        )
    except Exception as e:
        logger.warning(f"Connection sync failed for {connection_id}: {e}")
        return ConnectionSyncResult(
            success=False, connection_id=connection_id, nodes=[],
            storage_count=0, error=t("proxmoxConfig.syncFailed"),
        )

    invalidate_proxmox_client()
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.proxmox_sync_nodes,
        details=(
            f"Synced connection {conn.name}: {len(saved_nodes)} nodes, "
            f"{storage_count} storages"
        ),
    )
    return ConnectionSyncResult(
        success=True,
        connection_id=connection_id,
        nodes=[_node_to_public(n) for n in saved_nodes],
        storage_count=storage_count,
    )


@router.post("/sync-now", response_model=SyncNowResult)
def sync_now(
    session: SessionDep, current_user: AdminUser
) -> SyncNowResult:
    """
    同步所有啟用連線的節點與各節點的 Storage 到資料庫。
    節點既有的 priority 設定會被保留。
    Storage 既有的 enabled/speed_tier/user_priority 設定會被保留。
    """
    connections = proxmox_connection_repo.get_all_connections(
        session, enabled_only=True
    )

    if not connections:
        return SyncNowResult(
            success=False, nodes=[], storage_count=0,
            error=t("proxmoxConfig.notConfigured"),
        )

    all_nodes: list = []
    total_storages = 0
    errors: list[str] = []
    for conn in connections:
        try:
            saved_nodes, storage_count = connection_sync_service.sync_connection_inventory(session, conn)
            all_nodes.extend(saved_nodes)
            total_storages += storage_count
        except ValueError as e:
            errors.append(str(e))
        except Exception as e:
            logger.warning(f"sync-now failed for connection {conn.name}: {e}")
            errors.append(t("proxmoxConfig.connectionSyncFailed", name=conn.name))

    invalidate_proxmox_client()

    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.proxmox_sync_now,
        details=(
            f"Sync-now: {len(all_nodes)} nodes, "
            f"{total_storages} storages, {len(errors)} errors"
        ),
    )

    if not all_nodes and errors:
        return SyncNowResult(
            success=False, nodes=[], storage_count=0, error="；".join(errors)
        )

    return SyncNowResult(
        success=True,
        nodes=[_node_to_public(n) for n in all_nodes],
        storage_count=total_storages,
        error="；".join(errors) if errors else None,
    )


@router.post("/parse-cert", response_model=CertParseResult)
def parse_cert(
    current_user: AdminUser,
    pem: str = Body(..., embed=True),
) -> CertParseResult:
    """解析貼上的 PEM 憑證，回傳指紋與基本資訊供管理員確認"""
    try:
        cert = x509.load_pem_x509_certificate(pem.encode(), default_backend())
        return CertParseResult(
            valid=True,
            fingerprint=fingerprint_of(cert),
            subject=cert.subject.rfc4514_string(),
            issuer=cert.issuer.rfc4514_string(),
            not_before=cert.not_valid_before_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
            not_after=cert.not_valid_after_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        )
    except Exception as e:
        logger.warning(f"Certificate parse failed: {e}")
        return CertParseResult(valid=False, error="Invalid certificate")
