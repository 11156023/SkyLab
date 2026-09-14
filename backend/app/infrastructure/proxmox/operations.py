"""Centralized Proxmox VE API operations.

Provides a single place for common PVE interactions (resource lookup, config,
control, resize, specs, session ticket, etc.) so that callers no longer
duplicate the same cluster.resources iteration or qemu/lxc dispatch logic.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.infrastructure.proxmox import (
    ProxmoxSettings,
    basic_blocking_task_status,
    get_active_host,
    get_connection_id_for_node,
    get_proxmox_api,
    get_proxmox_api_for_node,
    get_proxmox_settings,
    get_proxmox_settings_for_node,
    list_enabled_connection_ids,
    wait_for_task_status,
)

logger = logging.getLogger(__name__)

ResourceType = Literal["qemu", "lxc"]


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

def _raw_vms_by_connection() -> list[tuple[int | None, list[dict]]]:
    """Return (connection_key, resources) for every connection, without pool filtering."""
    results: list[tuple[int | None, list[dict]]] = []
    errors: list[str] = []
    keys = _connection_keys()
    for key in keys:
        try:
            proxmox = get_proxmox_api(key)
            results.append((key, list(proxmox.cluster.resources.get(type="vm"))))
        except Exception as exc:
            errors.append(str(exc))
            logger.warning(
                "Failed to list resources for Proxmox connection %s: %s", key, exc
            )
    if errors and not results and len(errors) == len(keys):
        raise ProxmoxError(f"All Proxmox connections are unavailable. {errors[0]}")
    return results


def _raw_vms() -> list[dict]:
    """Return all resources of type vm across all connections, without pool filtering."""
    return [vm for _key, vms in _raw_vms_by_connection() for vm in vms]


def _pool_vms() -> list[dict]:
    """Return vm resources inside each connection's own pool.

    pool 名稱是每個連線（叢集）自己的設定，因此比對必須逐連線進行，
    不能用單一 pool 名稱去篩全部連線的資源。
    """
    matched: list[dict] = []
    for key, vms in _raw_vms_by_connection():
        pool = get_proxmox_settings(key).pool_name
        matched.extend(vm for vm in vms if vm.get("pool") == pool)
    return matched


def list_all_vmids() -> set[int]:
    """回傳所有連線上既有的 VMID 集合（不限 pool）。"""
    return {int(r["vmid"]) for r in _raw_vms()}


def find_resource(vmid: int) -> dict:
    """Find any resource (qemu or lxc) by VMID in its connection's pool."""
    for r in _pool_vms():
        if r["vmid"] == vmid:
            return r
    raise NotFoundError(f"Resource {vmid} not found")


def find_lxc(vmid: int) -> dict:
    """Find an LXC container by VMID in its connection's pool."""
    for r in _pool_vms():
        if r["vmid"] == vmid and r["type"] == "lxc":
            return r
    raise NotFoundError(f"LXC container {vmid} not found")


def list_all_resources() -> list[dict]:
    """Return all cluster resources of type vm in each connection's pool."""
    return _pool_vms()


def list_nodes() -> list[dict]:
    """Return all nodes across all connections."""
    results: list[dict] = []
    errors: list[str] = []
    keys = _connection_keys()
    for key in keys:
        try:
            proxmox = get_proxmox_api(key)
            results.extend(proxmox.nodes.get())
        except Exception as exc:
            errors.append(str(exc))
            logger.warning(
                "Failed to list nodes for Proxmox connection %s: %s", key, exc
            )
    if errors and not results and len(errors) == len(keys):
        raise ProxmoxError(f"All Proxmox connections are unavailable. {errors[0]}")
    return results


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


def _admin_disabled_node_names() -> set[str]:
    """讀取被管理員停用的節點名稱；DB 讀取失敗時不過濾（fail-open）。"""
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
    disabled = _admin_disabled_node_names()
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

def list_snapshots(node: str, vmid: int, resource_type: ResourceType) -> list:
    return _resource_api(node, vmid, resource_type).snapshot.get()


def create_snapshot(
    node: str,
    vmid: int,
    resource_type: ResourceType,
    wait_timeout_seconds: float | None = None,
    **params,
) -> str:
    task = _resource_api(node, vmid, resource_type).snapshot.post(**params)
    basic_blocking_task_status(node, task, timeout_seconds=wait_timeout_seconds)
    return task


