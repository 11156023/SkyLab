"""Coverage tests for app.services.teaching.class_capacity_service."""

import uuid

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.domain.placement.schemas import NodeCapacity, NodeSnapshot, ResourceSnapshot
from app.exceptions import BadRequestError
from app.models import (
    BatchProvisionTask,
    BatchProvisionTaskStatus,
    ClassCapacityReservation,
    TeachingClassMachineNode,
    TeachingClassStudent,
    TeachingClassStudentMachine,
)
from app.services.teaching import class_capacity_service as svc

GIB = 1024**3


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _machine(
    *,
    name: str = "lab",
    source_type: str = "custom",
    resource_type: str = "lxc",
    custom_image_ref: str | None = None,
    source_template_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
    cpu: int = 2,
    memory_mb: int = 2048,
    disk_gb: int = 10,
) -> TeachingClassMachineNode:
    return TeachingClassMachineNode(
        id=uuid.uuid4(),
        class_id=class_id or uuid.uuid4(),
        node_key=name,
        source_type=source_type,
        source_template_id=source_template_id,
        custom_image_ref=custom_image_ref,
        name=name,
        role="target",
        resource_type=resource_type,
        cpu=cpu,
        memory_mb=memory_mb,
        disk_gb=disk_gb,
        network="lab-net",
    )


def _capacity(name, *, status="online", cores=16.0, memory_gb=32, disk_gb=200):
    return NodeCapacity(
        node=name,
        status=status,
        total_cpu_cores=cores,
        allocatable_cpu_cores=cores,
        total_memory_bytes=memory_gb * GIB,
        allocatable_memory_bytes=memory_gb * GIB,
        total_disk_bytes=disk_gb * GIB,
        allocatable_disk_bytes=disk_gb * GIB,
    )


def _reservation(class_id, version_id, *, status="reserved", placement_plan=None):
    return ClassCapacityReservation(
        class_id=class_id,
        course_version_id=version_id,
        student_count=1,
        machine_count=1,
        cpu_cores=1,
        memory_mb=1,
        disk_gb=1,
        ip_count=1,
        network_count=1,
        placement_plan=placement_plan or {},
        student_placements={},
        status=status,
    )


# ------------------------------------------------------------------ helpers


def test_json_dict_accepts_legacy_strings():
    assert svc._json_dict({"a": 1}) == {"a": 1}
    assert svc._json_dict('{"a": 2}') == {"a": 2}
    assert svc._json_dict(None) == {}
    assert svc._json_dict("not json") == {}
    assert svc._json_dict(42) == {}
    assert svc._json_dict("[1, 2]") == {}


def test_ratio_with_zero_total_is_infinite():
    assert svc._ratio(1.0, 0) == float("inf")
    assert svc._ratio(1.0, 2.0) == 0.5


# ------------------------------------------------------------------ reserve


def test_reserve_rejects_other_course_version(session):
    class_id = uuid.uuid4()
    session.add(_reservation(class_id, uuid.uuid4()))
    session.commit()
    with pytest.raises(BadRequestError):
        svc.reserve(
            session,
            class_id=class_id,
            course_version_id=uuid.uuid4(),
            nodes=[],
            students=[],
        )


def test_reserve_returns_active_reservation(session):
    class_id, version_id = uuid.uuid4(), uuid.uuid4()
    existing = _reservation(class_id, version_id)
    session.add(existing)
    session.commit()
    assert (
        svc.reserve(
            session,
            class_id=class_id,
            course_version_id=version_id,
            nodes=[],
            students=[],
        )
        is existing
    )


def test_reserve_replaces_released_reservation_and_requires_roster(session):
    class_id, version_id = uuid.uuid4(), uuid.uuid4()
    session.add(_reservation(class_id, version_id, status="released"))
    session.commit()
    with pytest.raises(BadRequestError):
        svc.reserve(
            session,
            class_id=class_id,
            course_version_id=version_id,
            nodes=[],
            students=[],
        )
    remaining = session.exec(select(ClassCapacityReservation)).all()
    assert remaining == []


