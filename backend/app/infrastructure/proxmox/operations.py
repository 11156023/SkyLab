"""Centralized Proxmox VE API operations.

Provides a single place for common PVE interactions (resource lookup, config,
control, resize, specs, session ticket, etc.) so that callers no longer
duplicate the same cluster.resources iteration or qemu/lxc dispatch logic.
"""

import ipaddress
import logging
import re
import ssl
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from sqlalchemy import text

from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.infrastructure.proxmox import (
    ProxmoxSettings,
    basic_blocking_task_status,
    build_ws_ssl_context,
    get_active_host,
    get_connection_id_for_node,
    get_proxmox_api,
    get_proxmox_api_for_node,
    get_proxmox_settings,
    get_proxmox_settings_for_node,
    list_enabled_connection_ids,
)

logger = logging.getLogger(__name__)

ResourceType = Literal["qemu", "lxc"]


class ProxmoxConnectionUnavailableError(ProxmoxError):
    """找不到機器，但有 PVE 連線列不出資源：無法判定機器是否還在。

    ``find_resource(strict=True)`` 專用。呼叫端（排程器）把它當成「略過這一輪、
    等連線恢復再判斷」，與 VMID 重複等其他 ``ProxmoxError`` 區分開來。
    """

# ``cluster.nextid`` is a hint, not a reservation.  Every backend worker can
# observe the same hint before the first worker's clone is visible in PVE, so
# the lock must live outside the Python process.  PostgreSQL advisory locks
# give us that cross-worker boundary without adding a migration or a stale
# lease table.  The thread lock is still useful for SQLite-based tests and
# keeps same-process callers from opening needless DB connections.
_VMID_ALLOCATION_LOCK_KEY = 0x534B594C4142564  # stable 64-bit PostgreSQL key
_vmid_allocation_thread_lock = threading.Lock()


@contextmanager
def vmid_allocation_lock(*, db_engine: Any | None = None) -> Iterator[None]:
    """Serialize VMID selection through the first PVE clone/create call.

    ``next_vmid()`` only inspects PVE and therefore must be called inside this
    context, which must remain held until the mutating PVE request returns.

    The lock is transaction-level (``pg_advisory_xact_lock``): SQLAlchemy opens
    a transaction on the first execute and rolls it back when the connection
    context exits, so the lock lives exactly as long as this context.  A
    session-level lock would be unsafe behind PgBouncer's transaction pooling
    (an unreleased lock would stay on the pooled server connection).  PostgreSQL
    also releases it if the worker dies; the SQLite/no-database path retains
    the process lock for unit-test callers.
    """
    with _vmid_allocation_thread_lock:
        if db_engine is None:
            from app.core.db import engine as resolved_engine
        else:
            resolved_engine = db_engine

        if resolved_engine.dialect.name != "postgresql":
            yield
            return

        connection_context = (
            resolved_engine.connect()
            if hasattr(resolved_engine, "connect")
            else nullcontext(resolved_engine)
        )
        with connection_context as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": _VMID_ALLOCATION_LOCK_KEY},
            )
            try:
                yield
            finally:
                try:
                    connection.rollback()
                except Exception:
                    # Closing the connection ends the transaction and releases
                    # the xact-level lock; do not mask the provisioning exception.
                    logger.warning(
                        "Failed to explicitly release VMID allocation lock",
                        exc_info=True,
                    )


@dataclass(frozen=True)
class MonitoringSnapshot:
    """同一輪監控取樣的節點、資源與連線完成度。"""

    nodes: list[dict[str, Any]]
    resources: list[dict[str, Any]]
    failed_connections: int
    total_connections: int


def _connection_keys() -> list[int | None]:
    """回傳要彙總的連線 key 清單；尚未建立連線資料時退回單連線行為。"""
    connection_ids = list_enabled_connection_ids()
    if connection_ids:
        return list(connection_ids)
    return [None]


def iter_connection_clients():
    """Yield (connection_key, client) for every enabled connection.

    連不上的連線記 warning 後略過；全部失敗時 yield 不出任何項目，
    由呼叫端決定要視為空結果或錯誤。
    """
    for key in _connection_keys():
        try:
            yield key, get_proxmox_api(key)
        except Exception as exc:
            logger.warning(
                "Skipping unavailable Proxmox connection %s: %s", key, exc
            )


# ---------------------------------------------------------------------------
# Resource lookup
# ---------------------------------------------------------------------------

def _gather_per_connection(
    fetch: Callable[[Any], Iterable[dict]], *, what: str
) -> list[tuple[int | None, list[dict]]]:
    """對每個連線呼叫 ``fetch(client)``，回傳 (connection_key, 結果) 清單。

    單一連線失敗只記 warning 後略過；每個連線都失敗時才拋 ``ProxmoxError``。
    ``what`` 只用於 log 文字。
    """
    results: list[tuple[int | None, list[dict]]] = []
    errors: list[str] = []
    keys = _connection_keys()
    for key in keys:
        try:
            results.append((key, list(fetch(get_proxmox_api(key)))))
        except Exception as exc:
            errors.append(str(exc))
            logger.warning(
                "Failed to list %s for Proxmox connection %s: %s", what, key, exc
            )
    if errors and not results and len(errors) == len(keys):
        raise ProxmoxError(f"All Proxmox connections are unavailable. {errors[0]}")
    return results


# /cluster/resources 短暫快取：逐台迴圈（排程 tick、清單頁、拓撲）原本每台都重抓
# 整份清單，N 台就是 N×C 次。PVE 自己的 status 也是 pvestatd 約 10 秒更新一次，
# 幾秒的快取不會讓狀態明顯變舊。剛建立的機器由 find_resource 找不到時強制重抓，
# next_vmid 一律重抓，不會因快取漏看。
_CLUSTER_RESOURCES_TTL_SECONDS = 3.0
# 抓取期間一直持有：同時進來的呼叫排隊等同一份結果（single-flight）
_cluster_resources_lock = threading.Lock()


class _ClusterResourcesCache:
    refreshed_at: float = 0.0
    listed: list[tuple[int | None, list[dict]]] = []


