"""Whole-class capacity calculation and hard IP reservation."""

import json
import logging
import uuid
from collections import defaultdict

from sqlmodel import Session, select

from app.core.i18n import t
from app.domain.placement import advisor as placement_advisor
from app.domain.placement import storage as placement_storage
from app.exceptions import BadRequestError
from app.infrastructure.proxmox import (
    get_connection_id_for_node,
)
from app.models import (
    ClassCapacityReservation,
    TeachingClassMachineNode,
    TeachingClassStudent,
    VMTemplate,
)
from app.services.network import ip_management_service
from app.services.proxmox import provisioning_service, proxmox_service
from app.services.vm import placement_service, placement_support

GIB = 1024**3
logger = logging.getLogger(__name__)


def calculate(
    *,
    nodes: list[TeachingClassMachineNode],
    students: list[TeachingClassStudent],
) -> dict[str, int]:
    student_count = len(students)
    per_student_networks = {
        name.strip()
        for node in nodes
        for name in (node.network or "lab-net").replace("/", ",").split(",")
        if name.strip()
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
            f"IP 不足：需要 {totals['ip_count']} 個，"
            f"目前只剩 {ip_stats['available']} 個"
        ]
    )
    placement_plan: dict[str, dict[str, int]] = {}
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
        placement_plan=json.dumps(placement_plan, sort_keys=True),
        student_placements=json.dumps(
            {
                str(machine_node_id): {
                    str(user_id): node for user_id, node in per_student.items()
                }
                for machine_node_id, per_student in student_placements.items()
            },
            sort_keys=True,
        ),
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

    - LXC 範本克隆：linked clone 必須與範本同節點同 storage（PVE 限制），
      只有範本節點一個選擇。
    - VM（範本與自訂）：批次建機的 create_vm 與 clone_service 一律在範本節點
      上 clone，不接受指定節點。要放寬成整個連線，得先讓那兩條路徑支援跨節點
      full clone 與失敗退回。
    - 自訂 LXC：只有 iso_storage 看得到該 vztmpl 的節點；create_lxc 接受指定
      節點，所以這是目前唯一能在叢集內分散的來源。
    """
    if machine_node.source_type == "template":
        template = session.get(VMTemplate, machine_node.source_template_id)
        if template is None:
            raise LookupError("template not found")
        return {template.node}

    if machine_node.resource_type == "lxc":
        node_map = proxmox_service.get_lxc_template_node_map()
        # 整張映射為空多半是查詢失敗，沿用舊的單一節點行為而非誤判為全叢集可用
        if not node_map:
            return {provisioning_service._get_lxc_target_node()}
        return set(node_map.get(str(machine_node.custom_image_ref or ""), set()))

    return {
        provisioning_service._get_vm_target_node(
            int(machine_node.custom_image_ref or "0")
        )
    }


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
        return provisioning_service._get_lxc_target_node()
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
    """該叢集內、這堂課用得到的節點加總後的可用量。"""
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
        "disk": sum(float(getattr(r, "allocatable_disk_bytes", 0)) for r in rows),
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

    cpu_need, mem_need, disk_need = _student_footprint(nodes)

    def _capacity_in_students(cid: int | None) -> float:
        room = _cluster_headroom(
            connection_id=cid, eligibility=eligibility, capacities=capacities
        )
        return min(
            room["cpu"] / cpu_need if cpu_need > 0 else float("inf"),
            room["mem"] / mem_need if mem_need > 0 else float("inf"),
            room["disk"] / disk_need if disk_need > 0 else float("inf"),
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
        mem_need = float(machine_node.memory_mb * 1024**2)
        disk_need = float(machine_node.disk_gb * GIB)

        def _projected(
            name: str,
            *,
            cpu: float = cpu_need,
            mem: float = mem_need,
            disk: float = disk_need,
        ) -> float:
            # 需求量以預設參數綁在定義當下，避免閉包抓到下一輪的機器規格
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
                _ratio(
                    taken[name]["disk"] + disk,
                    float(getattr(capacity, "allocatable_disk_bytes", 0)),
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
    try:
        mapping = json.loads(reservation.student_placements or "{}")
    except (TypeError, ValueError):
        return None
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
    dict[str, dict[str, int]],
    dict[uuid.UUID, dict[uuid.UUID, str]],
    list[str],
]:
    """Return the per-node demand plan, the student placements and issues.

    整班固定在同一個叢集（跨叢集 L2 不通），但叢集內依容量把學生分散到不同
    的 server —— 同一個叢集不代表同一台 server。
    """
    demand: dict[str, dict[str, int]] = defaultdict(
        lambda: {"cpu_cores": 0, "memory_bytes": 0, "disk_bytes": 0, "machines": 0}
    )
    eligibility, clusters, issues = class_eligibility(session, nodes=nodes)
    if issues:
        return {}, {}, issues

    try:
        cluster_nodes, resources = placement_advisor._load_cluster_state()
        cpu_ratio, disk_ratio = placement_service.get_overcommit_ratios(session)
        capacities = {
            row.node: row
            for row in placement_advisor._build_node_capacities(
                nodes=cluster_nodes,
                resources=resources,
                cpu_overcommit_ratio=cpu_ratio,
                disk_overcommit_ratio=disk_ratio,
            )
        }
    except Exception:
        logger.exception("Failed to fetch Proxmox capacity for class reservation")
        return {}, {}, [t("class_capacity.capacity_check_failed")]

    # Pending reviewed classes are not necessarily visible as PVE guests yet.
    for reservation in session.exec(
        select(ClassCapacityReservation).where(
            ClassCapacityReservation.status == "reserved"
        )
    ).all():
        try:
            pending = json.loads(reservation.placement_plan or "{}")
        except (TypeError, ValueError):
            continue
        for node_name, values in pending.items():
            capacity = capacities.get(node_name)
            if capacity is None:
                continue
            capacity.allocatable_cpu_cores = max(
                0,
                capacity.allocatable_cpu_cores - float(values.get("cpu_cores") or 0),
            )
            capacity.allocatable_memory_bytes = max(
                0,
                capacity.allocatable_memory_bytes
                - int(values.get("memory_bytes") or 0),
            )
            capacity.allocatable_disk_bytes = max(
                0,
                capacity.allocatable_disk_bytes - int(values.get("disk_bytes") or 0),
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
            target["memory_bytes"] += node.memory_mb * 1024**2
            target["disk_bytes"] += node.disk_gb * GIB
            target["machines"] += 1

    issues = []
    for node_name, values in demand.items():
        capacity = capacities.get(node_name)
        if capacity is None or capacity.status != "online":
            issues.append(t("class_capacity.node_offline", node=node_name))
            continue
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
                    required=values["memory_bytes"] // GIB,
                    available=capacity.allocatable_memory_bytes // GIB,
                )
            )
        if capacity.allocatable_disk_bytes < values["disk_bytes"]:
            issues.append(
                t(
                    "class_capacity.disk_insufficient",
                    node=node_name,
                    required=values["disk_bytes"] // GIB,
                    available=capacity.allocatable_disk_bytes // GIB,
                )
            )
    if not issues:
        issues = _storage_pool_issues(session, nodes=nodes, placements=placements)
    return dict(demand), placements, issues


def _storage_pool_issues(
    session: Session,
    *,
    nodes: list[TeachingClassMachineNode],
    placements: dict[uuid.UUID, dict[uuid.UUID, str]],
) -> list[str]:
    """節點層磁碟總量夠，不代表任何一個儲存區放得下。

    上面的檢查看的是節點的 allocatable_disk_bytes 加總；實際開機是逐台挑
    儲存區，一台機器不能跨池。所以照 placement 的真實規則把整班逐台試放
    一次，避免預檢通過、開課當下才撞上「沒有可用儲存區」。
    """
    target_names = sorted(
        {name for machine in placements.values() for name in machine.values()}
    )
    if not target_names:
        return []
    pools_by_node, has_managed_storage = placement_support.build_storage_pool_state(
        session=session, node_names=target_names
    )
    if not has_managed_storage:
        return []

    _cpu_ratio, disk_ratio = placement_service.get_overcommit_ratios(session)
    tuning = placement_service._get_placement_tuning(session=session)
    for machine_node in nodes:
        disk_gb = int(machine_node.disk_gb)
        resource_type = "lxc" if machine_node.resource_type.lower() == "lxc" else "vm"
        for target_node in placements[machine_node.id].values():
            selection = placement_storage.select_best_storage_for_request(
                storage_pools=pools_by_node.get(target_node, []),
                resource_type=resource_type,
                disk_gb=disk_gb,
                disk_overcommit_ratio=disk_ratio,
                tuning=tuning,
            )
            if selection is None:
                # 第一個放不下的就足以擋下整班，不必把剩下的學生也列出來
                return [
                    t(
                        "class_capacity.storage_insufficient",
                        node=target_node,
                        required=disk_gb,
                    )
                ]
            placement_storage.reserve_storage_pool(
                selection=selection,
                disk_gb=disk_gb,
                disk_overcommit_ratio=disk_ratio,
            )
    return []


def _check_cluster_capacity(
    session: Session,
    *,
    nodes: list[TeachingClassMachineNode],
    student_ids: list[uuid.UUID],
) -> tuple[dict[str, dict[str, int]], dict[uuid.UUID, dict[uuid.UUID, str]]]:
    """Validate capacity for reservation while preview uses structured issues."""
    placement_plan, placements, issues = _evaluate_cluster_capacity(
        session,
        nodes=nodes,
        student_ids=student_ids,
    )
    if issues:
        raise BadRequestError("；".join(issues))
    return placement_plan, placements
