"""課堂機器必須放在同一個叢集。

同一堂課的機器要能彼此連通：IP 由全域單例網段配發、bridge 名稱全域共用、
firewall 規則逐台下在各自節點上。跨叢集時 L2 不通、同名 bridge 指向不同的
實體網路，拓樸形同虛設。

自訂 LXC 原本走 provisioning_service.get_lxc_target_node()（各連線
default_node → nodes[0]），可以挑到與範本機器不同的叢集 —— 這裡固定住
「整班同叢集」的行為。
"""

import json
import uuid
from collections import Counter

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.domain.placement.schemas import NodeCapacity
from app.models import (
    ClassCapacityReservation,
    TeachingClassMachineNode,
    VMTemplate,
    VMTemplateStatus,
)
from app.services.teaching import class_capacity_service

GIB = 1024**3

# 兩個叢集：clusterA = 連線 1，clusterB = 連線 2
_NODE_CONNECTIONS = {
    "a1": 1,
    "a2": 1,
    "b1": 2,
    "b2": 2,
}


@pytest.fixture(name="session")
def _session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def _stub_topology(monkeypatch):
    monkeypatch.setattr(
        class_capacity_service,
        "get_connection_id_for_node",
        lambda name: _NODE_CONNECTIONS.get(name),
    )


def _template(session, *, node: str, resource_type: str) -> VMTemplate:
    template = VMTemplate(
        pve_vmid=int(uuid.uuid4().int % 100000),
        name=f"tpl-{node}-{resource_type}",
        node=node,
        storage="local-lvm",
        resource_type=resource_type,
        status=VMTemplateStatus.ready,
    )
    session.add(template)
    session.flush()
    return template


def _machine(
    session,
    *,
    class_id: uuid.UUID,
    name: str,
    source_type: str = "template",
    source_template_id: uuid.UUID | None = None,
    custom_image_ref: str | None = None,
    resource_type: str = "lxc",
) -> TeachingClassMachineNode:
    machine = TeachingClassMachineNode(
        class_id=class_id,
        node_key=name,
        source_type=source_type,
        source_template_id=source_template_id,
        custom_image_ref=custom_image_ref,
        name=name,
        role="target",
        resource_type=resource_type,
        cpu=2,
        memory_mb=2048,
        disk_gb=10,
    )
    session.add(machine)
    session.flush()
    return machine


# ---------------------------------------------------------------------------
# 單台機器的可建節點
# ---------------------------------------------------------------------------

class TestEligibleNodes:
    def test_lxc_template_is_pinned_to_its_own_node(self, session):
        """LXC linked clone 必須與範本同節點同 storage，沒有第二個選擇。"""
        template = _template(session, node="a1", resource_type="lxc")
        machine = _machine(
            session,
            class_id=uuid.uuid4(),
            name="lab",
            source_template_id=template.id,
        )
        assert class_capacity_service.eligible_nodes_for_machine(
            session, machine_node=machine
        ) == {"a1"}

    def test_vm_template_is_pinned_to_the_template_node(self, session):
        """批次建機的 create_vm 與 clone_service 一律在範本節點 clone，不接受
        指定節點 —— eligibility 必須反映這件事，否則容量計畫會規劃出建機不會
        遵守的落點。"""
        template = _template(session, node="a1", resource_type="qemu")
        machine = _machine(
            session,
            class_id=uuid.uuid4(),
            name="lab",
            source_template_id=template.id,
            resource_type="vm",
        )
        assert class_capacity_service.eligible_nodes_for_machine(
            session, machine_node=machine
        ) == {"a1"}

    def test_custom_lxc_is_limited_to_nodes_that_see_the_image(
        self, session, monkeypatch
    ):
        monkeypatch.setattr(
            class_capacity_service.proxmox_service,
            "get_lxc_template_node_map",
            lambda: {"local:vztmpl/deb.tar.zst": {"a2", "b1"}},
        )
        machine = _machine(
            session,
            class_id=uuid.uuid4(),
            name="lab",
            source_type="custom",
            custom_image_ref="local:vztmpl/deb.tar.zst",
        )
        assert class_capacity_service.eligible_nodes_for_machine(
            session, machine_node=machine
        ) == {"a2", "b1"}

    def test_missing_template_is_reported(self, session):
        machine = _machine(
            session,
            class_id=uuid.uuid4(),
            name="lab",
            source_template_id=uuid.uuid4(),
        )
        with pytest.raises(LookupError):
            class_capacity_service.eligible_nodes_for_machine(
                session, machine_node=machine
            )