def test_reserve_creates_snapshot_and_ip_reservations(session, monkeypatch):
    class_id, version_id = uuid.uuid4(), uuid.uuid4()
    node = _machine(class_id=class_id)
    student = TeachingClassStudent(class_id=class_id, user_id=uuid.uuid4())
    plan = {"pve1": {"cpu_cores": 2, "memory_bytes": 1, "disk_bytes": 1, "machines": 1}}
    monkeypatch.setattr(
        svc,
        "_check_cluster_capacity",
        lambda _s, *, nodes, student_ids: (
            plan,
            {node.id: {student_ids[0]: "pve1"}},
        ),
    )
    reserved: list = []
    monkeypatch.setattr(
        svc.ip_management_service,
        "reserve_ips",
        lambda _s, *, teaching_class_id, reservation_keys: reserved.append(
            (teaching_class_id, reservation_keys)
        ),
    )
    reservation = svc.reserve(
        session,
        class_id=class_id,
        course_version_id=version_id,
        nodes=[node],
        students=[student],
    )
    assert reservation.machine_count == 1
    assert reservation.placement_plan == plan
    assert reservation.student_placements == {
        str(node.id): {str(student.user_id): "pve1"}
    }
    assert reserved == [(class_id, [f"{class_id}:lab:{student.user_id}"])]


# ------------------------------------------------------------------ release


def test_release_deletes_or_marks_snapshot(session, monkeypatch):
    monkeypatch.setattr(
        svc.ip_management_service, "release_class_reservations", lambda _s, _c: 3
    )
    keep_id, drop_id = uuid.uuid4(), uuid.uuid4()
    session.add(_reservation(keep_id, uuid.uuid4()))
    session.add(_reservation(drop_id, uuid.uuid4()))
    session.commit()

    assert svc.release(session, class_id=keep_id, delete_snapshot=False) == 3
    assert svc.release(session, class_id=drop_id) == 3
    assert svc.release(session, class_id=uuid.uuid4()) == 3
    rows = session.exec(select(ClassCapacityReservation)).all()
    assert [(r.class_id, r.status) for r in rows] == [(keep_id, "released")]


# ------------------------------------------------------------- eligibility


def test_custom_lxc_falls_back_when_template_map_is_empty(session, monkeypatch):
    monkeypatch.setattr(svc.proxmox_service, "get_lxc_template_node_map", lambda: {})
    monkeypatch.setattr(
        svc.provisioning_service, "get_lxc_target_node", lambda: "pve-default"
    )
    machine = _machine(custom_image_ref="local:vztmpl/x.tar.zst")
    assert svc.eligible_nodes_for_machine(session, machine_node=machine) == {
        "pve-default"
    }


def test_custom_vm_uses_template_node(session, monkeypatch):
    seen: list[int] = []
    monkeypatch.setattr(
        svc.provisioning_service,
        "get_vm_target_node",
        lambda vmid: seen.append(vmid) or "pve-vm",
    )
    machine = _machine(resource_type="qemu", custom_image_ref=None)
    assert svc.eligible_nodes_for_machine(session, machine_node=machine) == {"pve-vm"}
    assert seen == [0]


def test_class_eligibility_reports_missing_template(session):
    machine = _machine(source_type="template", source_template_id=uuid.uuid4())
    eligibility, shared, issues = svc.class_eligibility(session, nodes=[machine])
    assert (eligibility, shared) == ({}, set())
    assert len(issues) == 1


# ------------------------------------------------------- cluster selection


def test_choose_class_cluster_without_usable_cluster(monkeypatch):
    monkeypatch.setattr(svc, "get_connection_id_for_node", lambda name: 1)
    machine = _machine()
    assert (
        svc.choose_class_cluster(
            nodes=[machine],
            student_count=3,
            clusters={2},
            eligibility={machine.id: {"a1"}},
            capacities={},
        )
        is None
    )


def test_resolve_class_targets_rejects_foreign_cluster(session, monkeypatch):
    machine = _machine()
    monkeypatch.setattr(
        svc,
        "class_eligibility",
        lambda _s, *, nodes: ({machine.id: {"a1"}}, {1}, []),
    )
    targets, issues = svc.resolve_class_targets(
        session, nodes=[machine], connection_id=9
    )
    assert targets == {}
    assert len(issues) == 1


def test_resolve_class_targets_reports_no_target(session, monkeypatch):
    machine = _machine()
    monkeypatch.setattr(
        svc,
        "class_eligibility",
        lambda _s, *, nodes: ({machine.id: {"a1"}}, {1}, []),
    )
    monkeypatch.setattr(svc, "_preferred_node", lambda: None)
    monkeypatch.setattr(svc, "targets_in_cluster", lambda **kw: {})
    targets, issues = svc.resolve_class_targets(session, nodes=[machine])
    assert targets == {}
    assert len(issues) == 1


# ------------------------------------------------ _evaluate_cluster_capacity


