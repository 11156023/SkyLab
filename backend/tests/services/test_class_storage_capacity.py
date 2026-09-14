"""班級容量預檢：節點磁碟總量夠，不代表任何一個儲存區放得下一台機器。

一台機器不能跨儲存區，所以整班必須照實際的挑選規則逐台試放；否則預檢
會放行，開課當下才在 provisioning 撞上「沒有可用儲存區」。
"""

import uuid

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import ProxmoxStorage, TeachingClassMachineNode, VMTemplate
from app.services.proxmox import provisioning_service
from app.services.teaching import class_capacity_service


@pytest.fixture(name="session")
def _session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def _stub_placement_policy(monkeypatch):
    """固定 overcommit 與 tuning，讓測試只驗容量規則本身。"""
    monkeypatch.setattr(
        class_capacity_service.placement_service,
        "get_overcommit_ratios",
        lambda session: (1.0, 1.0),
    )


def _storage(session: Session, *, node: str, name: str, total: float, avail: float):
    session.add(
        ProxmoxStorage(
            node_name=node,
            storage=name,
            storage_type="lvmthin",
            total_gb=total,
            used_gb=total - avail,
            avail_gb=avail,
            can_vm=True,
            can_lxc=True,
            active=True,
            enabled=True,
            speed_tier="ssd",
        )
    )
    session.flush()


def _machine(session: Session, *, disk_gb: int) -> TeachingClassMachineNode:
    machine = TeachingClassMachineNode(
        class_id=uuid.uuid4(),
        node_key="lab",
        source_type="custom",
        custom_image_ref="local:vztmpl/debian.tar.zst",
        name="lab",
        role="target",
        resource_type="lxc",
        cpu=2,
        memory_mb=2048,
        disk_gb=disk_gb,
    )
    session.add(machine)
    session.flush()
    return machine


def _placements(machine, student_count: int) -> dict:
    return {machine.id: {uuid.uuid4(): "a1" for _ in range(student_count)}}


def test_no_issue_when_one_pool_can_host_the_machine(session: Session) -> None:
    _storage(session, node="a1", name="fast", total=200, avail=200)
    machine = _machine(session, disk_gb=60)

    issues = class_capacity_service._storage_pool_issues(
        session, nodes=[machine], placements=_placements(machine, 2)
    )

    assert issues == []


def test_split_pools_cannot_host_one_machine(session: Session) -> None:
    """兩個 40 GB 的池加起來 80 GB，但單台 60 GB 的機器無處可放。"""
    _storage(session, node="a1", name="poolA", total=40, avail=40)
    _storage(session, node="a1", name="poolB", total=40, avail=40)
    machine = _machine(session, disk_gb=60)

    issues = class_capacity_service._storage_pool_issues(
        session, nodes=[machine], placements=_placements(machine, 1)
    )

    assert len(issues) == 1
    assert "a1" in issues[0]


def test_reservations_accumulate_across_students(session: Session) -> None:
    """第一位學生放得下不代表整班放得下——逐台試放要把已放的算進去。"""
    _storage(session, node="a1", name="only", total=100, avail=100)
    machine = _machine(session, disk_gb=60)

    assert (
        class_capacity_service._storage_pool_issues(
            session, nodes=[machine], placements=_placements(machine, 1)
        )
        == []
    )
    assert (
        class_capacity_service._storage_pool_issues(
            session, nodes=[machine], placements=_placements(machine, 2)
        )
        != []
    )


def test_skipped_when_no_managed_storage_is_synced(session: Session) -> None:
    """還沒同步過 storage 清單時不擋，交由開機時的 PVE 端把關。"""
    machine = _machine(session, disk_gb=60)

    assert (
        class_capacity_service._storage_pool_issues(
            session, nodes=[machine], placements=_placements(machine, 3)
        )
        == []
    )


def test_class_machine_node_disk_floor_uses_source_template(session: Session) -> None:
    """班級機器節點與課程模板節點同形，共用同一個磁碟下限規則。"""
    template = VMTemplate(
        name="debian-12",
        pve_vmid=9100,
        node="a1",
        resource_type="lxc",
        default_disk=32,
    )
    session.add(template)
    session.flush()

    machine = _machine(session, disk_gb=10)
    machine.source_type = "template"
    machine.source_template_id = template.id
    machine.custom_image_ref = None

    assert provisioning_service.clone_source_disk_gb(session, machine) == 32