# ---------------------------------------------------------------------------
# 整堂課的叢集約束
# ---------------------------------------------------------------------------

class TestClassStaysInOneCluster:
    def test_machines_in_one_cluster_resolve_cleanly(self, session):
        class_id = uuid.uuid4()
        t1 = _template(session, node="a1", resource_type="lxc")
        t2 = _template(session, node="a2", resource_type="lxc")
        m1 = _machine(session, class_id=class_id, name="attacker", source_template_id=t1.id)
        m2 = _machine(session, class_id=class_id, name="target", source_template_id=t2.id)

        targets, issues = class_capacity_service.resolve_class_targets(
            session, nodes=[m1, m2]
        )
        assert issues == []
        assert targets == {m1.id: "a1", m2.id: "a2"}

    def test_machines_in_different_clusters_are_refused(self, session):
        """兩台範本分屬不同叢集 —— 這堂課建不出可連通的環境，必須擋下。"""
        class_id = uuid.uuid4()
        t1 = _template(session, node="a1", resource_type="lxc")
        t2 = _template(session, node="b1", resource_type="lxc")
        m1 = _machine(session, class_id=class_id, name="attacker", source_template_id=t1.id)
        m2 = _machine(session, class_id=class_id, name="target", source_template_id=t2.id)

        targets, issues = class_capacity_service.resolve_class_targets(
            session, nodes=[m1, m2]
        )
        assert targets == {}
        assert len(issues) == 1
        # 訊息要指出是哪些機器、各自能落在哪，才有辦法排除
        assert "attacker" in issues[0] and "target" in issues[0]

    def test_custom_lxc_follows_the_class_cluster(self, session, monkeypatch):
        """關鍵案例：自訂 LXC 在兩個叢集都看得到映像檔時，必須跟著班級走。"""
        monkeypatch.setattr(
            class_capacity_service.proxmox_service,
            "get_lxc_template_node_map",
            lambda: {"local:vztmpl/deb.tar.zst": {"a2", "b1", "b2"}},
        )
        # 預設節點在另一個叢集，舊行為會選到它
        monkeypatch.setattr(
            class_capacity_service.provisioning_service,
            "get_lxc_target_node",
            lambda: "b1",
        )
        class_id = uuid.uuid4()
        template = _template(session, node="a1", resource_type="lxc")
        pinned = _machine(
            session, class_id=class_id, name="lab", source_template_id=template.id
        )
        custom = _machine(
            session,
            class_id=class_id,
            name="extra",
            source_type="custom",
            custom_image_ref="local:vztmpl/deb.tar.zst",
        )

        targets, issues = class_capacity_service.resolve_class_targets(
            session, nodes=[pinned, custom]
        )
        assert issues == []
        assert targets[pinned.id] == "a1"
        # b1 雖是預設節點且看得到映像檔，但不在班級所屬叢集內
        assert targets[custom.id] == "a2"

    def test_default_node_is_kept_when_it_is_valid(self, session, monkeypatch):
        """對照組：預設節點合格時不改變既有選擇。"""
        monkeypatch.setattr(
            class_capacity_service.proxmox_service,
            "get_lxc_template_node_map",
            lambda: {"local:vztmpl/deb.tar.zst": {"a1", "a2"}},
        )
        monkeypatch.setattr(
            class_capacity_service.provisioning_service,
            "get_lxc_target_node",
            lambda: "a2",
        )
        class_id = uuid.uuid4()
        template = _template(session, node="a1", resource_type="lxc")
        pinned = _machine(
            session, class_id=class_id, name="lab", source_template_id=template.id
        )
        custom = _machine(
            session,
            class_id=class_id,
            name="extra",
            source_type="custom",
            custom_image_ref="local:vztmpl/deb.tar.zst",
        )
        targets, _ = class_capacity_service.resolve_class_targets(
            session, nodes=[pinned, custom]
        )
        assert targets[custom.id] == "a2"

    def test_machine_with_no_node_in_the_cluster_is_refused(
        self, session, monkeypatch
    ):
        monkeypatch.setattr(
            class_capacity_service.proxmox_service,
            "get_lxc_template_node_map",
            lambda: {"local:vztmpl/deb.tar.zst": set()},
        )
        class_id = uuid.uuid4()
        template = _template(session, node="a1", resource_type="lxc")
        pinned = _machine(
            session, class_id=class_id, name="lab", source_template_id=template.id
        )
        custom = _machine(
            session,
            class_id=class_id,
            name="extra",
            source_type="custom",
            custom_image_ref="local:vztmpl/deb.tar.zst",
        )
        targets, issues = class_capacity_service.resolve_class_targets(
            session, nodes=[pinned, custom]
        )
        assert targets == {}
        assert issues

    def test_empty_class_is_not_an_error(self, session):
        assert class_capacity_service.resolve_class_targets(session, nodes=[]) == ({}, [])


