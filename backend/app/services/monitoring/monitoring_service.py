"""資源監控 service：全域 overview 與節點/VM RRD 趨勢。

即時數據來自單次 PVE ``cluster/resources`` 與 ``/nodes`` 呼叫；
歷史趨勢直接代理 PVE 內建 RRD，後端不自存時序資料。
"""

from __future__ import annotations

from typing import Any, Literal

from sqlmodel import Session

from app.core.authorizers import require_resource_access
from app.exceptions import BadRequestError, NotFoundError
from app.models import User
from app.repositories import resource as resource_repo
from app.schemas.monitoring import MonitoringOverview, NodeMetrics, VMTopEntry
from app.services.proxmox import proxmox_service

TOP_N = 5

RRD_TIMEFRAMES = {"hour", "day", "week"}


def _validate_timeframe(timeframe: str) -> str:
    if timeframe not in RRD_TIMEFRAMES:
        raise BadRequestError(
            f"timeframe must be one of {sorted(RRD_TIMEFRAMES)}"
        )
    return timeframe


def _node_metrics(raw: dict[str, Any]) -> NodeMetrics:
    return NodeMetrics(
        node=str(raw.get("node") or ""),
        status=str(raw.get("status") or "unknown"),
        cpu=float(raw.get("cpu") or 0.0),
        maxcpu=int(raw.get("maxcpu") or 0),
        mem=int(raw.get("mem") or 0),
        maxmem=int(raw.get("maxmem") or 0),
        disk=int(raw.get("disk") or 0),
        maxdisk=int(raw.get("maxdisk") or 0),
        uptime=int(raw.get("uptime") or 0),
    )


def _vm_entry(raw: dict[str, Any]) -> VMTopEntry:
    return VMTopEntry(
        vmid=int(raw.get("vmid") or 0),
        name=str(raw.get("name") or ""),
        node=str(raw.get("node") or ""),
        type=str(raw.get("type") or ""),
        cpu=float(raw.get("cpu") or 0.0),
        mem=int(raw.get("mem") or 0),
        maxmem=int(raw.get("maxmem") or 0),
        status=str(raw.get("status") or ""),
    )


def build_overview(
    nodes: list[dict[str, Any]], resources: list[dict[str, Any]]
) -> MonitoringOverview:
    """純函式：由 PVE 原始回應聚合全域監控視圖。"""
    node_metrics = [_node_metrics(n) for n in nodes]

    running = [r for r in resources if str(r.get("status") or "") == "running"]
    stopped = [r for r in resources if str(r.get("status") or "") != "running"]
    running_entries = [_vm_entry(r) for r in running]

    def _count(items: list[dict[str, Any]], rtype: str) -> int:
        return sum(1 for r in items if str(r.get("type") or "") == rtype)

    return MonitoringOverview(
        nodes_online=sum(1 for n in node_metrics if n.status == "online"),
        nodes_total=len(node_metrics),
        cpu_used=sum(n.cpu * n.maxcpu for n in node_metrics),
        cpu_total=sum(n.maxcpu for n in node_metrics),
        mem_used=sum(n.mem for n in node_metrics),
        mem_total=sum(n.maxmem for n in node_metrics),
        disk_used=sum(n.disk for n in node_metrics),
        disk_total=sum(n.maxdisk for n in node_metrics),
        vms_running=_count(running, "qemu"),
        vms_stopped=_count(stopped, "qemu"),
        lxc_running=_count(running, "lxc"),
        lxc_stopped=_count(stopped, "lxc"),
        nodes=node_metrics,
        top_cpu=sorted(running_entries, key=lambda e: e.cpu, reverse=True)[:TOP_N],
        top_mem=sorted(running_entries, key=lambda e: e.mem, reverse=True)[:TOP_N],
    )


def get_overview() -> MonitoringOverview:
    nodes = proxmox_service.list_nodes()
    resources = proxmox_service.list_all_resources()
    return build_overview(nodes, resources)


def get_node_rrd(node: str, timeframe: str) -> list[dict[str, Any]]:
    _validate_timeframe(timeframe)
    return proxmox_service.get_node_rrd_data(node, timeframe)


def get_vm_rrd(
    *, session: Session, vmid: int, timeframe: str, user: User
) -> list[dict[str, Any]]:
    _validate_timeframe(timeframe)
    resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    if resource is None:
        raise NotFoundError(f"Resource {vmid} not found")
    require_resource_access(user, resource.user_id)
    info = proxmox_service.find_resource(vmid)
    resource_type: Literal["qemu", "lxc"] = (
        "lxc" if str(info.get("type") or "") == "lxc" else "qemu"
    )
    return proxmox_service.get_rrd_data(
        str(info["node"]), vmid, resource_type, timeframe
    )
