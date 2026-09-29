from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, ParamSpec, TypeVar

from proxmoxer import ProxmoxAPI
from proxmoxer.core import ResourceException

from app.ai.pve_log.config import settings
from app.ai.pve_log.schemas import (
    ClusterInfo,
    NetworkInterface,
    NodeInfo,
    ResourceConfig,
    ResourceStatus,
    ResourceSummary,
    StorageInfo,
)
from app.ai.utils import safe_bool, safe_float, safe_int
from app.core.i18n import t
from app.infrastructure.proxmox import (
    get_proxmox_api,
    get_proxmox_api_for_node,
    list_enabled_connection_ids,
)

logger = logging.getLogger(__name__)
_Args = ParamSpec("_Args")
_Result = TypeVar("_Result")

def _usage_pct(used: int, total: int) -> float:
    return round(used / total, 4) if total > 0 else 0.0


def _retry(func: Callable[_Args, _Result], *args: _Args.args, **kwargs: _Args.kwargs) -> _Result:
    attempts = max(settings.collector_retry_attempts, 1)
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if (
                isinstance(exc, ResourceException)
                and 400 <= exc.status_code < 500
                and exc.status_code not in {408, 429}
            ):
                raise
            if attempt >= attempts:
                break
            backoff = settings.collector_retry_backoff * (2 ** (attempt - 1))
            if backoff > 0:
                time.sleep(backoff)
    assert last_exc is not None
    raise last_exc


def _collect_cluster_info(proxmox: ProxmoxAPI) -> ClusterInfo:
    items = proxmox.cluster.status.get()

    cluster_name = None
    node_count = 0
    quorate = False
    version = None

    for item in items:
        if item.get("type") == "cluster":
            cluster_name = item.get("name")
            node_count = safe_int(item.get("nodes"), 0)
            quorate = safe_bool(item.get("quorate"))
            version = safe_int(item.get("version")) or None
        elif item.get("type") == "node":
            if node_count == 0:
                node_count += 1

    return ClusterInfo(
        cluster_name=cluster_name,
        is_cluster=cluster_name is not None,
        node_count=node_count if node_count > 0 else 1,
        quorate=quorate,
        cluster_version=version,
    )


def _collect_nodes(proxmox: ProxmoxAPI) -> list[NodeInfo]:
    items = proxmox.nodes.get()
    result = []
    for item in items:
        mem_used = safe_int(item.get("mem"))
        mem_total = safe_int(item.get("maxmem"))
        disk_used = safe_int(item.get("disk"))
        disk_total = safe_int(item.get("maxdisk"))
        result.append(
            NodeInfo(
                node=str(item.get("node") or "unknown"),
                status=str(item.get("status") or "unknown"),
                cpu_usage=safe_float(item.get("cpu")),
                cpu_cores=safe_int(item.get("maxcpu")),
                mem_used_bytes=mem_used,
                mem_total_bytes=mem_total,
                mem_used_pct=_usage_pct(mem_used, mem_total),
                disk_used_bytes=disk_used,
                disk_total_bytes=disk_total,
                disk_used_pct=_usage_pct(disk_used, disk_total),
                uptime_seconds=safe_int(item.get("uptime")) or None,
            )
        )
    return result


def _collect_storages_for_node(proxmox: ProxmoxAPI, node: str) -> list[StorageInfo]:
    items = proxmox.nodes(node).storage.get()

    result = []
    for item in items:
        avail = safe_int(item.get("avail"))
        used = safe_int(item.get("used"))
        total = safe_int(item.get("total"))
        result.append(
            StorageInfo(
                node=node,
                storage=str(item.get("storage") or item.get("id") or "unknown"),
                storage_type=str(item.get("type") or "unknown"),
                content=str(item.get("content") or ""),
                avail_bytes=avail,
                used_bytes=used,
                total_bytes=total,
                used_pct=_usage_pct(used, total),
                active=safe_bool(item.get("active", 1)),
                enabled=not safe_bool(item.get("disable", 0)),
                shared=safe_bool(item.get("shared", 0)),
            )
        )
    return result


