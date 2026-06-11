from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from app.domain.placement import advisor as placement_advisor
from app.domain.placement.schemas import (
    NodeCapacity,
    PlacementDecision,
    PlacementPlan,
    PlacementRequest,
    ResourceType,
)
from app.domain.placement import policy as placement_policy
from app.domain.placement import scorer as placement_scorer
from app.domain.placement.models import (
    PlacementTuning,
    StorageSelection,
    WorkingStoragePool,
)
from app.domain.placement.storage import (
    reserve_storage_pool,
    select_best_storage_for_request,
)
from app.models import VMRequest
from app.repositories import proxmox_storage as proxmox_storage_repo

GIB = 1024**3


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def request_window(db_request: VMRequest) -> tuple[datetime | None, datetime | None]:
    return normalize_datetime(db_request.start_at), normalize_datetime(db_request.end_at)


def request_capacity_tuple(db_request: VMRequest) -> tuple[float, int, int]:
    cpu_cores = float(db_request.cores or 1)
    memory_bytes = int(db_request.memory or 512) * 1024 * 1024
    disk_gb = (
        int(db_request.disk_size or 0)
        if db_request.resource_type == "vm"
        else int(db_request.rootfs_size or 0)
    )
    if disk_gb <= 0:
        disk_gb = 20 if db_request.resource_type == "vm" else 8
    return cpu_cores, memory_bytes, disk_gb * GIB


def build_storage_pool_state(
    *,
    session: Session,
    node_names: list[str],
) -> tuple[dict[str, list[WorkingStoragePool]], bool]:
    storages = proxmox_storage_repo.get_all_storages(session)
    if not storages:
        return {node_name: [] for node_name in node_names}, False

    shared_registry: dict[str, WorkingStoragePool] = {}
    by_node: dict[str, list[WorkingStoragePool]] = {node_name: [] for node_name in node_names}
    node_set = set(node_names)

    for storage in storages:
        node_name = str(storage.node_name or "")
        if node_name not in node_set:
            continue

        if storage.is_shared:
            pool = shared_registry.get(storage.storage)
            if pool is None:
                pool = WorkingStoragePool(
                    storage=storage.storage,
                    total_gb=float(storage.total_gb or 0.0),
                    avail_gb=float(storage.avail_gb or 0.0),
                    active=bool(storage.active),
                    enabled=bool(storage.enabled),
                    can_vm=bool(storage.can_vm),
                    can_lxc=bool(storage.can_lxc),
                    is_shared=bool(storage.is_shared),
                    speed_tier=str(storage.speed_tier or "unknown"),
                    user_priority=int(storage.user_priority or 5),
                )
                shared_registry[storage.storage] = pool
            by_node[node_name].append(pool)
            continue

        by_node[node_name].append(
            WorkingStoragePool(
                storage=storage.storage,
                total_gb=float(storage.total_gb or 0.0),
                avail_gb=float(storage.avail_gb or 0.0),
                active=bool(storage.active),
                enabled=bool(storage.enabled),
                can_vm=bool(storage.can_vm),
                can_lxc=bool(storage.can_lxc),
                is_shared=bool(storage.is_shared),
                speed_tier=str(storage.speed_tier or "unknown"),
                user_priority=int(storage.user_priority or 5),
            )
        )

    has_managed_storage = any(pools for pools in by_node.values())
    return by_node, has_managed_storage


def provisioned_current_node(request: VMRequest) -> str | None:
    if request.vmid is None:
        return None
    current = str(request.actual_node or "").strip()
    if current:
        return current
    assigned = str(request.assigned_node or "").strip()
    return assigned or None


def build_rebalance_baseline_nodes(
    *,
    session: Session,
    requests: list[VMRequest],
    get_overcommit_ratios_fn,
    release_request_from_capacities_fn,
) -> list[NodeCapacity]:
    nodes, resources = placement_advisor._load_cluster_state()
    cpu_overcommit_ratio, disk_overcommit_ratio = get_overcommit_ratios_fn(session)
    working_nodes = placement_advisor._build_node_capacities(
        nodes=nodes,
        resources=resources,
        cpu_overcommit_ratio=cpu_overcommit_ratio,
        disk_overcommit_ratio=disk_overcommit_ratio,
    )
    for request in requests:
        if request.vmid is not None:
            release_request_from_capacities_fn(
                node_capacities=working_nodes,
                db_request=request,
                node_name=str(request.actual_node or request.assigned_node or ""),
            )
    return working_nodes


