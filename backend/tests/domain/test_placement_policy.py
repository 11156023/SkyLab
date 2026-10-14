"""app.domain.placement.policy 從 proxmox_config 讀出的放置權重。"""

from __future__ import annotations

import pytest

from app.domain.placement import policy
from app.models.proxmox_config import ProxmoxConfig


def test_zero_weights_are_kept_not_replaced_by_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # schema 允許 0（ge=0）：管理員把某項權重設 0 就是要關掉它，
    # 以前 `value or default` 會把 0 悄悄換回預設值。
    config = ProxmoxConfig(
        placement_reassignment_cost=0.0,
        placement_loadavg_warn_per_core=0.0,
        placement_loadavg_penalty_weight=0.0,
        placement_disk_contention_warn_share=0.0,
        placement_disk_penalty_weight=0.0,
        placement_cpu_peak_warn_share=0.0,
        placement_memory_peak_warn_share=0.0,
        placement_resource_weight_cpu=0.0,
        placement_resource_weight_memory=0.0,
        placement_resource_weight_disk=0.0,
    )
    monkeypatch.setattr(
        policy.proxmox_config_repo, "get_proxmox_config", lambda _session: config
    )

    tuning = policy.get_placement_tuning(session=object())  # type: ignore[arg-type]

    assert tuning.reassignment_cost == 0.0
    assert tuning.loadavg_warn_per_core == 0.0
    assert tuning.loadavg_penalty_weight == 0.0
    assert tuning.disk_contention_warn_share == 0.0
    assert tuning.disk_penalty_weight == 0.0
    assert tuning.cpu_peak_warn_share == 0.0
    assert tuning.memory_peak_warn_share == 0.0
    assert tuning.resource_weight_cpu == 0.0
    assert tuning.resource_weight_memory == 0.0
    assert tuning.resource_weight_disk == 0.0
    # 下限保護照舊：margin 不得低於 1.0
    assert tuning.peak_cpu_margin == pytest.approx(1.1)


def test_missing_values_fall_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ProxmoxConfig()
    config.placement_resource_weight_disk = None  # type: ignore[assignment]
    config.placement_reassignment_cost = None  # type: ignore[assignment]
    monkeypatch.setattr(
        policy.proxmox_config_repo, "get_proxmox_config", lambda _session: config
    )

    tuning = policy.get_placement_tuning(session=object())  # type: ignore[arg-type]

    assert tuning.resource_weight_disk == 1.0
    assert tuning.reassignment_cost == pytest.approx(0.15)
