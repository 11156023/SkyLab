from __future__ import annotations

from app.domain.placement.models import (
    PlacementTuning,
    StorageSelection,
    WorkingStoragePool,
)
from app.domain.placement.schemas import ResourceType
from app.domain.placement.scorer import projected_share, storage_contention_penalty

STORAGE_SPEED_RANK = {"nvme": 0, "ssd": 1, "hdd": 2, "unknown": 3}


def select_best_storage_for_request(
    *,
    storage_pools: list[WorkingStoragePool],
    resource_type: ResourceType,
    disk_gb: int,
    disk_overcommit_ratio: float,
    tuning: PlacementTuning,
) -> StorageSelection | None:
    if disk_gb <= 0:
        return None

    capable = [
        pool
        for pool in storage_pools
        if pool.active
        and pool.enabled
        and ((resource_type == "lxc" and pool.can_lxc) or (resource_type == "vm" and pool.can_vm))
    ]
    if not capable:
        return None

    normal = [pool for pool in capable if pool.avail_gb + 1e-9 >= float(disk_gb)]
    if normal:
        chosen = min(
            normal,
            key=lambda pool: (
                STORAGE_SPEED_RANK.get(pool.speed_tier, 3),
                storage_contention_penalty(
                    projected_share=projected_share(
                        used=max(pool.total_gb - pool.avail_gb, 0.0) + float(disk_gb),
                        total=max(pool.total_gb, 1.0),
                    ),
                    placed_count=pool.placed_count,
                    overcommit_placed_count=pool.overcommit_placed_count,
                    tuning=tuning,
                    overcommit=False,
                ),
                int(pool.user_priority or 5),
                pool.placed_count,
                -float(pool.avail_gb),
                pool.storage,
            ),
        )
        selected_projected_share = projected_share(
            used=max(chosen.total_gb - chosen.avail_gb, 0.0) + float(disk_gb),
            total=max(chosen.total_gb, 1.0),
        )
        return StorageSelection(
            pool=chosen,
            projected_share=selected_projected_share,
            speed_rank=STORAGE_SPEED_RANK.get(chosen.speed_tier, 3),
            user_priority=int(chosen.user_priority or 5),
            contention_penalty=storage_contention_penalty(
                projected_share=selected_projected_share,
                placed_count=chosen.placed_count,
                overcommit_placed_count=chosen.overcommit_placed_count,
                tuning=tuning,
                overcommit=False,
            ),
        )

    overcommit = [
        pool
        for pool in capable
        if (
            max(
                float(pool.total_gb) * max(disk_overcommit_ratio, 1.0)
                - (pool.total_gb - pool.avail_gb),
                0.0,
            )
            + 1e-9
        )
        >= float(disk_gb)
    ]
    if not overcommit:
        return None

    chosen = min(
        overcommit,
        key=lambda pool: (
            storage_contention_penalty(
                projected_share=projected_share(
                    used=max(pool.total_gb - pool.avail_gb, 0.0) + float(disk_gb),
                    total=max(float(pool.total_gb) * max(disk_overcommit_ratio, 1.0), 1.0),
                ),
                placed_count=pool.placed_count,
                overcommit_placed_count=pool.overcommit_placed_count,
                tuning=tuning,
                overcommit=True,
            ),
            pool.overcommit_placed_count,
            STORAGE_SPEED_RANK.get(pool.speed_tier, 3),
            int(pool.user_priority or 5),
            -max(
                float(pool.total_gb) * max(disk_overcommit_ratio, 1.0)
                - (pool.total_gb - pool.avail_gb),
                0.0,
            ),
            pool.storage,
        ),
    )
    effective_total = max(float(chosen.total_gb) * max(disk_overcommit_ratio, 1.0), 1.0)
    current_used = max(chosen.total_gb - chosen.avail_gb, 0.0)
    selected_projected_share = projected_share(
        used=current_used + float(disk_gb),
        total=effective_total,
    )
    return StorageSelection(
        pool=chosen,
        projected_share=selected_projected_share,
        speed_rank=STORAGE_SPEED_RANK.get(chosen.speed_tier, 3),
        user_priority=int(chosen.user_priority or 5),
        contention_penalty=storage_contention_penalty(
            projected_share=selected_projected_share,
            placed_count=chosen.placed_count,
            overcommit_placed_count=chosen.overcommit_placed_count,
            tuning=tuning,
            overcommit=True,
        ),
    )


def reserve_storage_pool(
    *,
    selection: StorageSelection,
    disk_gb: int,
    disk_overcommit_ratio: float,
) -> None:
    pool = selection.pool
    remaining_physical = max(float(pool.avail_gb), 0.0)
    requested = float(max(disk_gb, 0))
    if remaining_physical + 1e-9 >= requested:
        pool.avail_gb = max(remaining_physical - requested, 0.0)
        pool.placed_count += 1
        return

    current_used = max(pool.total_gb - remaining_physical, 0.0)
    effective_total = max(float(pool.total_gb) * max(disk_overcommit_ratio, 1.0), float(pool.total_gb))
    remaining_effective = max(effective_total - current_used, 0.0)
    if remaining_effective + 1e-9 >= requested:
        pool.avail_gb = max(remaining_physical - requested, 0.0)
        pool.overcommit_placed_count += 1
        return

    raise ValueError(f"Storage pool {pool.storage} does not have enough capacity")