def build_preview_vm_request(
    *,
    request: PlacementRequest,
    start_at: datetime,
    end_at: datetime,
) -> VMRequest:
    is_vm = str(request.resource_type) == "vm"
    return VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="placement-preview",
        resource_type=str(request.resource_type),
        hostname="placement-preview",
        cores=int(request.cpu_cores or 1),
        memory=int(request.memory_mb or 512),
        password="preview",
        storage="preview",
        environment_type="Preview",
        start_at=start_at,
        end_at=end_at,
        ostemplate=None if is_vm else "preview",
        rootfs_size=None if is_vm else int(request.disk_gb or 0),
        unprivileged=True,
        template_id=1 if is_vm else None,
        disk_size=int(request.disk_gb or 0) if is_vm else None,
        username="preview" if is_vm else None,
        created_at=utc_now(),
    )


def refresh_node_candidate(node: NodeCapacity) -> None:
    node.guest_pressure_ratio = placement_advisor._guest_pressure_ratio(
        int(node.running_resources),
        int(node.total_cpu_cores),
    )
    node.guest_overloaded = (
        node.guest_pressure_ratio >= placement_advisor.settings.guest_pressure_threshold
    )
    node.candidate = (
        node.status == "online"
        and node.allocatable_cpu_cores > 0
        and node.allocatable_memory_bytes > 0
        and node.allocatable_disk_bytes > 0
        and not node.guest_overloaded
    )


def release_request_from_capacities(
    *,
    node_capacities: list[NodeCapacity],
    db_request: VMRequest,
    node_name: str | None,
    request_capacity_tuple_fn,
    refresh_node_candidate_fn,
) -> None:
    if not node_name:
        return
    node = next((item for item in node_capacities if item.node == node_name), None)
    if node is None:
        return

    cpu_cores, memory_bytes, disk_bytes = request_capacity_tuple_fn(db_request)
    node.allocatable_cpu_cores = min(
        round(node.allocatable_cpu_cores + cpu_cores, 2),
        round(float(node.total_cpu_cores), 2),
    )
    node.allocatable_memory_bytes = min(
        node.allocatable_memory_bytes + memory_bytes,
        int(node.total_memory_bytes),
    )
    node.allocatable_disk_bytes = min(
        node.allocatable_disk_bytes + disk_bytes,
        int(node.total_disk_bytes),
    )
    node.running_resources = max(int(node.running_resources) - 1, 0)
    refresh_node_candidate_fn(node)


def reserve_request_on_capacities(
    *,
    node_capacities: list[NodeCapacity],
    db_request: VMRequest,
    node_name: str,
    request_capacity_tuple_fn,
    refresh_node_candidate_fn,
) -> None:
    node = next((item for item in node_capacities if item.node == node_name), None)
    if node is None:
        raise ValueError(f"Target node {node_name} not found in capacity list")

    cpu_cores, memory_bytes, disk_bytes = request_capacity_tuple_fn(db_request)
    node.allocatable_cpu_cores = max(round(node.allocatable_cpu_cores - cpu_cores, 2), 0.0)
    node.allocatable_memory_bytes = max(node.allocatable_memory_bytes - memory_bytes, 0)
    node.allocatable_disk_bytes = max(node.allocatable_disk_bytes - disk_bytes, 0)
    node.running_resources = int(node.running_resources) + 1
    refresh_node_candidate_fn(node)


def hour_window_iter(start_at: datetime, end_at: datetime) -> list[datetime]:
    if end_at <= start_at:
        return [start_at]
    cursor = start_at.replace(minute=0, second=0, microsecond=0)
    if cursor < start_at:
        cursor += timedelta(hours=1)
    checkpoints: list[datetime] = []
    while cursor < end_at:
        checkpoints.append(cursor)
        cursor += timedelta(hours=1)
    return checkpoints or [start_at]


