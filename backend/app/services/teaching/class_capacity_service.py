"""Whole-class capacity calculation and hard IP reservation."""

import json
import logging
import uuid
from collections import defaultdict
from typing import Any

from sqlmodel import Session, select

from app.core.i18n import t
from app.domain.placement import storage as placement_storage
from app.domain.placement.schemas import (
    NodeCapacity,
    ResourceType,
)
from app.exceptions import BadRequestError
from app.infrastructure.proxmox import (
    get_connection_id_for_node,
    get_nodes_for_connection,
)
from app.models import (
    ClassCapacityReservation,
    TeachingClassMachineNode,
    TeachingClassStudent,
    TeachingClassStudentMachine,
    VMTemplate,
)
from app.services.network import ip_management_service
from app.services.proxmox import provisioning_service, proxmox_service
from app.services.teaching.class_network_service import network_segments
from app.services.vm import placement_service, placement_support

GIB = 1024**3
MIB = 1024**2
# 存進預留快照的逐節點需求欄位；預覽另外附上的可用量不存
_PLAN_DEMAND_KEYS = ("cpu_cores", "memory_bytes", "disk_bytes", "machines")
logger = logging.getLogger(__name__)


def _gib(value: float) -> str:
    """位元組換成一位小數的 GB：整數除法會把需要 7.5、可用 7.9 都顯示成 7。"""
    return f"{value / GIB:.1f}"


def _json_dict(value: object) -> dict:
    """預留的 JSON 欄位；相容遷移前以字串存放的舊值。"""
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(value or "{}")  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def calculate(
    *,
    nodes: list[TeachingClassMachineNode],
    students: list[TeachingClassStudent],
) -> dict[str, int]:
    student_count = len(students)
    per_student_networks = {
        name for node in nodes for name in network_segments(node.network)
    }
    return {
        "student_count": student_count,
        "machines_per_student": len(nodes),
        "machine_count": student_count * len(nodes),
        "cpu_cores": student_count * sum(node.cpu for node in nodes),
        "memory_mb": student_count * sum(node.memory_mb for node in nodes),
        "disk_gb": student_count * sum(node.disk_gb for node in nodes),
        "ip_count": student_count * len(nodes),
        "network_count": student_count * max(1, len(per_student_networks)),
    }


def preview(
    session: Session,
    *,
    nodes: list[TeachingClassMachineNode],
    students: list[TeachingClassStudent],
    check_cluster: bool = False,
) -> dict[str, object]:
    totals = calculate(nodes=nodes, students=students)
    ip_stats = ip_management_service.get_ip_stats(session)
    issues = (
        []
        if ip_stats["available"] >= totals["ip_count"]
        else [
            t(
                "class_capacity.ip_insufficient",
                required=totals["ip_count"],
                available=ip_stats["available"],
            )
        ]
    )
    placement_plan: dict[str, dict[str, float]] = {}
    if check_cluster and nodes and students:
        placement_plan, _placements, cluster_issues = _evaluate_cluster_capacity(
            session,
            nodes=nodes,
            student_ids=[student.user_id for student in students],
        )
        issues.extend(cluster_issues)
    return {
        **totals,
        "available_ips": ip_stats["available"],
        "ready": bool(nodes) and bool(students) and not issues,
        "issues": issues,
        "cluster_checked": check_cluster,
        "placement_plan": placement_plan,
    }