def invalidate_cluster_resources_cache() -> None:
    with _cluster_resources_lock:
        _ClusterResourcesCache.refreshed_at = 0.0
        _ClusterResourcesCache.listed = []


def _raw_vms_by_connection(
    *, fresh: bool = False
) -> list[tuple[int | None, list[dict]]]:
    """Return (connection_key, resources) for every connection, without pool filtering.

    ``fresh=True`` 略過快取（需要確定看到最新機器清單的呼叫端用）。回傳的條目
    都是複本，呼叫端可以自由修改。
    """
    with _cluster_resources_lock:
        age = time.monotonic() - _ClusterResourcesCache.refreshed_at
        if fresh or age >= _CLUSTER_RESOURCES_TTL_SECONDS:
            _ClusterResourcesCache.listed = _gather_per_connection(
                lambda proxmox: proxmox.cluster.resources.get(type="vm"),
                what="resources",
            )
            _ClusterResourcesCache.refreshed_at = time.monotonic()
        listed = _ClusterResourcesCache.listed
    return [(key, [dict(vm) for vm in vms]) for key, vms in listed]


def list_connection_vms(connection_id: int | None) -> list[dict]:
    """單一連線的 ``/cluster/resources?type=vm``（不論啟用與否、不套 pool 篩選）。

    錯誤直接往上拋，由呼叫端決定如何處理。
    """
    return list(get_proxmox_api(connection_id).cluster.resources.get(type="vm"))


def find_vmid_on_connections(
    vmid: int, connection_ids: Iterable[int | None]
) -> tuple[int | None, dict] | None:
    """逐一詢問給定的連線（不套 pool 篩選），回傳第一個 vmid 相符的 (連線, 條目)。

    用於「確認機器真的不在任何 PVE 上」：呼叫端應傳入所有連線（含停用中的）。
    本模組沒有 DB session，因此連線清單由呼叫端提供。任何一個連線列不出清單
    都丟 ``ProxmoxError``（訊息含連線 id），因為那條連線上可能還有這台機器；
    vmid 不是整數的條目略過。
    """
    for connection_id in connection_ids:
        try:
            vms = list_connection_vms(connection_id)
        except Exception as exc:
            raise ProxmoxError(
                f"Proxmox connection {connection_id} could not be listed: {exc}"
            ) from exc
        for vm in vms:
            try:
                matched = int(vm.get("vmid")) == vmid
            except (TypeError, ValueError):
                continue
            if matched:
                return connection_id, vm
    return None


def _raw_vms(*, fresh: bool = False) -> list[dict]:
    """Return all resources of type vm across all connections, without pool filtering."""
    return [vm for _key, vms in _raw_vms_by_connection(fresh=fresh) for vm in vms]


def _in_own_pool(connection_key: int | None, vms: Iterable[dict]) -> list[dict]:
    """只留下該連線自己 pool 內的條目（pool 名稱是每個連線各自的設定）。"""
    pool = get_proxmox_settings(connection_key).pool_name
    return [vm for vm in vms if vm.get("pool") == pool]


def _pool_vms(*, fresh: bool = False) -> list[dict]:
    """Return vm resources inside each connection's own pool.

    pool 名稱是每個連線（叢集）自己的設定，因此比對必須逐連線進行，
    不能用單一 pool 名稱去篩全部連線的資源。
    """
    matched: list[dict] = []
    for key, vms in _raw_vms_by_connection(fresh=fresh):
        matched.extend(_in_own_pool(key, vms))
    return matched


def _single_match(matches: list[dict], vmid: int) -> dict | None:
    """多連線下同一 VMID 出現在多個叢集時不能猜：回第一筆可能操作到別人的機器。"""
    if len(matches) > 1:
        nodes = ", ".join(str(r.get("node")) for r in matches)
        raise ProxmoxError(
            f"VMID {vmid} exists on multiple Proxmox connections ({nodes}); "
            "refusing to guess which one is meant."
        )
    return matches[0] if matches else None


def find_resource(vmid: int, *, strict: bool = False) -> dict:
    """Find any resource (qemu or lxc) by VMID in its connection's pool.

    預設（``strict=False``）會略過連不上的連線，只要還有一個連線答得出來，
    那些連線上的機器就會被當成 ``NotFoundError``。

    ``strict=True`` 給「NotFound 代表機器被刪了」的呼叫端（例如排程器）：
    找不到且有連線列不出清單（含全部連線都失敗）時改丟
    ``ProxmoxConnectionUnavailableError``，只有每個連線都列得到才回
    ``NotFoundError``。同一 VMID 出現在多個連線時兩種模式都丟一般的
    ``ProxmoxError``（不是連線問題，重試也不會好）。
    """
    if not strict:
        found = _single_match([r for r in _pool_vms() if r["vmid"] == vmid], vmid)
        if found is None:
            # 快取裡沒有可能只是剛建立：重抓一次再下結論
            found = _single_match(
                [r for r in _pool_vms(fresh=True) if r["vmid"] == vmid], vmid
            )
        if found is None:
            raise NotFoundError(f"Resource {vmid} not found")
        return found

    connection_keys = _connection_keys()

    def _match(listed: list[tuple[int | None, list[dict]]]) -> dict | None:
        return _single_match(
            [
                vm
                for key, vms in listed
                for vm in _in_own_pool(key, vms)
                if vm.get("vmid") == vmid
            ],
            vmid,
        )

    def _list(*, fresh: bool) -> list[tuple[int | None, list[dict]]]:
        try:
            return _raw_vms_by_connection(fresh=fresh)
        except ProxmoxError as exc:  # 全部連線都失敗
            raise ProxmoxConnectionUnavailableError(str(exc)) from exc

    listed = _list(fresh=False)
    found = _match(listed)
    if found is None:
        # 「找不到」會被當成機器已刪除，一定要以最新清單判斷
        listed = _list(fresh=True)
        found = _match(listed)
    if found is not None:
        return found
    listed_keys = {key for key, _vms in listed}
    unreachable = [key for key in connection_keys if key not in listed_keys]
    if unreachable:
        raise ProxmoxConnectionUnavailableError(
            f"Resource {vmid} not found while Proxmox connection(s) "
            f"{unreachable} are unavailable"
        )
    raise NotFoundError(f"Resource {vmid} not found")