@pytest.fixture
def cluster_state(monkeypatch):
    """Stub the live PVE state; tests set ``state['capacities']``.

    ``nodes``／``resources`` 是 PVE 節點與機器快照，用來算已分配的用量。
    """
    state: dict = {"capacities": [], "error": None, "nodes": [], "resources": []}

    def load():
        if state["error"]:
            raise state["error"]
        return state["nodes"], state["resources"]

    monkeypatch.setattr(svc.placement_support, "load_cluster_state", load)
    monkeypatch.setattr(
        svc.placement_service, "get_overcommit_ratios", lambda _s: (1.0, 1.0)
    )
    monkeypatch.setattr(
        svc.placement_support,
        "build_live_node_capacities",
        lambda **kw: state["capacities"],
    )
    monkeypatch.setattr(svc, "get_connection_id_for_node", lambda name: 1)
    return state


def _eligible(monkeypatch, machine, names):
    monkeypatch.setattr(
        svc,
        "class_eligibility",
        lambda _s, *, nodes: ({machine.id: set(names)}, {1}, []),
    )


def test_evaluate_reports_capacity_lookup_failure(session, monkeypatch, cluster_state):
    machine = _machine()
    _eligible(monkeypatch, machine, ["a1"])
    cluster_state["error"] = RuntimeError("pve down")
    plan, placements, issues = svc._evaluate_cluster_capacity(
        session, nodes=[machine], student_ids=[uuid.uuid4()]
    )
    assert (plan, placements) == ({}, {})
    assert len(issues) == 1


def test_evaluate_reports_no_placement(session, monkeypatch, cluster_state):
    machine = _machine()
    _eligible(monkeypatch, machine, ["a1"])
    cluster_state["capacities"] = [_capacity("a1", status="offline")]
    plan, placements, issues = svc._evaluate_cluster_capacity(
        session, nodes=[machine], student_ids=[uuid.uuid4()]
    )
    assert (plan, placements) == ({}, {})
    assert len(issues) == 1


def test_evaluate_subtracts_pending_reservations_and_reports_shortage(
    session, monkeypatch, cluster_state
):
    machine = _machine(cpu=4, memory_mb=4096, disk_gb=50)
    _eligible(monkeypatch, machine, ["a1"])
    session.add(
        _reservation(
            uuid.uuid4(),
            uuid.uuid4(),
            placement_plan={
                "a1": {
                    "cpu_cores": 15,
                    "memory_bytes": 31 * GIB,
                    "disk_bytes": 190 * GIB,
                },
                "ghost": {"cpu_cores": 1},
            },
        )
    )
    session.add(
        _reservation(
            uuid.uuid4(),
            uuid.uuid4(),
            status="released",
            placement_plan={"a1": {"cpu_cores": 100}},
        )
    )
    session.commit()
    cluster_state["capacities"] = [_capacity("a1")]
    plan, placements, issues = svc._evaluate_cluster_capacity(
        session, nodes=[machine], student_ids=[uuid.uuid4()]
    )
    assert plan["a1"]["machines"] == 1
    assert len(placements[machine.id]) == 1
    # 沒有逐位落點的舊預留整份扣掉 → CPU、RAM 不足；磁碟不再拿節點系統碟比
    assert len(issues) == 2
    assert not any("Disk" in issue for issue in issues)


def test_evaluate_reports_offline_target(session, monkeypatch, cluster_state):
    machine = _machine()
    _eligible(monkeypatch, machine, ["a1"])
    cluster_state["capacities"] = [_capacity("a1")]
    monkeypatch.setattr(
        svc,
        "allocate_class_placements",
        lambda **kw: {machine.id: {uuid.uuid4(): "gone"}},
    )
    _plan, _placements, issues = svc._evaluate_cluster_capacity(
        session, nodes=[machine], student_ids=[uuid.uuid4()]
    )
    assert len(issues) == 1


def test_evaluate_checks_storage_pools_when_nodes_fit(
    session, monkeypatch, cluster_state
):
    machine = _machine()
    _eligible(monkeypatch, machine, ["a1"])
    cluster_state["capacities"] = [_capacity("a1")]
    monkeypatch.setattr(
        svc, "_storage_pool_issues", lambda _s, **_kw: ["pool full"]
    )
    _plan, _placements, issues = svc._evaluate_cluster_capacity(
        session, nodes=[machine], student_ids=[uuid.uuid4()]
    )
    assert issues == ["pool full"]