def reserve(
    session: Session,
    *,
    class_id: uuid.UUID,
    course_version_id: uuid.UUID,
    nodes: list[TeachingClassMachineNode],
    students: list[TeachingClassStudent],
) -> ClassCapacityReservation:
    existing = session.exec(
        select(ClassCapacityReservation).where(
            ClassCapacityReservation.class_id == class_id
        )
    ).first()
    if existing:
        if existing.course_version_id != course_version_id:
            raise BadRequestError(t("class_capacity.version_mismatch"))
        if existing.status != "released":
            return existing
        session.delete(existing)
        session.flush()

    totals = calculate(nodes=nodes, students=students)
    if not nodes or not students:
        raise BadRequestError(t("class_capacity.missing_students_or_nodes"))
    placement_plan, student_placements = _check_cluster_capacity(
        session,
        nodes=nodes,
        student_ids=[student.user_id for student in students],
    )
    reservation_keys = [
        f"{class_id}:{node.node_key}:{student.user_id}"
        for node in nodes
        for student in students
    ]
    ip_management_service.reserve_ips(
        session,
        teaching_class_id=class_id,
        reservation_keys=reservation_keys,
    )
    reservation = ClassCapacityReservation(
        class_id=class_id,
        course_version_id=course_version_id,
        student_count=totals["student_count"],
        machine_count=totals["machine_count"],
        cpu_cores=totals["cpu_cores"],
        memory_mb=totals["memory_mb"],
        disk_gb=totals["disk_gb"],
        ip_count=totals["ip_count"],
        network_count=totals["network_count"],
        placement_plan={
            node_name: {
                key: values[key] for key in _PLAN_DEMAND_KEYS if key in values
            }
            for node_name, values in placement_plan.items()
        },
        student_placements={
            str(machine_node_id): {
                str(user_id): node for user_id, node in per_student.items()
            }
            for machine_node_id, per_student in student_placements.items()
        },
    )
    session.add(reservation)
    session.flush()
    return reservation


def release(
    session: Session,
    *,
    class_id: uuid.UUID,
    delete_snapshot: bool = True,
) -> int:
    """Release unused class IPs and its capacity snapshot."""
    released_ips = ip_management_service.release_class_reservations(
        session, class_id
    )
    reservation = session.exec(
        select(ClassCapacityReservation).where(
            ClassCapacityReservation.class_id == class_id
        )
    ).first()
    if reservation:
        if delete_snapshot:
            session.delete(reservation)
        else:
            reservation.status = "released"
            session.add(reservation)
    session.flush()
    return released_ips


def eligible_nodes_for_machine(
    session: Session,
    *,
    machine_node: TeachingClassMachineNode,
) -> set[str]:
    """這台課程機器實際上能被建立在哪些節點。

    刻意不重用 placement 的 allowed_template_nodes_for_request：那個函式對
    「LXC + 範本克隆」回傳不受限（節點由 provisioning 稍後以範本節點覆寫），
    容量計畫若照它算，就會出現「檢查說可行、建機卻落在別處」的落差。

    這裡只列出**建機端今天真的做得到**的節點，不是理論上可行的節點。多列一個
    就會讓容量計畫規劃出建機不會遵守的落點（檢查說可行、機器卻建在別處）。

    - 平台範本（VM 與 LXC）：範本節點，加上同叢集裡有共享儲存的節點。平台
      範本一律 full clone，PVE 允許 clone 到同叢集的其他節點，條件是目標
      儲存區是共享儲存（範本母機放在哪不影響）。批次建機的 clone_service
      照預留的落點與儲存區建，跨節點失敗才退回範本節點。
    - 自訂 VM（直接用 PVE 範本建）：與平台範本相同，範本節點加上同叢集有共享
      儲存的節點；create_vm 照預留落點跨節點 clone，失敗退回範本節點。
    - 自訂 LXC：只有 iso_storage 看得到該 vztmpl 的節點；create_lxc 接受指定
      節點。
    """
    if machine_node.source_type == "template":
        template = session.get(VMTemplate, machine_node.source_template_id)
        if template is None:
            raise LookupError("template not found")
        return {template.node} | _shared_clone_nodes(
            session,
            template_node=template.node,
            resource_type=_storage_resource_type(machine_node),
        )

    if machine_node.resource_type == "lxc":
        node_map = proxmox_service.get_lxc_template_node_map()
        # 整張映射為空多半是查詢失敗，沿用舊的單一節點行為而非誤判為全叢集可用
        if not node_map:
            return {provisioning_service.get_lxc_target_node()}
        return set(node_map.get(str(machine_node.custom_image_ref or ""), set()))

    template_node = provisioning_service.get_vm_target_node(
        int(machine_node.custom_image_ref or "0")
    )
    return {template_node} | _shared_clone_nodes(
        session, template_node=template_node, resource_type="vm"
    )