def find_lxc(vmid: int) -> dict:
    """Find an LXC container by VMID in its connection's pool."""
    def _match(vms: list[dict]) -> dict | None:
        return _single_match(
            [r for r in vms if r["vmid"] == vmid and r["type"] == "lxc"], vmid
        )

    found = _match(_pool_vms())
    if found is None:
        found = _match(_pool_vms(fresh=True))
    if found is None:
        raise NotFoundError(f"LXC container {vmid} not found")
    return found


def list_all_resources() -> list[dict]:
    """Return all cluster resources of type vm in each connection's pool."""
    return _pool_vms()


def list_all_resources_by_vmid() -> dict[int, dict]:
    """vmid → cluster/resources 條目（單次 PVE 呼叫；治理／反挖礦掃描共用）。"""
    return {
        int(r["vmid"]): r
        for r in list_all_resources()
        if r.get("vmid") is not None
    }


def list_nodes() -> list[dict]:
    """Return all nodes across all connections."""
    return [
        node
        for _key, nodes in _gather_per_connection(
            lambda proxmox: proxmox.nodes.get(), what="nodes"
        )
        for node in nodes
    ]


def collect_monitoring_snapshot() -> MonitoringSnapshot:
    """以每個連線一次取回 nodes/resources，供監控讀模型使用。

    ``list_nodes`` 與 ``list_all_resources`` 各自查詢時，可能在兩次呼叫間
    得到不同的連線可用性；監控需要知道這一輪是否只拿到部分叢集資料，
    因此在同一個 connection client 上完成兩項取樣並保留失敗數。
    """
    nodes: list[dict[str, Any]] = []
    resources: list[dict[str, Any]] = []
    failures: list[str] = []
    keys = _connection_keys()

    for key in keys:
        try:
            proxmox = get_proxmox_api(key)
            nodes.extend(proxmox.nodes.get())
            raw_resources = list(proxmox.cluster.resources.get(type="vm"))
            pool_name = get_proxmox_settings(key).pool_name
            resources.extend(
                resource
                for resource in raw_resources
                if resource.get("pool") == pool_name
            )
        except Exception as exc:
            failures.append(str(exc))
            logger.warning(
                "Failed to collect monitoring snapshot for Proxmox connection %s: %s",
                key,
                exc,
            )

    if failures and not nodes and not resources and len(failures) == len(keys):
        raise ProxmoxError(
            f"All Proxmox connections are unavailable. {failures[0]}"
        )

    return MonitoringSnapshot(
        nodes=nodes,
        resources=resources,
        failed_connections=len(failures),
        total_connections=len(keys),
    )


def admin_disabled_node_names() -> set[str]:
    """讀取被管理員停用的節點名稱；DB 讀取失敗時不過濾（fail-open）。

    placement advisor 與節點挑選共用同一份判斷。
    """
    try:
        from sqlmodel import Session

        from app.core.db import engine
        from app.repositories.proxmox_node import get_disabled_node_names

        with Session(engine) as session:
            return get_disabled_node_names(session)
    except Exception:
        return set()


def get_available_nodes() -> list[dict]:
    """Return online nodes first, or all nodes if status data is unavailable.

    管理員停用的節點一律排除（停用＝不接收新 VM）。
    """
    disabled = admin_disabled_node_names()
    nodes = [
        node for node in list_nodes()
        if str(node.get("node") or node.get("name") or "") not in disabled
    ]
    online_nodes = [node for node in nodes if node.get("status") == "online"]
    return online_nodes or nodes


def _default_node_candidates() -> list[str]:
    """各連線自己設定的預設節點，預設連線優先。"""
    candidates: list[str] = []
    for key in _connection_keys():
        try:
            default_node = get_proxmox_settings(key).default_node
        except Exception as exc:
            logger.warning(
                "Unable to read default node for Proxmox connection %s: %s", key, exc
            )
            continue
        if default_node and default_node not in candidates:
            candidates.append(default_node)
    return candidates


def pick_target_node(preferred_node: str | None = None) -> str:
    """Pick a usable target node, preferring an explicitly requested one.

    Priority: preferred_node > 各連線的 default_node（預設連線優先） > nodes[0]
    """
    nodes = get_available_nodes()
    if not nodes:
        raise ProxmoxError("No Proxmox nodes are available")

    candidates = [preferred_node] if preferred_node else _default_node_candidates()
    for candidate in candidates:
        for node in nodes:
            node_name = node.get("node") or node.get("name")
            if node_name == candidate:
                return node_name
    if candidates:
        logger.warning(
            "Preferred node(s) %s not found or offline; falling back to first available node",
            ", ".join(candidates),
        )

    selected = nodes[0].get("node") or nodes[0].get("name")
    if not selected:
        raise ProxmoxError("No usable Proxmox node name was returned")
    return selected


def list_node_storages(node: str) -> list[dict]:
    """Return storages visible on a node."""
    proxmox = get_proxmox_api_for_node(node)
    return proxmox.nodes(node).storage.get()


def _storage_name(storage: dict) -> str | None:
    return storage.get("storage") or storage.get("id")


def _storage_is_enabled(storage: dict) -> bool:
    enabled = storage.get("enabled")
    if enabled is None:
        return storage.get("disable") not in (1, "1", True, "true")
    return enabled not in (0, "0", False, "false")


def _storage_is_active(storage: dict) -> bool:
    active = storage.get("active")
    if active is None:
        return storage.get("status") != "disabled"
    return active not in (0, "0", False, "false")


def _storage_supports_content(storage: dict, required_content: str) -> bool:
    content = storage.get("content")
    if not content:
        return True
    supported = {part.strip() for part in str(content).split(",") if part.strip()}
    return required_content in supported