# ---------------------------------------------------------------------------
# 建機時取得的節點必須與預留時一致
# ---------------------------------------------------------------------------

class TestProvisioningUsesTheSamePlan:
    def test_target_node_matches_the_reservation_plan(self, session, monkeypatch):
        monkeypatch.setattr(
            class_capacity_service.proxmox_service,
            "get_lxc_template_node_map",
            lambda: {"local:vztmpl/deb.tar.zst": {"a2", "b1"}},
        )
        monkeypatch.setattr(
            class_capacity_service.provisioning_service,
            "get_lxc_target_node",
            lambda: "b1",
        )
        class_id = uuid.uuid4()
        template = _template(session, node="a1", resource_type="lxc")
        pinned = _machine(
            session, class_id=class_id, name="lab", source_template_id=template.id
        )
        custom = _machine(
            session,
            class_id=class_id,
            name="extra",
            source_type="custom",
            custom_image_ref="local:vztmpl/deb.tar.zst",
        )
        plan, _ = class_capacity_service.resolve_class_targets(
            session, nodes=[pinned, custom]
        )
        # 建機端只拿到單一台機器，仍須解出與整班計畫相同的節點
        assert (
            class_capacity_service.target_node_for_machine(
                session, machine_node=custom
            )
            == plan[custom.id]
            == "a2"
        )

    def test_unresolvable_class_returns_none(self, session):
        class_id = uuid.uuid4()
        t1 = _template(session, node="a1", resource_type="lxc")
        t2 = _template(session, node="b1", resource_type="lxc")
        _machine(session, class_id=class_id, name="attacker", source_template_id=t1.id)
        m2 = _machine(session, class_id=class_id, name="target", source_template_id=t2.id)
        # 跨叢集無解時回 None，讓建機沿用既有預設行為而不是中斷
        assert (
            class_capacity_service.target_node_for_machine(session, machine_node=m2)
            is None
        )



# ---------------------------------------------------------------------------
# 整班同叢集，叢集內分散到不同 server
# ---------------------------------------------------------------------------

def _capacity(name: str, *, cores: float, memory_gb: int, disk_gb: int):
    return NodeCapacity(
        node=name,
        status="online",
        total_cpu_cores=cores,
        allocatable_cpu_cores=cores,
        total_memory_bytes=memory_gb * GIB,
        allocatable_memory_bytes=memory_gb * GIB,
        total_disk_bytes=disk_gb * GIB,
        allocatable_disk_bytes=disk_gb * GIB,
        guest_soft_limit=1000,
    )


