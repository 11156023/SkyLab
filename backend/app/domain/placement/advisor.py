from __future__ import annotations

import threading
import time
from collections import Counter
from dataclasses import dataclass

from app.domain.placement.config import settings
from app.domain.placement.schemas import (
    NodeCapacity,
    NodeSnapshot,
    PlacementDecision,
    PlacementRequest,
    ResourceSnapshot,
    ResourceType,
)
from app.services.proxmox import gpu_service, proxmox_service

GIB = 1024**3
MIB = 1024**2


@dataclass(frozen=True)
class _ClusterCacheEntry:
    cached_at: float
    nodes: list[NodeSnapshot]
    resources: list[ResourceSnapshot]


_cluster_cache: _ClusterCacheEntry | None = None
_cluster_cache_lock = threading.Lock()


def _disabled_node_names() -> set[str]:
    """讀取被管理員停用的節點名稱；DB 讀取失敗時不過濾（fail-open）。"""
    try:
        from sqlmodel import Session

        from app.core.db import engine
        from app.repositories.proxmox_node import get_disabled_node_names

        with Session(engine) as session:
            return get_disabled_node_names(session)
    except Exception:
        return set()


def _gpu_used_slots() -> dict[str, int]:
    """各節點已被 VM 佔用的 GPU 插槽數；查詢失敗回空 dict（fail-open）。"""
    try:
        return gpu_service.get_gpu_used_slots_by_node()
    except Exception:
        return {}


def _load_cluster_state() -> tuple[list[NodeSnapshot], list[ResourceSnapshot]]:
    cached = _get_cached_cluster_state()
    if cached is not None:
        return cached.nodes, cached.resources

    gpu_map = gpu_service.get_gpu_node_counts()
    disabled = _disabled_node_names()
    nodes = [
        NodeSnapshot(
            node=str(item.get("node") or "unknown"),
            status=str(item.get("status") or "unknown").lower(),
            cpu_ratio=float(item.get("cpu") or 0.0),
            maxcpu=int(item.get("maxcpu") or 0),
            mem_bytes=int(item.get("mem") or 0),
            maxmem_bytes=int(item.get("maxmem") or 0),
            disk_bytes=int(item.get("disk") or 0),
            maxdisk_bytes=int(item.get("maxdisk") or 0),
            uptime=_optional_int(item.get("uptime")),
            gpu_count=gpu_map.get(str(item.get("node") or "unknown"), 0),
            current_loadavg_1=_parse_loadavg_1(item.get("loadavg")),
            average_loadavg_1=_parse_loadavg_1(
                item.get("avg_load")
                or item.get("avgload")
                or item.get("average_loadavg")
            ),
        )
        for item in proxmox_service.list_nodes()
        if str(item.get("node") or "unknown") not in disabled
    ]
    resources = [
        ResourceSnapshot(
            vmid=int(item.get("vmid") or 0),
            name=str(item.get("name") or ""),
            resource_type=str(item.get("type") or "unknown"),
            node=str(item.get("node") or "unknown"),
            status=str(item.get("status") or "unknown").lower(),
        )
        for item in proxmox_service.list_all_resources()
        if item.get("template") != 1 and str(item.get("type") or "") in {"lxc", "qemu", "vm"}
    ]

    _set_cached_cluster_state(nodes=nodes, resources=resources)
    return nodes, resources


