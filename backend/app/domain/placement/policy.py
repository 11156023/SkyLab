from __future__ import annotations

from sqlmodel import Session

from app.domain.placement.constants import DEFAULT_PLACEMENT_STRATEGY
from app.domain.placement.models import (
    DEFAULT_CPU_PEAK_HIGH_SHARE,
    DEFAULT_CPU_PEAK_WARN_SHARE,
    DEFAULT_RAM_PEAK_HIGH_SHARE,
    DEFAULT_RAM_PEAK_WARN_SHARE,
    PlacementTuning,
)
from app.repositories import proxmox_config as proxmox_config_repo
from app.repositories import proxmox_node as proxmox_node_repo


def get_placement_tuning(*, session: Session) -> PlacementTuning:
    config = proxmox_config_repo.get_proxmox_config(session)
    if config is None:
        return PlacementTuning(
            reassignment_cost=0.15,
            peak_cpu_margin=1.1,
            peak_memory_margin=1.05,
            loadavg_warn_per_core=0.8,
            loadavg_max_per_core=1.5,
            loadavg_penalty_weight=0.9,
            disk_contention_warn_share=0.7,
            disk_contention_high_share=0.9,
            disk_penalty_weight=0.75,
        )
    return PlacementTuning(
        reassignment_cost=max(float(config.placement_reassignment_cost or 0.15), 0.0),
        peak_cpu_margin=max(float(config.placement_peak_cpu_margin or 1.1), 1.0),
        peak_memory_margin=max(float(config.placement_peak_memory_margin or 1.05), 1.0),
        loadavg_warn_per_core=max(float(config.placement_loadavg_warn_per_core or 0.8), 0.0),
        loadavg_max_per_core=max(float(config.placement_loadavg_max_per_core or 1.5), 0.01),
        loadavg_penalty_weight=max(float(config.placement_loadavg_penalty_weight or 0.9), 0.0),
        disk_contention_warn_share=max(
            float(config.placement_disk_contention_warn_share or 0.7),
            0.0,
        ),
        disk_contention_high_share=max(
            float(config.placement_disk_contention_high_share or 0.9),
            0.01,
        ),
        disk_penalty_weight=max(float(config.placement_disk_penalty_weight or 0.75), 0.0),
        cpu_peak_warn_share=max(
            float(config.placement_cpu_peak_warn_share or DEFAULT_CPU_PEAK_WARN_SHARE),
            0.0,
        ),
        cpu_peak_high_share=max(
            float(config.placement_cpu_peak_high_share or DEFAULT_CPU_PEAK_HIGH_SHARE),
            0.01,
        ),
        memory_peak_warn_share=max(
            float(config.placement_memory_peak_warn_share or DEFAULT_RAM_PEAK_WARN_SHARE),
            0.0,
        ),
        memory_peak_high_share=max(
            float(config.placement_memory_peak_high_share or DEFAULT_RAM_PEAK_HIGH_SHARE),
            0.01,
        ),
        resource_weight_cpu=max(float(config.placement_resource_weight_cpu or 1.0), 0.0),
        resource_weight_memory=max(float(config.placement_resource_weight_memory or 1.0), 0.0),
        resource_weight_disk=max(float(config.placement_resource_weight_disk or 1.0), 0.0),
    )


def get_overcommit_ratios(session: Session) -> tuple[float, float]:
    config = proxmox_config_repo.get_proxmox_config(session)
    if not config:
        return 1.0, 1.0

    return (
        max(float(config.cpu_overcommit_ratio or 1.0), 1.0),
        max(float(config.disk_overcommit_ratio or 1.0), 1.0),
    )


def get_node_priorities(session: Session) -> dict[str, int]:
    return {
        item.name: int(item.priority)
        for item in proxmox_node_repo.get_all_nodes(session)
    }


def get_placement_strategy(session: Session) -> str:
    """目前只有一種排程策略。

    評分行為由 PlacementTuning 的各項權重決定（見 get_placement_tuning），
    不再由「策略」切換。過去這裡會讀取 proxmox_config.placement_strategy，
    但回傳值一律被正規化成 DEFAULT_PLACEMENT_STRATEGY —— 等於讓管理員以為
    切換生效。設定頁的選項已移除，該欄位也已由 sched01 從 DB 移除。

    保留此函式與 vm_requests.placement_strategy_used 欄位，是為了讓落點紀錄
    仍能標示當時採用的策略名稱；日後真的支援多策略時，從這裡擴充。
    """
    del session
    return DEFAULT_PLACEMENT_STRATEGY