def apply_reserved_requests_to_capacities(
    *,
    baseline_capacities,
    reserved_requests: list[VMRequest],
    at_time: datetime,
    normalize_datetime_fn,
    request_capacity_tuple_fn,
):
    adjusted = [item.model_copy(deep=True) for item in baseline_capacities]
    by_node = {item.node: item for item in adjusted}

    for reserved in reserved_requests:
        reserved_start = normalize_datetime_fn(reserved.start_at)
        reserved_end = normalize_datetime_fn(reserved.end_at)
        assigned_node = str(reserved.assigned_node or "")
        if not reserved_start or not reserved_end or not assigned_node:
            continue
        if not (reserved_start <= at_time < reserved_end):
            continue

        node = by_node.get(assigned_node)
        if not node:
            continue

        reserved_cpu, reserved_memory, reserved_disk = request_capacity_tuple_fn(reserved)
        node.allocatable_cpu_cores = max(node.allocatable_cpu_cores - reserved_cpu, 0.0)
        node.allocatable_memory_bytes = max(node.allocatable_memory_bytes - reserved_memory, 0)
        node.allocatable_disk_bytes = max(node.allocatable_disk_bytes - reserved_disk, 0)
        node.candidate = (
            node.status == "online"
            and node.allocatable_cpu_cores > 0
            and node.allocatable_memory_bytes > 0
            and node.allocatable_disk_bytes > 0
        )

    return adjusted


def build_plan(
    *,
    session: Session,
    request: PlacementRequest,
    node_capacities: list[NodeCapacity],
    effective_resource_type: ResourceType,
    resource_type_reason: str,
    placement_strategy: str | None = None,
    node_priorities: dict[str, int] | None = None,
    current_node: str | None = None,
    build_storage_pool_state_fn,
    get_placement_tuning_fn,
    get_overcommit_ratios_fn,
    get_node_priorities_fn,
    placement_sort_key_fn,
) -> PlacementPlan:
    strategy = placement_policy.normalize_strategy(
        placement_strategy or placement_policy.get_placement_strategy(session)
    )
    priorities = node_priorities or get_node_priorities_fn(session)
    tuning = get_placement_tuning_fn(session=session)
    working_nodes = [item.model_copy(deep=True) for item in node_capacities]
    storage_pools_by_node, has_managed_storage = build_storage_pool_state_fn(
        session=session,
        node_names=[item.node for item in working_nodes],
    )
    _, disk_overcommit_ratio = get_overcommit_ratios_fn(session)
    required_cpu = placement_advisor._effective_cpu_cores(request, effective_resource_type)
    required_memory = placement_advisor._effective_memory_bytes(request, effective_resource_type)
    required_disk = request.disk_gb * GIB
    placements: dict[str, int] = {item.node: 0 for item in working_nodes}
    remaining = request.instance_count

    while remaining > 0:
        candidates: list[tuple[NodeCapacity, StorageSelection | None]] = []
        for item in working_nodes:
            if not item.candidate or not placement_advisor._can_fit(
                item,
                cores=required_cpu,
                memory_bytes=required_memory,
                disk_bytes=required_disk,
                gpu_required=request.gpu_required,
            ):
                continue
            storage_selection: StorageSelection | None = None
            if has_managed_storage:
                storage_selection = select_best_storage_for_request(
                    storage_pools=storage_pools_by_node.get(item.node, []),
                    resource_type=str(request.resource_type),
                    disk_gb=int(request.disk_gb),
                    disk_overcommit_ratio=disk_overcommit_ratio,
                    tuning=tuning,
                )
                if storage_selection is None:
                    continue
            candidates.append((item, storage_selection))
        if not candidates:
            break

        chosen, chosen_storage = min(
            candidates,
            key=lambda candidate: placement_sort_key_fn(
                candidate[0],
                placements=placements,
                priorities=priorities,
                strategy=strategy,
                cores=required_cpu,
                memory_bytes=required_memory,
                disk_bytes=required_disk,
                storage_selection=candidate[1],
                tuning=tuning,
                current_node=current_node,
            ),
        )
        placements[chosen.node] += 1
        chosen.allocatable_cpu_cores = max(chosen.allocatable_cpu_cores - required_cpu, 0.0)
        chosen.allocatable_memory_bytes = max(chosen.allocatable_memory_bytes - required_memory, 0)
        chosen.allocatable_disk_bytes = max(chosen.allocatable_disk_bytes - required_disk, 0)
        chosen.running_resources += 1
        refresh_node_candidate(chosen)
        if chosen_storage is not None:
            reserve_storage_pool(
                selection=chosen_storage,
                disk_gb=int(request.disk_gb),
                disk_overcommit_ratio=disk_overcommit_ratio,
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
        summary=placement_advisor._build_summary_text(
            request=request,
            placement_decisions=placement_decisions,
            effective_resource_type=effective_resource_type,
            assigned=assigned,
            remaining=remaining,
        ),
        rationale=placement_advisor._build_rationale(
            request=request,
            placement_decisions=placement_decisions,
            effective_resource_type=effective_resource_type,
            node_capacities=node_capacities,
        ),
        warnings=placement_advisor._build_warnings(
            node_capacities=node_capacities,
            request=request,
            effective_resource_type=effective_resource_type,
            remaining=remaining,
        ),
        placements=placement_decisions,
        candidate_nodes=node_capacities,
    )