def resolve_target_storage(
    node: str,
    requested_storage: str | None,
    *,
    required_content: Literal["images", "rootdir"],
) -> str:
    """Pick a usable storage on a node, falling back when the requested one is unavailable."""
    storages = list_node_storages(node)
    compatible = [
        storage
        for storage in storages
        if _storage_is_enabled(storage)
        and _storage_is_active(storage)
        and _storage_supports_content(storage, required_content)
    ]

    if requested_storage:
        for storage in compatible:
            if _storage_name(storage) == requested_storage:
                return requested_storage

        logger.warning(
            "Storage %s is unavailable on node %s for content %s; attempting fallback",
            requested_storage,
            node,
            required_content,
        )

    if compatible:
        fallback = _storage_name(compatible[0])
        if fallback:
            return fallback

    available_names = [
        name
        for storage in storages
        if (name := _storage_name(storage))
    ]
    raise BadRequestError(
        "No enabled Proxmox storage is available on "
        f"node '{node}' for content '{required_content}'. "
        f"Configured/requested storage: '{requested_storage or get_proxmox_settings_for_node(node).data_storage}'. "
        f"Node storages: {', '.join(available_names) if available_names else 'none'}."
    )


def find_vm_template(template_id: int) -> dict:
    """Find a VM template by VMID in its connection's pool."""
    for vm in _pool_vms():
        if vm["vmid"] == template_id and vm.get("template") == 1:
            return vm
    raise NotFoundError(f"VM template {template_id} not found")


# ---------------------------------------------------------------------------
# Node helper — dispatches qemu / lxc transparently
# ---------------------------------------------------------------------------

def _resource_api(node: str, vmid: int, resource_type: ResourceType):
    """Return the proxmoxer node resource handle (qemu or lxc)."""
    proxmox = get_proxmox_api_for_node(node)
    if resource_type == "qemu":
        return proxmox.nodes(node).qemu(vmid)
    return proxmox.nodes(node).lxc(vmid)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def get_config(
    node: str, vmid: int, resource_type: ResourceType, *, current: bool = False
) -> dict:
    """GET /nodes/{node}/{type}/{vmid}/config

    預設回傳的是「含 pending 的設定」：執行中的機器若有尚未生效的變更
    （例如改了 cores 但還沒重開機），拿到的會是那個尚未生效的值。
    ``current=True`` 改要實際生效中的值。
    """
    api = _resource_api(node, vmid, resource_type).config
    return api.get(current=1) if current else api.get()


def update_config(
    node: str, vmid: int, resource_type: ResourceType, **params
) -> None:
    """PUT /nodes/{node}/{type}/{vmid}/config"""
    _resource_api(node, vmid, resource_type).config.put(**params)


def list_storage_content(
    node: str, storage: str, content: str | None = None
) -> list[dict]:
    """GET /nodes/{node}/storage/{storage}/content（可用 content=iso 過濾）"""
    api = get_proxmox_api_for_node(node).nodes(node).storage(storage).content
    items = api.get(content=content) if content else api.get()
    return list(items or [])


def list_iso_images(node: str) -> list[dict]:
    """列出該節點 ISO 儲存區上的 ISO 映像（儲存區取自該節點所屬連線的設定）。"""
    iso_storage = get_proxmox_settings_for_node(node).iso_storage
    if not iso_storage:
        return []
    return [
        item
        for item in list_storage_content(node, iso_storage, "iso")
        if str(item.get("content") or "iso") == "iso"
    ]


# ---------------------------------------------------------------------------
# Control (start / stop / reboot / shutdown / reset)
# ---------------------------------------------------------------------------

# 電源動作 → 該資源「應該」處於的開機狀態；onboot 跟著它走，不開放使用者自行設定：
# 主機重開後只把本來就該開著的機器拉起來，關掉／暫停的機器不會偷偷復活。
_ONBOOT_BY_ACTION: dict[str, int] = {
    "start": 1,
    "resume": 1,
    "reboot": 1,
    "reset": 1,
    "stop": 0,
    "shutdown": 0,
    "suspend": 0,
}


def sync_onboot(
    node: str, vmid: int, resource_type: ResourceType, action: str
) -> None:
    """依電源動作把 guest config 的 ``onboot`` 對齊到應有狀態（best-effort）。

    寫入失敗（例如 guest 被 backup/snapshot 鎖住）只記 warning，不能讓
    已成功送出的電源動作跟著報錯；下一次開關機會再對齊一次。
    """
    onboot = _ONBOOT_BY_ACTION.get(action)
    if onboot is None:
        return
    try:
        update_config(node, vmid, resource_type, onboot=onboot)
    except Exception as exc:
        logger.warning(
            "Failed to sync onboot=%s for %s %s on %s after %s: %s",
            onboot, resource_type, vmid, node, action, exc,
        )


def control(
    node: str,
    vmid: int,
    resource_type: ResourceType,
    action: str,
    *,
    wait_timeout_seconds: float | None = None,
) -> None:
    """Execute a power action on a resource.

    ``wait_timeout_seconds`` 有值時阻塞等待 PVE 任務結束：任務失敗拋
    ``ProxmoxError``（訊息含 task log tail，可辨識 vGPU 開機失敗等原因），
    逾時拋 ``TimeoutError``（任務在 PVE 端照跑）。預設 fire-and-forget。

    電源動作送出成功後會順手把 ``onboot`` 對齊（start/resume/reboot/reset → 1，
    stop/shutdown/suspend → 0），見 :func:`sync_onboot`。
    """
    upid = getattr(_resource_api(node, vmid, resource_type).status, action).post()
    if wait_timeout_seconds is not None and upid:
        basic_blocking_task_status(
            node, str(upid), timeout_seconds=wait_timeout_seconds
        )
    sync_onboot(node, vmid, resource_type, action)


def get_status(node: str, vmid: int, resource_type: ResourceType) -> dict:
    """GET /nodes/{node}/{type}/{vmid}/status/current"""
    return _resource_api(node, vmid, resource_type).status.current.get()


# 開機類 task 跑完前 QEMU 的 QMP 可能還沒就緒（GPU 直通機要先配置並鎖定整段
# 記憶體），這時開 vncproxy 會 ``set_password`` 逾時；cluster/resources 卻早已
# 回報 running，所以要另外看節點上進行中的 task。
BOOT_TASK_TYPES = frozenset({"qmstart", "qmreboot", "vzstart", "vzreboot"})


