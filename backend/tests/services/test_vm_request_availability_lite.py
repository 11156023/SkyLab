"""可用性月曆 lite 路徑的效能回歸測試。

使用者連續調整規格時，前端每次都會重算 90 天 × 24 小時；後端若每格都 deep copy
全部節點並重跑配置，會把 worker 的 GIL 吃滿讓整個 API 卡住。這裡鎖住兩件事：
1. 預約狀態沒變的連續時段必須共用同一份容量清單
2. 容量狀態相同的時段必須共用配置結果（fit 次數遠小於時段數）
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.placement.schemas import NodeCapacity
from app.models.vm_request import VMRequest
from app.schemas.vm_request import VMRequestAvailabilityRequest
from app.services.vm import vm_request_availability_service as svc

GIB = 1024**3


def _capacity(node: str = "pve-a", cpu: float = 10.0) -> NodeCapacity:
    return NodeCapacity(
        node=node,
        status="online",
        candidate=True,
        running_resources=3,
        guest_soft_limit=16,
        guest_pressure_ratio=0.2,
        guest_overloaded=False,
        cpu_ratio=0.2,
        memory_ratio=0.3,
        disk_ratio=0.25,
        total_cpu_cores=16,
        allocatable_cpu_cores=cpu,
        total_memory_bytes=64 * GIB,
        allocatable_memory_bytes=40 * GIB,
        total_disk_bytes=1000 * GIB,
        allocatable_disk_bytes=700 * GIB,
    )


def _slot_starts(count: int) -> list[datetime]:
    base = datetime(2026, 9, 2, 0, tzinfo=UTC)
    return [base + timedelta(hours=i) for i in range(count)]


def _reservation(start: datetime, end: datetime) -> VMRequest:
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
        assigned_node="pve-a",
    )


def test_reserved_timeline_shares_lists_when_state_is_unchanged() -> None:
    baseline = [_capacity()]
    starts = _slot_starts(6)
    timeline = svc._build_reserved_capacity_timeline(
        baseline_capacities=baseline,
        reserved_requests=[],
        slot_starts=starts,
    )
    lists = [timeline[s] for s in starts]
    assert all(item is lists[0] for item in lists)
    # 仍然是 baseline 的複本，不能把原始清單交出去
    assert lists[0][0] is not baseline[0]
    assert lists[0][0].allocatable_cpu_cores == 10.0


def test_reserved_timeline_rebuilds_only_at_reservation_boundaries() -> None:
    baseline = [_capacity()]
    starts = _slot_starts(6)
    timeline = svc._build_reserved_capacity_timeline(
        baseline_capacities=baseline,
        reserved_requests=[_reservation(starts[2], starts[4])],
        slot_starts=starts,
    )
    lists = [timeline[s] for s in starts]
    assert lists[0] is lists[1]
    assert lists[2] is not lists[1]
    assert lists[2] is lists[3]
    assert lists[4] is not lists[3]
    assert lists[4] is lists[5]
    assert lists[1][0].allocatable_cpu_cores == 10.0
    assert lists[2][0].allocatable_cpu_cores == 8.0
    assert lists[4][0].allocatable_cpu_cores == 10.0


def test_capacity_state_key_tracks_allocatable_changes() -> None:
    a, b = _capacity(), _capacity()
    assert svc._capacity_state_key([a]) == svc._capacity_state_key([b])
    b.allocatable_cpu_cores = 9.5
    assert svc._capacity_state_key([a]) != svc._capacity_state_key([b])


def test_lite_calendar_reuses_fit_results_across_slots(monkeypatch) -> None:
    """3 天、沒有預約：真正跑 fit 的次數最多等於不同的小時需求係數數量（24）。"""
    monkeypatch.setattr(svc.placement_advisor, "_load_cluster_state", lambda: ([], []))
    monkeypatch.setattr(
        svc.placement_advisor, "_build_node_capacities", lambda **kwargs: [_capacity()]
    )
    monkeypatch.setattr(
        svc.placement_advisor, "_decide_resource_type", lambda request: ("lxc", "Prefer LXC.")
    )
    monkeypatch.setattr(
        svc.vm_request_placement_service, "get_overcommit_ratios", lambda session: (1.0, 1.0)
    )
    monkeypatch.setattr(
        svc.vm_request_placement_service, "get_placement_strategy", lambda session: "balanced"
    )
    monkeypatch.setattr(svc.vm_request_placement_service, "get_node_priorities", lambda session: {})
    monkeypatch.setattr(
        svc.vm_request_placement_service, "_build_storage_pool_state", lambda **kwargs: ({}, False)
    )
    monkeypatch.setattr(
        svc.vm_request_placement_service, "get_placement_tuning", lambda **kwargs: None
    )
    monkeypatch.setattr(svc.placement_support, "allowed_gpu_nodes_for_request", lambda request: None)
    monkeypatch.setattr(
        svc.placement_support, "allowed_template_nodes_for_request", lambda request: None
    )
    monkeypatch.setattr(
        svc, "_load_hourly_demand_profile", lambda **kwargs: {hour: hour / 48 for hour in range(24)}
    )
    monkeypatch.setattr(svc, "_pending_pressure_ratio", lambda **kwargs: 0.1)
    monkeypatch.setattr(
        svc.vm_request_repo, "get_approved_vm_requests_overlapping_window", lambda **kwargs: []
    )

    calls = {"fit": 0}
    real_fit = svc._lightweight_fit_nodes

    def counting_fit(**kwargs):
        calls["fit"] += 1
        return real_fit(**kwargs)

    monkeypatch.setattr(svc, "_lightweight_fit_nodes", counting_fit)

    response = svc._build_availability_response(
        session=None,  # type: ignore[arg-type]
        source_request=VMRequestAvailabilityRequest(
            resource_type="lxc",
            cores=2,
            memory=2048,
            rootfs_size=12,
            days=3,
            detail=False,
        ),
        role=svc.UserRole("student"),
        stack_label="Requested LXC",
    )

    assert response.summary.checked_days == 3
    assert response.summary.feasible_slot_count > 0
    # 舊實作是每一格都 fit（最多 72 次），現在只依 24 種小時需求係數各算一次
    assert calls["fit"] <= 24