def build_preview_selection_reasons(
    *,
    selected_node: str,
    selected_eval,
    candidate_evals: dict,
    priorities: dict[str, int],
) -> list[str]:
    alternatives = [
        (node, evaluation)
        for node, evaluation in candidate_evals.items()
        if node != selected_node and evaluation.feasible
    ]
    if not alternatives:
        return [f"因為 {selected_node} 是目前這個時段唯一可行的節點。"]

    runner_up_node, runner_up_eval = min(alternatives, key=lambda item: item[1].objective)
    reasons = [
        (
            f"因為把本次申請放在 {selected_node}，可以讓這個時段整體 cohort "
            "的最大節點負載分數更低。"
        )
    ]

    if selected_eval.max_node_score + 0.01 < runner_up_eval.max_node_score:
        bottleneck_node = max(
            (runner_up_eval.node_scores or {}).items(),
            key=lambda item: item[1],
            default=(runner_up_node, runner_up_eval.max_node_score),
        )[0]
        reasons.append(f"因為可降低 {bottleneck_node} 的整體負載尖峰風險。")

    selected_storage_penalty = (selected_eval.storage_penalties or {}).get(selected_node, 0.0)
    runner_up_storage_penalty = (runner_up_eval.storage_penalties or {}).get(runner_up_node, 0.0)
    if selected_storage_penalty + 0.08 < runner_up_storage_penalty:
        reasons.append(
            f"因為 {selected_node} 的磁碟 contention 風險較低，可避免把壓力集中到 {runner_up_node}。"
        )

    if selected_eval.movement_count < runner_up_eval.movement_count:
        delta = runner_up_eval.movement_count - selected_eval.movement_count
        reasons.append(f"因為不需要多搬 {delta} 台 VM。")

    selected_priority = priorities.get(selected_node, 5)
    runner_up_priority = priorities.get(runner_up_node, 5)
    if (
        selected_priority < runner_up_priority
        and abs(selected_eval.total_score - runner_up_eval.total_score) <= 0.15
    ):
        reasons.append(f"在平衡結果接近時，{selected_node} 的節點優先級也比較高。")

    return reasons[:4]