def list_booting_vmids(nodes: Iterable[str]) -> set[int]:
    """回傳指定節點上開機 task 仍在進行中的 VMID。

    查的是各節點自己的 ``tasks?source=active``（權威、即時），而不是
    ``cluster/tasks``（經 pmxcfs 同步，剛開機的前幾秒可能還沒出現）。
    查不到的節點記 warning 後略過，視為沒有開機中的機器。
    """
    booting: set[int] = set()
    for node in sorted({n for n in nodes if n}):
        try:
            tasks = get_proxmox_api_for_node(node).nodes(node).tasks.get(
                source="active"
            )
        except Exception as exc:
            logger.warning("Failed to list active tasks on node %s: %s", node, exc)
            continue
        for task in tasks or []:
            if task.get("type") not in BOOT_TASK_TYPES:
                continue
            try:
                booting.add(int(task.get("id")))
            except (TypeError, ValueError):
                continue
    return booting


# ---------------------------------------------------------------------------
# Disk resize
# ---------------------------------------------------------------------------

def resize_disk(
    node: str,
    vmid: int,
    resource_type: ResourceType,
    disk: str,
    size: str,
) -> None:
    """PUT /nodes/{node}/{type}/{vmid}/resize"""
    _resource_api(node, vmid, resource_type).resize.put(disk=disk, size=size)


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

# PVE 快照名稱格式（pve-configid，2～40 字）。名稱會被 proxmoxer 當成 URL 路徑
# 片段，requests/urllib3 又會消去 dot segment：不先驗證的話 snapname=".." 會讓
# DELETE .../snapshot/.. 變成 DELETE /nodes/{node}/{type}/{vmid}，直接刪掉整台機器。
_SNAPNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,39}$")


def _validate_snapname(snapname: object) -> None:
    if not isinstance(snapname, str) or not _SNAPNAME_RE.fullmatch(snapname):
        raise BadRequestError(f"Invalid snapshot name: {snapname!r}")


def has_snapshot_feature(node: str, vmid: int, resource_type: ResourceType) -> bool:
    """GET /nodes/{node}/{type}/{vmid}/feature?feature=snapshot

    PVE 依這台機器「目前」所有磁碟（VM 的 scsi/virtio/efidisk/tpmstate…、
    LXC 的 rootfs/mp*）所在 storage 與格式判斷能否做快照，和它自己在建立
    快照前做的檢查是同一套；例如 LVM（非 thin）、目錄型 storage 上的 raw
    磁碟、已轉成範本的機器都會回 hasFeature=0。VM 與 LXC 都有這支端點。
    """
    result = _resource_api(node, vmid, resource_type).feature.get(feature="snapshot")
    if not isinstance(result, dict):
        return False
    return bool(result.get("hasFeature"))


def list_snapshots(node: str, vmid: int, resource_type: ResourceType) -> list:
    return _resource_api(node, vmid, resource_type).snapshot.get()


def create_snapshot(
    node: str,
    vmid: int,
    resource_type: ResourceType,
    wait_timeout_seconds: float | None = None,
    **params,
) -> str:
    _validate_snapname(params.get("snapname"))
    task = _resource_api(node, vmid, resource_type).snapshot.post(**params)
    basic_blocking_task_status(node, task, timeout_seconds=wait_timeout_seconds)
    return task


def delete_snapshot(
    node: str, vmid: int, resource_type: ResourceType, snapname: str
) -> str:
    _validate_snapname(snapname)
    task = _resource_api(node, vmid, resource_type).snapshot(snapname).delete()
    basic_blocking_task_status(node, task)
    return task


def rollback_snapshot(
    node: str, vmid: int, resource_type: ResourceType, snapname: str
) -> str:
    _validate_snapname(snapname)
    task = _resource_api(node, vmid, resource_type).snapshot(snapname).rollback.post()
    basic_blocking_task_status(node, task)
    return task


# ---------------------------------------------------------------------------
# Backups (vzdump)
# ---------------------------------------------------------------------------

BackupMode = Literal["snapshot", "suspend", "stop"]


def storage_accepts_backups(node: str, storage: str) -> bool:
    """這個節點上是否看得到名為 ``storage``、已啟用、在線且可放備份的 storage。"""
    for item in list_node_storages(node):
        if _storage_name(item) != storage:
            continue
        content = {
            part.strip() for part in str(item.get("content") or "").split(",")
        }
        return (
            _storage_is_enabled(item)
            and _storage_is_active(item)
            and "backup" in content
        )
    return False


def list_backups(node: str, storage: str, vmid: int) -> list[dict]:
    """GET /nodes/{node}/storage/{storage}/content?content=backup&vmid={vmid}

    回傳這個 storage 上屬於該 VMID 的**所有**備份（含機構自己排程的備份），
    每筆有 volid／ctime／size／format／subtype，另有選填的 notes／protected。
    哪些算「SkyLab 建的」由 service 層依 notes 標記判斷。
    """
    api = get_proxmox_api_for_node(node).nodes(node).storage(storage).content
    return list(api.get(content="backup", vmid=vmid) or [])


def create_backup(
    node: str,
    vmid: int,
    storage: str,
    *,
    mode: BackupMode,
    notes: str,
    wait_timeout_seconds: float | None = None,
) -> str:
    """POST /nodes/{node}/vzdump：對單一機器做一次備份並等它完成。

    - ``protected=1``：這份備份不參與 storage／PBS 的 prune。備份 storage 常與
      機構排程備份共用，不保護的話使用者的還原點會被排程的保留策略清掉，也會
      佔掉排程備份的保留名額。
    - ``remove=0``：這次備份不觸發 prune，不會順手刪掉同一台機器的其他備份。
    - ``notes-template`` 裡的反斜線與 ``{{…}}`` 會被 PVE 展開，呼叫端要先清掉。
    """
    params: dict[str, Any] = {
        "vmid": str(vmid),
        "storage": storage,
        "mode": mode,
        "compress": "zstd",
        "protected": 1,
        "remove": 0,
        "notes-template": notes,
    }
    task = get_proxmox_api_for_node(node).nodes(node).vzdump.post(**params)
    basic_blocking_task_status(node, task, timeout_seconds=wait_timeout_seconds)
    return task


