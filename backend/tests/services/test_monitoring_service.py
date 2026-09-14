"""監控 service 純函式測試（不依賴 PVE/DB）。"""

import uuid

import pytest

from app.exceptions import BadRequestError, PermissionDeniedError
from app.infrastructure.proxmox.operations import MonitoringSnapshot
from app.schemas.monitoring import MonitoringThresholds
from app.services.monitoring import monitoring_service

NODES = [
    {
        "node": "pve1",
        "status": "online",
        "cpu": 0.5,
        "maxcpu": 8,
        "mem": 8 * 1024**3,
        "maxmem": 16 * 1024**3,
        "disk": 100 * 1024**3,
        "maxdisk": 500 * 1024**3,
        "uptime": 3600,
    },
    {
        "node": "pve2",
        "status": "offline",
        "cpu": 0,
        "maxcpu": 4,
        "mem": 0,
        "maxmem": 8 * 1024**3,
        "disk": 0,
        "maxdisk": 250 * 1024**3,
        "uptime": 0,
    },
]

RESOURCES = [
    {
        "vmid": 100,
        "name": "vm-a",
        "node": "pve1",
        "type": "qemu",
        "status": "running",
        "cpu": 0.9,
        "maxcpu": 4,
        "mem": 2 * 1024**3,
        "maxmem": 4 * 1024**3,
    },
    {
        "vmid": 101,
        "name": "vm-b",
        "node": "pve1",
        "type": "qemu",
        "status": "stopped",
        "cpu": 0,
        "maxcpu": 2,
        "mem": 0,
        "maxmem": 2 * 1024**3,
    },
    {
        "vmid": 200,
        "name": "ct-a",
        "node": "pve1",
        "type": "lxc",
        "status": "running",
        "cpu": 0.1,
        "maxcpu": 2,
        "mem": 1 * 1024**3,
        "maxmem": 2 * 1024**3,
    },
    {
        "vmid": 201,
        "name": "ct-b",
        "node": "pve2",
        "type": "lxc",
        "status": "stopped",
        "cpu": 0,
        "maxcpu": 1,
        "mem": 0,
        "maxmem": 1 * 1024**3,
    },
    # 缺鍵資源：不得使聚合崩潰
    {"vmid": 300, "type": "qemu", "status": "running"},
]


def test_build_overview_aggregates() -> None:
    overview = monitoring_service.build_overview(NODES, RESOURCES)

    assert overview.nodes_online == 1
    assert overview.nodes_total == 2
    # 容量含 offline 節點
    assert overview.cpu_total == 12
    assert overview.mem_total == 24 * 1024**3
    assert overview.disk_total == 750 * 1024**3
    # 用量 = Σ(node.cpu * maxcpu)
    assert overview.cpu_used == pytest.approx(4.0)
    assert overview.mem_used == 8 * 1024**3
    assert overview.disk_used == 100 * 1024**3
    # 計數（含缺鍵 running qemu）
    assert overview.vms_running == 2
    assert overview.vms_stopped == 1
    assert overview.lxc_running == 1
    assert overview.lxc_stopped == 1


def test_build_overview_top_lists() -> None:
    overview = monitoring_service.build_overview(NODES, RESOURCES)

    # top 僅含 running；cpu 降冪
    assert [e.vmid for e in overview.top_cpu][:2] == [100, 200]
    assert all(e.status == "running" for e in overview.top_cpu)
    # top_mem 降冪
    assert overview.top_mem[0].vmid == 100
    # 缺鍵資源以 0 參與，不崩潰
    assert 300 in [e.vmid for e in overview.top_cpu]


def test_build_overview_node_vm_counts() -> None:
    """節點 VM 台數含已停止、排除範本與非 VM 類型（原叢集概覽提供的資訊）。"""
    resources = [
        *RESOURCES,
        {"vmid": 900, "node": "pve1", "type": "qemu", "status": "stopped", "template": 1},
        {"vmid": 901, "node": "pve1", "type": "storage", "status": "available"},
    ]
    by_node = {n.node: n for n in monitoring_service.build_overview(NODES, resources).nodes}

    assert by_node["pve1"].vm_count == 3  # 100, 101, 200
    assert by_node["pve2"].vm_count == 1  # 201


def test_build_overview_connection_names() -> None:
    """節點標示所屬 PVE 連線；未在對應表中的節點為 None。"""
    overview = monitoring_service.build_overview(
        NODES, RESOURCES, {"pve1": "機房A"}
    )
    by_node = {n.node: n for n in overview.nodes}

    assert by_node["pve1"].connection_name == "機房A"
    assert by_node["pve2"].connection_name is None


def test_build_overview_empty() -> None:
    overview = monitoring_service.build_overview([], [])
    assert overview.nodes_total == 0
    assert overview.cpu_total == 0
    assert overview.top_cpu == []