def _shared_clone_nodes(
    session: Session, *, template_node: str, resource_type: ResourceType
) -> set[str]:
    """同叢集裡、有可放這種機器的共享儲存的其他節點（範本可以跨節點 clone 過去）。

    儲存區資訊來自受管儲存的同步紀錄；沒有紀錄時無從判斷是否共享，回空集合
    （維持只在範本節點建）。
    """
    nodes = get_nodes_for_connection(get_connection_id_for_node(template_node))
    others = sorted(nodes - {template_node})
    if not others:
        return set()
    pools_by_node, has_managed_storage = placement_support.build_storage_pool_state(
        session=session, node_names=others
    )
    if not has_managed_storage:
        return set()
    return {
        name
        for name, pools in pools_by_node.items()
        if any(_shared_pool_usable(pool, resource_type) for pool in pools)
    }


def _shared_pool_usable(pool: object, resource_type: ResourceType) -> bool:
    return bool(
        getattr(pool, "is_shared", False)
        and getattr(pool, "active", False)
        and getattr(pool, "enabled", False)
        and getattr(pool, "can_lxc" if resource_type == "lxc" else "can_vm", False)
    )


def class_eligibility(
    session: Session,
    *,
    nodes: list[TeachingClassMachineNode],
) -> tuple[dict[uuid.UUID, set[str]], set[int | None], list[str]]:
    """整堂課的可建節點與可用叢集。

    回傳 (每台機器的可建節點, 全班共用的叢集集合, issues)。共用叢集是各機器
    「可落腳連線」的交集 —— 一位學生的機器必須彼此互通，所以只能落在所有
    機器類型都建得起來的叢集裡。
    """
    if not nodes:
        return {}, set(), []

    eligibility: dict[uuid.UUID, set[str]] = {}
    for machine_node in nodes:
        try:
            eligibility[machine_node.id] = eligible_nodes_for_machine(
                session, machine_node=machine_node
            )
        except LookupError:
            return {}, set(), [
                t("class_capacity.template_not_found", name=machine_node.name)
            ]
        except Exception:
            logger.exception(
                "Failed to resolve placement for class machine node_id=%s",
                machine_node.id,
            )
            return {}, set(), [
                t("class_capacity.node_resolution_failed", name=machine_node.name)
            ]

    options: dict[uuid.UUID, set[int | None]] = {
        node_id: {get_connection_id_for_node(name) for name in names}
        for node_id, names in eligibility.items()
    }
    shared: set[int | None] = set.intersection(*options.values())
    if not shared:
        detail = "；".join(
            f"{machine_node.name}: "
            f"{', '.join(sorted(eligibility[machine_node.id])) or '無可用節點'}"
            for machine_node in nodes
        )
        return {}, set(), [t("class_capacity.cross_cluster", detail=detail)]
    return eligibility, shared, []


def _preferred_node() -> str | None:
    """沿用各連線 default_node 的既有偏好；取不到時回 None。"""
    try:
        return provisioning_service.get_lxc_target_node()
    except Exception:
        return None


def targets_in_cluster(
    *,
    nodes: list[TeachingClassMachineNode],
    eligibility: dict[uuid.UUID, set[str]],
    connection_id: int | None,
    preferred: str | None = None,
) -> dict[uuid.UUID, str]:
    """指定叢集內每台課程機器的建機節點；有機器落不了時回空 dict。"""
    targets: dict[uuid.UUID, str] = {}
    for machine_node in nodes:
        in_cluster = sorted(
            name
            for name in eligibility.get(machine_node.id, set())
            if get_connection_id_for_node(name) == connection_id
        )
        if not in_cluster:
            return {}
        targets[machine_node.id] = (
            preferred if preferred in in_cluster else in_cluster[0]
        )
    return targets


