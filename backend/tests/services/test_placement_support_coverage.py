"""Coverage tests for app.services.vm.placement_support (pure unit, no DB/PVE)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.placement.models import PlacementTuning, WorkingStoragePool
from app.domain.placement.schemas import NodeCapacity, PlacementRequest
from app.exceptions import NotFoundError
from app.services.vm import placement_support

GIB = 1024**3
T0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)

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
        gpu_count=0,
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


@pytest.fixture(autouse=True)
def _isolate_module_caches(monkeypatch):
    monkeypatch.setattr(placement_support, "_template_nodes_cache", {})
    monkeypatch.setattr(placement_support, "_cluster_cache", None)


# ---------------------------------------------------------------------------
# GPU slots / cluster state cache
# ---------------------------------------------------------------------------


def test_gpu_used_slots_fail_open(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("pve down")

    monkeypatch.setattr(
        placement_support.gpu_service, "get_gpu_used_slots_by_node", _boom
    )
    assert placement_support.gpu_used_slots() == {}


def _patch_cluster_sources(monkeypatch, calls: list[str]) -> None:
    def _list_nodes():
        calls.append("list_nodes")
        return []

    monkeypatch.setattr(placement_support.proxmox_service, "list_nodes", _list_nodes)
    monkeypatch.setattr(
        placement_support.proxmox_service, "admin_disabled_node_names", set
    )
    monkeypatch.setattr(
        placement_support.proxmox_service, "list_all_resources", lambda: []
    )
    monkeypatch.setattr(
        placement_support.gpu_service, "get_gpu_node_counts", lambda: {}
    )


def test_load_cluster_state_returns_fresh_cache(monkeypatch) -> None:
    calls: list[str] = []
    _patch_cluster_sources(monkeypatch, calls)
    entry = placement_support._ClusterCacheEntry(
        cached_at=time.monotonic(), nodes=["cached-node"], resources=["cached-res"]
    )
    monkeypatch.setattr(placement_support, "_cluster_cache", entry)

    assert placement_support.load_cluster_state() == (["cached-node"], ["cached-res"])
    assert calls == []


def test_load_cluster_state_refreshes_stale_cache(monkeypatch) -> None:
    calls: list[str] = []
    _patch_cluster_sources(monkeypatch, calls)
    stale = placement_support._ClusterCacheEntry(
        cached_at=time.monotonic() - 10_000, nodes=["old"], resources=["old"]
    )
    monkeypatch.setattr(placement_support, "_cluster_cache", stale)

    assert placement_support.load_cluster_state() == ([], [])
    assert calls == ["list_nodes"]
    assert placement_support._cluster_cache is not stale


def test_set_cached_cluster_state_disabled_when_ttl_zero(monkeypatch) -> None:
    monkeypatch.setattr(
        placement_support.placement_settings, "source_cache_ttl_seconds", 0
    )
    placement_support._set_cached_cluster_state(nodes=[], resources=[])
    assert placement_support._cluster_cache is None


def test_utc_now_is_timezone_aware() -> None:
    assert placement_support.utc_now().tzinfo is not None


# ---------------------------------------------------------------------------
# storage pool state / current node / preview request
# ---------------------------------------------------------------------------


def test_build_storage_pool_state_skips_unrequested_nodes(monkeypatch) -> None:
    storages = [
        SimpleNamespace(
            node_name="other",
            storage="local",
            total_gb=10,
            avail_gb=5,
            active=True,
            enabled=True,
            can_vm=True,
            can_lxc=True,
            is_shared=False,
            speed_tier=None,
            user_priority=None,
        ),
        SimpleNamespace(
            node_name="pve1",
            storage="local",
            total_gb=None,
            avail_gb=None,
            active=True,
            enabled=True,
            can_vm=True,
            can_lxc=True,
            is_shared=False,
            speed_tier=None,
            user_priority=None,
        ),
    ]
    monkeypatch.setattr(
        placement_support.proxmox_storage_repo,
        "get_all_storages",
        lambda session: storages,
    )
    monkeypatch.setattr(
        placement_support.proxmox_node_repo,
        "get_node_connection_map",
        lambda session: {"pve1": (1, "c1")},
    )

    by_node, managed = placement_support.build_storage_pool_state(
        session=None, node_names=["pve1"]
    )

    assert managed is True
    assert list(by_node) == ["pve1"]
    [pool] = by_node["pve1"]
    assert pool.speed_tier == "unknown"
    assert pool.user_priority == 5
    assert pool.total_gb == 0.0


@pytest.mark.parametrize(
    ("request_obj", "expected"),
    [
        (SimpleNamespace(vmid=None, actual_node="a", assigned_node="b"), None),
        (SimpleNamespace(vmid=101, actual_node=" pve-a ", assigned_node="b"), "pve-a"),
        (SimpleNamespace(vmid=101, actual_node=None, assigned_node=" pve-b "), "pve-b"),
        (SimpleNamespace(vmid=101, actual_node="", assigned_node=None), None),
    ],
)
def test_provisioned_current_node(request_obj, expected) -> None:
    assert placement_support.provisioned_current_node(request_obj) == expected


def test_build_preview_vm_request_for_vm_and_lxc() -> None:
    end = T0 + timedelta(hours=2)
    vm = placement_support.build_preview_vm_request(
        request=PlacementRequest(
            resource_type="vm", cpu_cores=4, memory_mb=4096, disk_gb=40
        ),
        start_at=T0,
        end_at=end,
    )
    assert vm.resource_type == "vm"
    assert vm.disk_size == 40
    assert vm.rootfs_size is None
    assert vm.template_id == 1
    assert vm.username == "preview"
    assert vm.ostemplate is None

    lxc = placement_support.build_preview_vm_request(
        request=PlacementRequest(resource_type="lxc", disk_gb=8),
        start_at=T0,
        end_at=end,
    )
    assert lxc.rootfs_size == 8
    assert lxc.disk_size is None
    assert lxc.ostemplate == "preview"
    assert lxc.template_id is None


# ---------------------------------------------------------------------------
# node_can_host_request GPU count
# ---------------------------------------------------------------------------


def test_node_can_host_request_rejects_insufficient_gpu_count() -> None:
    assert (
        placement_support.node_can_host_request(
            _node("pve1", gpu_count=0),
            cores=1.0,
            memory_bytes=GIB,
            disk_bytes=GIB,
            gpu_required=1,
            has_managed_storage=False,
        )
        is False
    )


# ---------------------------------------------------------------------------
# allowed_template_nodes_for_request: lxc clone / vm without node / gpu failure
# ---------------------------------------------------------------------------


def test_lxc_clone_template_missing_returns_empty(monkeypatch) -> None:
    def _missing(vmid):
        raise NotFoundError(f"{vmid} missing")

    monkeypatch.setattr(placement_support.proxmox_service, "find_resource", _missing)
    request = PlacementRequest(resource_type="lxc", template_vmid=9001)
    assert placement_support.allowed_template_nodes_for_request(request) == set()


def test_lxc_clone_template_without_node_is_unconstrained(monkeypatch) -> None:
    monkeypatch.setattr(
        placement_support.proxmox_service,
        "find_resource",
        lambda vmid: {"node": "", "name": "debian"},
    )
    request = PlacementRequest(resource_type="lxc", template_vmid=9002)
    assert placement_support.allowed_template_nodes_for_request(request) is None


def test_lxc_clone_gpu_template_keeps_node_when_gpu_lookup_fails(
    monkeypatch, caplog
) -> None:
    monkeypatch.setattr(
        placement_support.proxmox_service,
        "find_resource",
        lambda vmid: {"node": "pve1", "name": "ubuntu-GPU"},
    )

    def _boom():
        raise RuntimeError("gpu lookup failed")

    monkeypatch.setattr(placement_support, "_gpu_capable_nodes", _boom)
    request = PlacementRequest(resource_type="lxc", template_vmid=9003)

    assert placement_support.allowed_template_nodes_for_request(request) == {"pve1"}
    assert "Unable to resolve GPU-capable nodes" in caplog.text


def test_vm_template_without_node_is_unconstrained(monkeypatch) -> None:
    monkeypatch.setattr(
        placement_support.proxmox_service,
        "find_vm_template",
        lambda vmid: {"node": None, "name": "win"},
    )
    request = PlacementRequest(resource_type="vm", template_vmid=9004)
    assert placement_support.allowed_template_nodes_for_request(request) is None


# ---------------------------------------------------------------------------
# reservations / checkpoints
# ---------------------------------------------------------------------------


def test_reserve_request_on_capacities_rejects_unknown_node() -> None:
    with pytest.raises(ValueError, match="not found in capacity list"):
        placement_support.reserve_request_on_capacities(
            node_capacities=[_node("pve1")],
            db_request=SimpleNamespace(),
            node_name="ghost",
            request_capacity_tuple_fn=lambda r: (1.0, GIB, GIB),
            refresh_node_candidate_fn=lambda n: None,
        )


def test_window_checkpoints_degenerate_and_open_start() -> None:
    assert placement_support.window_checkpoints(
        start_at=T0,
        end_at=T0,
        reserved_requests=[],
        normalize_datetime_fn=lambda v: v,
    ) == [T0]

    inside = T0 + timedelta(minutes=30)
    points = placement_support.window_checkpoints(
        start_at=T0,
        end_at=T0 + timedelta(hours=1),
        reserved_requests=[
            SimpleNamespace(start_at=None),
            SimpleNamespace(start_at=inside),
        ],
        normalize_datetime_fn=lambda v: v,
    )
    assert points == [T0, inside]


def test_apply_reserved_requests_skips_incomplete_and_unknown_nodes() -> None:
    window_end = T0 + timedelta(hours=1)
    reserved = [
        # 沒有 assigned_node
        SimpleNamespace(vmid=None, start_at=T0, end_at=window_end, assigned_node=None),
        # 落在不在 baseline 裡的節點
        SimpleNamespace(
            vmid=None, start_at=T0, end_at=window_end, assigned_node="ghost"
        ),
        # 正常扣容量
        SimpleNamespace(
            vmid=None,
            start_at=T0,
            end_at=window_end,
            assigned_node="pve1",
            gpu_mapping_id=None,
        ),
    ]

    adjusted = placement_support.apply_reserved_requests_to_capacities(
        baseline_capacities=[_node("pve1")],
        reserved_requests=reserved,
        at_time=T0,
        normalize_datetime_fn=lambda v: v,
        request_capacity_tuple_fn=lambda r: (2.0, 2 * GIB, 0),
    )

    assert adjusted[0].allocatable_cpu_cores == 14.0
    assert adjusted[0].allocatable_memory_bytes == 30 * GIB


# ---------------------------------------------------------------------------
# build_plan: node whose managed storage cannot host the request
# ---------------------------------------------------------------------------


def test_build_plan_skips_node_without_usable_storage() -> None:
    request = PlacementRequest(
        resource_type="lxc", cpu_cores=1, memory_mb=512, disk_gb=8
    )

    plan = placement_support.build_plan(
        session=None,
        request=request,
        node_capacities=[_node("pve1"), _node("pve2")],
        effective_resource_type="lxc",
        resource_type_reason="test",
        placement_strategy="priority_dominant_share",
        node_priorities={"pve1": 1},
        build_storage_pool_state_fn=lambda **kw: (
            {
                "pve1": [_pool("vm-only", can_lxc=False)],
                "pve2": [_pool("good")],
            },
            True,
        ),
        get_placement_tuning_fn=lambda **kw: TUNING,
        get_overcommit_ratios_fn=lambda session: (1.0, 1.0),
        get_node_priorities_fn=lambda session: {},
        placement_sort_key_fn=placement_support.placement_sort_key,
    )

    assert plan.feasible is True
    assert plan.recommended_node == "pve2"


def test_build_live_node_capacities_uses_gpu_slots(monkeypatch) -> None:
    monkeypatch.setattr(placement_support, "gpu_used_slots", lambda: {"pve1": 1})
    seen: dict = {}

    def _fake_build(**kwargs):
        seen.update(kwargs)
        return [SimpleNamespace(node="pve1")]

    monkeypatch.setattr(
        placement_support.placement_advisor, "build_node_capacities", _fake_build
    )
    assert [
        item.node
        for item in placement_support.build_live_node_capacities(nodes=[], resources=[])
    ] == ["pve1"]
    assert seen["gpu_used"] == {"pve1": 1}


def test_load_cluster_state_without_cache_queries_pve(monkeypatch) -> None:
    calls: list[str] = []
    _patch_cluster_sources(monkeypatch, calls)
    assert placement_support.load_cluster_state() == ([], [])
    assert calls == ["list_nodes"]


@pytest.mark.parametrize(
    ("resource_type", "expected"),
    [("vm", 20), ("lxc", 8)],
)
def test_request_disk_gb_defaults(resource_type, expected) -> None:
    assert (
        placement_support.request_disk_gb(
            resource_type=resource_type, disk_size=None, rootfs_size=0
        )
        == expected
    )


def test_apply_reserved_requests_skips_provisioned_machines() -> None:
    reserved = [
        SimpleNamespace(
            vmid=101,
            start_at=T0,
            end_at=T0 + timedelta(hours=1),
            assigned_node="pve1",
        )
    ]
    adjusted = placement_support.apply_reserved_requests_to_capacities(
        baseline_capacities=[_node("pve1")],
        reserved_requests=reserved,
        at_time=T0,
        normalize_datetime_fn=lambda v: v,
        request_capacity_tuple_fn=lambda r: (2.0, 2 * GIB, 0),
    )
    assert adjusted[0].allocatable_cpu_cores == 16.0
