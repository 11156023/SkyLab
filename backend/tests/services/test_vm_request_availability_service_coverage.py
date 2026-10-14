"""Coverage tests for app.services.vm.vm_request_availability_service (no DB/PVE)."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.placement.models import PlacementTuning, WorkingStoragePool
from app.domain.placement.schemas import NodeCapacity, PlacementRequest
from app.exceptions import BadRequestError, NotFoundError
from app.models.vm_request import VMRequest
from app.schemas.vm_request import (
    VMRequestAvailabilityRequest,
    VMRequestWindowAvailabilityRequest,
)
from app.services.vm import vm_request_availability_service as svc

GIB = 1024**3

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


def _capacity(node: str = "pve-a", cpu: float = 10.0, **overrides) -> NodeCapacity:
    defaults = dict(
        node=node,
        status="online",
        candidate=True,
        running_resources=0,
        guest_soft_limit=16,
        total_cpu_cores=16,
        allocatable_cpu_cores=cpu,
        total_memory_bytes=64 * GIB,
        allocatable_memory_bytes=40 * GIB,
        total_disk_bytes=1000 * GIB,
        allocatable_disk_bytes=700 * GIB,
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


def _lxc_request(**overrides) -> PlacementRequest:
    defaults = dict(resource_type="lxc", cpu_cores=2, memory_mb=2048, disk_gb=8)
    defaults.update(overrides)
    return PlacementRequest(**defaults)


def _fit(**overrides):
    kwargs = dict(
        request=_lxc_request(),
        effective_resource_type="lxc",
        adjusted_nodes=[_capacity()],
        storage_pools_by_node={},
        has_managed_storage=False,
        disk_overcommit_ratio=1.0,
        tuning=TUNING,
    )
    kwargs.update(overrides)
    return svc._lightweight_fit_nodes(**kwargs)


# ---------------------------------------------------------------------------
# assess_existing_request / window validation
# ---------------------------------------------------------------------------


def test_assess_existing_request_missing_raises_not_found(monkeypatch) -> None:
    monkeypatch.setattr(svc.vm_request_repo, "get_vm_request_by_id", lambda **kw: None)
    with pytest.raises(NotFoundError):
        svc.assess_existing_request(
            session=None,
            request_id=uuid.uuid4(),
            current_user=SimpleNamespace(),
            days=7,
            timezone="Asia/Taipei",
        )


def test_validate_request_window_requires_both_ends() -> None:
    with pytest.raises(BadRequestError):
        svc.validate_request_window(
            session=None,
            current_user=None,
            request_in=SimpleNamespace(start_at=None, end_at=datetime.now(UTC)),
        )


def test_validate_request_window_rejects_end_before_start() -> None:
    now = datetime.now(UTC)
    with pytest.raises(BadRequestError):
        svc.validate_request_window(
            session=None,
            current_user=None,
            request_in=SimpleNamespace(start_at=now, end_at=now),
        )


def _window_request(start: datetime, end: datetime, **overrides):
    defaults = dict(resource_type="lxc", rootfs_size=8, start_at=start, end_at=end)
    defaults.update(overrides)
    return VMRequestWindowAvailabilityRequest(**defaults)


def test_assess_request_window_rejects_end_before_start() -> None:
    now = datetime.now(UTC) + timedelta(hours=1)
    with pytest.raises(BadRequestError):
        svc.assess_request_window(
            session=None, current_user=None, request_in=_window_request(now, now)
        )


def test_assess_request_window_requires_both_ends(monkeypatch) -> None:
    now = datetime.now(UTC) + timedelta(hours=1)
    monkeypatch.setattr(svc, "normalize_datetime", lambda value: None)
    with pytest.raises(BadRequestError):
        svc.assess_request_window(
            session=None,
            current_user=None,
            request_in=_window_request(now, now + timedelta(hours=1)),
        )


def test_assess_request_window_quick_template_long_window(monkeypatch) -> None:
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=1)
    end = start + timedelta(days=31, minutes=30)
    monkeypatch.setattr(
        svc.vm_request_placement_service,
        "select_reserved_target_node_for_request",
        lambda **kw: SimpleNamespace(
            node="pve-a",
            strategy="priority_dominant_share",
            plan=SimpleNamespace(feasible=True, warnings=["w1"], summary="ok"),
        ),
    )

    response = svc.assess_request_window(
        session=None,
        current_user=None,
        request_in=_window_request(start, end, mode="quick_template"),
    )

    assert response.status == "limited"
    assert response.feasible is True
    # 31 天又 30 分鐘：不足一小時的部分進位
    assert response.duration_hours == 31 * 24 + 1
    assert response.warnings[0] == "w1"
    assert len(response.warnings) == 2
    assert response.selected_node == "pve-a"


# ---------------------------------------------------------------------------
# calendar: slot without reserved timeline entry
# ---------------------------------------------------------------------------


def test_build_availability_response_copies_baseline_when_timeline_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(svc.placement_support, "load_cluster_state", lambda: ([], []))
    monkeypatch.setattr(
        svc.placement_support,
        "build_live_node_capacities",
        lambda **kwargs: [_capacity()],
    )
    monkeypatch.setattr(
        svc.vm_request_placement_service,
        "get_overcommit_ratios",
        lambda session: (1.0, 1.0),
    )
    monkeypatch.setattr(
        svc.vm_request_placement_service,
        "get_placement_strategy",
        lambda session: "balanced",
    )
    monkeypatch.setattr(
        svc.vm_request_placement_service, "get_node_priorities", lambda session: {}
    )
    monkeypatch.setattr(
        svc.vm_request_placement_service,
        "_build_storage_pool_state",
        lambda **kwargs: ({}, False),
    )
    monkeypatch.setattr(
        svc.vm_request_placement_service, "get_placement_tuning", lambda **kwargs: None
    )
    monkeypatch.setattr(
        svc.placement_support, "allowed_gpu_nodes_for_request", lambda request: None
    )
    monkeypatch.setattr(
        svc.placement_support,
        "allowed_affinity_nodes_for_request",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        svc,
        "_load_hourly_demand_profile",
        lambda **kwargs: dict.fromkeys(range(24), 0.0),
    )
    monkeypatch.setattr(svc, "_pending_pressure_ratio", lambda **kwargs: 0.0)
    monkeypatch.setattr(
        svc.vm_request_repo,
        "get_approved_vm_requests_overlapping_window",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(svc, "_build_reserved_capacity_timeline", lambda **kwargs: {})

    response = svc._build_availability_response(
        session=None,  # type: ignore[arg-type]
        source_request=VMRequestAvailabilityRequest(
            resource_type="lxc", rootfs_size=8, days=2, detail=False
        ),
        role=svc.UserRole("student"),
        stack_label="Requested LXC",
    )

    assert response.summary.feasible_slot_count > 0


# ---------------------------------------------------------------------------
# reserved capacity timeline
# ---------------------------------------------------------------------------


def test_reserved_timeline_empty_slot_starts() -> None:
    assert (
        svc._build_reserved_capacity_timeline(
            baseline_capacities=[_capacity()], reserved_requests=[], slot_starts=[]
        )
        == {}
    )


def _reservation(start, end, node="pve-a") -> VMRequest:
    return VMRequest(
        user_id=uuid.uuid4(),
        reason="test",
        resource_type="lxc",
        hostname="box",
        password="x",
        cores=2,
        memory=1024,
        rootfs_size=8,
        start_at=start,
        end_at=end,
        assigned_node=node,
    )


def test_reserved_timeline_skips_incomplete_out_of_range_and_unknown_nodes() -> None:
    base = datetime(2026, 9, 2, 0, tzinfo=UTC)
    starts = [base + timedelta(hours=i) for i in range(4)]
    reserved = [
        _reservation(starts[0], starts[2], node=""),  # 沒有指派節點
        _reservation(base + timedelta(days=5), base + timedelta(days=6)),  # 超出範圍
        _reservation(starts[2], starts[2]),  # 零長度
        _reservation(starts[1], starts[3], node="ghost"),  # 不在 baseline 的節點
    ]

    timeline = svc._build_reserved_capacity_timeline(
        baseline_capacities=[_capacity()],
        reserved_requests=reserved,
        slot_starts=starts,
    )

    assert all(timeline[s][0].allocatable_cpu_cores == 10.0 for s in starts)


# ---------------------------------------------------------------------------
# lite fit
# ---------------------------------------------------------------------------


def test_lightweight_fit_partial_when_capacity_runs_out() -> None:
    placed = _fit(
        request=_lxc_request(instance_count=2),
        adjusted_nodes=[_capacity(cpu=3.0)],
    )
    assert placed == ["pve-a"]


def test_lightweight_fit_managed_storage_without_tuning_skips_nodes() -> None:
    assert (
        _fit(
            storage_pools_by_node={"pve-a": [_pool("good")]},
            has_managed_storage=True,
            tuning=None,
        )
        == []
    )


def test_lightweight_fit_skips_nodes_without_usable_pool() -> None:
    assert (
        _fit(
            storage_pools_by_node={"pve-a": [_pool("vm-only", can_lxc=False)]},
            has_managed_storage=True,
        )
        == []
    )


def test_lightweight_fit_reserves_storage_pool() -> None:
    pools = {"pve-a": [_pool("good", avail_gb=20.0, total_gb=20.0)]}
    placed = _fit(
        request=_lxc_request(instance_count=2),
        storage_pools_by_node=pools,
        has_managed_storage=True,
    )
    assert placed == ["pve-a", "pve-a"]
    # 原始池子是深拷貝出去用的，不能被改動
    assert pools["pve-a"][0].avail_gb == 20.0


def _lite_slot(**overrides):
    start = datetime(2026, 9, 2, 10, tzinfo=UTC)
    kwargs = dict(
        request=_lxc_request(instance_count=2),
        effective_resource_type="lxc",
        adjusted_nodes=[_capacity()],
        storage_pools_by_node={},
        has_managed_storage=False,
        disk_overcommit_ratio=1.0,
        tuning=TUNING,
        slot_start=start,
        slot_end=start + timedelta(hours=1),
        demand_ratio=0.1,
        pending_pressure=0.0,
        placement_strategy="balanced",
    )
    kwargs.update(overrides)
    return svc._lite_slot_from_capacities(**kwargs)


def test_lite_slot_partial_is_limited_with_pressure_reason() -> None:
    slot = _lite_slot(adjusted_nodes=[_capacity(cpu=3.0)])
    assert slot.status == "limited"
    assert slot.label == "Limited"
    assert slot.feasible is False
    assert len(slot.reasons) == 2
    assert slot.target_node == "pve-a"


def test_lite_slot_unavailable_without_any_node() -> None:
    slot = _lite_slot(adjusted_nodes=[_capacity(cpu=1.0)])
    assert slot.status == "unavailable"
    assert slot.label == "Unavailable"
    assert slot.target_node is None


def test_lite_slot_uses_fit_cache() -> None:
    cache = {("k", 0.1): ["pve-z"]}
    slot = _lite_slot(request=_lxc_request(), fit_cache=cache, fit_key=("k", 0.1))
    assert slot.recommended_nodes == ["pve-z"]
    assert slot.status == "available"


# ---------------------------------------------------------------------------
# demand profile / plan slot / resource stack / usage share
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, rows) -> None:
        self.rows = rows

    def exec(self, _statement):
        return SimpleNamespace(all=lambda: list(self.rows))


def test_hourly_demand_profile_skips_rows_without_created_at() -> None:
    created = datetime(2026, 9, 2, 2, tzinfo=UTC)  # 台北 10 點
    profile = svc._load_hourly_demand_profile(
        session=_FakeSession(
            [SimpleNamespace(created_at=None), SimpleNamespace(created_at=created)]
        ),
        timezone=svc.ZoneInfo("Asia/Taipei"),
    )
    assert profile[10] == 1.0
    assert sum(profile.values()) == 1.0


def _plan(**overrides):
    defaults = dict(
        rationale=[],
        warnings=[],
        placements=[],
        feasible=False,
        assigned_instances=0,
        summary="",
        recommended_node=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _plan_slot(plan):
    start = datetime(2026, 9, 2, 10, tzinfo=UTC)
    return svc._slot_from_plan(
        plan=plan,
        slot_start=start,
        slot_end=start + timedelta(hours=1),
        within_policy=True,
        demand_ratio=0.0,
        pending_pressure=0.0,
        adjusted_nodes=[_capacity()],
        node_priorities={},
        resource_stack_by_node={},
        stack_label="Requested LXC",
        placement_strategy="balanced",
    )


def test_slot_from_plan_partial_assignment_is_limited() -> None:
    slot = _plan_slot(_plan(assigned_instances=1, summary="partly"))
    assert slot.status == "limited"
    assert slot.label == "部分可行"
    assert slot.reasons[0] == "partly"
    assert len(slot.reasons) == 2


def test_slot_from_plan_unavailable_uses_default_summary() -> None:
    slot = _plan_slot(_plan())
    assert slot.status == "unavailable"
    assert slot.label == "容量不足"
    assert slot.summary == "此時段沒有足夠容量。"
    assert slot.reasons == ["此時段沒有足夠容量。"]


def test_build_resource_stack_by_node_defaults_names() -> None:
    stacks = svc._build_resource_stack_by_node(
        [
            SimpleNamespace(node=None, name=None, resource_type="lxc", vmid=101),
            SimpleNamespace(node="pve-a", name="web", resource_type="qemu", vmid=102),
            SimpleNamespace(node="pve-a", name="web", resource_type="qemu", vmid=103),
        ]
    )
    assert stacks == {
        "unknown": Counter({"lxc-101": 1}),
        "pve-a": Counter({"web": 2}),
    }


def test_usage_share_zero_total() -> None:
    assert svc._usage_share(total=0.0, remaining=5.0) == 0.0


def test_ensure_window_bounds_rejects_too_long_window() -> None:
    start = datetime.now(UTC)
    with pytest.raises(BadRequestError):
        svc.ensure_window_bounds(
            start_at=start,
            end_at=start + timedelta(days=svc.MAX_REQUEST_WINDOW_DAYS + 1),
        )


def test_ensure_window_bounds_rejects_start_far_in_past() -> None:
    start = datetime.now(UTC) - timedelta(days=3)
    with pytest.raises(BadRequestError):
        svc.ensure_window_bounds(start_at=start, end_at=start + timedelta(hours=1))


def test_validate_request_window_rejects_infeasible_selection(monkeypatch) -> None:
    start = datetime.now(UTC) + timedelta(hours=1)
    captured: dict = {}

    def _select(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            node=None,
            strategy="s",
            plan=SimpleNamespace(feasible=False, summary="no capacity"),
        )

    monkeypatch.setattr(
        svc.vm_request_placement_service,
        "select_reserved_target_node_for_request",
        _select,
    )
    with pytest.raises(BadRequestError):
        svc.validate_request_window(
            session=None,
            current_user=None,
            request_in=SimpleNamespace(
                resource_type="vm",
                start_at=start,
                end_at=start + timedelta(hours=2),
                cores=2,
                memory=2048,
                disk_size=30,
                rootfs_size=None,
                gpu_mapping_id=None,
                ostemplate=None,
                template_id=9001,
            ),
        )
    assert captured["request"].template_vmid == 9001
    assert captured["request"].ostemplate is None


def test_assess_request_window_scheduled_unavailable(monkeypatch) -> None:
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=1)
    captured: dict = {}

    def _select(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            node=None,
            strategy="s",
            plan=SimpleNamespace(feasible=False, warnings=None, summary="none"),
        )

    monkeypatch.setattr(
        svc.vm_request_placement_service,
        "select_reserved_target_node_for_request",
        _select,
    )
    response = svc.assess_request_window(
        session=None,
        current_user=None,
        request_in=_window_request(
            start,
            start + timedelta(hours=2),
            mode="scheduled",
            ostemplate="local:vztmpl/debian.tar.zst",
        ),
    )
    assert response.status == "unavailable"
    assert response.feasible is False
    assert response.warnings == []
    assert captured["request"].ostemplate == "local:vztmpl/debian.tar.zst"


def test_reserved_timeline_skips_provisioned_reservation() -> None:
    base = datetime(2026, 9, 2, 0, tzinfo=UTC)
    starts = [base + timedelta(hours=i) for i in range(2)]
    provisioned = _reservation(starts[0], starts[1])
    provisioned.vmid = 101
    timeline = svc._build_reserved_capacity_timeline(
        baseline_capacities=[_capacity()],
        reserved_requests=[provisioned],
        slot_starts=starts,
    )
    assert timeline[starts[0]][0].allocatable_cpu_cores == 10.0


def test_resolve_timezone_rejects_invalid_value() -> None:
    with pytest.raises(BadRequestError):
        svc._resolve_timezone("Not/AZone")


def test_hourly_demand_profile_without_rows_is_flat() -> None:
    profile = svc._load_hourly_demand_profile(
        session=_FakeSession([]), timezone=svc.ZoneInfo("Asia/Taipei")
    )
    assert profile == dict.fromkeys(range(24), 0.0)


def test_pending_pressure_ratio_without_pending_or_capacity() -> None:
    assert (
        svc._pending_pressure_ratio(
            session=_FakeSession([]), baseline_capacities=[_capacity()]
        )
        == 0.0
    )
    pending = [SimpleNamespace(cores=2, memory=1024, disk_size=None, rootfs_size=8)]
    assert (
        svc._pending_pressure_ratio(
            session=_FakeSession(pending), baseline_capacities=[]
        )
        == 0.0
    )


def test_pending_pressure_ratio_takes_dominant_and_caps_at_one() -> None:
    pending = [
        SimpleNamespace(cores=5, memory=1024, disk_size=None, rootfs_size=8),
        SimpleNamespace(cores=None, memory=None, disk_size=None, rootfs_size=None),
    ]
    assert (
        svc._pending_pressure_ratio(
            session=_FakeSession(pending), baseline_capacities=[_capacity()]
        )
        == 0.5
    )
    zero = _capacity(cpu=0.0, allocatable_memory_bytes=0, allocatable_disk_bytes=0)
    assert (
        svc._pending_pressure_ratio(
            session=_FakeSession(pending), baseline_capacities=[zero]
        )
        == 0.0
    )
    huge = [SimpleNamespace(cores=100, memory=0, disk_size=0, rootfs_size=0)]
    assert (
        svc._pending_pressure_ratio(
            session=_FakeSession(huge), baseline_capacities=[_capacity()]
        )
        == 1.0
    )