def _student_footprint(
    nodes: list[TeachingClassMachineNode],
) -> tuple[float, int, int]:
    """一位學生整套環境的資源用量。"""
    return (
        float(sum(node.cpu for node in nodes)),
        sum(node.memory_mb * 1024**2 for node in nodes),
        sum(node.disk_gb * GIB for node in nodes),
    )


def _ratio(used: float, total: float) -> float:
    if total <= 0:
        return float("inf")
    return used / total


def _cluster_headroom(
    *,
    connection_id: int | None,
    eligibility: dict[uuid.UUID, set[str]],
    capacities: dict[str, object],
) -> dict[str, float]:
    """該叢集內、這堂課用得到的節點加總後的可用 CPU 與 RAM。

    磁碟不在這裡比：節點層的 disk 是 PVE 主機系統碟，不是機器實際放的儲存區，
    磁碟一律交給 ``_storage_pool_issues`` 依儲存區檢查。
    """
    rows = [
        capacities[name]
        for name in {
            name
            for names in eligibility.values()
            for name in names
            if get_connection_id_for_node(name) == connection_id
        }
        if capacities.get(name) is not None
        and getattr(capacities[name], "status", "") == "online"
    ]
    return {
        "cpu": sum(float(getattr(r, "allocatable_cpu_cores", 0.0)) for r in rows),
        "mem": sum(float(getattr(r, "allocatable_memory_bytes", 0)) for r in rows),
    }


def choose_class_cluster(
    *,
    nodes: list[TeachingClassMachineNode],
    student_count: int,
    clusters: set[int | None],
    eligibility: dict[uuid.UUID, set[str]],
    capacities: dict[str, object],
) -> int | None:
    """整堂課要用哪一個叢集。

    一堂課的所有學生固定在同一個叢集：跨叢集時 L2 不通、同名 bridge 指向
    不同的實體網路、firewall 規則逐台下在各自節點上 —— 教室功能、監控與
    故障範圍也都會被切開。

    在放得下全班的叢集之中挑最寬鬆的；都放不下時挑最寬鬆的那個，交由後續
    的節點容量檢查明確回報不足，而不是在這裡無聲失敗。
    """
    usable = [
        cid
        for cid in clusters
        if targets_in_cluster(
            nodes=nodes, eligibility=eligibility, connection_id=cid
        )
    ]
    if not usable:
        return None

    cpu_need, mem_need, _disk_need = _student_footprint(nodes)

    def _capacity_in_students(cid: int | None) -> float:
        room = _cluster_headroom(
            connection_id=cid, eligibility=eligibility, capacities=capacities
        )
        return min(
            room["cpu"] / cpu_need if cpu_need > 0 else float("inf"),
            room["mem"] / mem_need if mem_need > 0 else float("inf"),
        )

    ranked = sorted(
        usable,
        key=lambda cid: (-_capacity_in_students(cid), cid is None, cid),
    )
    fits = [cid for cid in ranked if _capacity_in_students(cid) >= student_count]
    return fits[0] if fits else ranked[0]