class TestClassPicksOneCluster:
    """一堂課的所有學生固定在同一個叢集，不會被拆開。"""

    @pytest.fixture(autouse=True)
    def _spanning_image(self, monkeypatch):
        # 同一個 vztmpl 在兩個叢集都看得到 —— 有得選才驗得出「只挑一個」
        monkeypatch.setattr(
            class_capacity_service.proxmox_service,
            "get_lxc_template_node_map",
            lambda: {"local:vztmpl/deb.tar.zst": {"a1", "a2", "b1"}},
        )
        monkeypatch.setattr(
            class_capacity_service.provisioning_service,
            "get_lxc_target_node",
            lambda: "a1",
        )

    def _setup(self, session, *, cpu=2, memory_mb=4096, disk_gb=20):
        machine = _machine(
            session,
            class_id=uuid.uuid4(),
            name="lab",
            source_type="custom",
            custom_image_ref="local:vztmpl/deb.tar.zst",
        )
        machine.cpu = cpu
        machine.memory_mb = memory_mb
        machine.disk_gb = disk_gb
        session.flush()
        eligibility, clusters, issues = class_capacity_service.class_eligibility(
            session, nodes=[machine]
        )
        assert issues == []
        return machine, eligibility, clusters

    def test_prefers_the_cluster_that_fits_the_whole_class(self, session):
        machine, eligibility, clusters = self._setup(session)
        capacities = {
            # A 叢集（a1+a2）合計只夠 20 位
            "a1": _capacity("a1", cores=20, memory_gb=40, disk_gb=200),
            "a2": _capacity("a2", cores=20, memory_gb=40, disk_gb=200),
            # B 叢集一台就夠全班
            "b1": _capacity("b1", cores=200, memory_gb=400, disk_gb=2000),
        }
        chosen = class_capacity_service.choose_class_cluster(
            nodes=[machine],
            student_count=35,
            clusters=clusters,
            eligibility=eligibility,
            capacities=capacities,
        )
        assert chosen == 2

    def test_small_class_can_use_the_smaller_cluster(self, session):
        """放得下就好，不是一味挑最大的 —— 這裡兩邊都放得下，取容量較大者。"""
        machine, eligibility, clusters = self._setup(session)
        capacities = {
            "a1": _capacity("a1", cores=200, memory_gb=400, disk_gb=2000),
            "a2": _capacity("a2", cores=200, memory_gb=400, disk_gb=2000),
            "b1": _capacity("b1", cores=100, memory_gb=200, disk_gb=1000),
        }
        chosen = class_capacity_service.choose_class_cluster(
            nodes=[machine],
            student_count=5,
            clusters=clusters,
            eligibility=eligibility,
            capacities=capacities,
        )
        assert chosen == 1

    def test_never_splits_the_class_across_clusters(self, session):
        """即使沒有任何叢集放得下，也只挑一個 —— 不足由容量檢查回報。"""
        machine, eligibility, clusters = self._setup(session)
        capacities = {
            "a1": _capacity("a1", cores=4, memory_gb=8, disk_gb=40),
            "a2": _capacity("a2", cores=4, memory_gb=8, disk_gb=40),
            "b1": _capacity("b1", cores=6, memory_gb=12, disk_gb=60),
        }
        chosen = class_capacity_service.choose_class_cluster(
            nodes=[machine],
            student_count=50,
            clusters=clusters,
            eligibility=eligibility,
            capacities=capacities,
        )
        placements = class_capacity_service.allocate_class_placements(
            nodes=[machine],
            student_ids=[uuid.uuid4() for _ in range(50)],
            connection_id=chosen,
            eligibility=eligibility,
            capacities=capacities,
        )
        used_nodes = set(placements[machine.id].values())
        used_clusters = {_NODE_CONNECTIONS[n] for n in used_nodes}
        assert used_clusters == {chosen}


class TestSpreadAcrossServersInsideTheCluster:
    """同一個叢集不代表同一台 server —— 叢集內要依容量攤開。"""

    @pytest.fixture(autouse=True)
    def _two_node_cluster(self, monkeypatch):
        monkeypatch.setattr(
            class_capacity_service.proxmox_service,
            "get_lxc_template_node_map",
            lambda: {"local:vztmpl/deb.tar.zst": {"a1", "a2"}},
        )
        monkeypatch.setattr(
            class_capacity_service.provisioning_service,
            "get_lxc_target_node",
            lambda: "a1",
        )

    def _custom_machine(self, session, class_id):
        machine = _machine(
            session,
            class_id=class_id,
            name="lab",
            source_type="custom",
            custom_image_ref="local:vztmpl/deb.tar.zst",
        )
        machine.cpu = 2
        machine.memory_mb = 4096
        machine.disk_gb = 20
        session.flush()
        return machine

    def test_students_are_spread_over_both_servers(self, session):
        machine = self._custom_machine(session, uuid.uuid4())
        eligibility, _clusters, _ = class_capacity_service.class_eligibility(
            session, nodes=[machine]
        )
        students = [uuid.uuid4() for _ in range(30)]
        capacities = {
            "a1": _capacity("a1", cores=100, memory_gb=200, disk_gb=1000),
            "a2": _capacity("a2", cores=100, memory_gb=200, disk_gb=1000),
        }
        placements = class_capacity_service.allocate_class_placements(
            nodes=[machine],
            student_ids=students,
            connection_id=1,
            eligibility=eligibility,
            capacities=capacities,
        )
        counts = Counter(placements[machine.id].values())
        # 兩台容量相同 → 平均攤開，不會全擠在預設節點 a1
        assert set(counts) == {"a1", "a2"}
        assert counts["a1"] == counts["a2"] == 15

    def test_bigger_server_takes_more(self, session):
        machine = self._custom_machine(session, uuid.uuid4())
        eligibility, _clusters, _ = class_capacity_service.class_eligibility(
            session, nodes=[machine]
        )
        capacities = {
            "a1": _capacity("a1", cores=300, memory_gb=600, disk_gb=3000),
            "a2": _capacity("a2", cores=100, memory_gb=200, disk_gb=1000),
        }
        placements = class_capacity_service.allocate_class_placements(
            nodes=[machine],
            student_ids=[uuid.uuid4() for _ in range(40)],
            connection_id=1,
            eligibility=eligibility,
            capacities=capacities,
        )
        counts = Counter(placements[machine.id].values())
        assert counts["a1"] > counts["a2"]

    def test_pinned_machine_cannot_spread(self, session):
        """LXC 範本克隆只能待在範本節點，全班同型機器都在那一台。"""
        class_id = uuid.uuid4()
        template = _template(session, node="a1", resource_type="lxc")
        machine = _machine(
            session, class_id=class_id, name="lab", source_template_id=template.id
        )
        eligibility, _clusters, _ = class_capacity_service.class_eligibility(
            session, nodes=[machine]
        )
        placements = class_capacity_service.allocate_class_placements(
            nodes=[machine],
            student_ids=[uuid.uuid4() for _ in range(30)],
            connection_id=1,
            eligibility=eligibility,
            capacities={
                "a1": _capacity("a1", cores=100, memory_gb=200, disk_gb=1000),
                "a2": _capacity("a2", cores=100, memory_gb=200, disk_gb=1000),
            },
        )
        assert set(placements[machine.id].values()) == {"a1"}

    def test_every_student_gets_exactly_one_node_per_machine(self, session):
        machine = self._custom_machine(session, uuid.uuid4())
        eligibility, _clusters, _ = class_capacity_service.class_eligibility(
            session, nodes=[machine]
        )
        students = [uuid.uuid4() for _ in range(12)]
        placements = class_capacity_service.allocate_class_placements(
            nodes=[machine],
            student_ids=students,
            connection_id=1,
            eligibility=eligibility,
            capacities={
                "a1": _capacity("a1", cores=100, memory_gb=200, disk_gb=1000),
                "a2": _capacity("a2", cores=100, memory_gb=200, disk_gb=1000),
            },
        )
        assert set(placements[machine.id]) == set(students)
        assert all(node in {"a1", "a2"} for node in placements[machine.id].values())


