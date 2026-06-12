from __future__ import annotations

import threading
import time
from collections import Counter
from dataclasses import dataclass
from math import floor

from app.domain.placement.config import settings
from app.domain.placement.schemas import (
    NodeCapacity,
    NodeSnapshot,
    PlacementDecision,
    PlacementPlan,
    PlacementRequest,
    ResourceSnapshot,
    ResourceType,
)
from app.services.proxmox import proxmox_service

GIB = 1024**3
MIB = 1024**2


@dataclass(frozen=True)
class _ClusterCacheEntry:
    cached_at: float
    nodes: list[NodeSnapshot]
    resources: list[ResourceSnapshot]


_cluster_cache: _ClusterCacheEntry | None = None
_cluster_cache_lock = threading.Lock()


def _load_cluster_state() -> tuple[list[NodeSnapshot], list[ResourceSnapshot]]:
    cached = _get_cached_cluster_state()
    if cached is not None:
        return cached.nodes, cached.resources

    gpu_map = settings.parsed_backend_node_gpu_map
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


def _build_rule_based_plan(
    *,
    request: PlacementRequest,
    node_capacities: list[NodeCapacity],
    effective_resource_type: ResourceType,
    resource_type_reason: str,
) -> PlacementPlan:
    working_nodes = [item.model_copy(deep=True) for item in node_capacities]
    required_cpu = _effective_cpu_cores(request, effective_resource_type)
    required_memory = _effective_memory_bytes(request, effective_resource_type)
    required_disk = request.disk_gb * GIB
    placements: dict[str, int] = {item.node: 0 for item in working_nodes}
    remaining = request.instance_count

    while remaining > 0:
        candidates = [
            item
            for item in working_nodes
            if item.candidate
            and _can_fit(
                item,
                cores=required_cpu,
                memory_bytes=required_memory,
                disk_bytes=required_disk,
                gpu_required=request.gpu_required,
            )
        ]
        if not candidates:
            break

        chosen = _choose_node(
            nodes=candidates,
            placements=placements,
            cores=required_cpu,
            memory_bytes=required_memory,
            disk_bytes=required_disk,
        )
        placements[chosen.node] += 1
        chosen.allocatable_cpu_cores = max(chosen.allocatable_cpu_cores - required_cpu, 0.0)
        chosen.allocatable_memory_bytes = max(chosen.allocatable_memory_bytes - required_memory, 0)
        chosen.allocatable_disk_bytes = max(chosen.allocatable_disk_bytes - required_disk, 0)
        chosen.running_resources += 1
        chosen.guest_pressure_ratio = _guest_pressure_ratio(
            chosen.running_resources,
            int(chosen.total_cpu_cores),
        )
        chosen.guest_overloaded = (
            chosen.guest_pressure_ratio >= settings.guest_pressure_threshold
        )
        chosen.candidate = (
            chosen.status == "online"
            and chosen.allocatable_cpu_cores > 0
            and chosen.allocatable_memory_bytes > 0
            and chosen.allocatable_disk_bytes > 0
            and not chosen.guest_overloaded
        )
        remaining -= 1

    assigned = request.instance_count - remaining
    placement_decisions = [
        PlacementDecision(
            node=item.node,
            instance_count=placements[item.node],
            cpu_cores_reserved=round(placements[item.node] * required_cpu, 2),
            memory_bytes_reserved=placements[item.node] * required_memory,
            disk_bytes_reserved=placements[item.node] * required_disk,
            remaining_cpu_cores=round(item.allocatable_cpu_cores, 2),
            remaining_memory_bytes=item.allocatable_memory_bytes,
            remaining_disk_bytes=item.allocatable_disk_bytes,
        )
        for item in working_nodes
        if placements[item.node] > 0
    ]
    placement_decisions.sort(key=lambda item: (-item.instance_count, item.node))

    return PlacementPlan(
        feasible=remaining == 0,
        requested_resource_type=request.resource_type,
        effective_resource_type=effective_resource_type,
        resource_type_reason=resource_type_reason,
        assigned_instances=assigned,
        unassigned_instances=remaining,
        recommended_node=placement_decisions[0].node if placement_decisions else None,
        summary=_build_summary_text(
            request=request,
            placement_decisions=placement_decisions,
            effective_resource_type=effective_resource_type,
            assigned=assigned,
            remaining=remaining,
        ),
        rationale=_build_rationale(
            request=request,
            placement_decisions=placement_decisions,
            effective_resource_type=effective_resource_type,
            node_capacities=node_capacities,
        ),
        warnings=_build_warnings(
            node_capacities=node_capacities,
            request=request,
            effective_resource_type=effective_resource_type,
            remaining=remaining,
        ),
        placements=placement_decisions,
        candidate_nodes=node_capacities,
    )


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


