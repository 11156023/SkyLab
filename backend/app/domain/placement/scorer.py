from __future__ import annotations

from app.domain.placement.models import PlacementTuning
from app.domain.placement.schemas import NodeCapacity


def projected_share(*, used: float | int, total: float | int) -> float:
    denominator = float(total or 1.0)
    return float(used) / denominator if denominator > 0 else 1.0


def linear_penalty(value: float, *, low: float, high: float) -> float:
    if value <= low:
        return 0.0
    if value >= high:
        return 1.0
    denominator = max(high - low, 0.0001)
    return (value - low) / denominator


def storage_contention_penalty(
    *,
    projected_share: float,
    placed_count: int,
    overcommit_placed_count: int,
    tuning: PlacementTuning,
    overcommit: bool,
) -> float:
    share_penalty = linear_penalty(
        projected_share,
        low=tuning.disk_contention_warn_share,
        high=max(
            tuning.disk_contention_high_share,
            tuning.disk_contention_warn_share + 0.01,
        ),
    )
    placement_penalty = min(
        (max(int(placed_count), 0) + max(int(overcommit_placed_count), 0)) / 6.0,
        1.0,
    ) * 0.35
    overcommit_penalty = 0.5 if overcommit else 0.0
    return share_penalty + placement_penalty + overcommit_penalty


def peak_penalty(
    *,
    projected_cpu_share: float,
    projected_memory_share: float,
    tuning: PlacementTuning,
) -> float:
    return max(
        linear_penalty(
            projected_cpu_share,
            low=tuning.cpu_peak_warn_share,
            high=max(tuning.cpu_peak_high_share, tuning.cpu_peak_warn_share + 0.01),
        ),
        linear_penalty(
            projected_memory_share,
            low=tuning.memory_peak_warn_share,
            high=max(
                tuning.memory_peak_high_share,
                tuning.memory_peak_warn_share + 0.01,
            ),
        ),
    )


def cpu_contention_penalty(
    projected_cpu_share: float,
    *,
    tuning: PlacementTuning,
) -> float:
    return linear_penalty(
        projected_cpu_share,
        low=tuning.cpu_peak_warn_share,
        high=max(tuning.cpu_peak_high_share, tuning.cpu_peak_warn_share + 0.01),
    )


def loadavg_penalty(
    loadavg_per_core: float | None,
    *,
    tuning: PlacementTuning,
) -> float:
    if loadavg_per_core is None or loadavg_per_core <= tuning.loadavg_warn_per_core:
        return 0.0
    if loadavg_per_core >= tuning.loadavg_max_per_core:
        return 1.0
    denominator = max(
        tuning.loadavg_max_per_core - tuning.loadavg_warn_per_core,
        0.01,
    )
    return (loadavg_per_core - tuning.loadavg_warn_per_core) / denominator


def reference_loadavg_per_core(node: NodeCapacity) -> float | None:
    total_cpu = max(float(node.total_cpu_cores or 0.0), 0.0)
    if total_cpu <= 0:
        return None
    reference = max(
        float(node.current_loadavg_1 or 0.0),
        float(node.average_loadavg_1 or 0.0),
    )
    if reference <= 0:
        return None
    return reference / total_cpu


def node_balance_score(node: NodeCapacity, *, tuning: PlacementTuning) -> float:
    cpu_share = projected_share(
        used=max(node.total_cpu_cores - node.allocatable_cpu_cores, 0.0),
        total=max(node.total_cpu_cores, 1.0),
    )
    memory_share = projected_share(
        used=max(node.total_memory_bytes - node.allocatable_memory_bytes, 0),
        total=max(node.total_memory_bytes, 1),
    )
    disk_share = projected_share(
        used=max(node.total_disk_bytes - node.allocatable_disk_bytes, 0),
        total=max(node.total_disk_bytes, 1),
    )
    w_cpu = tuning.resource_weight_cpu
    w_mem = tuning.resource_weight_memory
    w_disk = tuning.resource_weight_disk
    weighted_shares = [
        cpu_share * w_cpu,
        memory_share * w_mem,
        disk_share * w_disk,
    ]
    dominant_share = max(weighted_shares)
    weight_sum = w_cpu + w_mem + w_disk
    average_share = sum(weighted_shares) / max(weight_sum, 0.01)
    cpu_contention = cpu_contention_penalty(cpu_share, tuning=tuning)
    memory_overflow = tuning.memory_overflow_weight if memory_share > 1.0 + 1e-9 else 0.0
    return (
        dominant_share
        + (average_share * 0.2)
        + peak_penalty(
            projected_cpu_share=cpu_share,
            projected_memory_share=memory_share,
            tuning=tuning,
        )
        + (cpu_contention * tuning.cpu_contention_weight)
        + memory_overflow
        + (loadavg_penalty(reference_loadavg_per_core(node), tuning=tuning) * tuning.loadavg_penalty_weight)
    )