def restore_backup(
    node: str,
    vmid: int,
    resource_type: ResourceType,
    volid: str,
    *,
    storage: str | None = None,
    unprivileged: bool | None = None,
    wait_timeout_seconds: float | None = None,
) -> str:
    """以備份覆蓋還原同一個 VMID（機器必須已關機）。

    - VM：POST /nodes/{node}/qemu，``archive`` + ``force=1``；不帶 storage，磁碟回到
      備份設定檔裡記的原 storage。
    - LXC：POST /nodes/{node}/lxc，``ostemplate`` + ``restore=1`` + ``force=1``。LXC
      還原不帶 storage 時 PVE 一律放到 ``local``，所以呼叫端要傳目前 rootfs 所在
      的 storage。``unprivileged`` 也要明確帶：非 root 的 API 帳號還原時 PVE 不會
      沿用備份裡的設定，不帶的話非特權容器可能被還原成特權容器。
    """
    proxmox = get_proxmox_api_for_node(node)
    try:
        if resource_type == "qemu":
            task = proxmox.nodes(node).qemu.post(vmid=vmid, archive=volid, force=1)
        else:
            params: dict[str, Any] = {
                "vmid": vmid,
                "ostemplate": volid,
                "restore": 1,
                "force": 1,
            }
            if storage:
                params["storage"] = storage
            if unprivileged is not None:
                params["unprivileged"] = 1 if unprivileged else 0
            task = proxmox.nodes(node).lxc.post(**params)
        basic_blocking_task_status(node, task, timeout_seconds=wait_timeout_seconds)
    finally:
        # 還原會重建機器：成功或半途失敗，快取的叢集清單都不可信
        invalidate_cluster_resources_cache()
    return task


def delete_backup(
    node: str,
    storage: str,
    volid: str,
    *,
    wait_timeout_seconds: float | None = 300.0,
) -> None:
    """DELETE /nodes/{node}/storage/{storage}/content/{volid}

    ``volid`` 會成為 URL 路徑的一部分，呼叫端只能傳 ``list_backups`` 回來的值，
    不可直接轉送使用者輸入。受保護的備份要先解除保護才刪得掉。
    """
    volume = get_proxmox_api_for_node(node).nodes(node).storage(storage).content(volid)
    try:
        volume.put(protected=0)
    except Exception:
        # 本來就沒保護、或 storage 不支援保護旗標：直接往下刪，刪不掉再報錯
        logger.debug("Could not clear protection on backup %s", volid, exc_info=True)
    result = volume.delete()
    if isinstance(result, str) and result.startswith("UPID:"):
        basic_blocking_task_status(node, result, timeout_seconds=wait_timeout_seconds)


# ---------------------------------------------------------------------------
# RRD stats
# ---------------------------------------------------------------------------

def get_rrd_data(
    node: str, vmid: int, resource_type: ResourceType, timeframe: str
) -> list[dict]:
    return _resource_api(node, vmid, resource_type).rrddata.get(timeframe=timeframe)


def get_node_rrd_data(node: str, timeframe: str) -> list[dict]:
    """GET /nodes/{node}/rrddata"""
    proxmox = get_proxmox_api_for_node(node)
    return proxmox.nodes(node).rrddata.get(timeframe=timeframe)


# ---------------------------------------------------------------------------
# Delete resource
# ---------------------------------------------------------------------------

def delete_resource(
    node: str, vmid: int, resource_type: ResourceType, **params
) -> str:
    task = _resource_api(node, vmid, resource_type).delete(**params)
    try:
        basic_blocking_task_status(node, task)
    finally:
        invalidate_cluster_resources_cache()
    return task


# ---------------------------------------------------------------------------
# IP address
# ---------------------------------------------------------------------------

def _is_usable_ipv4(ip: str) -> bool:
    """過濾 loopback、link-local、multicast 等不可用的 IPv4 位址。

    改用 ``ipaddress`` 實際解析：字串前綴比對擋不掉 ``0.0.0.1``、
    ``224.x``（multicast）、``240.x``（reserved）這類位址，也會把
    ``127.0.0.1/8`` 之外寫法不同的 loopback 漏掉。解析不了就當作不可用。
    """
    if not ip:
        return False
    try:
        addr = ipaddress.IPv4Address(ip.strip())
    except (ipaddress.AddressValueError, ValueError):
        return False
    return not (
        addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or addr.is_multicast
        or addr.is_reserved
    )


def get_ip_address(node: str, vmid: int, resource_type: ResourceType) -> str | None:
    """取得 VM 的 IP 位址，掃描全部網卡（跳過 loopback / link-local）。"""
    proxmox = get_proxmox_api_for_node(node)
    try:
        if resource_type == "lxc":
            interfaces = proxmox.nodes(node).lxc(vmid).interfaces.get()
            for iface in interfaces or []:
                if iface.get("name") == "lo":
                    continue
                inet = iface.get("inet")
                if inet:
                    ip = inet.split("/")[0]
                    if _is_usable_ipv4(ip):
                        return ip
        else:
            network_info = (
                proxmox.nodes(node)
                .qemu(vmid)("agent")("network-get-interfaces")
                .get()
            )
            if network_info and "result" in network_info:
                for iface in network_info["result"]:
                    if iface.get("name") == "lo":
                        continue
                    for ip_entry in iface.get("ip-addresses", []):
                        if ip_entry.get("ip-address-type") == "ipv4":
                            ip = ip_entry.get("ip-address", "")
                            if _is_usable_ipv4(ip):
                                return ip
    except Exception as e:
        logger.debug(f"Failed to get IP for VMID {vmid}: {e}")
    return None


# ---------------------------------------------------------------------------
# Current specs (parsed from config)
# ---------------------------------------------------------------------------