def allocate_class_placements(
    *,
    nodes: list[TeachingClassMachineNode],
    student_ids: list[uuid.UUID],
    connection_id: int | None,
    eligibility: dict[uuid.UUID, set[str]],
    capacities: dict[str, object],
) -> dict[uuid.UUID, dict[uuid.UUID, str]]:
    """叢集內把學生分散到不同節點。

    同一個叢集不代表同一台 server：叢集內跨節點靠同一個 bridge 加 PVE
    firewall 是通的，所以一位學生的機器不必擠在同一台，可以依容量攤開。

    每種機器類型各自分配 —— 它們的可建節點不同（LXC 範本克隆只能待在範本
    節點，自訂 LXC 則是所有看得到該 vztmpl 的節點）。逐位學生放置，每次挑
    「放上去之後占用比例最低」的節點，讓同型機器平均攤在該叢集的各台
    server 上。

    回傳 {machine_node_id: {student_id: 節點名}}。
    """
    placements: dict[uuid.UUID, dict[uuid.UUID, str]] = {}
    # 同一份 taken 跨機器類型累加：一台 server 已經接了很多 attacker 時，
    # 後面的 target 就會傾向落到別台，而不是每種類型各自從零開始攤。
    taken: dict[str, dict[str, float]] = defaultdict(
        lambda: {"cpu": 0.0, "mem": 0.0, "disk": 0.0}
    )

    for machine_node in nodes:
        candidates = sorted(
            name
            for name in eligibility.get(machine_node.id, set())
            if get_connection_id_for_node(name) == connection_id
            and capacities.get(name) is not None
            and getattr(capacities[name], "status", "") == "online"
        )
        if not candidates:
            return {}

        cpu_need = float(machine_node.cpu)
        mem_need = float(machine_node.memory_mb * MIB)
        disk_need = float(machine_node.disk_gb * GIB)

        def _projected(
            name: str,
            *,
            cpu: float = cpu_need,
            mem: float = mem_need,
        ) -> float:
            # 需求量以預設參數綁在定義當下，避免閉包抓到下一輪的機器規格。
            # 只比 CPU／RAM：節點層的 disk 是主機系統碟，拿來攤開學生沒有意義
            capacity = capacities[name]
            return max(
                _ratio(
                    taken[name]["cpu"] + cpu,
                    float(getattr(capacity, "allocatable_cpu_cores", 0.0)),
                ),
                _ratio(
                    taken[name]["mem"] + mem,
                    float(getattr(capacity, "allocatable_memory_bytes", 0)),
                ),
            )

        per_student: dict[uuid.UUID, str] = {}
        for student_id in student_ids:
            chosen = min(candidates, key=_projected)
            per_student[student_id] = chosen
            taken[chosen]["cpu"] += cpu_need
            taken[chosen]["mem"] += mem_need
            taken[chosen]["disk"] += disk_need
        placements[machine_node.id] = per_student

    return placements


def resolve_class_targets(
    session: Session,
    *,
    nodes: list[TeachingClassMachineNode],
    connection_id: int | None = None,
) -> tuple[dict[uuid.UUID, str], list[str]]:
    """指定叢集（未指定時取全班共用叢集）內每台課程機器的建機節點。

    這是「不分散」的退路：預留階段沒留下落點時（舊資料、或預覽情境）用它得到
    一個確定的節點。整班仍固定在同一個叢集。
    """
    eligibility, shared, issues = class_eligibility(session, nodes=nodes)
    if issues or not nodes:
        return {}, issues

    if connection_id is None:
        connection_id = sorted(shared, key=lambda item: (item is None, item))[0]
    elif connection_id not in shared:
        return {}, [t("class_capacity.no_node_in_cluster", name=nodes[0].name)]

    targets = targets_in_cluster(
        nodes=nodes,
        eligibility=eligibility,
        connection_id=connection_id,
        preferred=_preferred_node(),
    )
    if not targets:
        return {}, [t("class_capacity.no_node_in_cluster", name=nodes[0].name)]
    return targets, []


def _reserved_placement(
    session: Session,
    *,
    class_id: uuid.UUID,
    machine_node_id: uuid.UUID,
    user_id: uuid.UUID | None,
) -> str | None:
    """預留時定案的建機節點；查不到時回 None（沿用單一節點行為）。"""
    if user_id is None:
        return None
    reservation = session.exec(
        select(ClassCapacityReservation).where(
            ClassCapacityReservation.class_id == class_id
        )
    ).first()
    if reservation is None:
        return None
    mapping = _json_dict(reservation.student_placements)
    node = mapping.get(str(machine_node_id), {}).get(str(user_id))
    return str(node) if node else None