def _resource_type_reason_from_choice(
    *,
    request: PlacementRequest,
    effective_resource_type: ResourceType,
) -> str:
    if request.resource_type == effective_resource_type:
        return _decide_resource_type(request)[1]
    return f"Placement selected {effective_resource_type.upper()} capacity rules."


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


def _choose_node(
    *,
    nodes: list[NodeCapacity],
    placements: dict[str, int],
    cores: float,
    memory_bytes: int,
    disk_bytes: int,
) -> NodeCapacity:
    return max(
        nodes,
        key=lambda item: (
            _fit_count(item, cores=cores, memory_bytes=memory_bytes, disk_bytes=disk_bytes),
            _weighted_headroom_score(
                item,
                cores=cores,
                memory_bytes=memory_bytes,
                disk_bytes=disk_bytes,
            ),
            -placements[item.node],
            -item.guest_pressure_ratio,
        ),
    )


def _fit_count(
    node: NodeCapacity,
    *,
    cores: float,
    memory_bytes: int,
    disk_bytes: int,
) -> int:
    cpu_fit = floor(node.allocatable_cpu_cores / float(cores)) if cores > 0 else 0
    memory_fit = floor(node.allocatable_memory_bytes / memory_bytes) if memory_bytes > 0 else 0
    disk_fit = floor(node.allocatable_disk_bytes / disk_bytes) if disk_bytes > 0 else 0
    guest_fit = max(node.guest_soft_limit - node.running_resources, 0)
    return max(min(cpu_fit, memory_fit, disk_fit, guest_fit), 0)


def _weighted_headroom_score(
    node: NodeCapacity,
    *,
    cores: float,
    memory_bytes: int,
    disk_bytes: int,
) -> float:
    cpu_total = max(node.total_cpu_cores, 1.0)
    memory_total = max(float(node.total_memory_bytes), 1.0)
    disk_total = max(float(node.total_disk_bytes), 1.0)
    guest_total = max(float(node.guest_soft_limit), 1.0)

    cpu_headroom = max(node.allocatable_cpu_cores - cores, 0.0) / cpu_total
    memory_headroom = max(node.allocatable_memory_bytes - memory_bytes, 0) / memory_total
    disk_headroom = max(node.allocatable_disk_bytes - disk_bytes, 0) / disk_total
    guest_headroom = max(node.guest_soft_limit - node.running_resources - 1, 0) / guest_total

    return (
        (settings.placement_weight_cpu * cpu_headroom)
        + (settings.placement_weight_memory * memory_headroom)
        + (settings.placement_weight_disk * disk_headroom)
        + (settings.placement_weight_guest * guest_headroom)
    )


def _can_fit(
    node: NodeCapacity,
    *,
    cores: float,
    memory_bytes: int,
    disk_bytes: int,
    gpu_required: int,
) -> bool:
    return (
        node.allocatable_cpu_cores >= cores
        and node.allocatable_memory_bytes >= memory_bytes
        and node.allocatable_disk_bytes >= disk_bytes
        and node.gpu_count >= gpu_required
        and node.running_resources < node.guest_soft_limit
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