def get_current_specs(node: str, vmid: int, resource_type: ResourceType) -> dict:
    """Returns {"cpu": int|None, "memory": int|None, "disk": int|None}.

    讀實際生效值（current=1），規格調整申請的「目前規格」才不會抄到
    尚未生效的 pending 設定。
    """
    config = get_config(node, vmid, resource_type, current=True)

    current_cpu = config.get("cores") or config.get("cpus")
    current_memory = config.get("memory")
    current_disk = None

    if resource_type == "qemu":
        scsi0 = config.get("scsi0", "")
        if "size=" in scsi0:
            size_str = scsi0.split("size=")[1].split(",")[0].split(")")[0]
            if size_str.endswith("G"):
                current_disk = int(size_str[:-1])
    else:
        rootfs = config.get("rootfs", "")
        if "size=" in rootfs:
            size_str = rootfs.split("size=")[1].split(",")[0]
            if size_str.endswith("G"):
                current_disk = int(size_str[:-1])

    return {"cpu": current_cpu, "memory": current_memory, "disk": current_disk}


# ---------------------------------------------------------------------------
# LXC creation
# ---------------------------------------------------------------------------

def create_lxc(node: str, **config) -> str:
    """Create an LXC container and wait for the task to finish. Returns UPID."""
    proxmox = get_proxmox_api_for_node(node)
    task = proxmox.nodes(node).lxc.create(**config)
    try:
        basic_blocking_task_status(node, task)
    finally:
        invalidate_cluster_resources_cache()
    return task


# ---------------------------------------------------------------------------
# VM clone + configure
# ---------------------------------------------------------------------------

def clone_vm(node: str, template_id: int, **clone_config) -> str:
    """Clone a VM template and wait. Returns UPID."""
    proxmox = get_proxmox_api_for_node(node)
    task = proxmox.nodes(node).qemu(template_id).clone.post(**clone_config)
    try:
        basic_blocking_task_status(node, task)
    finally:
        invalidate_cluster_resources_cache()
    return task


def clone_lxc(node: str, template_id: int, **clone_config) -> str:
    """Clone an LXC template and wait. Returns UPID."""
    proxmox = get_proxmox_api_for_node(node)
    task = proxmox.nodes(node).lxc(template_id).clone.post(**clone_config)
    try:
        basic_blocking_task_status(node, task)
    finally:
        invalidate_cluster_resources_cache()
    return task


def convert_to_template(
    node: str, vmid: int, resource_type: ResourceType = "qemu"
) -> None:
    """POST /nodes/{node}/{type}/{vmid}/template — 轉為唯讀範本（不可逆）。

    VM 必須處於 stopped 狀態，呼叫端負責先關機。
    """
    _resource_api(node, vmid, resource_type).template.post()
    invalidate_cluster_resources_cache()


def _db_claimed_vmids() -> set[int]:
    """DB 已登記但 PVE 可能看不到的 VMID（資源列、IP 預留）。

    PVE 端機器被直接刪掉後 resources 列可能還在；併發 plan 也會先在
    ip_allocation 預留 VMID 才去 PVE 建機。nextid 看不到這些，撞到就會在
    PVE 建好機器之後才因主鍵衝突失敗。
    """
    from sqlmodel import Session, select

    from app.core.db import engine
    from app.models import IpAllocation, Resource

    with Session(engine) as session:
        claimed = set(session.exec(select(Resource.vmid)).all())
        claimed.update(
            vmid
            for vmid in session.exec(select(IpAllocation.vmid)).all()
            if vmid is not None
        )
    return {int(vmid) for vmid in claimed}


def next_vmid() -> int:
    """回傳一個在所有連線上、以及 DB 登記中都未使用的 VMID。

    多連線架構下各入口的 ``cluster.nextid`` 彼此獨立，可能互相碰撞，
    因此取所有連線 nextid 的最大值，再對彙總的既有 VMID 遞增避讓。
    """
    keys = _connection_keys()
    candidates: list[int] = []
    for key in keys:
        try:
            proxmox = get_proxmox_api(key)
            candidates.append(int(proxmox.cluster.nextid.get()))
        except Exception as exc:
            logger.warning(
                "Failed to fetch nextid for Proxmox connection %s: %s", key, exc
            )
    if not candidates:
        raise ProxmoxError("All Proxmox connections are unavailable.")

    # nextid 回的是最小空號；被 DB 擋下往上遞增時可能踩到 PVE 已用的 VMID，
    # 所以單連線也要一併避開 PVE 現有機器
    used = {int(r["vmid"]) for r in _raw_vms(fresh=True)} | _db_claimed_vmids()
    candidate = max(candidates)
    while candidate in used:
        candidate += 1
    return candidate


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def get_lxc_templates(node: str) -> list[dict]:
    proxmox = get_proxmox_api_for_node(node)
    iso_storage = get_proxmox_settings_for_node(node).iso_storage
    return proxmox.nodes(node).storage(iso_storage).content.get()


def get_vm_templates() -> list[dict]:
    """Return all VM templates in each connection's pool."""
    return [vm for vm in _pool_vms() if vm.get("template") == 1]


_TEMPLATE_NODE_MAP_TTL_SECONDS = 60.0
_template_node_map: dict[str, set[str]] = {}
_template_node_map_lock = threading.Lock()


class _TemplateNodeMapCacheMeta:
    """快取最後刷新時間（集中在物件上，避免 global 重新指派）。"""

    refreshed_at: float = 0.0


_template_node_map_meta = _TemplateNodeMapCacheMeta()