def _collect_resource_summary(item: dict[str, Any]) -> ResourceSummary:
    mem_used = safe_int(item.get("mem"))
    mem_total = safe_int(item.get("maxmem"))
    disk_used = safe_int(item.get("disk"))
    disk_total = safe_int(item.get("maxdisk"))
    return ResourceSummary(
        vmid=safe_int(item.get("vmid")),
        name=str(item.get("name") or ""),
        resource_type=str(item.get("type") or "unknown"),
        node=str(item.get("node") or "unknown"),
        status=str(item.get("status") or "unknown"),
        pool=item.get("pool") or None,
        cpu_usage=safe_float(item.get("cpu")),
        cpu_cores=safe_int(item.get("maxcpu")),
        mem_used_bytes=mem_used,
        mem_total_bytes=mem_total,
        mem_used_pct=_usage_pct(mem_used, mem_total),
        disk_used_bytes=disk_used,
        disk_total_bytes=disk_total,
        disk_used_pct=_usage_pct(disk_used, disk_total),
        net_in_bytes=safe_int(item.get("netin")),
        net_out_bytes=safe_int(item.get("netout")),
        uptime_seconds=safe_int(item.get("uptime")) or None,
        is_template=safe_bool(item.get("template", 0)),
    )


def _collect_resource_status(proxmox: ProxmoxAPI, node: str, vmid: int, resource_type: str) -> ResourceStatus | None:
    if resource_type == "qemu":
        s = proxmox.nodes(node).qemu(vmid).status.current.get()
    else:
        s = proxmox.nodes(node).lxc(vmid).status.current.get()

    mem_used = safe_int(s.get("mem"))
    mem_total = safe_int(s.get("maxmem"))
    return ResourceStatus(
        vmid=vmid,
        node=node,
        resource_type=resource_type,
        status=str(s.get("status") or "unknown"),
        cpu_usage=safe_float(s.get("cpu")),
        cpu_cores=safe_int(s.get("cpus") or s.get("maxcpu")),
        mem_used_bytes=mem_used,
        mem_total_bytes=mem_total,
        mem_used_pct=_usage_pct(mem_used, mem_total),
        disk_read_bytes=safe_int(s.get("diskread")),
        disk_write_bytes=safe_int(s.get("diskwrite")),
        disk_total_bytes=safe_int(s.get("maxdisk")),
        net_in_bytes=safe_int(s.get("netin")),
        net_out_bytes=safe_int(s.get("netout")),
        uptime_seconds=safe_int(s.get("uptime")) or None,
        pid=safe_int(s.get("pid")) or None,
    )


def _collect_resource_config(proxmox: ProxmoxAPI, node: str, vmid: int, resource_type: str) -> ResourceConfig | None:
    if resource_type == "qemu":
        c = proxmox.nodes(node).qemu(vmid).config.get()
    else:
        c = proxmox.nodes(node).lxc(vmid).config.get()

    disk_key = "scsi0" if resource_type == "qemu" else "rootfs"
    disk_str = c.get(disk_key, "")
    disk_size_gb = None
    if "size=" in disk_str:
        size_part = disk_str.split("size=")[1].split(",")[0].strip()
        if size_part.endswith("G"):
            try:
                disk_size_gb = int(size_part[:-1])
            except ValueError:
                # size 解析失敗時保留 disk_size_gb=None
                pass

    name_key = "name" if resource_type == "qemu" else "hostname"
    return ResourceConfig(
        vmid=vmid,
        node=node,
        resource_type=resource_type,
        name=c.get(name_key) or None,
        cpu_cores=safe_int(c.get("cores") or c.get("cpus")) or None,
        cpu_type=c.get("cpu") or None,
        memory_mb=safe_int(c.get("memory")) or None,
        disk_info=disk_str or None,
        disk_size_gb=disk_size_gb,
        os_type=c.get("ostype") or None,
        net0=c.get("net0") or None,
        description=c.get("description") or None,
        tags=c.get("tags") or None,
        onboot=safe_bool(c.get("onboot", 0)),
        protection=safe_bool(c.get("protection", 0)),
        raw=dict(c),
    )