def target_node_for_machine(
    session: Session,
    *,
    machine_node: TeachingClassMachineNode,
    user_id: uuid.UUID | None = None,
) -> str | None:
    """建機時取這位學生這台機器的節點，與容量預留使用同一份分配。

    優先查預留階段存下的落點 —— 整班在同一個叢集內，但學生被分散到不同的
    server，重算得不到同樣的結果。查不到時退回全班共用叢集的單一節點。
    """
    placed = _reserved_placement(
        session,
        class_id=machine_node.class_id,
        machine_node_id=machine_node.id,
        user_id=user_id,
    )
    if placed:
        return placed

    siblings = list(
        session.exec(
            select(TeachingClassMachineNode).where(
                TeachingClassMachineNode.class_id == machine_node.class_id
            )
        ).all()
    )
    targets, issues = resolve_class_targets(
        session, nodes=siblings or [machine_node]
    )
    if issues:
        return None
    return targets.get(machine_node.id)


def _evaluate_cluster_capacity(
    session: Session,
    *,
    nodes: list[TeachingClassMachineNode],
    student_ids: list[uuid.UUID],
) -> tuple[
    dict[str, dict[str, float]],
    dict[uuid.UUID, dict[uuid.UUID, str]],
    list[str],
]:
    """Return the per-node demand plan, the student placements and issues.

    整班固定在同一個叢集（跨叢集 L2 不通），但叢集內依容量把學生分散到不同
    的 server —— 同一個叢集不代表同一台 server。
    """
    demand: dict[str, dict[str, float]] = defaultdict(
        lambda: {"cpu_cores": 0, "memory_bytes": 0, "disk_bytes": 0, "machines": 0}
    )
    eligibility, clusters, issues = class_eligibility(session, nodes=nodes)
    if issues:
        return {}, {}, issues

    try:
        cluster_nodes, resources = placement_support.load_cluster_state()
        cpu_ratio, disk_ratio = placement_service.get_overcommit_ratios(session)
        memory_ratio = placement_service.get_memory_overcommit_ratio(session)
        capacities = {
            row.node: row
            for row in placement_support.build_live_node_capacities(
                nodes=cluster_nodes,
                resources=resources,
                cpu_overcommit_ratio=cpu_ratio,
                disk_overcommit_ratio=disk_ratio,
                memory_overcommit_ratio=memory_ratio,
            )
        }
    except Exception:
        logger.exception("Failed to fetch Proxmox capacity for class reservation")
        return {}, {}, [t("class_capacity.capacity_check_failed")]

    # build_live_node_capacities 已經套過一次；這裡用本次 session 的 DB 再套一次
    # 範本母機的排除名單（重複套用結果不變）
    placement_support.apply_committed_allocations(
        capacities,
        nodes=cluster_nodes,
        resources=resources,
        cpu_overcommit_ratio=cpu_ratio,
        memory_overcommit_ratio=memory_ratio,
        excluded_vmids=_template_guest_vmids(session),
    )
    # 已預留、還沒建出來的機器 PVE 上看不到，要另外扣；已建出來的上面已經算過
    pending_machines, legacy_plans = _pending_reservation_load(session)
    for node_name, spec in pending_machines:
        _subtract_capacity(
            capacities.get(node_name),
            cpu_cores=float(spec.cpu),
            memory_bytes=int(spec.memory_mb) * MIB,
        )
    for plan in legacy_plans:
        for node_name, values in plan.items():
            _subtract_capacity(
                capacities.get(node_name),
                cpu_cores=float(values.get("cpu_cores") or 0),
                memory_bytes=int(values.get("memory_bytes") or 0),
            )

    # 整班鎖定一個叢集，再於叢集內把學生分散到不同 server。
    connection_id = choose_class_cluster(
        nodes=nodes,
        student_count=len(student_ids),
        clusters=clusters,
        eligibility=eligibility,
        capacities=capacities,
    )
    placements = allocate_class_placements(
        nodes=nodes,
        student_ids=student_ids,
        connection_id=connection_id,
        eligibility=eligibility,
        capacities=capacities,
    )
    if not placements:
        return {}, {}, [t("class_capacity.no_node_in_cluster", name=nodes[0].name)]

    for node in nodes:
        for target_node in placements[node.id].values():
            target = demand[target_node]
            target["cpu_cores"] += node.cpu
            target["memory_bytes"] += node.memory_mb * MIB
            target["disk_bytes"] += node.disk_gb * GIB
            target["machines"] += 1

    issues = []
    for node_name, values in demand.items():
        capacity = capacities.get(node_name)
        if capacity is None or capacity.status != "online":
            issues.append(t("class_capacity.node_offline", node=node_name))
            continue
        # 預覽頁逐節點列出「需要／可用」；存進預留快照前會拿掉
        values["available_cpu_cores"] = round(float(capacity.allocatable_cpu_cores), 1)
        values["available_memory_bytes"] = int(capacity.allocatable_memory_bytes)
        if capacity.allocatable_cpu_cores < values["cpu_cores"]:
            issues.append(
                t(
                    "class_capacity.cpu_insufficient",
                    node=node_name,
                    required=values["cpu_cores"],
                    available=f"{capacity.allocatable_cpu_cores:.1f}",
                )
            )
        if capacity.allocatable_memory_bytes < values["memory_bytes"]:
            issues.append(
                t(
                    "class_capacity.ram_insufficient",
                    node=node_name,
                    required=_gib(values["memory_bytes"]),
                    available=_gib(capacity.allocatable_memory_bytes),
                )
            )
    # 磁碟一律依儲存區檢查（節點層的 disk 是主機系統碟），不因 CPU／RAM
    # 不足就略過，老師才能一次看到所有不足的項目
    issues.extend(
        _storage_pool_issues(
            session,
            nodes=nodes,
            placements=placements,
            pending_machines=pending_machines,
        )
    )
    return dict(demand), placements, issues