def get_lxc_template_node_map() -> dict[str, set[str]]:
    """volid → 看得到該 vztmpl 的節點集合（跨連線彙總，TTL 快取）。

    vztmpl 存在與否是節點層事實（各連線 iso_storage 未必共享到每個節點），
    placement 與模板清單都以此判斷。個別節點查詢失敗視為該節點沒有模板。
    """
    now = time.monotonic()
    with _template_node_map_lock:
        if (
            now - _template_node_map_meta.refreshed_at
        ) < _TEMPLATE_NODE_MAP_TTL_SECONDS:
            return {volid: set(nodes) for volid, nodes in _template_node_map.items()}

    mapping: dict[str, set[str]] = {}
    for node in get_available_nodes():
        node_name = str(node.get("node") or node.get("name") or "")
        if not node_name:
            continue
        try:
            contents = get_lxc_templates(node_name)
        except Exception as exc:
            logger.warning(
                "Failed to list LXC templates on node %s: %s", node_name, exc
            )
            continue
        for item in contents:
            if item.get("content") != "vztmpl":
                continue
            volid = item.get("volid")
            if volid:
                mapping.setdefault(str(volid), set()).add(node_name)

    with _template_node_map_lock:
        _template_node_map.clear()
        _template_node_map.update(mapping)
        _template_node_map_meta.refreshed_at = time.monotonic()
    return {volid: set(nodes) for volid, nodes in mapping.items()}


# ---------------------------------------------------------------------------
# Session ticket (for WebSocket auth — password-based, not API token)
# ---------------------------------------------------------------------------

def _ws_verify(cfg: ProxmoxSettings) -> ssl.SSLContext | bool:
    """httpx 的 ``verify`` 參數：ticket／vncproxy 請求與後續 WebSocket 同一套 TLS 規則。

    有 CA 時沿用 ``build_ws_ssl_context``（驗鏈也驗主機名，與 WebSocket 連同一個
    active host）；沒有 CA 時直接交 bool 給 httpx（保留它內建的 certifi bundle）。
    """
    if cfg.ca_cert:
        return build_ws_ssl_context(cfg)
    return cfg.verify_ssl


async def get_session_ticket(node: str | None = None) -> tuple[str, str]:
    """Authenticate via password and return (pve_auth_cookie, csrf_token).

    Proxmox WebSocket endpoints (termproxy, vncproxy) require a session
    ticket obtained via password auth; API tokens are not accepted.

    ``node`` 有值時對該節點所屬的連線認證（session ticket 不可跨連線）。
    """
    connection_id = get_connection_id_for_node(node) if node else None
    cfg = get_proxmox_settings(connection_id)

    async with httpx.AsyncClient(verify=_ws_verify(cfg)) as client:
        resp = await client.post(
            f"https://{get_active_host(connection_id)}:{cfg.port}"
            "/api2/json/access/ticket",
            data={
                "username": cfg.user,
                "password": cfg.password,
            },
        )
        if resp.status_code != 200:
            raise ProxmoxError(
                f"Proxmox session authentication failed: HTTP {resp.status_code}"
            )
        data = resp.json()["data"]
        return data["ticket"], data.get("CSRFPreventionToken", "")


async def get_vnc_ticket_with_session(
    node: str,
    vmid: int,
    pve_auth_cookie: str,
    csrf_token: str,
) -> dict:
    """Get a VM VNC proxy ticket using the same PVE session used for websocket auth."""
    connection_id = get_connection_id_for_node(node)
    cfg = get_proxmox_settings(connection_id)

    headers = {"Cookie": f"PVEAuthCookie={pve_auth_cookie}"}
    if csrf_token:
        headers["CSRFPreventionToken"] = csrf_token

    async with httpx.AsyncClient(verify=_ws_verify(cfg)) as client:
        resp = await client.post(
            f"https://{get_active_host(connection_id)}:{cfg.port}"
            f"/api2/json/nodes/{node}/qemu/{vmid}/vncproxy",
            data={"websocket": 1},
            headers=headers,
        )
        if resp.status_code != 200:
            raise ProxmoxError(
                f"Proxmox VNC ticket creation failed: HTTP {resp.status_code}"
            )
        return resp.json()["data"]


# ---------------------------------------------------------------------------
# Console tickets
# ---------------------------------------------------------------------------

def get_terminal_ticket(node: str, vmid: int) -> dict:
    """Get termproxy ticket for an LXC container (port + ticket)."""
    proxmox = get_proxmox_api_for_node(node)
    return proxmox.nodes(node).lxc(vmid).termproxy.post()


# ---------------------------------------------------------------------------
# PCI resource mappings (GPU) — /cluster/mapping/pci 與 mdev 探測
#
# mapping id 只在單一叢集內唯一，呼叫端（gpu_service）要自己決定對哪個連線
# 查詢／刪除，所以這些函式直接收 ``iter_connection_clients`` 給的 client。
# ---------------------------------------------------------------------------

def list_pci_mappings(proxmox: Any) -> list[dict]:
    """GET /cluster/mapping/pci：該連線上所有 PCI mapping。"""
    return proxmox.cluster.mapping.pci.get()


def get_pci_mapping(proxmox: Any, mapping_id: str) -> dict:
    """GET /cluster/mapping/pci/{id}；該連線沒有這個 id 時 PVE 會拋錯。"""
    return proxmox.cluster.mapping.pci(mapping_id).get()


def create_pci_mapping(
    proxmox: Any, *, mapping_id: str, description: str, map_entries: list[str]
) -> None:
    """POST /cluster/mapping/pci。"""
    proxmox.cluster.mapping.pci.post(
        id=mapping_id, description=description, **{"map": map_entries}
    )


def delete_pci_mapping(proxmox: Any, mapping_id: str) -> None:
    """DELETE /cluster/mapping/pci/{id}。"""
    proxmox.cluster.mapping.pci(mapping_id).delete()


def list_pci_mdev_types(node: str, pci_path: str) -> list[dict]:
    """GET /nodes/{node}/hardware/pci/{path}/mdev：該 PCI 裝置目前可建立的 vGPU 型別。"""
    proxmox = get_proxmox_api_for_node(node)
    return proxmox.nodes(node).hardware.pci(pci_path).mdev.get()


def list_cluster_vm_resources(proxmox: Any) -> list[dict]:
    """GET /cluster/resources?type=vm（單一連線、不做 pool 過濾）。"""
    return proxmox.cluster.resources.get(type="vm")


def get_qemu_config_via(proxmox: Any, node: str, vmid: int) -> dict:
    """用指定連線的 client 讀 VM 設定（批次掃描時沿用已取得的 client）。"""
    return proxmox.nodes(node).qemu(vmid).config.get()