def _collect_lxc_interfaces(proxmox: ProxmoxAPI, node: str, vmid: int) -> list[NetworkInterface]:
    items = proxmox.nodes(node).lxc(vmid).interfaces.get()
    result = []
    for iface in items or []:
        result.append(
            NetworkInterface(
                vmid=vmid,
                name=str(iface.get("name") or "unknown"),
                inet=iface.get("inet") or None,
                inet6=iface.get("inet6") or None,
                hwaddr=iface.get("hwaddr") or None,
            )
        )
    return result


def _fetch_resource_summaries(proxmox: ProxmoxAPI) -> list[ResourceSummary]:
    """只取得 cluster.resources 摘要，不延伸讀取每個 guest 的細節。"""
    raw_resources = _retry(lambda: proxmox.cluster.resources.get(type="vm")) or []
    return [
        _collect_resource_summary(item)
        for item in raw_resources
        if not safe_bool(item.get("template", 0))
    ]


def _empty_cluster_info() -> ClusterInfo:
    return ClusterInfo(
        cluster_name=None,
        is_cluster=False,
        node_count=0,
        quorate=False,
        cluster_version=None,
    )


def _merge_cluster_infos(infos: list[ClusterInfo]) -> ClusterInfo:
    """把多個 PVE 連線的叢集概覽合併成一份（單一連線時原樣回傳）。"""
    if not infos:
        return _empty_cluster_info()
    if len(infos) == 1:
        return infos[0]
    names = [info.cluster_name for info in infos if info.cluster_name]
    return ClusterInfo(
        cluster_name=", ".join(names) or None,
        is_cluster=any(info.is_cluster for info in infos),
        node_count=sum(info.node_count for info in infos),
        quorate=all(info.quorate for info in infos),
        cluster_version=None,
    )