def placement_sort_key(
    node: NodeCapacity,
    *,
    placements: dict[str, int],
    priorities: dict[str, int],
    strategy: str,
    cores: float,
    memory_bytes: int,
    disk_bytes: int,
    storage_selection: StorageSelection | None = None,
    tuning: PlacementTuning | None = None,
    current_node: str | None = None,
) -> tuple:
    tuning = tuning or PlacementTuning(
        migration_cost=0.15,
        peak_cpu_margin=1.1,
        peak_memory_margin=1.05,
        loadavg_warn_per_core=0.8,
        loadavg_max_per_core=1.5,
        loadavg_penalty_weight=0.9,
        disk_contention_warn_share=0.7,
        disk_contention_high_share=0.9,
        disk_penalty_weight=0.75,
        search_max_relocations=2,
        search_depth=3,
    )
    projected_cpu_share = placement_scorer.projected_share(
        used=max(node.total_cpu_cores - node.allocatable_cpu_cores, 0.0) + cores,
        total=max(node.total_cpu_cores, 1.0),
    )
    projected_memory_share = placement_scorer.projected_share(
        used=max(node.total_memory_bytes - node.allocatable_memory_bytes, 0) + memory_bytes,
        total=max(node.total_memory_bytes, 1),
    )
    projected_disk_share = placement_scorer.projected_share(
        used=max(node.total_disk_bytes - node.allocatable_disk_bytes, 0) + disk_bytes,
        total=max(node.total_disk_bytes, 1),
    )
    w_cpu = tuning.resource_weight_cpu
    w_mem = tuning.resource_weight_memory
    w_disk = tuning.resource_weight_disk
    weighted_shares = [
        projected_cpu_share * w_cpu,
        projected_memory_share * w_mem,
        projected_disk_share * w_disk,
    ]
    dominant_share = max(weighted_shares)
    weight_sum = w_cpu + w_mem + w_disk
    average_share = sum(weighted_shares) / max(weight_sum, 0.01)
    peak_penalty = placement_scorer.peak_penalty(
        projected_cpu_share=placement_scorer.projected_share(
            used=max(node.total_cpu_cores - node.allocatable_cpu_cores, 0.0)
            + (cores * tuning.peak_cpu_margin),
            total=max(node.total_cpu_cores, 1.0),
        ),
        projected_memory_share=placement_scorer.projected_share(
            used=max(node.total_memory_bytes - node.allocatable_memory_bytes, 0)
            + int(memory_bytes * tuning.peak_memory_margin),
            total=max(node.total_memory_bytes, 1),
        ),
        tuning=tuning,
    )
    loadavg_penalty = placement_scorer.loadavg_penalty(
        placement_scorer.reference_loadavg_per_core(node),
        tuning=tuning,
    )
    cpu_contention = placement_scorer.cpu_contention_penalty(projected_cpu_share, tuning=tuning)
    cpu_contention_score = cpu_contention * tuning.cpu_contention_weight
    memory_overflow_penalty = (
        tuning.memory_overflow_weight if projected_memory_share > 1.0 + 1e-9 else 0.0
    )
    migration_penalty = tuning.migration_cost if current_node and current_node != node.node else 0.0
    disk_penalty = (
        storage_selection.contention_penalty * tuning.disk_penalty_weight
        if storage_selection is not None
        else 0.0
    )
    total_score = (
        dominant_share
        + peak_penalty
        + cpu_contention_score
        + memory_overflow_penalty
        + (loadavg_penalty * tuning.loadavg_penalty_weight)
        + migration_penalty
        + disk_penalty
    )
    placement_count = placements.get(node.node, 0)
    storage_speed_rank = storage_selection.speed_rank if storage_selection is not None else 99
    storage_user_priority = storage_selection.user_priority if storage_selection is not None else 99
    storage_projected_share = storage_selection.projected_share if storage_selection is not None else 1.0
    return (
        total_score,
        dominant_share,
        average_share,
        priorities.get(node.node, 5),
        placement_count,
        projected_cpu_share,
        storage_speed_rank,
        storage_user_priority,
        storage_projected_share,
        node.node,
    )


def to_placement_request(db_request: VMRequest) -> PlacementRequest:
    disk_gb = (
        int(db_request.disk_size or 0)
        if db_request.resource_type == "vm"
        else int(db_request.rootfs_size or 0)
    )
    if disk_gb <= 0:
        disk_gb = 20 if db_request.resource_type == "vm" else 8
    return PlacementRequest(
        resource_type=db_request.resource_type,
        cpu_cores=int(db_request.cores or 1),
        memory_mb=int(db_request.memory or 512),
        disk_gb=disk_gb,
        instance_count=1,
        gpu_required=1 if bool(getattr(db_request, "gpu_mapping_id", None)) else 0,
    )