def test_build_overview_reports_saturation_once_per_target() -> None:
    nodes = [
        {"node": "pve1", "status": "online", "cpu": 0.95, "maxcpu": 8,
         "mem": 95, "maxmem": 100, "disk": 99, "maxdisk": 100},
        {"node": "pve2", "status": "offline", "cpu": 0, "maxcpu": 8,
         "mem": 0, "maxmem": 100, "disk": 0, "maxdisk": 100},
    ]
    resources = [
        {"vmid": 100, "name": "vm-cpu-and-memory", "node": "pve1",
         "type": "qemu", "status": "running", "cpu": 0.95,
         "mem": 95, "maxmem": 100},
        {"vmid": 101, "name": "vm-memory", "node": "pve1",
         "type": "qemu", "status": "running", "cpu": 0.1,
         "mem": 95, "maxmem": 100},
        {"vmid": 200, "name": "ct-cpu", "node": "pve1",
         "type": "lxc", "status": "running", "cpu": 0.95,
         "mem": 0, "maxmem": 0},
        {"vmid": 201, "name": "stopped", "node": "pve1",
         "type": "lxc", "status": "stopped", "cpu": 0.99,
         "mem": 99, "maxmem": 100},
        {"vmid": 900, "name": "template", "node": "pve1",
         "type": "qemu", "status": "running", "template": 1,
         "cpu": 0.99, "mem": 99, "maxmem": 100},
    ]

    overview = monitoring_service.build_overview(
        nodes,
        resources,
        thresholds=MonitoringThresholds(cpu=90, memory=90),
    )

    assert overview.nodes_saturated == 1
    assert overview.vms_saturated == 2
    assert overview.lxc_saturated == 1
    assert overview.vms_running == 2
    assert overview.lxc_running == 1
    assert overview.overall_status == "critical"

    by_target = {issue.target: issue for issue in overview.issues}
    assert set(by_target) == {"pve1", "pve2", "vm-cpu-and-memory", "vm-memory", "ct-cpu"}
    assert by_target["pve2"].kind == "node_offline"
    assert {signal.metric for signal in by_target["pve1"].signals} == {"cpu", "memory"}
    assert by_target["vm-cpu-and-memory"].kind == "guest_overloaded"
    assert {signal.metric for signal in by_target["vm-cpu-and-memory"].signals} == {"cpu", "memory"}


def test_build_overview_does_not_mark_zero_capacity_as_saturated() -> None:
    overview = monitoring_service.build_overview(
        [{"node": "pve1", "status": "online", "cpu": 0, "maxcpu": 0,
          "mem": 0, "maxmem": 0, "disk": 0, "maxdisk": 0}],
        [{"vmid": 100, "name": "vm-empty", "type": "qemu", "status": "running",
          "cpu": 0, "mem": 0, "maxmem": 0}],
    )

    assert overview.overall_status == "healthy"
    assert overview.nodes_saturated == 0
    assert overview.vms_saturated == 0
    assert overview.issues == []


def test_get_overview_uses_governance_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeConfig:
        alert_cpu_threshold = 80
        alert_memory_threshold = 85

    monkeypatch.setattr(
        monitoring_service.proxmox_service,
        "collect_monitoring_snapshot",
        lambda: MonitoringSnapshot([], [], 0, 1),
    )
    monkeypatch.setattr(monitoring_service, "_connection_names", lambda session: {})
    monkeypatch.setattr(
        monitoring_service.governance_repo,
        "get_governance_config",
        lambda *, session: FakeConfig(),
    )

    overview = monitoring_service.get_overview(session=None)  # type: ignore[arg-type]

    assert overview.thresholds.cpu == 80
    assert overview.thresholds.memory == 85


def test_get_overview_defaults_thresholds_when_governance_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_config(*, session: object) -> None:
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(
        monitoring_service.proxmox_service,
        "collect_monitoring_snapshot",
        lambda: MonitoringSnapshot([], [], 0, 1),
    )
    monkeypatch.setattr(monitoring_service, "_connection_names", lambda session: {})
    monkeypatch.setattr(
        monitoring_service.governance_repo,
        "get_governance_config",
        unavailable_config,
    )

    overview = monitoring_service.get_overview(session=None)  # type: ignore[arg-type]

    assert overview.thresholds.cpu == 90
    assert overview.thresholds.memory == 90


def test_get_overview_marks_partial_pve_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        monitoring_service.proxmox_service,
        "collect_monitoring_snapshot",
        lambda: MonitoringSnapshot(
            nodes=[{
                "node": "pve1",
                "status": "online",
                "cpu": 0.1,
                "maxcpu": 4,
                "mem": 1,
                "maxmem": 4,
                "disk": 1,
                "maxdisk": 4,
                "uptime": 60,
            }],
            resources=[],
            failed_connections=1,
            total_connections=2,
        ),
    )
    monkeypatch.setattr(monitoring_service, "_connection_names", lambda session: {})
    monkeypatch.setattr(
        monitoring_service.governance_repo,
        "get_governance_config",
        lambda *, session: type(
            "FakeConfig",
            (),
            {"alert_cpu_threshold": 90, "alert_memory_threshold": 90},
        )(),
    )

    overview = monitoring_service.get_overview(session=None)  # type: ignore[arg-type]

    assert overview.data_status == "partial"
    assert overview.overall_status == "warning"


def test_get_node_rrd_rejects_bad_timeframe() -> None:
    with pytest.raises(BadRequestError):
        monitoring_service.get_node_rrd("pve1", "month")


def test_get_vm_rrd_checks_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()

    class FakeResource:
        user_id = owner_id

    class FakeUser:
        id = other_id
        is_superuser = False
        role = "student"

    monkeypatch.setattr(
        monitoring_service.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: FakeResource(),
    )
    with pytest.raises(PermissionDeniedError):
        monitoring_service.get_vm_rrd(
            session=None,  # type: ignore[arg-type]
            vmid=100,
            timeframe="hour",
            user=FakeUser(),  # type: ignore[arg-type]
        )


def test_get_vm_rrd_unknown_vmid(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exceptions import NotFoundError

    monkeypatch.setattr(
        monitoring_service.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: None,
    )

    class FakeUser:
        id = uuid.uuid4()
        is_superuser = True
        role = "admin"

    with pytest.raises(NotFoundError):
        monitoring_service.get_vm_rrd(
            session=None,  # type: ignore[arg-type]
            vmid=999,
            timeframe="hour",
            user=FakeUser(),  # type: ignore[arg-type]
        )
