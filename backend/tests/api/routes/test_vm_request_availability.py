from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.core.config import settings
from app.domain.placement.schemas import NodeCapacity, PlacementDecision, PlacementPlan
from app.services.vm import vm_request_availability_service


def _fake_capacities() -> list[NodeCapacity]:
    return [
        NodeCapacity(
            node="pve-a",
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
            allocatable_cpu_cores=10,
            total_memory_bytes=64 * 1024**3,
            allocatable_memory_bytes=40 * 1024**3,
            total_disk_bytes=1000 * 1024**3,
            allocatable_disk_bytes=700 * 1024**3,
        )
    ]


def _patch_availability(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        vm_request_availability_service.placement_advisor,
        "_load_cluster_state",
        lambda: ([], []),
    )
    monkeypatch.setattr(
        vm_request_availability_service.placement_advisor,
        "_build_node_capacities",
        lambda **kwargs: _fake_capacities(),
    )
    monkeypatch.setattr(
        vm_request_availability_service.placement_advisor,
        "_decide_resource_type",
        lambda request: ("lxc", "Prefer LXC for this request."),
    )

    def fake_plan(
        *,
        request,
        node_capacities,
        effective_resource_type,
        resource_type_reason,
        **kwargs,
    ):
        del request, node_capacities, effective_resource_type, resource_type_reason, kwargs
        return PlacementPlan(
            feasible=True,
            requested_resource_type="lxc",
            effective_resource_type="lxc",
            resource_type_reason="Prefer LXC for this request.",
            assigned_instances=1,
            unassigned_instances=0,
            recommended_node="pve-a",
            summary="This slot can fit the request.",
            rationale=["pve-a still has enough headroom."],
            warnings=[],
            placements=[
                PlacementDecision(
                    node="pve-a",
                    instance_count=1,
                    cpu_cores_reserved=2.0,
                    memory_bytes_reserved=2 * 1024**3,
                    disk_bytes_reserved=20 * 1024**3,
                    remaining_cpu_cores=8.0,
                    remaining_memory_bytes=38 * 1024**3,
                    remaining_disk_bytes=680 * 1024**3,
                )
            ],
            candidate_nodes=_fake_capacities(),
        )

    monkeypatch.setattr(
        vm_request_availability_service.vm_request_placement_service,
        "build_plan",
        fake_plan,
    )

def test_preview_availability_returns_hourly_suggestions(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_availability(monkeypatch)

    response = client.post(
        f"{settings.API_V1_STR}/vm-requests/availability",
        headers=normal_user_token_headers,
        json={
            "resource_type": "lxc",
            "cores": 2,
            "memory": 2048,
            "rootfs_size": 12,
            "days": 2,
            "timezone": "Asia/Taipei",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["checked_days"] == 2
    assert data["summary"]["feasible_slot_count"] > 0
    assert len(data["recommended_slots"]) > 0
    assert any(day["available_hours"] for day in data["days"])


def test_existing_request_availability_uses_request_id(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_availability(monkeypatch)

    start_at = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    end_at = start_at + timedelta(hours=2)
    create_response = client.post(
        f"{settings.API_V1_STR}/vm-requests/",
        headers=normal_user_token_headers,
        json={
            "reason": "Need a container for class project testing.",
            "resource_type": "lxc",
            "hostname": "class-project-box",
            "cores": 2,
            "memory": 2048,
            "password": "strongpass123",
            "storage": "local-lvm",
            "ostemplate": "local:vztmpl/debian-12-standard.tar.zst",
            "rootfs_size": 16,
            "mode": "scheduled",
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
        },
    )
    assert create_response.status_code == 200
    request_id = create_response.json()["id"]

    response = client.get(
        f"{settings.API_V1_STR}/vm-requests/{request_id}/availability",
        headers=normal_user_token_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["role"] == "student"
    assert len(data["days"]) == 7
