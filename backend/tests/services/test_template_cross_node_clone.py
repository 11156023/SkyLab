"""範本跨節點 clone：同叢集內可以 clone 到其他節點，條件是目標儲存區是共享儲存。

PVE 跨節點 clone 只接受共享儲存（Ceph 等）當目標，範本母機放在哪不影響。
做不到（沒有共享儲存、或跨節點 clone 失敗）時一律退回範本節點，整班／整組
仍在同一個叢集。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.domain.placement.models import PlacementTuning, WorkingStoragePool
from app.domain.placement.schemas import NodeCapacity, PlacementRequest
from app.models import TeachingClassMachineNode, VMTemplate, VMTemplateStatus
from app.services.proxmox import provisioning_service
from app.services.teaching import class_capacity_service
from app.services.template import clone_service
from app.services.vm import (
    batch_provision_service,
    placement_service,
    placement_support,
)

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


def _pool(name: str, *, shared: bool, avail_gb: float = 500.0) -> WorkingStoragePool:
    return WorkingStoragePool(
        storage=name,
        total_gb=1000.0,
        avail_gb=avail_gb,
        active=True,
        enabled=True,
        can_vm=True,
        can_lxc=True,
        is_shared=shared,
        speed_tier="ssd",
        user_priority=5,
    )


def _node(name: str, *, memory_gb: int = 32) -> NodeCapacity:
    return NodeCapacity(
        node=name,
        status="online",
        guest_soft_limit=100,
        total_cpu_cores=32.0,
        allocatable_cpu_cores=16.0,
        total_memory_bytes=64 * GIB,
        allocatable_memory_bytes=memory_gb * GIB,
        total_disk_bytes=2048 * GIB,
        allocatable_disk_bytes=1024 * GIB,
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def _clear_template_nodes_cache():
    with placement_support._template_nodes_cache_lock:
        placement_support._template_nodes_cache.clear()
    yield
    with placement_support._template_nodes_cache_lock:
        placement_support._template_nodes_cache.clear()


# ---------------------------------------------------------------- clone_to_node


@pytest.fixture
def pve(monkeypatch):
    calls: dict = {"clone_vm": [], "clone_lxc": [], "fallback": [], "cleanup": []}

    def _clone(kind):
        def run(node, template_vmid, **config):
            calls[kind].append({"node": node, "template": template_vmid, **config})
            if calls.get("fail"):
                raise RuntimeError(calls["fail"])

        return run

    monkeypatch.setattr(clone_service.proxmox_ops, "clone_vm", _clone("clone_vm"))
    monkeypatch.setattr(clone_service.proxmox_ops, "clone_lxc", _clone("clone_lxc"))
    monkeypatch.setattr(
        clone_service,
        "get_proxmox_settings_for_node",
        lambda _node: SimpleNamespace(pool_name="skylab"),
    )

    def fallback(**kwargs):
        calls["fallback"].append(kwargs)
        return "full"

    monkeypatch.setattr(clone_service, "clone_with_fallback", fallback)
    monkeypatch.setattr(
        provisioning_service,
        "cleanup_provisioned_resource",
        lambda vmid: calls["cleanup"].append(vmid),
    )
    return calls


def _clone_to(**overrides):
    kwargs = dict(
        template_node="pve1",
        template_vmid=9000,
        new_vmid=501,
        hostname="lab-1",
        resource_type="qemu",
        target_node="pve2",
        target_storage="ceph",
        fallback_storage="data-ssd-1",
    )
    kwargs.update(overrides)
    return clone_service.clone_to_node(**kwargs)


def test_clone_to_other_node_sends_target_and_shared_storage(pve):
    assert _clone_to() == ("full", "pve2")
    call = pve["clone_vm"][0]
    assert call["node"] == "pve1"  # PVE 在來源節點執行 clone
    assert (call["target"], call["storage"], call["full"]) == ("pve2", "ceph", 1)
    assert call["name"] == "lab-1"
    assert pve["fallback"] == []


def test_lxc_clone_uses_hostname_key(pve):
    assert _clone_to(resource_type="lxc") == ("full", "pve2")
    assert pve["clone_lxc"][0]["hostname"] == "lab-1"


def test_failed_cross_node_clone_falls_back_to_template_node(pve):
    pve["fail"] = "storage 'ceph' is not available on node 'pve2'"
    assert _clone_to() == ("full", "pve1")
    # 半成品先清掉，再用同一個 VMID 在範本節點建，磁碟放範本節點的儲存區
    assert pve["cleanup"] == [501]
    assert pve["fallback"][0]["node"] == "pve1"
    assert pve["fallback"][0]["full_kwargs"] == {"storage": "data-ssd-1"}


def test_vmid_collision_is_not_retried(pve):
    pve["fail"] = "VM 501 already exists on node 'pve2'"
    with pytest.raises(RuntimeError):
        _clone_to()
    assert pve["cleanup"] == [] and pve["fallback"] == []


def test_without_shared_storage_clone_stays_on_template_node(pve):
    assert _clone_to(target_storage=None) == ("full", "pve1")
    assert pve["clone_vm"] == []
    assert pve["fallback"][0]["node"] == "pve1"


def test_same_node_without_fallback_storage_keeps_template_storage(pve):
    _clone_to(target_node="pve1", fallback_storage=None)
    assert pve["fallback"][0]["full_kwargs"] is None


# ------------------------------------------------ select_best_storage_name


def _storage_state(monkeypatch, pools, has_managed=True):
    monkeypatch.setattr(
        placement_service,
        "_build_storage_pool_state",
        lambda **kw: (dict.fromkeys(kw["node_names"], pools), has_managed),
    )
    monkeypatch.setattr(placement_service, "get_overcommit_ratios", lambda _s: (1.0, 1.0))
    monkeypatch.setattr(placement_service, "get_placement_tuning", lambda **kw: TUNING)


def test_shared_only_skips_local_storage(monkeypatch):
    _storage_state(
        monkeypatch, [_pool("data-ssd-1", shared=False, avail_gb=900), _pool("ceph", shared=True)]
    )
    pick = lambda **kw: placement_service.select_best_storage_name(  # noqa: E731
        session=None, node_name="pve2", resource_type="vm", disk_gb=20, **kw
    )
    assert pick(shared_only=True) == "ceph"


def test_shared_only_without_managed_storage_returns_none(monkeypatch):
    _storage_state(monkeypatch, [], has_managed=False)
    assert (
        placement_service.select_best_storage_name(
            session=None,
            node_name="pve2",
            resource_type="vm",
            disk_gb=20,
            fallback_storage="local-lvm",
            shared_only=True,
        )
        is None
    )


# ------------------------------------------------ placement（個人申請／快速練習）


def test_lxc_template_clone_may_use_the_whole_cluster(monkeypatch):
    monkeypatch.setattr(
        placement_support.proxmox_service,
        "find_resource",
        lambda vmid: {"vmid": vmid, "node": "pve1", "name": "n8n"},
    )
    monkeypatch.setattr(placement_support, "get_connection_id_for_node", lambda node: 1)
    monkeypatch.setattr(
        placement_support, "get_nodes_for_connection", lambda cid: {"pve1", "pve2"}
    )
    request = PlacementRequest(resource_type="lxc", template_vmid=481)
    assert placement_support.allowed_template_nodes_for_request(request) == {"pve1", "pve2"}
    assert placement_support.template_source_node(request) == "pve1"


def _build_plan(monkeypatch, *, pools_by_node, nodes):
    monkeypatch.setattr(
        placement_support.proxmox_service,
        "find_resource",
        lambda vmid: {"vmid": vmid, "node": "pve1", "name": "n8n"},
    )
    monkeypatch.setattr(placement_support, "get_connection_id_for_node", lambda node: 1)
    monkeypatch.setattr(
        placement_support, "get_nodes_for_connection", lambda cid: {"pve1", "pve2"}
    )
    request = PlacementRequest(
        resource_type="lxc", template_vmid=481, cpu_cores=1, memory_mb=512, disk_gb=8
    )
    return placement_support.build_plan(
        session=None,
        request=request,
        node_capacities=nodes,
        effective_resource_type="lxc",
        resource_type_reason="test",
        placement_strategy="priority_dominant_share",
        node_priorities={},
        build_storage_pool_state_fn=lambda **kw: (pools_by_node, True),
        get_placement_tuning_fn=lambda **kw: TUNING,
        get_overcommit_ratios_fn=lambda session: (1.0, 1.0),
        get_node_priorities_fn=lambda session: {},
        placement_sort_key_fn=placement_support.placement_sort_key,
    )


def test_other_node_with_only_local_storage_is_not_chosen(monkeypatch):
    # pve2 比較空，但只有本機儲存，範本 clone 不過去
    plan = _build_plan(
        monkeypatch,
        pools_by_node={
            "pve1": [_pool("data-ssd-1", shared=False)],
            "pve2": [_pool("data-ssd-2", shared=False)],
        },
        nodes=[_node("pve1", memory_gb=4), _node("pve2", memory_gb=60)],
    )
    assert plan.recommended_node == "pve1"


def test_other_node_with_shared_storage_can_be_chosen(monkeypatch):
    ceph = _pool("ceph", shared=True)
    plan = _build_plan(
        monkeypatch,
        pools_by_node={"pve1": [ceph], "pve2": [ceph]},
        nodes=[_node("pve1", memory_gb=4), _node("pve2", memory_gb=60)],
    )
    assert plan.recommended_node == "pve2"


# ------------------------------------------------ plan / execute（個人申請路徑）


def test_cross_node_storage_asks_pve_when_storage_is_not_synced(monkeypatch):
    monkeypatch.setattr(
        provisioning_service.placement_support,
        "build_storage_pool_state",
        lambda **kw: ({}, False),
    )
    monkeypatch.setattr(
        provisioning_service.proxmox_service,
        "list_node_storages",
        lambda node: [
            {"storage": "local-lvm", "shared": 0, "content": "images,rootdir"},
            {"storage": "ceph", "shared": 1, "content": "images,rootdir"},
        ],
    )
    assert (
        provisioning_service._cross_node_template_storage(
            session=None,
            template_node="pve1",
            target_node="pve2",
            resource_type="lxc",
            requested_storage="local-lvm",
            disk_gb=8,
            required_content="rootdir",
        )
        == "ceph"
    )


def test_cross_node_storage_is_none_on_the_template_node():
    assert (
        provisioning_service._cross_node_template_storage(
            session=None,
            template_node="pve1",
            target_node="pve1",
            resource_type="vm",
            requested_storage=None,
            disk_gb=20,
            required_content="images",
        )
        is None
    )


# ------------------------------------------------ 班級：容量預檢與批次建機


def _template(session, *, node="pve1", resource_type="lxc") -> VMTemplate:
    template = VMTemplate(
        name="n8n",
        resource_type=resource_type,
        node=node,
        pve_vmid=481,
        status=VMTemplateStatus.ready,
        owner_id=uuid.uuid4(),
    )
    session.add(template)
    session.flush()
    return template


def _class_machine(template: VMTemplate) -> TeachingClassMachineNode:
    return TeachingClassMachineNode(
        id=uuid.uuid4(),
        class_id=uuid.uuid4(),
        node_key="lab",
        source_type="template",
        source_template_id=template.id,
        name="lab",
        role="target",
        resource_type="lxc",
        cpu=1,
        memory_mb=1024,
        disk_gb=10,
        network="lab-net",
    )


def _class_storage(monkeypatch, pools_by_node):
    monkeypatch.setattr(
        class_capacity_service, "get_connection_id_for_node", lambda node: 1
    )
    monkeypatch.setattr(
        class_capacity_service,
        "get_nodes_for_connection",
        lambda cid: {"pve1", "pve2", "pve3"},
    )
    monkeypatch.setattr(
        class_capacity_service.placement_support,
        "build_storage_pool_state",
        lambda **kw: (
            {name: pools_by_node.get(name, []) for name in kw["node_names"]},
            True,
        ),
    )
    monkeypatch.setattr(
        class_capacity_service.placement_service,
        "get_overcommit_ratios",
        lambda _s: (1.0, 1.0),
    )
    monkeypatch.setattr(
        class_capacity_service.placement_service,
        "get_placement_tuning",
        lambda **kw: TUNING,
    )


def test_class_template_machine_may_use_nodes_with_shared_storage(session, monkeypatch):
    template = _template(session)
    ceph = _pool("ceph", shared=True)
    _class_storage(
        monkeypatch,
        {"pve2": [ceph], "pve3": [_pool("data-ssd-3", shared=False)]},
    )
    nodes = class_capacity_service.eligible_nodes_for_machine(
        session, machine_node=_class_machine(template)
    )
    # pve3 只有本機儲存，範本 clone 不過去
    assert nodes == {"pve1", "pve2"}


def test_class_template_stays_on_template_node_without_storage_records(
    session, monkeypatch
):
    template = _template(session)
    monkeypatch.setattr(
        class_capacity_service, "get_connection_id_for_node", lambda node: 1
    )
    monkeypatch.setattr(
        class_capacity_service, "get_nodes_for_connection", lambda cid: {"pve1", "pve2"}
    )
    monkeypatch.setattr(
        class_capacity_service.placement_support,
        "build_storage_pool_state",
        lambda **kw: ({}, False),
    )
    assert class_capacity_service.eligible_nodes_for_machine(
        session, machine_node=_class_machine(template)
    ) == {"pve1"}


def test_storage_check_requires_shared_storage_off_the_template_node(
    session, monkeypatch
):
    template = _template(session)
    machine = _class_machine(template)
    # pve2 本機儲存很空，但範本 clone 到 pve2 只能放共享儲存，而 ceph 滿了
    _class_storage(
        monkeypatch,
        {
            "pve2": [
                _pool("data-ssd-2", shared=False, avail_gb=900),
                _pool("ceph", shared=True, avail_gb=1),
            ]
        },
    )
    issues = class_capacity_service._storage_pool_issues(
        session,
        nodes=[machine],
        placements={machine.id: {uuid.uuid4(): "pve2"}},
    )
    assert len(issues) == 1 and "pve2" in issues[0]


def test_batch_clone_placement_picks_shared_storage_for_other_nodes(
    session, monkeypatch
):
    template = _template(session)
    picked: list[dict] = []

    def select(**kw):
        picked.append(kw)
        return "ceph" if kw.get("shared_only") else "data-ssd-1"

    monkeypatch.setattr(placement_service, "select_best_storage_name", select)
    placement = batch_provision_service._template_clone_placement(
        session, template=template, target_node="pve2", disk_gb=10
    )
    assert placement == {
        "target_node": "pve2",
        "target_storage": "ceph",
        "fallback_storage": "data-ssd-1",
    }
    assert [kw["node_name"] for kw in picked] == ["pve2", "pve1"]


def test_batch_clone_placement_on_the_template_node_only_picks_its_storage(
    session, monkeypatch
):
    template = _template(session)
    monkeypatch.setattr(
        placement_service, "select_best_storage_name", lambda **kw: "data-ssd-1"
    )
    placement = batch_provision_service._template_clone_placement(
        session, template=template, target_node="pve1", disk_gb=10
    )
    assert placement["target_storage"] is None
    assert placement["fallback_storage"] == "data-ssd-1"


# ------------------------------------------------ 自訂 VM（直接用 PVE 範本）


@pytest.fixture
def vm_clone(monkeypatch):
    calls: dict = {"clone": [], "cleanup": []}

    def clone(node, template_id, **config):
        calls["clone"].append({"node": node, "template": template_id, **config})
        if calls.get("fail") and "target" in config:
            raise RuntimeError(calls["fail"])
        return "UPID:1"

    monkeypatch.setattr(provisioning_service.proxmox_service, "clone_vm", clone)
    monkeypatch.setattr(
        provisioning_service,
        "cleanup_provisioned_resource",
        lambda vmid: calls["cleanup"].append(vmid),
    )
    return calls


def _clone_vm(**overrides):
    kwargs = dict(
        template_node="pve1",
        template_id=9000,
        new_vmid=601,
        clone_config={"newid": 601, "name": "lab-1", "full": 1, "storage": "data-ssd-1", "pool": "skylab"},
        cross_node="pve2",
        cross_node_storage="ceph",
    )
    kwargs.update(overrides)
    return provisioning_service._clone_vm_to_node(**kwargs)


def test_custom_vm_clones_to_the_reserved_node(vm_clone):
    assert _clone_vm() == ("UPID:1", "pve2")
    call = vm_clone["clone"][0]
    assert (call["node"], call["target"], call["storage"], call["full"]) == (
        "pve1",
        "pve2",
        "ceph",
        1,
    )


def test_custom_vm_falls_back_to_template_node(vm_clone):
    vm_clone["fail"] = "can't clone VM to node 'pve2' (VM uses local storage)"
    assert _clone_vm() == ("UPID:1", "pve1")
    assert vm_clone["cleanup"] == [601]
    fallback = vm_clone["clone"][1]
    assert fallback["node"] == "pve1" and "target" not in fallback
    assert fallback["storage"] == "data-ssd-1"


def test_custom_vm_collision_is_not_retried(vm_clone):
    vm_clone["fail"] = "VM 601 already exists on node 'pve2'"
    with pytest.raises(RuntimeError):
        _clone_vm()
    assert len(vm_clone["clone"]) == 1 and vm_clone["cleanup"] == []


def test_custom_vm_without_target_stays_on_template_node(vm_clone):
    assert _clone_vm(cross_node=None) == ("UPID:1", "pve1")
    assert "target" not in vm_clone["clone"][0]


def test_class_custom_vm_may_use_nodes_with_shared_storage(session, monkeypatch):
    monkeypatch.setattr(
        class_capacity_service.provisioning_service,
        "get_vm_target_node",
        lambda template_id: "pve1",
    )
    _class_storage(monkeypatch, {"pve2": [_pool("ceph", shared=True)]})
    machine = TeachingClassMachineNode(
        id=uuid.uuid4(),
        class_id=uuid.uuid4(),
        node_key="win",
        source_type="custom",
        custom_image_ref="9000",
        name="win",
        role="target",
        resource_type="qemu",
        cpu=2,
        memory_mb=4096,
        disk_gb=40,
        network="lab-net",
    )
    assert class_capacity_service.eligible_nodes_for_machine(
        session, machine_node=machine
    ) == {"pve1", "pve2"}