def _build_node_capacities(
    *,
    nodes: list[NodeSnapshot],
    resources: list[ResourceSnapshot],
    cpu_overcommit_ratio: float = 1.0,
    disk_overcommit_ratio: float = 1.0,
) -> list[NodeCapacity]:
    running_counter = Counter(
        resource.node for resource in resources if resource.status == "running"
    )
    gpu_used = _gpu_used_slots()
    capacities: list[NodeCapacity] = []
    for node in nodes:
        running_resources = running_counter.get(node.node, 0)
        guest_soft_limit = _guest_soft_limit(node.maxcpu)
        guest_pressure_ratio = _guest_pressure_ratio(running_resources, node.maxcpu)
        used_cpu = max(float(node.maxcpu) * node.cpu_ratio, 0.0)
        effective_total_cpu = max(float(node.maxcpu) * max(cpu_overcommit_ratio, 1.0), 0.0)
        raw_available_cpu = max(effective_total_cpu - used_cpu, 0.0)
        raw_available_memory = _raw_available_bytes(node.mem_bytes, node.maxmem_bytes)
        effective_total_disk = max(
            int(float(node.maxdisk_bytes) * max(disk_overcommit_ratio, 1.0)),
            0,
        )
        raw_available_disk = max(effective_total_disk - node.disk_bytes, 0)
        allocatable_cpu = _safe_available_float(raw_available_cpu, int(effective_total_cpu))
        allocatable_memory = _safe_available_int(raw_available_memory, node.maxmem_bytes)
        allocatable_disk = _safe_available_int(raw_available_disk, effective_total_disk)
        guest_overloaded = guest_pressure_ratio >= settings.guest_pressure_threshold

        capacities.append(
            NodeCapacity(
                node=node.node,
                status=node.status,
                gpu_count=node.gpu_count,
                allocatable_gpu_slots=max(
                    node.gpu_count - gpu_used.get(node.node, 0), 0
                ),
                running_resources=running_resources,
                guest_soft_limit=guest_soft_limit,
                guest_pressure_ratio=guest_pressure_ratio,
                guest_overloaded=guest_overloaded,
                candidate=(
                    node.status == "online"
                    and allocatable_cpu > 0
                    and allocatable_memory > 0
                    and allocatable_disk > 0
                    and not guest_overloaded
                ),
                cpu_ratio=node.cpu_ratio,
                memory_ratio=_ratio(node.mem_bytes, node.maxmem_bytes),
                disk_ratio=_ratio(node.disk_bytes, node.maxdisk_bytes),
                total_cpu_cores=round(effective_total_cpu, 2),
                allocatable_cpu_cores=allocatable_cpu,
                total_memory_bytes=node.maxmem_bytes,
                allocatable_memory_bytes=allocatable_memory,
                total_disk_bytes=effective_total_disk,
                allocatable_disk_bytes=allocatable_disk,
                current_loadavg_1=node.current_loadavg_1,
                average_loadavg_1=node.average_loadavg_1,
            )
        )

    return sorted(capacities, key=lambda item: item.node)


def _build_warnings(
    *,
    node_capacities: list[NodeCapacity],
    request: PlacementRequest,
    effective_resource_type: ResourceType,
    remaining: int,
) -> list[str]:
    warnings: list[str] = []
    overloaded = [item.node for item in node_capacities if item.guest_overloaded]
    if overloaded:
        warnings.append(f"Guest density is high on: {', '.join(overloaded)}.")
    if request.gpu_required > 0 and not any(
        item.gpu_count >= request.gpu_required for item in node_capacities
    ):
        warnings.append(f"No node currently exposes {request.gpu_required} GPU(s).")
    if request.resource_type != effective_resource_type:
        warnings.append(
            f"Requested {request.resource_type.upper()} is evaluated as "
            f"{effective_resource_type.upper()} for placement."
        )
    if remaining > 0:
        warnings.append(f"{remaining} instance(s) cannot be placed with current capacity.")
    return warnings


def _build_rationale(
    *,
    request: PlacementRequest,
    placement_decisions: list[PlacementDecision],
    effective_resource_type: ResourceType,
    node_capacities: list[NodeCapacity],
) -> list[str]:
    capacity_map = {item.node: item for item in node_capacities}
    reasons = [
        _resource_type_summary(
            requested_type=request.resource_type,
            effective_type=effective_resource_type,
            gpu_required=request.gpu_required,
        )
    ]
    for item in placement_decisions:
        baseline = capacity_map.get(item.node)
        if baseline is None:
            continue
        reasons.append(
            f"Node {item.node} keeps {item.remaining_cpu_cores:.2f} vCPU, "
            f"{item.remaining_memory_bytes / GIB:.1f} GiB RAM, and "
            f"{item.remaining_disk_bytes / GIB:.1f} GiB disk after placement; "
            f"status is {baseline.status}."
        )
    return reasons