def _template_guest_vmids(session: Session) -> set[int]:
    """平台範本母機的 VMID（查不到時回空集合，等於把母機也算進去）。"""
    return placement_support.template_guest_vmids(db_engine=session.get_bind())


def _subtract_capacity(
    capacity: NodeCapacity | None, *, cpu_cores: float, memory_bytes: int
) -> None:
    if capacity is None:
        return
    capacity.allocatable_cpu_cores = max(
        0.0, float(capacity.allocatable_cpu_cores) - cpu_cores
    )
    capacity.allocatable_memory_bytes = max(
        0, int(capacity.allocatable_memory_bytes) - memory_bytes
    )


def _pending_reservation_load(
    session: Session,
) -> tuple[list[tuple[str, TeachingClassMachineNode]], list[dict[str, Any]]]:
    """其他班級已預留、但機器還沒建出來的量。

    回傳 (逐台待建機器與預定節點, 沒有逐位落點的舊預留之 placement_plan)。
    預留在班級第一次上線前都是 reserved；這段期間已經建好的機器會出現在 PVE
    上、由 ``_apply_committed_allocations`` 算進去，這裡再整份扣一次就重複了。
    """
    pending: list[tuple[str, TeachingClassMachineNode]] = []
    legacy_plans: list[dict[str, Any]] = []
    for reservation in session.exec(
        select(ClassCapacityReservation).where(
            ClassCapacityReservation.status == "reserved"
        )
    ).all():
        placements = _json_dict(reservation.student_placements)
        if not placements:
            legacy_plans.append(_json_dict(reservation.placement_plan))
            continue
        specs = {
            str(row.id): row
            for row in session.exec(
                select(TeachingClassMachineNode).where(
                    TeachingClassMachineNode.class_id == reservation.class_id
                )
            ).all()
        }
        built = {
            (str(machine.machine_node_id), str(student.user_id))
            for machine, student in session.exec(
                select(TeachingClassStudentMachine, TeachingClassStudent)
                .join(
                    TeachingClassStudent,
                    TeachingClassStudentMachine.class_student_id  # type: ignore[arg-type]
                    == TeachingClassStudent.id,
                )
                .where(TeachingClassStudent.class_id == reservation.class_id)
            ).all()
            if machine.vmid is not None
        }
        for machine_node_id, per_student in placements.items():
            spec = specs.get(str(machine_node_id))
            if spec is None or not isinstance(per_student, dict):
                continue
            for user_id, node_name in per_student.items():
                if node_name and (str(machine_node_id), str(user_id)) not in built:
                    pending.append((str(node_name), spec))
    return pending, legacy_plans


