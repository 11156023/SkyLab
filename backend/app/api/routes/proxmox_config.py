"""Proxmox 連線設定管理 API（僅管理員）"""

import hashlib
import logging
from typing import Any

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.serialization import Encoding
from fastapi import APIRouter, Body, HTTPException

from app.api.deps import AdminUser, SessionDep
from app.core.i18n import t
from app.exceptions import BadRequestError
from app.infrastructure.proxmox import (
    DEFAULT_PROXMOX_POOL_NAME,
    _tcp_ping,
    _verify_server_with_ca,
    fetch_cluster_nodes,
    invalidate_proxmox_client,
)
from app.models import AuditAction
from app.models.proxmox_storage import ProxmoxStorage
from app.repositories import proxmox_config as proxmox_config_repo
from app.repositories import proxmox_connection as proxmox_connection_repo
from app.repositories import proxmox_node as proxmox_node_repo
from app.repositories import proxmox_storage as proxmox_storage_repo
from app.schemas.proxmox_config import (
    CertParseResult,
    ClusterPreviewResult,
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
from app.services.user import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proxmox-config", tags=["proxmox-config"])


# ── 內部工具 ────────────────────────────────────────────────────────────────


def _cert_fingerprint(pem: str) -> str:
    """計算 PEM 憑證的 SHA-256 指紋（格式：AA:BB:CC:...）"""
    cert = x509.load_pem_x509_certificate(pem.encode(), default_backend())
    digest = hashlib.sha256(cert.public_bytes(encoding=Encoding.DER)).digest()
    return ":".join(f"{b:02X}" for b in digest)


def _to_public(config, *, is_configured: bool) -> ProxmoxConfigPublic:
    fingerprint = None
    if config.ca_cert:
        try:
            fingerprint = _cert_fingerprint(config.ca_cert)
        except Exception:
            # 憑證指紋計算失敗時以 None 呈現
            pass
    return ProxmoxConfigPublic(
        host=config.host,
        user=config.user,
        verify_ssl=config.verify_ssl,
        iso_storage=config.iso_storage,
        data_storage=config.data_storage,
        api_timeout=config.api_timeout,
        task_check_interval=config.task_check_interval,
        pool_name=config.pool_name,
        gateway_ip=config.gateway_ip,
        local_subnet=config.local_subnet,
        default_node=config.default_node,
        cpu_overcommit_ratio=config.cpu_overcommit_ratio,
        disk_overcommit_ratio=config.disk_overcommit_ratio,
        placement_reassignment_cost=config.placement_reassignment_cost,
        placement_peak_cpu_margin=config.placement_peak_cpu_margin,
        placement_peak_memory_margin=config.placement_peak_memory_margin,
        placement_loadavg_warn_per_core=config.placement_loadavg_warn_per_core,
        placement_loadavg_max_per_core=config.placement_loadavg_max_per_core,
        placement_loadavg_penalty_weight=config.placement_loadavg_penalty_weight,
        placement_disk_contention_warn_share=config.placement_disk_contention_warn_share,
        placement_disk_contention_high_share=config.placement_disk_contention_high_share,
        placement_disk_penalty_weight=config.placement_disk_penalty_weight,
        placement_cpu_peak_warn_share=config.placement_cpu_peak_warn_share,
        placement_cpu_peak_high_share=config.placement_cpu_peak_high_share,
        placement_memory_peak_warn_share=config.placement_memory_peak_warn_share,
        placement_memory_peak_high_share=config.placement_memory_peak_high_share,
        placement_resource_weight_cpu=config.placement_resource_weight_cpu,
        placement_resource_weight_memory=config.placement_resource_weight_memory,
        placement_resource_weight_disk=config.placement_resource_weight_disk,
        scheduled_boot_batch_size=config.scheduled_boot_batch_size,
        scheduled_boot_batch_interval_seconds=config.scheduled_boot_batch_interval_seconds,
        scheduled_boot_lead_time_minutes=config.scheduled_boot_lead_time_minutes,
        window_grace_period_minutes=config.window_grace_period_minutes,
        practice_session_hours=config.practice_session_hours,
        practice_warning_minutes=config.practice_warning_minutes,
        expiry_warning_hours=config.expiry_warning_hours,
        updated_at=config.updated_at,
        is_configured=is_configured,
        has_ca_cert=bool(config.ca_cert),
        ca_fingerprint=fingerprint,
    )


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


def _sync_one_connection(session, conn) -> tuple[list, int]:
    """同步單一連線的節點與 Storage，回傳 (nodes, storage_count)。

    節點名稱與其他連線衝突時拋 ValueError；連線失敗時拋原始例外。
    """
    from proxmoxer import ProxmoxAPI

    password = proxmox_connection_repo.get_decrypted_password(conn)

    if conn.ca_cert:
        _verify_server_with_ca(conn.host, conn.ca_cert)
        verify_ssl: bool = False
    else:
        verify_ssl = conn.verify_ssl

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
            for st in raw_storages:
                # PVE 端已禁用、或在此節點不可用（node-restricted）的 storage 不同步
                if not st.get("enabled", 1):
                    continue
                if not st.get("active", 1):
                    continue
                content = st.get("content", "")
                total = st.get("total", 0)
                used = st.get("used", 0)
                avail = st.get("avail", 0)
                storage_dicts.append({
                    "node_name": node.name,
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
                })
        except Exception as e:
            logger.warning(f"Failed to fetch storage for node {node.name}: {e}")

    saved_storages = proxmox_storage_repo.upsert_storages(
        session,
        storage_dicts,
        scope_node_names={node.name for node in saved_nodes},
    )
    return saved_nodes, len(saved_storages)


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

    /preview 與 PUT / 都要檢查：評分函式雖然會用 max(high, warn + 0.01) 兜底，
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


def _resolve_credentials(
    session,
    config_in: ProxmoxConfigUpdate,
) -> tuple[str, str | bool]:
    """
    解析連線所需的 password 與 verify_ssl/ca_cert。
    password：用請求提供的；若無則從 DB 取。
    ca_cert：用請求提供的；若無則從 DB 取。
    回傳 (password, verify_ssl_or_ca_cert_pem)。
    """
    _validate_threshold_ordering(
        loadavg_warn=config_in.placement_loadavg_warn_per_core,
        loadavg_max=config_in.placement_loadavg_max_per_core,
        disk_warn=config_in.placement_disk_contention_warn_share,
        disk_high=config_in.placement_disk_contention_high_share,
    )

    existing = proxmox_config_repo.get_proxmox_config(session)

    # 決定密碼
    if config_in.password:
        password = config_in.password
    elif existing:
        password = proxmox_config_repo.get_decrypted_password(existing)
    else:
        raise BadRequestError(t("proxmoxConfig.passwordRequired"))

    # 決定 CA cert / verify_ssl
    ca_cert = config_in.ca_cert
    if ca_cert is None and existing:
        ca_cert = existing.ca_cert

    if ca_cert:
        return password, ca_cert  # ca_cert PEM string
    else:
        return password, config_in.verify_ssl  # bool


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/", response_model=ProxmoxConfigPublic)
def get_proxmox_config(session: SessionDep, current_user: AdminUser) -> Any:
    """取得目前的 Proxmox 連線設定（密碼不回傳）"""
    config = proxmox_config_repo.get_proxmox_config(session)
    if config is None:
        return ProxmoxConfigPublic(
            host="",
            user="",
            verify_ssl=False,
            iso_storage="local",
            data_storage="local-lvm",
            api_timeout=30,
            task_check_interval=2,
            pool_name=DEFAULT_PROXMOX_POOL_NAME,
            gateway_ip=None,
            local_subnet=None,
            default_node=None,
            cpu_overcommit_ratio=2.0,
            disk_overcommit_ratio=1.0,
            placement_reassignment_cost=0.15,
            placement_peak_cpu_margin=1.1,
            placement_peak_memory_margin=1.05,
            placement_loadavg_warn_per_core=0.8,
            placement_loadavg_max_per_core=1.5,
            placement_loadavg_penalty_weight=0.9,
            placement_disk_contention_warn_share=0.7,
            placement_disk_contention_high_share=0.9,
            placement_disk_penalty_weight=0.75,
            placement_cpu_peak_warn_share=0.7,
            placement_cpu_peak_high_share=1.2,
            placement_memory_peak_warn_share=0.8,
            placement_memory_peak_high_share=0.85,
            placement_resource_weight_cpu=1.0,
            placement_resource_weight_memory=1.0,
            placement_resource_weight_disk=1.0,
            updated_at=None,
            is_configured=False,
            has_ca_cert=False,
            ca_fingerprint=None,
        )
    return _to_public(config, is_configured=True)


@router.put("/", response_model=ProxmoxConfigPublic)
def update_proxmox_config(
    session: SessionDep, current_user: AdminUser, config_in: ProxmoxConfigUpdate
) -> Any:
    """新增或更新 Proxmox 設定。

    **部分更新**：payload 沒帶的欄位維持 DB 現值，呼叫端只需送自己管的
    欄位（資源排程頁不必回送連線欄位）。尚無設定列時，未帶欄位退回
    schema 預設值。
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

    password = config_in.password  # None = 不更新，repo 原生支援
    if existing is None and password is None:
        # 連線帳密已改由 proxmox_connections 管理，此 singleton 只承載放置與
        # 排程參數；已經有連線時不必再要一次密碼。
        if proxmox_connection_repo.get_all_connections(session):
            password = ""
        else:
            raise BadRequestError(t("proxmoxConfig.passwordRequired"))

    if config_in.ca_cert:
        try:
            x509.load_pem_x509_certificate(
                config_in.ca_cert.encode(), default_backend()
            )
        except Exception:
            raise BadRequestError(t("proxmoxConfig.invalidCaCert"))

    config = proxmox_config_repo.upsert_proxmox_config(
        session=session,
        host=merged("host"),
        user=merged("user"),
        password=password,
        verify_ssl=merged("verify_ssl"),
        iso_storage=merged("iso_storage"),
        data_storage=merged("data_storage"),
        api_timeout=merged("api_timeout"),
        task_check_interval=merged("task_check_interval"),
        pool_name=merged("pool_name"),
        ca_cert=config_in.ca_cert,  # None = 不更新，repo 原生支援
        gateway_ip=merged("gateway_ip"),
        local_subnet=merged("local_subnet"),
        default_node=merged("default_node"),
        cpu_overcommit_ratio=merged("cpu_overcommit_ratio"),
        disk_overcommit_ratio=merged("disk_overcommit_ratio"),
        placement_reassignment_cost=merged("placement_reassignment_cost"),
        placement_peak_cpu_margin=merged("placement_peak_cpu_margin"),
        placement_peak_memory_margin=merged("placement_peak_memory_margin"),
        placement_loadavg_warn_per_core=merged("placement_loadavg_warn_per_core"),
        placement_loadavg_max_per_core=merged("placement_loadavg_max_per_core"),
        placement_loadavg_penalty_weight=merged("placement_loadavg_penalty_weight"),
        placement_disk_contention_warn_share=merged("placement_disk_contention_warn_share"),
        placement_disk_contention_high_share=merged("placement_disk_contention_high_share"),
        placement_disk_penalty_weight=merged("placement_disk_penalty_weight"),
        placement_cpu_peak_warn_share=merged("placement_cpu_peak_warn_share"),
        placement_cpu_peak_high_share=merged("placement_cpu_peak_high_share"),
        placement_memory_peak_warn_share=merged("placement_memory_peak_warn_share"),
        placement_memory_peak_high_share=merged("placement_memory_peak_high_share"),
        placement_resource_weight_cpu=merged("placement_resource_weight_cpu"),
        placement_resource_weight_memory=merged("placement_resource_weight_memory"),
        placement_resource_weight_disk=merged("placement_resource_weight_disk"),
        scheduled_boot_batch_size=merged("scheduled_boot_batch_size"),
        scheduled_boot_batch_interval_seconds=merged("scheduled_boot_batch_interval_seconds"),
        scheduled_boot_lead_time_minutes=merged("scheduled_boot_lead_time_minutes"),
        window_grace_period_minutes=merged("window_grace_period_minutes"),
        practice_session_hours=merged("practice_session_hours"),
        practice_warning_minutes=merged("practice_warning_minutes"),
        expiry_warning_hours=merged("expiry_warning_hours"),
    )

    # 連線欄位與 pool / storage / gateway 的唯一真相來源是 proxmox_connections，
    # 這裡不再回寫預設連線；此 singleton 僅在尚無任何連線時作為相容退路。
    invalidate_proxmox_client()

    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.proxmox_config_update,
        details=f"Updated Proxmox config fields: {', '.join(sorted(provided)) or '(none)'}",
    )

    return _to_public(config, is_configured=True)


@router.post("/preview", response_model=ClusterPreviewResult)
def preview_cluster(
    session: SessionDep,
    current_user: AdminUser,
    config_in: ProxmoxConfigUpdate,
) -> ClusterPreviewResult:
    """
    用表單內容臨時連線，偵測叢集節點。不儲存任何資料。
    前端在儲存前呼叫此 endpoint，根據回傳決定是否顯示確認 popup。
    """
    # schema 已改為部分更新用（host/user 可缺省），preview 要實際連線必須有值
    if not config_in.host or not config_in.user:
        raise HTTPException(
            status_code=400, detail="host and user are required for preview"
        )
    try:
        password, ssl_param = _resolve_credentials(session, config_in)

        # 若有 CA cert，先驗證再連線
        if isinstance(ssl_param, str):  # ca_cert PEM
            _verify_server_with_ca(config_in.host, ssl_param)
            verify_ssl: bool | str = False
        else:
            verify_ssl = ssl_param

        raw_nodes = fetch_cluster_nodes(
            host=config_in.host,
            user=config_in.user,
            password=password,
            verify_ssl=verify_ssl,
            timeout=config_in.api_timeout,
        )

        nodes = [
            ProxmoxNodePublic(
                name=n["name"],
                host=n["host"],
                port=n.get("port", 8006),
                is_primary=n.get("is_primary", False),
                is_online=True,
            )
            for n in raw_nodes
        ]
        return ClusterPreviewResult(
            success=True,
            is_cluster=len(nodes) > 1,
            nodes=nodes,
        )
    except Exception as e:
        logger.warning(f"Cluster preview failed: {e}")
        return ClusterPreviewResult(
            success=False,
            is_cluster=False,
            nodes=[],
            error="Cluster preview failed",
        )


@router.post("/sync-nodes", response_model=list[ProxmoxNodePublic])
def sync_nodes(
    session: SessionDep,
    current_user: AdminUser,
    nodes: list[ProxmoxNodePublic],
) -> list[ProxmoxNodePublic]:
    """
    將前端確認過的節點清單寫入資料庫。
    先清除舊節點再寫入新節點。
    """
    node_dicts = [
        {
            "name": n.name,
            "host": n.host,
            "port": n.port,
            "is_primary": n.is_primary,
        }
        for n in nodes
    ]
    # 多連線下此舊端點只作用於預設連線，避免誤刪其他連線的節點
    default_conn = proxmox_connection_repo.get_default_connection(session)
    saved = proxmox_node_repo.upsert_nodes(
        session,
        node_dicts,
        connection_id=default_conn.id if default_conn else None,
    )

    invalidate_proxmox_client()

    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.proxmox_sync_nodes,
        details=(
            f"Synced {len(saved)} cluster nodes: "
            + ", ".join(n.name for n in saved)
        ),
    )

    return [_node_to_public(n) for n in saved]


@router.get("/nodes", response_model=list[ProxmoxNodePublic])
def get_nodes(session: SessionDep, current_user: AdminUser) -> list[ProxmoxNodePublic]:
    """取得所有已儲存的叢集節點清單。"""
    nodes = proxmox_node_repo.get_all_nodes(session)
    return [_node_to_public(n) for n in nodes]


@router.post("/check-nodes", response_model=list[ProxmoxNodePublic])
def check_nodes(session: SessionDep, current_user: AdminUser) -> list[ProxmoxNodePublic]:
    """
    對所有已儲存的節點做 TCP ping 健康檢查，更新 is_online 狀態後回傳最新清單。
    前端開啟 Proxmox 設定頁面時呼叫。
    """
    nodes = proxmox_node_repo.get_all_nodes(session)
    for node in nodes:
        is_online = _tcp_ping(node.host, node.port)
        if node.id is not None:
            proxmox_node_repo.update_node_status(session, node.id, is_online)

    # 重新讀取以取得更新後的 last_checked
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
    if conn_in.ca_cert:
        try:
            x509.load_pem_x509_certificate(
                conn_in.ca_cert.encode(), default_backend()
            )
        except Exception:
            raise BadRequestError(t("proxmoxConfig.invalidCaCert"))

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
    """更新一組 PVE 連線設定。"""
    if conn_in.ca_cert:
        try:
            x509.load_pem_x509_certificate(
                conn_in.ca_cert.encode(), default_backend()
            )
        except Exception:
            raise BadRequestError(t("proxmoxConfig.invalidCaCert"))

    conn = proxmox_connection_repo.update_connection(
        session,
        connection_id,
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
        is_default=conn_in.is_default,
    )
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    invalidate_proxmox_client()
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.proxmox_config_update,
        details=f"Updated Proxmox connection: {conn.name} ({conn.host})",
    )
    return _connection_to_public(session, conn)


@router.delete("/connections/{connection_id}")
def delete_connection(
    connection_id: int, session: SessionDep, current_user: AdminUser
) -> dict:
    """刪除一組 PVE 連線（其節點與 Storage 記錄一併移除）。"""
    conn = proxmox_connection_repo.get_connection(session, connection_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    others = [
        c for c in proxmox_connection_repo.get_all_connections(session)
        if c.id != connection_id
    ]
    if conn.is_default and others:
        raise BadRequestError(t("proxmoxConfig.cannotDeleteDefault"))

    # 先清掉該連線的節點對應 Storage 記錄，再刪節點與連線
    node_names = {
        n.name for n in proxmox_node_repo.get_all_nodes(
            session, connection_id=connection_id
        )
    }
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
        details=f"Deleted Proxmox connection: {conn.name} ({conn.host})",
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
        from proxmoxer import ProxmoxAPI

        password = proxmox_connection_repo.get_decrypted_password(conn)
        if conn.ca_cert:
            _verify_server_with_ca(conn.host, conn.ca_cert)
            verify_ssl: bool = False
        else:
            verify_ssl = conn.verify_ssl

        client = ProxmoxAPI(
            conn.host,
            port=conn.port,
            user=conn.user,
            password=password,
            verify_ssl=verify_ssl,
            timeout=conn.api_timeout,
        )
        nodes = client.nodes.get()
        node_names = [n.get("node", "") for n in nodes]
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
        saved_nodes, storage_count = _sync_one_connection(session, conn)
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
    尚未建立任何連線資料時，退回 proxmox_config 單連線行為。
    """
    connections = proxmox_connection_repo.get_all_connections(
        session, enabled_only=True
    )

    if not connections:
        # 舊版單連線相容：以 proxmox_config 建立暫時性的連線物件同步
        config = proxmox_config_repo.get_proxmox_config(session)
        if config is None:
            return SyncNowResult(
                success=False, nodes=[], storage_count=0,
                error=t("proxmoxConfig.notConfigured"),
            )
        from app.models.proxmox_connection import ProxmoxConnection

        connections = [
            ProxmoxConnection(
                id=None,
                name=config.host,
                host=config.host,
                port=8006,
                user=config.user,
                encrypted_password=config.encrypted_password,
                verify_ssl=config.verify_ssl,
                ca_cert=config.ca_cert,
                api_timeout=config.api_timeout,
            )
        ]

    all_nodes: list = []
    total_storages = 0
    errors: list[str] = []
    for conn in connections:
        try:
            saved_nodes, storage_count = _sync_one_connection(session, conn)
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
        digest = hashlib.sha256(cert.public_bytes(encoding=Encoding.DER)).digest()
        fingerprint = ":".join(f"{b:02X}" for b in digest)
        return CertParseResult(
            valid=True,
            fingerprint=fingerprint,
            subject=cert.subject.rfc4514_string(),
            issuer=cert.issuer.rfc4514_string(),
            not_before=cert.not_valid_before_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
            not_after=cert.not_valid_after_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        )
    except Exception as e:
        logger.warning(f"Certificate parse failed: {e}")
        return CertParseResult(valid=False, error="Invalid certificate")


@router.post("/test", response_model=ProxmoxConnectionTestResult)
def test_proxmox_connection(
    session: SessionDep, current_user: AdminUser
) -> ProxmoxConnectionTestResult:
    """測試目前設定的 Proxmox 連線"""
    config = proxmox_config_repo.get_proxmox_config(session)
    if config is None:
        return ProxmoxConnectionTestResult(
            success=False, message=t("proxmoxConfig.notConfigured")
        )

    try:
        from proxmoxer import ProxmoxAPI

        password = proxmox_config_repo.get_decrypted_password(config)

        if config.ca_cert:
            _verify_server_with_ca(config.host, config.ca_cert)
            verify_ssl: bool = False
        else:
            verify_ssl = config.verify_ssl

        client = ProxmoxAPI(
            config.host,
            user=config.user,
            password=password,
            verify_ssl=verify_ssl,
            timeout=config.api_timeout,
        )
        nodes = client.nodes.get()
        node_names = [n.get("node", "") for n in nodes]
        return ProxmoxConnectionTestResult(
            success=True,
            message=t("proxmoxConfig.connectionSuccess", nodes=", ".join(node_names)),
        )
    except Exception as e:
        logger.warning(f"Proxmox connection test failed: {e}")
        return ProxmoxConnectionTestResult(
            success=False, message=t("proxmoxConfig.connectionFailed")
        )