class TestProvisioningFollowsStoredPlacement:
    def test_stored_placement_wins_over_recomputation(self, session, monkeypatch):
        """建機端必須照預留時存下的落點走，否則學生會被搬到別台 server。"""
        monkeypatch.setattr(
            class_capacity_service.proxmox_service,
            "get_lxc_template_node_map",
            lambda: {"local:vztmpl/deb.tar.zst": {"a1", "a2"}},
        )
        monkeypatch.setattr(
            class_capacity_service.provisioning_service,
            "get_lxc_target_node",
            lambda: "a1",
        )
        class_id = uuid.uuid4()
        machine = _machine(
            session,
            class_id=class_id,
            name="lab",
            source_type="custom",
            custom_image_ref="local:vztmpl/deb.tar.zst",
        )
        alice, bob = uuid.uuid4(), uuid.uuid4()
        session.add(
            ClassCapacityReservation(
                class_id=class_id,
                course_version_id=uuid.uuid4(),
                student_count=2,
                machine_count=2,
                cpu_cores=4,
                memory_mb=8192,
                disk_gb=40,
                ip_count=2,
                network_count=1,
                placement_plan="{}",
                student_placements=json.dumps(
                    {str(machine.id): {str(alice): "a1", str(bob): "a2"}}
                ),
            )
        )
        session.flush()

        assert (
            class_capacity_service.target_node_for_machine(
                session, machine_node=machine, user_id=alice
            )
            == "a1"
        )
        # 預設節點是 a1，但 Bob 的落點是 a2 —— 存下的分配必須勝過重算
        assert (
            class_capacity_service.target_node_for_machine(
                session, machine_node=machine, user_id=bob
            )
            == "a2"
        )

    def test_falls_back_when_there_is_no_reservation(self, session, monkeypatch):
        monkeypatch.setattr(
            class_capacity_service.proxmox_service,
            "get_lxc_template_node_map",
            lambda: {"local:vztmpl/deb.tar.zst": {"a1", "a2"}},
        )
        monkeypatch.setattr(
            class_capacity_service.provisioning_service,
            "get_lxc_target_node",
            lambda: "a1",
        )
        machine = _machine(
            session,
            class_id=uuid.uuid4(),
            name="lab",
            source_type="custom",
            custom_image_ref="local:vztmpl/deb.tar.zst",
        )
        # 沒有預留紀錄 → 沿用單一節點行為，不讓建機中斷
        assert (
            class_capacity_service.target_node_for_machine(
                session, machine_node=machine, user_id=uuid.uuid4()
            )
            == "a1"
        )