def _storage_pool_issues(
    session: Session,
    *,
    nodes: list[TeachingClassMachineNode],
    placements: dict[uuid.UUID, dict[uuid.UUID, str]],
    pending_machines: list[tuple[str, TeachingClassMachineNode]] | None = None,
) -> list[str]:
    """依實際儲存區檢查磁碟：整班逐台試放，一台機器不能跨池。

    節點層的 disk 是 PVE 主機系統碟，跟機器放的 local-lvm／Ceph 無關，不能
    拿來判斷磁碟夠不夠。這裡照 placement 的真實規則逐台挑儲存區；其他班級
    已預留、還沒建出來的機器先放進去，避免兩班都通過、開課才撞上空間不足。
    """
    target_names = sorted(
        {name for machine in placements.values() for name in machine.values()}
    )
    if not target_names:
        return []
    pending_machines = pending_machines or []
    pools_by_node, has_managed_storage = placement_support.build_storage_pool_state(
        session=session,
        node_names=sorted(
            set(target_names) | {node_name for node_name, _spec in pending_machines}
        ),
    )
    if not has_managed_storage:
        return []

    _cpu_ratio, disk_ratio = placement_service.get_overcommit_ratios(session)
    tuning = placement_service.get_placement_tuning(session=session)
    template_nodes: dict[uuid.UUID | None, str | None] = {}

    def _template_node(machine_node: TeachingClassMachineNode) -> str | None:
        if machine_node.source_type != "template":
            return None
        key = machine_node.source_template_id
        if key not in template_nodes:
            template = session.get(VMTemplate, key) if key else None
            template_nodes[key] = template.node if template else None
        return template_nodes[key]

    def _place(target_node: str, machine_node: TeachingClassMachineNode) -> bool:
        disk_gb = int(machine_node.disk_gb)
        pools = pools_by_node.get(target_node, [])
        source_node = _template_node(machine_node)
        if source_node is not None and target_node != source_node:
            # 範本 clone 到其他節點只能放共享儲存（與批次建機挑的一致）
            pools = [pool for pool in pools if pool.is_shared]
        selection = placement_storage.select_best_storage_for_request(
            storage_pools=pools,
            resource_type=_storage_resource_type(machine_node),
            disk_gb=disk_gb,
            disk_overcommit_ratio=disk_ratio,
            tuning=tuning,
        )
        if selection is None:
            return False
        placement_storage.reserve_storage_pool(
            selection=selection,
            disk_gb=disk_gb,
            disk_overcommit_ratio=disk_ratio,
        )
        return True

    for target_node, machine_node in pending_machines:
        # 別班放不下是別班的問題，這裡只是先把它的份占掉
        _place(target_node, machine_node)
    for machine_node in nodes:
        for target_node in placements[machine_node.id].values():
            if not _place(target_node, machine_node):
                # 第一個放不下的就足以擋下整班，不必把剩下的學生也列出來
                return [
                    t(
                        "class_capacity.storage_insufficient",
                        node=target_node,
                        required=int(machine_node.disk_gb),
                    )
                ]
    return []


def _storage_resource_type(machine_node: TeachingClassMachineNode) -> ResourceType:
    return "lxc" if machine_node.resource_type.lower() == "lxc" else "vm"


def _check_cluster_capacity(
    session: Session,
    *,
    nodes: list[TeachingClassMachineNode],
    student_ids: list[uuid.UUID],
) -> tuple[dict[str, dict[str, float]], dict[uuid.UUID, dict[uuid.UUID, str]]]:
    """Validate capacity for reservation while preview uses structured issues."""
    placement_plan, placements, issues = _evaluate_cluster_capacity(
        session,
        nodes=nodes,
        student_ids=student_ids,
    )
    if issues:
        raise BadRequestError("；".join(issues))
    return placement_plan, placements
