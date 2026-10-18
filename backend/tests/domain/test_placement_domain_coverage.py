"""Coverage for app.domain.placement advisor / scorer / schemas / storage / config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.placement import advisor
from app.domain.placement import config as placement_config
from app.domain.placement.models import (
    PlacementTuning,
    StorageSelection,
    WorkingStoragePool,
)
from app.domain.placement.schemas import (
    NodeCapacity,
    PlacementDecision,
    PlacementRequest,
)
from app.domain.placement.scorer import node_balance_score
from app.domain.placement.storage import (
    reserve_storage_pool,
    select_best_storage_for_request,
)


def _tuning(**overrides: float) -> PlacementTuning:
    values: dict[str, float] = dict(
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
    values.update(overrides)
    return PlacementTuning(**values)


def _capacity(node: str = "n1", **overrides: object) -> NodeCapacity:
    data: dict[str, object] = {"node": node, "status": "online"}
    data.update(overrides)
    return NodeCapacity(**data)


# ---------------------------------------------------------------- advisor


def test_parse_node_snapshots_parses_loadavg_variants() -> None:
    items = [
        {
            "node": "a",
            "status": "ONLINE",
            "cpu": 0.5,
            "maxcpu": 8,
            "uptime": "123",
            "loadavg": ["1.5", "1.0", "0.5"],
            "avg_load": "0.7, 0.6",
        },
        {"node": "b", "uptime": "bogus", "loadavg": -1, "avgload": "  "},
        {"node": "c", "uptime": None, "loadavg": "2.5 1.0", "average_loadavg": "x"},
        {"node": "d", "loadavg": "3/4", "avg_load": "-2"},
        {"node": "e", "loadavg": "0.25"},
        {"node": "off"},
    ]
    nodes = advisor.parse_node_snapshots(
        items, gpu_counts={"a": 2}, disabled_nodes=["off"]
    )

    by_name = {n.node: n for n in nodes}
    assert set(by_name) == {"a", "b", "c", "d", "e"}
    assert by_name["a"].status == "online"
    assert by_name["a"].uptime == 123
    assert by_name["a"].gpu_count == 2
    assert by_name["a"].current_loadavg_1 == 1.5
    assert by_name["a"].average_loadavg_1 == 0.7
    assert by_name["b"].uptime is None
    assert by_name["b"].current_loadavg_1 is None
    assert by_name["b"].average_loadavg_1 is None
    assert by_name["c"].uptime is None
    assert by_name["c"].current_loadavg_1 == 2.5
    assert by_name["c"].average_loadavg_1 is None
    assert by_name["d"].current_loadavg_1 == 3.0
    assert by_name["d"].average_loadavg_1 is None
    assert by_name["e"].current_loadavg_1 == 0.25


def test_parse_resource_snapshots_keeps_allocated_spec() -> None:
    """整班容量預檢要用分配規格（關機也算）；LXC 的小數 cpulimit 無條件進位。"""
    items = [
        {"vmid": 100, "type": "qemu", "node": "n1", "maxcpu": 4, "maxmem": 8 * 1024**3},
        {
            "vmid": 101,
            "type": "lxc",
            "node": "n1",
            "maxcpu": 1.5,
            "maxmem": 512 * 1024**2,
        },
        {"vmid": 102, "type": "lxc", "node": "n1"},
    ]
    out = advisor.parse_resource_snapshots(items)
    assert [(r.maxcpu, r.maxmem_bytes) for r in out] == [
        (4, 8 * 1024**3),
        (2, 512 * 1024**2),
        (0, 0),
    ]


def test_build_warnings_all_branches() -> None:
    request = PlacementRequest(resource_type="lxc", gpu_required=2)
    capacities = [
        _capacity("busy", guest_overloaded=True, gpu_count=1),
        _capacity("idle", gpu_count=0),
    ]

    warnings = advisor.build_warnings(
        node_capacities=capacities,
        request=request,
        effective_resource_type="vm",
        remaining=3,
    )

    assert warnings == [
        "Guest density is high on: busy.",
        "No node currently exposes 2 GPU(s).",
        "Requested LXC is evaluated as VM for placement.",
        "3 instance(s) cannot be placed with current capacity.",
    ]


def test_build_rationale_skips_unknown_node_and_reports_type_switch() -> None:
    request = PlacementRequest(resource_type="lxc", gpu_required=1)
    decisions = [
        PlacementDecision(node="ghost", instance_count=1),
        PlacementDecision(
            node="n1",
            instance_count=1,
            remaining_cpu_cores=3.5,
            remaining_memory_bytes=2 * advisor.GIB,
            remaining_disk_bytes=10 * advisor.GIB,
        ),
    ]

    reasons = advisor.build_rationale(
        request=request,
        placement_decisions=decisions,
        effective_resource_type="vm",
        node_capacities=[_capacity("n1")],
    )

    assert reasons[0] == "Requested LXC requires 1 GPU(s), so placement uses VM."
    assert len(reasons) == 2
    assert reasons[1].startswith("Node n1 keeps 3.50 vCPU, 2.0 GiB RAM")


def test_build_summary_text_variants() -> None:
    request = PlacementRequest(resource_type="vm", instance_count=3)
    assert (
        advisor.build_summary_text(
            request=request,
            placement_decisions=[],
            effective_resource_type="vm",
            assigned=0,
            remaining=3,
        )
        == "VM request cannot be placed on the current PVE nodes."
    )
    decisions = [PlacementDecision(node="n1", instance_count=2)]
    assert (
        advisor.build_summary_text(
            request=request,
            placement_decisions=decisions,
            effective_resource_type="vm",
            assigned=2,
            remaining=1,
        )
        == "VM request can place 2 / 3 VM instance(s): n1 x2."
    )
    assert (
        advisor.build_summary_text(
            request=request,
            placement_decisions=decisions,
            effective_resource_type="vm",
            assigned=2,
            remaining=0,
        )
        == "VM request can place all 2 VM instance(s): n1 x2."
    )


def test_decide_resource_type() -> None:
    assert (
        advisor.decide_resource_type(PlacementRequest(resource_type="lxc"))[0] == "lxc"
    )
    assert (
        advisor.decide_resource_type(
            PlacementRequest(resource_type="lxc", gpu_required=1)
        )[0]
        == "vm"
    )
    assert advisor.decide_resource_type(PlacementRequest(resource_type="vm"))[0] == "vm"


def test_guest_pressure_ratio_zero_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # _guest_soft_limit 本身保底 1；這裡替換掉它以走到防禦分支
    monkeypatch.setattr(advisor, "_guest_soft_limit", lambda _maxcpu: 0)
    assert advisor.guest_pressure_ratio(5, 4) == 0.0


# ---------------------------------------------------------------- scorer


def test_node_balance_score_zero_totals() -> None:
    node = _capacity(total_cpu_cores=0.0, total_memory_bytes=0, total_disk_bytes=0)
    # 總量 0 時以 max(...,1) 當分母，份額為 0
    assert node_balance_score(node, tuning=_tuning()) == 0.0


# ---------------------------------------------------------------- schemas


def test_placement_request_memory_gb_unparseable_keeps_default() -> None:
    assert PlacementRequest.model_validate({"memory_gb": "lots"}).memory_mb == 2048


def test_placement_request_non_dict_raw_passthrough() -> None:
    assert PlacementRequest.normalize_compat_fields("raw") == "raw"


# ---------------------------------------------------------------- storage


def _pool(**overrides: object) -> WorkingStoragePool:
    data: dict[str, object] = dict(
        storage="local",
        total_gb=100.0,
        avail_gb=50.0,
        active=True,
        enabled=True,
        can_vm=True,
        can_lxc=True,
        is_shared=False,
        speed_tier="ssd",
        user_priority=5,
    )
    data.update(overrides)
    return WorkingStoragePool(**data)  # type: ignore[arg-type]


def test_select_storage_zero_disk_returns_none() -> None:
    assert (
        select_best_storage_for_request(
            storage_pools=[_pool()],
            resource_type="vm",
            disk_gb=0,
            disk_overcommit_ratio=1.0,
            tuning=_tuning(),
        )
        is None
    )


def test_reserve_storage_pool_raises_when_full() -> None:
    pool = _pool(total_gb=10.0, avail_gb=0.0)
    selection = StorageSelection(
        pool=pool,
        projected_share=1.0,
        speed_rank=1,
        user_priority=5,
        contention_penalty=0.0,
    )
    with pytest.raises(ValueError, match="does not have enough capacity"):
        reserve_storage_pool(selection=selection, disk_gb=20, disk_overcommit_ratio=1.0)


# ---------------------------------------------------------------- config


def test_load_placement_config_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(placement_config, "CONFIG_FILE", tmp_path / "missing.json")
    assert (
        placement_config.load_placement_config() == placement_config.PlacementConfig()
    )


def test_load_placement_config_rejects_non_object(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "placement.json"
    path.write_text(json.dumps([1, 2]), encoding="utf-8")
    monkeypatch.setattr(placement_config, "CONFIG_FILE", path)
    with pytest.raises(ValueError, match="JSON object"):
        placement_config.load_placement_config()


def test_load_placement_config_reads_object(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "placement.json"
    path.write_text(json.dumps({"guest_per_core_limit": 3}), encoding="utf-8")
    monkeypatch.setattr(placement_config, "CONFIG_FILE", path)
    assert placement_config.load_placement_config().guest_per_core_limit == 3.0