def test_evaluate_checks_storage_pools_even_when_nodes_are_short(
    session, monkeypatch, cluster_state
):
    """CPU／RAM 不足時照樣檢查儲存區，老師一次看到所有不足的項目。"""
    machine = _machine(cpu=64)
    _eligible(monkeypatch, machine, ["a1"])
    cluster_state["capacities"] = [_capacity("a1", cores=4)]
    monkeypatch.setattr(
        svc, "_storage_pool_issues", lambda _s, **_kw: ["pool full"]
    )
    _plan, _placements, issues = svc._evaluate_cluster_capacity(
        session, nodes=[machine], student_ids=[uuid.uuid4()]
    )
    assert len(issues) == 2
    assert issues[-1] == "pool full"


def test_node_root_disk_is_not_used_for_the_disk_check(
    session, monkeypatch, cluster_state
):
    """節點層的 disk 是 PVE 主機系統碟；它滿了不代表機器放不下。"""
    machine = _machine(disk_gb=500)
    _eligible(monkeypatch, machine, ["a1"])
    cluster_state["capacities"] = [_capacity("a1", disk_gb=10)]
    _plan, _placements, issues = svc._evaluate_cluster_capacity(
        session, nodes=[machine], student_ids=[uuid.uuid4()]
    )
    assert issues == []


def _node_snapshot(name="a1", *, cores=8, memory_gb=16):
    return NodeSnapshot(
        node=name,
        status="online",
        maxcpu=cores,
        maxmem_bytes=memory_gb * GIB,
    )


def _guest(vmid, *, node="a1", cores=2, memory_gb=4, status="stopped"):
    return ResourceSnapshot(
        vmid=vmid,
        node=node,
        status=status,
        maxcpu=cores,
        maxmem_bytes=memory_gb * GIB,
    )


def test_stopped_machines_still_count_against_capacity(
    session, monkeypatch, cluster_state
):
    """下課時段整班關機，即時用量是 0；已分配的規格仍要算進去。"""
    machine = _machine(cpu=2, memory_mb=4096)
    _eligible(monkeypatch, machine, ["a1"])
    # 即時看起來很空，但節點只有 16 GB，關機的機器已經分配掉 14 GB
    cluster_state["capacities"] = [_capacity("a1", cores=64, memory_gb=64)]
    cluster_state["nodes"] = [_node_snapshot(memory_gb=16)]
    cluster_state["resources"] = [_guest(201, memory_gb=14)]
    _plan, _placements, issues = svc._evaluate_cluster_capacity(
        session, nodes=[machine], student_ids=[uuid.uuid4()]
    )
    assert len(issues) == 1
    assert "RAM" in issues[0]


def test_platform_template_guests_are_not_counted(
    session, monkeypatch, cluster_state
):
    """範本母機關機且永不開機，不能佔掉容量。"""
    machine = _machine(cpu=2, memory_mb=4096)
    _eligible(monkeypatch, machine, ["a1"])
    monkeypatch.setattr(svc, "_template_guest_vmids", lambda _s: {201})
    cluster_state["capacities"] = [_capacity("a1", cores=64, memory_gb=64)]
    cluster_state["nodes"] = [_node_snapshot(memory_gb=16)]
    cluster_state["resources"] = [_guest(201, memory_gb=14)]
    _plan, _placements, issues = svc._evaluate_cluster_capacity(
        session, nodes=[machine], student_ids=[uuid.uuid4()]
    )
    assert issues == []


def test_cpu_commitment_uses_overcommit_ratio(session, monkeypatch, cluster_state):
    machine = _machine(cpu=4, memory_mb=1024)
    _eligible(monkeypatch, machine, ["a1"])
    monkeypatch.setattr(
        svc.placement_service, "get_overcommit_ratios", lambda _s: (4.0, 1.0)
    )
    cluster_state["capacities"] = [_capacity("a1", cores=64, memory_gb=64)]
    # 8 核 ×4 = 32 vCPU，已分配 24，扣 10% 餘裕後還剩 4.8 → 放得下 4
    cluster_state["nodes"] = [_node_snapshot(cores=8, memory_gb=64)]
    cluster_state["resources"] = [_guest(201, cores=24, memory_gb=1)]
    plan, _placements, issues = svc._evaluate_cluster_capacity(
        session, nodes=[machine], student_ids=[uuid.uuid4()]
    )
    assert issues == []
    assert plan["a1"]["available_cpu_cores"] == pytest.approx(4.8)


def test_ram_shortage_message_keeps_one_decimal(session, monkeypatch, cluster_state):
    machine = _machine(cpu=1, memory_mb=7680)  # 7.5 GB
    _eligible(monkeypatch, machine, ["a1"])
    cluster_state["capacities"] = [_capacity("a1", memory_gb=7)]
    _plan, _placements, issues = svc._evaluate_cluster_capacity(
        session, nodes=[machine], student_ids=[uuid.uuid4()]
    )
    assert len(issues) == 1
    assert "7.5" in issues[0] and "7.0" in issues[0]