def delete_snapshot(
    node: str, vmid: int, resource_type: ResourceType, snapname: str
) -> str:
    task = _resource_api(node, vmid, resource_type).snapshot(snapname).delete()
    basic_blocking_task_status(node, task)
    return task


def rollback_snapshot(
    node: str, vmid: int, resource_type: ResourceType, snapname: str
) -> str:
    task = _resource_api(node, vmid, resource_type).snapshot(snapname).rollback.post()
    basic_blocking_task_status(node, task)
    return task


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
    basic_blocking_task_status(node, task)
    return task


# ---------------------------------------------------------------------------
# IP address
# ---------------------------------------------------------------------------

def _is_usable_ipv4(ip: str) -> bool:
    """過濾 loopback、link-local 等不可用的 IPv4 位址"""
    return (
        bool(ip)
        and not ip.startswith("127.")
        and not ip.startswith("169.254.")
        and ip != "0.0.0.0"
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
            try:
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
            except Exception:
                # 單一查詢來源失敗時繼續嘗試下一個
                pass
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
    basic_blocking_task_status(node, task)
    return task


# ---------------------------------------------------------------------------
# VM clone + configure
# ---------------------------------------------------------------------------

def clone_vm(node: str, template_id: int, **clone_config) -> str:
    """Clone a VM template and wait. Returns UPID."""
    proxmox = get_proxmox_api_for_node(node)
    task = proxmox.nodes(node).qemu(template_id).clone.post(**clone_config)
    basic_blocking_task_status(node, task)
    return task


def clone_lxc(node: str, template_id: int, **clone_config) -> str:
    """Clone an LXC template and wait. Returns UPID."""
    proxmox = get_proxmox_api_for_node(node)
    task = proxmox.nodes(node).lxc(template_id).clone.post(**clone_config)
    basic_blocking_task_status(node, task)
    return task


def convert_to_template(
    node: str, vmid: int, resource_type: ResourceType = "qemu"
) -> None:
    """POST /nodes/{node}/{type}/{vmid}/template — 轉為唯讀範本（不可逆）。

    VM 必須處於 stopped 狀態，呼叫端負責先關機。
    """
    _resource_api(node, vmid, resource_type).template.post()


def next_vmid() -> int:
    """回傳一個在所有連線上都未使用的 VMID。

    多連線架構下各入口的 ``cluster.nextid`` 彼此獨立，可能互相碰撞，
    因此取所有連線 nextid 的最大值，再對彙總的既有 VMID 遞增避讓。
    """
    keys = _connection_keys()
    if len(keys) == 1:
        proxmox = get_proxmox_api(keys[0])
        return int(proxmox.cluster.nextid.get())

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

    used = {int(r["vmid"]) for r in _raw_vms()}
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


def get_vztmpl_nodes(volid: str) -> set[str]:
    """看得到指定 vztmpl volid 的節點集合；模板不存在任何節點時為空集合。"""
    return get_lxc_template_node_map().get(str(volid), set())


# ---------------------------------------------------------------------------
# Session ticket (for WebSocket auth — password-based, not API token)
# ---------------------------------------------------------------------------

def _ws_verify(cfg: "ProxmoxSettings") -> "Any":
    """Build the httpx verify parameter for a connection's TLS settings."""
    import ssl as _ssl

    if cfg.ca_cert:
        # Build a custom SSL context that accepts the PVE self-signed CA cert
        _ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
        _ctx.check_hostname = False
        _ctx.verify_mode = _ssl.CERT_REQUIRED
        _ctx.load_verify_locations(cadata=cfg.ca_cert)
        if hasattr(_ssl, "VERIFY_X509_STRICT"):
            _ctx.verify_flags &= ~_ssl.VERIFY_X509_STRICT
        return _ctx
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


async def wait_task(task_id: str, node: str, check_interval: int | None = None) -> dict:
    return await wait_for_task_status(
        node_name=node,
        task_id=task_id,
        check_interval=check_interval,
    )


# ---------------------------------------------------------------------------
# Console tickets
# ---------------------------------------------------------------------------

def get_terminal_ticket(node: str, vmid: int) -> dict:
    """Get termproxy ticket for an LXC container (port + ticket)."""
    proxmox = get_proxmox_api_for_node(node)
    return proxmox.nodes(node).lxc(vmid).termproxy.post()


def get_vnc_ticket(node: str, vmid: int) -> dict:
    """Get VNC proxy ticket for a VM (port + ticket)."""
    proxmox = get_proxmox_api_for_node(node)
    return proxmox.nodes(node).qemu(vmid).vncproxy.post(websocket=1)
