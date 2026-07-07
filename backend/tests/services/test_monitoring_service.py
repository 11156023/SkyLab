"""監控 service 純函式測試（不依賴 PVE/DB）。"""

import uuid

import pytest

from app.exceptions import BadRequestError, PermissionDeniedError
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


def test_build_overview_empty() -> None:
    overview = monitoring_service.build_overview([], [])
    assert overview.nodes_total == 0
    assert overview.cpu_total == 0
    assert overview.top_cpu == []


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