def _other_class_reservation(session, *, built_users, pending_users, node="a1"):
    """另一個還在 reserved 的班級：built_users 的機器已建好，其餘還沒建。"""
    class_id = uuid.uuid4()
    spec = _machine(class_id=class_id, cpu=4, memory_mb=8192)
    session.add(spec)
    placements = {}
    for user_id in [*built_users, *pending_users]:
        student = TeachingClassStudent(class_id=class_id, user_id=user_id)
        session.add(student)
        session.flush()
        placements[str(user_id)] = node
        if user_id in built_users:
            task = BatchProvisionTask(
                job_id=uuid.uuid4(),
                user_id=user_id,
                member_index=0,
                vmid=300 + len(placements),
                status=BatchProvisionTaskStatus.completed,
            )
            session.add(task)
            session.flush()
            session.add(
                TeachingClassStudentMachine(
                    class_student_id=student.id,
                    machine_node_id=spec.id,
                    batch_task_id=task.id,
                )
            )
    reservation = _reservation(class_id, uuid.uuid4())
    reservation.student_placements = {str(spec.id): placements}
    session.add(reservation)
    session.commit()
    return spec


def test_pending_reservation_only_counts_machines_not_built_yet(session):
    built, pending = [uuid.uuid4()], [uuid.uuid4(), uuid.uuid4()]
    spec = _other_class_reservation(session, built_users=built, pending_users=pending)
    machines, legacy = svc._pending_reservation_load(session)
    assert legacy == []
    # 已建好的那台由 PVE 已分配量算，這裡只剩兩台待建
    assert [(node, item.id) for node, item in machines] == [("a1", spec.id)] * 2


def test_pending_reservations_reduce_capacity(session, monkeypatch, cluster_state):
    _other_class_reservation(
        session, built_users=[], pending_users=[uuid.uuid4(), uuid.uuid4()]
    )
    machine = _machine(cpu=1, memory_mb=1024)
    _eligible(monkeypatch, machine, ["a1"])
    # 16 GB 被別班兩台各 8 GB 的待建機器預留光了
    cluster_state["capacities"] = [_capacity("a1", memory_gb=16)]
    _plan, _placements, issues = svc._evaluate_cluster_capacity(
        session, nodes=[machine], student_ids=[uuid.uuid4()]
    )
    assert len(issues) == 1
    assert "RAM" in issues[0]


def test_reserve_does_not_store_preview_availability(session, monkeypatch):
    machine = _machine()
    student = TeachingClassStudent(class_id=machine.class_id, user_id=uuid.uuid4())
    monkeypatch.setattr(
        svc,
        "_check_cluster_capacity",
        lambda _s, *, nodes, student_ids: (
            {
                "a1": {
                    "cpu_cores": 2,
                    "memory_bytes": 1,
                    "disk_bytes": 1,
                    "machines": 1,
                    "available_cpu_cores": 9.5,
                    "available_memory_bytes": 9,
                }
            },
            {},
        ),
    )
    monkeypatch.setattr(svc.ip_management_service, "reserve_ips", lambda *_a, **_k: None)
    reservation = svc.reserve(
        session,
        class_id=machine.class_id,
        course_version_id=uuid.uuid4(),
        nodes=[machine],
        students=[student],
    )
    assert svc._json_dict(reservation.placement_plan) == {
        "a1": {"cpu_cores": 2, "memory_bytes": 1, "disk_bytes": 1, "machines": 1}
    }


def test_storage_pool_issues_without_targets(session):
    assert svc._storage_pool_issues(session, nodes=[], placements={}) == []


def test_check_cluster_capacity_raises_on_issues(session, monkeypatch):
    monkeypatch.setattr(
        svc,
        "_evaluate_cluster_capacity",
        lambda _s, *, nodes, student_ids: ({}, {}, ["a", "b"]),
    )
    with pytest.raises(BadRequestError):
        svc._check_cluster_capacity(session, nodes=[], student_ids=[])


def test_check_cluster_capacity_returns_plan(session, monkeypatch):
    monkeypatch.setattr(
        svc,
        "_evaluate_cluster_capacity",
        lambda _s, *, nodes, student_ids: ({"a1": {}}, {"x": {}}, []),
    )
    assert svc._check_cluster_capacity(session, nodes=[], student_ids=[]) == (
        {"a1": {}},
        {"x": {}},
    )
