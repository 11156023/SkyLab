"""儲存池超賣額度必須跨多台放置累計。

實體空間用完後 avail_gb 停在 0；若不另外記錄已用掉的超賣量，每次
預留都會把超賣額度重算成 total*(ratio-1)，同一個池子可無限放置。
"""

from __future__ import annotations

from app.domain.placement.models import PlacementTuning, WorkingStoragePool
from app.domain.placement.storage import (
    reserve_storage_pool,
    select_best_storage_for_request,
)


def _tuning() -> PlacementTuning:
    return PlacementTuning(
        reassignment_cost=0.5,
        peak_cpu_margin=0.1,
        peak_memory_margin=0.1,
        loadavg_warn_per_core=1.0,
        loadavg_max_per_core=2.0,
        loadavg_penalty_weight=1.0,
        disk_contention_warn_share=0.7,
        disk_contention_high_share=0.9,
        disk_penalty_weight=1.0,
    )


def _pool(*, total: float, avail: float) -> WorkingStoragePool:
    return WorkingStoragePool(
        storage="local-lvm",
        total_gb=total,
        avail_gb=avail,
        active=True,
        enabled=True,
        can_vm=True,
        can_lxc=True,
        is_shared=False,
        speed_tier="ssd",
        user_priority=5,
    )


def _place_until_full(pool: WorkingStoragePool, *, disk_gb: int, ratio: float) -> int:
    placed = 0
    for _ in range(20):
        selection = select_best_storage_for_request(
            storage_pools=[pool],
            resource_type="vm",
            disk_gb=disk_gb,
            disk_overcommit_ratio=ratio,
            tuning=_tuning(),
        )
        if selection is None:
            break
        reserve_storage_pool(
            selection=selection, disk_gb=disk_gb, disk_overcommit_ratio=ratio
        )
        placed += 1
    return placed


def test_overcommit_budget_is_consumed_across_placements() -> None:
    pool = _pool(total=100, avail=10)

    # 預算 = 100*1.5 - 90 = 60 GB：10 實體 + 20 超賣，再 30 超賣，第三台放不下
    assert _place_until_full(pool, disk_gb=30, ratio=1.5) == 2
    assert pool.avail_gb == 0
    assert pool.overcommit_used_gb == 50
    assert pool.overcommit_placed_count == 2


def test_physical_placements_do_not_touch_overcommit_budget() -> None:
    pool = _pool(total=100, avail=100)

    # 100 實體可放 5 台 20 GB，超賣額度 50 GB 再放 2 台（最後剩 10 GB 不夠）
    assert _place_until_full(pool, disk_gb=20, ratio=1.5) == 7
    assert pool.placed_count == 5
    assert pool.overcommit_placed_count == 2
    assert pool.overcommit_used_gb == 40


def test_no_overcommit_ratio_stops_at_physical_capacity() -> None:
    pool = _pool(total=100, avail=50)

    assert _place_until_full(pool, disk_gb=20, ratio=1.0) == 2
    assert pool.overcommit_used_gb == 0