def _build_summary_text(
    *,
    request: PlacementRequest,
    placement_decisions: list[PlacementDecision],
    effective_resource_type: ResourceType,
    assigned: int,
    remaining: int,
) -> str:
    request_label = _request_label(request)
    if not placement_decisions:
        return f"{request_label} cannot be placed on the current PVE nodes."

    distribution = ", ".join(f"{item.node} x{item.instance_count}" for item in placement_decisions)
    if remaining == 0:
        return (
            f"{request_label} can place all {assigned} "
            f"{effective_resource_type.upper()} instance(s): {distribution}."
        )
    return (
        f"{request_label} can place {assigned} / {request.instance_count} "
        f"{effective_resource_type.upper()} instance(s): {distribution}."
    )


def _decide_resource_type(request: PlacementRequest) -> tuple[ResourceType, str]:
    if request.resource_type == "lxc":
        if request.gpu_required > 0:
            return "vm", "GPU requests are evaluated as VM placement."
        return "lxc", "Linux container request can use LXC placement."
    return "vm", "VM request uses VM placement."


def _resource_type_summary(
    *,
    requested_type: ResourceType,
    effective_type: ResourceType,
    gpu_required: int,
) -> str:
    if requested_type != effective_type:
        return (
            f"Requested {requested_type.upper()} requires {gpu_required} GPU(s), "
            f"so placement uses {effective_type.upper()}."
        )
    return f"Placement uses {effective_type.upper()} capacity rules."


def _request_label(request: PlacementRequest) -> str:
    return f"{request.resource_type.upper()} request"


def _get_cached_cluster_state() -> _ClusterCacheEntry | None:
    with _cluster_cache_lock:
        if _cluster_cache is None:
            return None
        age = time.monotonic() - _cluster_cache.cached_at
        if age > settings.source_cache_ttl_seconds:
            return None
        return _cluster_cache


def _set_cached_cluster_state(
    *,
    nodes: list[NodeSnapshot],
    resources: list[ResourceSnapshot],
) -> None:
    if settings.source_cache_ttl_seconds <= 0:
        return

    with _cluster_cache_lock:
        global _cluster_cache
        _cluster_cache = _ClusterCacheEntry(
            cached_at=time.monotonic(),
            nodes=nodes,
            resources=resources,
        )


def _effective_cpu_cores(request: PlacementRequest, resource_type: ResourceType) -> float:
    requested = float(request.cpu_cores)
    hypervisor_overhead = 0.25 if resource_type == "vm" else 0.0
    return round(requested + hypervisor_overhead, 2)


def _effective_memory_bytes(request: PlacementRequest, resource_type: ResourceType) -> int:
    base = request.memory_mb * MIB
    hypervisor_overhead = 256 * MIB if resource_type == "vm" else 0
    return base + hypervisor_overhead


def _guest_soft_limit(maxcpu: int) -> int:
    return max(int(maxcpu * settings.guest_per_core_limit), 1)


def _guest_pressure_ratio(running_resources: int, maxcpu: int) -> float:
    guest_limit = _guest_soft_limit(maxcpu)
    if guest_limit <= 0:
        return 0.0
    return max(float(running_resources) / float(guest_limit), 0.0)


def _raw_available_bytes(used: int, total: int) -> int:
    return max(total - used, 0)


def _safe_available_float(raw_available: float, total: int) -> float:
    reserve = float(total) * settings.placement_headroom_ratio
    return max(raw_available - reserve, 0.0)


def _safe_available_int(raw_available: int, total: int) -> int:
    reserve = int(total * settings.placement_headroom_ratio)
    return max(raw_available - reserve, 0)


def _ratio(used: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return max(float(used) / float(total), 0.0)


def _optional_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_loadavg_1(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if parsed >= 0 else None
    if isinstance(value, (list, tuple)) and value:
        return _parse_loadavg_1(value[0])
    text = str(value).strip()
    if not text:
        return None
    for separator in [",", " ", "/"]:
        if separator in text:
            return _parse_loadavg_1(text.split(separator)[0])
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None
