"""Coverage tests for app.services.teaching.class_network_service."""

import uuid
from datetime import date, time

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import (
    CourseEnvironmentEdge,
    CourseEnvironmentVersion,
    IpAllocation,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStudent,
    User,
)
from app.models.base import get_datetime_utc
from app.services.teaching import class_network_service as svc
from tests.utils.class_machines import add_student_machine

PREFIX = svc.COMMENT_PREFIX


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_sync_scope_rules_reports_delete_and_create_failures(monkeypatch):
    monkeypatch.setattr(
        svc.proxmox_service,
        "find_resource",
        lambda vmid, **_kw: {"node": "pve1", "type": "lxc"},
    )
    monkeypatch.setattr(
        svc.firewall_service,
        "list_vm_firewall_rules_strict",
        lambda _n, _v, _t: [
            {"comment": f"{PREFIX}old", "pos": 3},
            {"comment": f"{PREFIX}nopos"},
            {"comment": "gateway"},
            {"pos": 1},
            {"comment": f"{PREFIX}keep", "pos": 0},
        ],
    )

    def fail_delete(_n, _v, _t, pos):
        raise RuntimeError("delete failed")

    def fail_create(_n, _v, _t, rule):
        raise RuntimeError("create failed")

    monkeypatch.setattr(svc.firewall_service, "delete_rule_by_pos", fail_delete)
    monkeypatch.setattr(svc.firewall_service, "create_rule", fail_create)
    errors = svc.sync_scope_rules(
        comment_prefix=PREFIX,
        scope_vmids={101},
        planned=[
            svc.PlannedRule(vmid=101, comment=f"{PREFIX}keep", rule={}),
            svc.PlannedRule(vmid=101, comment=f"{PREFIX}new", rule={"type": "in"}),
        ],
    )
    assert errors == [
        "101: stale firewall rule cleanup failed",
        "101: firewall configuration failed",
    ]


def test_apply_topology_for_unknown_class(session, monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(
        svc, "sync_scope_rules", lambda **kw: seen.append(kw) or ["sync"]
    )
    assert svc.apply_class_topology(session, class_id=uuid.uuid4()) == ["sync"]
    assert seen[0]["scope_vmids"] == set()
    assert seen[0]["planned"] == []


def _ip(session, vmid, address):
    session.add(IpAllocation(ip_address=address, purpose="vm", vmid=vmid))


def test_apply_topology_plans_rules_per_student(session, monkeypatch):
    version = CourseEnvironmentVersion(
        environment_id=uuid.uuid4(),
        version=1,
        peer_policy="explicit",
        created_at=get_datetime_utc(),
    )
    session.add(version)
    item = TeachingClass(
        owner_id=uuid.uuid4(),
        name="Net",
        code="NETWORKING-101",
        term="115-1",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 12, 31),
        weekday=1,
        start_time=time(9, 0),
        end_time=time(12, 0),
        course_version_id=version.id,
    )
    session.add(item)
    nodes = {}
    for order, key in enumerate(["web", "db"]):
        node = TeachingClassMachineNode(
            class_id=item.id,
            node_key=key,
            source_type="custom",
            name=key,
            role="server",
            resource_type="lxc",
            cpu=1,
            memory_mb=512,
            disk_gb=8,
            sort_order=order,
        )
        session.add(node)
        nodes[key] = node
    session.add(
        CourseEnvironmentEdge(
            version_id=version.id,
            source_node_key="web",
            target_node_key="db",
            direction="one_way",
            protocol="tcp",
            port=5432,
        )
    )
    student = User(email="student@example.com", hashed_password="x")
    session.add(student)
    session.flush()

    with_user = TeachingClassStudent(class_id=item.id, user_id=student.id)
    without_user = TeachingClassStudent(class_id=item.id, user_id=uuid.uuid4())
    session.add(with_user)
    session.add(without_user)
    session.flush()
    add_student_machine(session, enrollment=with_user, node=nodes["web"], vmid=101)
    add_student_machine(session, enrollment=with_user, node=nodes["db"], vmid=102)
    add_student_machine(session, enrollment=without_user, node=nodes["web"], vmid=201)
    add_student_machine(session, enrollment=without_user, node=nodes["db"], vmid=202)
    _ip(session, 101, "10.0.0.11")
    _ip(session, 102, "10.0.0.12")
    _ip(session, 201, "10.0.0.21")
    session.commit()

    monkeypatch.setattr(svc.proxmox_service, "find_resource", lambda vmid: {})
    published: list[dict] = []
    monkeypatch.setattr(
        svc.course_publication_service,
        "apply_for_machines",
        lambda _s, **kw: published.append(kw) or ["publication failed"],
    )
    synced: list[dict] = []
    monkeypatch.setattr(svc, "sync_scope_rules", lambda **kw: synced.append(kw) or [])

    errors = svc.apply_class_topology(session, class_id=item.id)

    assert errors == [
        "publication failed",
        "201 → 202: firewall configuration failed",
    ]
    assert len(published) == 1
    assert published[0]["owner"].id == student.id
    assert published[0]["vmid_by_key"] == {"web": 101, "db": 102}
    assert "scope" not in published[0]
    assert synced[0]["scope_vmids"] == {101, 102, 201, 202}
    planned = synced[0]["planned"]
    assert [(rule.vmid, rule.rule["type"]) for rule in planned] == [
        (101, "out"),
        (102, "in"),
    ]
    assert planned[0].rule["dport"] == "5432"
