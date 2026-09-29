from __future__ import annotations

from dataclasses import dataclass

DEFAULT_CPU_PEAK_WARN_SHARE = 0.7
DEFAULT_CPU_PEAK_HIGH_SHARE = 1.2
DEFAULT_RAM_PEAK_WARN_SHARE = 0.8
DEFAULT_RAM_PEAK_HIGH_SHARE = 0.85


@dataclass
class WorkingStoragePool:
    storage: str
    total_gb: float
    avail_gb: float
    active: bool
    enabled: bool
    can_vm: bool
    can_lxc: bool
    is_shared: bool
    speed_tier: str
    user_priority: int
    placed_count: int = 0
    overcommit_placed_count: int = 0
    # 已用掉的超賣額度（GB）。實體空間用完後 avail_gb 停在 0，
    # 必須另外累計，否則每次預留都會把超賣額度重新算成滿的。
    overcommit_used_gb: float = 0.0


@dataclass
class StorageSelection:
    pool: WorkingStoragePool
    projected_share: float
    speed_rank: int
    user_priority: int
    contention_penalty: float


@dataclass(frozen=True)
class PlacementTuning:
    reassignment_cost: float
    peak_cpu_margin: float
    peak_memory_margin: float
    loadavg_warn_per_core: float
    loadavg_max_per_core: float
    loadavg_penalty_weight: float
    disk_contention_warn_share: float
    disk_contention_high_share: float
    disk_penalty_weight: float
    cpu_peak_warn_share: float = DEFAULT_CPU_PEAK_WARN_SHARE
    cpu_peak_high_share: float = DEFAULT_CPU_PEAK_HIGH_SHARE
    memory_peak_warn_share: float = DEFAULT_RAM_PEAK_WARN_SHARE
    memory_peak_high_share: float = DEFAULT_RAM_PEAK_HIGH_SHARE
    resource_weight_cpu: float = 1.0
    resource_weight_memory: float = 1.0
    resource_weight_disk: float = 1.0
    cpu_contention_weight: float = 2.0
    memory_overflow_weight: float = 5.0


@dataclass
class NodeScoreBreakdown:
    node: str
    balance_score: float = 0.0
    cpu_share: float = 0.0
    memory_share: float = 0.0
    disk_share: float = 0.0
    peak_penalty: float = 0.0
    loadavg_penalty: float = 0.0
    storage_penalty: float = 0.0
    reassignment_cost: float = 0.0
    priority: int = 5
    is_selected: bool = False
    reason: str | None = None