@dataclass
class PveToolContext:
    """一次 chat request 內的 PVE 工具資料快取。

    每個工具只載入自己需要的資料；同一 request 後續 tool call 會重用已
    取得的結果。這個 context 不跨 request 保存。
    所有會觸發 PVE I/O 的操作都由 chat 呼叫端放到 worker thread 執行。

    未注入 ``proxmox`` 時會彙總所有啟用中的 PVE 連線；任一連線連不上會
    記進 ``errors``，讓工具結果帶上「部分資料缺漏」警示，而不是把其他
    連線上的節點／VM 當成不存在。節點層級的呼叫一律走該節點所屬連線。
    """

    proxmox: ProxmoxAPI | None = None
    errors: list[str] = field(default_factory=list)
    _clients: list[tuple[int | None, ProxmoxAPI]] | None = None
    _connection_failed: bool = False
    _node_clients: dict[str, ProxmoxAPI] = field(default_factory=dict)
    _cluster: ClusterInfo | None = None
    _cluster_loaded: bool = False
    _nodes: list[NodeInfo] = field(default_factory=list)
    _nodes_loaded: bool = False
    _storages_by_node: dict[str, list[StorageInfo]] = field(default_factory=dict)
    _resources: list[ResourceSummary] = field(default_factory=list)
    _resources_loaded: bool = False
    _resource_load_failed: bool = False
    _resource_load_partial: bool = False
    _statuses: dict[int, ResourceStatus | None] = field(default_factory=dict)
    _configs: dict[int, ResourceConfig | None] = field(default_factory=dict)
    _interfaces: dict[int, list[NetworkInterface]] = field(default_factory=dict)

    def _connection_clients(self) -> list[tuple[int | None, ProxmoxAPI]]:
        """回傳本 request 要查詢的 (connection_id, client) 清單（request 內快取）。"""
        if self.proxmox is not None:
            return [(None, self.proxmox)]
        if self._clients is None:
            clients: list[tuple[int | None, ProxmoxAPI]] = []
            connection_ids: list[int | None] = list(list_enabled_connection_ids())
            if not connection_ids:
                # 尚未建立任何連線資料：退回單一預設連線（相容舊部署）。
                connection_ids = [None]
            for connection_id in connection_ids:
                try:
                    client = (
                        get_proxmox_api()
                        if connection_id is None
                        else get_proxmox_api(connection_id)
                    )
                except Exception as exc:
                    self._connection_failed = True
                    self._record_error(f"PVE 連線 {connection_id or '預設'}", exc)
                    continue
                clients.append((connection_id, client))
            self._clients = clients
        return self._clients

    def _client_for_node(self, node: str) -> ProxmoxAPI:
        """取得可操作指定節點的 client（優先用本 request 已知的歸屬）。"""
        if self.proxmox is not None:
            return self.proxmox
        client = self._node_clients.get(node)
        if client is None:
            client = get_proxmox_api_for_node(node)
            self._node_clients[node] = client
        return client

    def _record_error(self, label: str, exc: Exception) -> None:
        logger.error("PVE 工具資料收集失敗（%s）：%s", label, exc)
        self.errors.append(f"{label}：{exc}")

    def _load_cluster(self) -> ClusterInfo:
        if not self._cluster_loaded:
            self._cluster_loaded = True
            infos: list[ClusterInfo] = []
            for connection_id, client in self._connection_clients():
                try:
                    infos.append(_retry(_collect_cluster_info, client))
                except Exception as exc:
                    label = (
                        "叢集資訊"
                        if connection_id is None
                        else f"連線 {connection_id} 叢集資訊"
                    )
                    self._record_error(label, exc)
            self._cluster = _merge_cluster_infos(infos)
        assert self._cluster is not None
        return self._cluster

    def _load_nodes(self) -> list[NodeInfo]:
        if not self._nodes_loaded:
            self._nodes_loaded = True
            nodes: list[NodeInfo] = []
            for connection_id, client in self._connection_clients():
                try:
                    connection_nodes = _retry(_collect_nodes, client)
                except Exception as exc:
                    label = (
                        "節點清單"
                        if connection_id is None
                        else f"連線 {connection_id} 節點清單"
                    )
                    self._record_error(label, exc)
                    continue
                for item in connection_nodes:
                    self._node_clients.setdefault(item.node, client)
                nodes.extend(connection_nodes)
            self._nodes = nodes
        return self._nodes

    def _load_storages(self, node: str | None) -> list[StorageInfo]:
        # 先取得節點清單，讓 node 篩選維持既有可見範圍，不直接探測任意名稱。
        node_names = [item.node for item in self._load_nodes()]
        requested_nodes = [node] if node else node_names
        if node and node not in node_names:
            return []

        missing_nodes = [
            node_name
            for node_name in requested_nodes
            if node_name not in self._storages_by_node
        ]
        if missing_nodes:
            with ThreadPoolExecutor(max_workers=settings.collector_max_workers) as pool:
                futures: dict[Future[list[StorageInfo]], str] = {
                    pool.submit(
                        _retry,
                        _collect_storages_for_node,
                        self._client_for_node(node_name),
                        node_name,
                    ): node_name
                    for node_name in missing_nodes
                }
                for storage_future in as_completed(futures):
                    node_name = futures[storage_future]
                    try:
                        self._storages_by_node[node_name] = storage_future.result()
                    except Exception as exc:
                        self._record_error(f"節點 {node_name} 儲存空間", exc)
                        self._storages_by_node[node_name] = []

        result: list[StorageInfo] = []
        for node_name in requested_nodes:
            result.extend(self._storages_by_node.get(node_name, []))
        return result

    def _load_resources(self) -> list[ResourceSummary]:
        if not self._resources_loaded:
            self._resources_loaded = True
            clients = self._connection_clients()
            # 有連線連不上時，找不到 VMID 不能斷言「不存在」。
            self._resource_load_partial = self._connection_failed
            resources: list[ResourceSummary] = []
            loaded_any = False
            for connection_id, client in clients:
                try:
                    connection_resources = _fetch_resource_summaries(client)
                except Exception as exc:
                    self._resource_load_partial = True
                    label = (
                        "cluster.resources"
                        if connection_id is None
                        else f"連線 {connection_id} cluster.resources"
                    )
                    self._record_error(label, exc)
                    continue
                loaded_any = True
                for item in connection_resources:
                    self._node_clients.setdefault(item.node, client)
                resources.extend(connection_resources)
            if not loaded_any:
                self._resource_load_failed = True
            self._resources = resources
        return self._resources

    def _load_resource_detail_fields(self, summary: ResourceSummary) -> None:
        vmid = summary.vmid
        if (
            vmid in self._statuses
            and vmid in self._configs
            and vmid in self._interfaces
        ):
            return

        # 先標記不適用欄位，避免同一 request 內重複判斷或重抓。
        if vmid not in self._configs and not settings.collector_fetch_config:
            self._configs[vmid] = None
        if vmid not in self._interfaces and not (
            settings.collector_fetch_lxc_interfaces
            and summary.resource_type == "lxc"
            and summary.status == "running"
        ):
            self._interfaces[vmid] = []

        proxmox = self._client_for_node(summary.node)
        pending: dict[str, Future[Any]] = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            if vmid not in self._statuses:
                pending["status"] = pool.submit(
                    _retry,
                    _collect_resource_status,
                    proxmox,
                    summary.node,
                    vmid,
                    summary.resource_type,
                )
            if vmid not in self._configs:
                pending["config"] = pool.submit(
                    _retry,
                    _collect_resource_config,
                    proxmox,
                    summary.node,
                    vmid,
                    summary.resource_type,
                )
            if vmid not in self._interfaces:
                pending["interfaces"] = pool.submit(
                    _retry,
                    _collect_lxc_interfaces,
                    proxmox,
                    summary.node,
                    vmid,
                )
            for field_name, future in pending.items():
                try:
                    value = future.result()
                except Exception as exc:
                    self._record_error(
                        f"{summary.resource_type} {vmid} {field_name}", exc
                    )
                    value = [] if field_name == "interfaces" else None
                if field_name == "status":
                    self._statuses[vmid] = value
                elif field_name == "config":
                    self._configs[vmid] = value
                else:
                    self._interfaces[vmid] = value or []

    def execute(
        self,
        name: str,
        args: dict[str, Any],
        *,
        allowed_vmids: set[int] | None = None,
    ) -> Any:
        """依 tool 需求載入資料並回傳與既有工具相同的 shape。"""
        if name == "get_cluster":
            return self._load_cluster().model_dump(mode="json")
        if name == "get_nodes":
            return [node.model_dump(mode="json") for node in self._load_nodes()]
        if name == "get_storage":
            node = str(args["node"]) if args.get("node") else None
            return [
                item.model_dump(mode="json")
                for item in self._load_storages(node)
            ]
        if name == "get_resources":
            result = self._load_resources()
            if args.get("node"):
                result = [item for item in result if item.node == args["node"]]
            if args.get("resource_type"):
                result = [
                    item for item in result if item.resource_type == args["resource_type"]
                ]
            if args.get("status"):
                result = [item for item in result if item.status == args["status"]]
            if allowed_vmids is not None:
                result = [item for item in result if item.vmid in allowed_vmids]
            return [item.model_dump(mode="json") for item in result]
        if name == "get_resource_detail":
            try:
                vmid = int(args["vmid"])
            except (KeyError, TypeError, ValueError) as exc:
                return {"error": f"缺少有效 vmid：{exc}"}
            if allowed_vmids is not None and vmid not in allowed_vmids:
                return {"error": t("pveLog.scopeRestricted")}
            resources = self._load_resources()
            if self._resource_load_failed:
                return {
                    "error": "PVE 資源摘要無法取得；缺少資料不代表資源不存在。"
                }
            summary = next((item for item in resources if item.vmid == vmid), None)
            if summary is None and self._resource_load_partial:
                return {
                    "error": "部分 PVE 連線的資源摘要無法取得；找不到不代表資源不存在。"
                }
            if summary is None:
                return {"error": t("pveLog.vmidNotFound", vmid=vmid)}
            self._load_resource_detail_fields(summary)
            status = self._statuses.get(vmid)
            config = self._configs.get(vmid)
            return {
                "summary": summary.model_dump(mode="json"),
                "status": status.model_dump(mode="json") if status else None,
                "config": (
                    config.model_dump(mode="json", exclude={"raw"})
                    if config
                    else None
                ),
                "network_interfaces": [
                    item.model_dump(mode="json")
                    for item in self._interfaces.get(vmid, [])
                ],
            }
        return {"error": t("pveLog.unknownTool", name=name)}
