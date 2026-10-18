"""Coverage tests for app.services.vm.placement_service (fake PVE state, no DB)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.placement.models import PlacementTuning, WorkingStoragePool
from app.domain.placement.schemas import NodeCapacity, PlacementRequest
from app.models import VMProvisioningStatus, VMRequest, VMRequestStatus
from app.services.vm import placement_service, placement_support

GIB = 1024**3
START = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
END = START + timedelta(hours=2)

TUNING = PlacementTuning(
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


def _node(name: str, **overrides) -> NodeCapacity:
    defaults = dict(
        node=name,
        status="online",
        running_resources=0,
        guest_soft_limit=100,
        total_cpu_cores=32.0,
        allocatable_cpu_cores=16.0,
        total_memory_bytes=64 * GIB,
        allocatable_memory_bytes=32 * GIB,
        total_disk_bytes=2048 * GIB,
        allocatable_disk_bytes=1024 * GIB,
    )
    defaults.update(overrides)
    return NodeCapacity(**defaults)


def _pool(name: str, **overrides) -> WorkingStoragePool:
    defaults = dict(
        storage=name,
        total_gb=400.0,
        avail_gb=200.0,
        active=True,
        enabled=True,
        can_vm=True,
        can_lxc=True,
        is_shared=False,
        speed_tier="ssd",
        user_priority=5,
    )
    defaults.update(overrides)
    return WorkingStoragePool(**defaults)


def _vm_request(**overrides) -> VMRequest:
    defaults = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="coverage",
        resource_type="lxc",
        hostname="cov-lxc",
        cores=2,
        memory=2048,
        rootfs_size=8,
        password="encrypted",
        storage="local-lvm",
        environment_type="Test",
        ostemplate="local:vztmpl/debian.tar.zst",
        status=VMRequestStatus.approved,
        start_at=START,
        end_at=END,
        created_at=START - timedelta(days=1),
    )
    defaults.update(overrides)
    return VMRequest(**defaults)


class _Env:
    def __init__(self) -> None:
        self.nodes: list[NodeCapacity] = [_node("pve1"), _node("pve2")]
        self.pools: dict[str, list[WorkingStoragePool]] = {}
        self.managed = False
        self.overlapping: list[VMRequest] = []


@pytest.fixture
def env(monkeypatch) -> _Env:
    state = _Env()
    monkeypatch.setattr(placement_support, "load_cluster_state", lambda: ([], []))
    monkeypatch.setattr(
        placement_support,
        "build_live_node_capacities",
        lambda **kw: [item.model_copy(deep=True) for item in state.nodes],
    )

    def _pool_state(*, session, node_names):
        return (
            {name: list(state.pools.get(name, [])) for name in node_names},
            state.managed,
        )

    monkeypatch.setattr(placement_support, "build_storage_pool_state", _pool_state)
    monkeypatch.setattr(
        placement_service.placement_policy,
        "get_overcommit_ratios",
        lambda session: (1.0, 1.0),
    )
    monkeypatch.setattr(
        placement_service.placement_policy,
        "get_memory_overcommit_ratio",
        lambda session: 1.0,
    )
    monkeypatch.setattr(
        placement_service.placement_policy,
        "get_placement_tuning",
        lambda *, session: TUNING,
    )
    monkeypatch.setattr(
        placement_service.placement_policy,
        "get_node_priorities",
        lambda session: {"pve1": 1, "pve2": 2},
    )
    monkeypatch.setattr(
        placement_service.placement_policy,
        "get_placement_strategy",
        lambda session: "priority_dominant_share",
    )
    monkeypatch.setattr(
        placement_service.vm_request_repo,
        "get_approved_vm_requests_overlapping_window",
        lambda **kw: list(state.overlapping),
    )
    return state


# ---------------------------------------------------------------------------
# current / reserved selection
# ---------------------------------------------------------------------------


def test_select_current_target_node_uses_live_capacity(env) -> None:
    selection = placement_service.select_current_target_node(
        session=None, db_request=_vm_request()
    )
    assert selection.node in {"pve1", "pve2"}
    assert selection.strategy == "priority_dominant_share"
    assert selection.plan.feasible is True


def test_select_reserved_target_node_without_window_uses_current(env) -> None:
    selection = placement_service.select_reserved_target_node(
        session=None, db_request=_vm_request(start_at=None, end_at=None)
    )
    assert selection.plan.feasible is True
    assert selection.node is not None


def test_select_reserved_target_node_with_no_feasible_node(env) -> None:
    env.nodes = [_node("pve1", allocatable_cpu_cores=0.5)]

    selection = placement_service.select_reserved_target_node(
        session=None, db_request=_vm_request()
    )

    assert selection.node is None
    assert selection.plan.feasible is False
    assert selection.plan.candidate_nodes == []


# ---------------------------------------------------------------------------
# pinned / rebuild
# ---------------------------------------------------------------------------


def test_provisioning_pinned_node_ignores_provisioned_request() -> None:
    request = _vm_request(
        vmid=101,
        provisioning_status=VMProvisioningStatus.running,
        desired_node="pve1",
    )
    assert placement_service.provisioning_pinned_node(request) is None


def test_rebuild_keeps_provisioned_node_and_raises_on_infeasible(
    monkeypatch,
) -> None:
    provisioned = _vm_request(
        vmid=101, actual_node="pve-live", assigned_node="pve-old", hostname="done"
    )
    blocked = _vm_request(hostname="blocked", start_at=START + timedelta(hours=1))

    def _fake_select(*, session, db_request, reserved_requests):
        return SimpleNamespace(
            node=None, strategy="s", plan=SimpleNamespace(feasible=False)
        )

    monkeypatch.setattr(placement_service, "select_reserved_target_node", _fake_select)

    with pytest.raises(ValueError, match="No feasible reservation exists"):
        placement_service.rebuild_reserved_assignments(
            session=None, requests=[blocked, provisioned]
        )

    assert provisioned.assigned_node == "pve-live"
    assert provisioned.desired_node == "pve-live"


# ---------------------------------------------------------------------------
# preview scores
# ---------------------------------------------------------------------------


def test_preview_scores_empty_without_window(env) -> None:
    assert (
        placement_service.get_preview_node_scores(
            session=None, db_request=_vm_request(start_at=None)
        )
        == []
    )


def test_preview_scores_select_node_and_skip_storage_infeasible(env) -> None:
    env.managed = True
    env.pools = {
        "pve1": [_pool("vm-only", can_lxc=False)],
        "pve2": [_pool("good")],
    }

    breakdowns = placement_service.get_preview_node_scores(
        session=None, db_request=_vm_request()
    )

    assert [item.node for item in breakdowns] == ["pve2"]
    [only] = breakdowns
    assert only.is_selected is True
    assert only.reason == "Selected node"
    assert only.storage_penalty >= 0.0
    assert only.reassignment_cost == 0.0


def test_preview_scores_without_managed_storage_mark_alternatives(env) -> None:
    db_request = _vm_request(vmid=200, actual_node="pve1")

    breakdowns = placement_service.get_preview_node_scores(
        session=None,
        db_request=db_request,
        reserved_requests=[],
        selected_node="pve2",
    )

    by_node = {item.node: item for item in breakdowns}
    assert breakdowns[0].node == "pve2"
    assert by_node["pve2"].is_selected is True
    assert by_node["pve2"].reassignment_cost == round(TUNING.reassignment_cost, 4)
    assert by_node["pve1"].is_selected is False
    assert by_node["pve1"].reason == "Feasible alternative"
    assert by_node["pve1"].reassignment_cost == 0.0
    assert by_node["pve1"].storage_penalty == 0.0


def test_preview_breakdown_returns_none_for_unknown_node() -> None:
    assert (
        placement_service._preview_breakdown_for_node(
            candidate_node="ghost",
            projected_baseline=[_node("pve1")],
            preview_request=_vm_request(),
            request=PlacementRequest(resource_type="lxc", disk_gb=8),
            storage_pools_by_node={},
            has_managed_storage=False,
            disk_overcommit_ratio=1.0,
            tuning=TUNING,
            priorities={},
            current_node=None,
            is_selected=False,
        )
        is None
    )


# ---------------------------------------------------------------------------
# select_best_storage_name
# ---------------------------------------------------------------------------


def test_select_best_storage_name_picks_pool(env) -> None:
    env.managed = True
    env.pools = {"pve1": [_pool("fast")]}
    assert (
        placement_service.select_best_storage_name(
            session=None, node_name="pve1", resource_type="lxc", disk_gb=8
        )
        == "fast"
    )


def test_select_best_storage_name_none_when_no_pool_fits(env) -> None:
    env.managed = True
    env.pools = {"pve1": [_pool("vm-only", can_lxc=False)]}
    assert (
        placement_service.select_best_storage_name(
            session=None, node_name="pve1", resource_type="lxc", disk_gb=8
        )
        is None
    )
