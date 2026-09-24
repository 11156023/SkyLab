from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.domain.placement import advisor as placement_advisor
from app.domain.placement import policy as placement_policy
from app.domain.placement import scorer as placement_scorer
from app.domain.placement.models import (
    PlacementTuning,
    StorageSelection,
    WorkingStoragePool,
)
from app.domain.placement.schemas import (
    NodeCapacity,
    PlacementDecision,
    PlacementPlan,
    PlacementRequest,
    ResourceType,
)
from app.domain.placement.storage import (
    reserve_storage_pool,
    select_best_storage_for_request,
)
from app.exceptions import NotFoundError
from app.infrastructure.proxmox import (
    get_connection_id_for_node,
    get_nodes_for_connection,
)
from app.models import VMRequest
from app.repositories import proxmox_storage as proxmox_storage_repo
from app.services.proxmox import gpu_service, proxmox_service

GIB = 1024**3

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def request_window(db_request: VMRequest) -> tuple[datetime | None, datetime | None]:
    return normalize_datetime(db_request.start_at), normalize_datetime(db_request.end_at)


def request_capacity_tuple(db_request: VMRequest) -> tuple[float, int, int]:
    cpu_cores = float(db_request.cores or 1)
    memory_bytes = int(db_request.memory or 512) * 1024 * 1024
    disk_gb = (
        int(db_request.disk_size or 0)
        if db_request.resource_type == "vm"
        else int(db_request.rootfs_size or 0)
    )
    if disk_gb <= 0:
        disk_gb = 20 if db_request.resource_type == "vm" else 8
    return cpu_cores, memory_bytes, disk_gb * GIB


def request_gpu_slots(db_request: VMRequest) -> int:
    """這張申請會佔用幾個 GPU 槽。目前一台機器最多掛一張卡。"""
    return 1 if str(getattr(db_request, "gpu_mapping_id", "") or "").strip() else 0


def build_storage_pool_state(
    *,
    session: Session,
    node_names: list[str],
) -> tuple[dict[str, list[WorkingStoragePool]], bool]:
    storages = proxmox_storage_repo.get_all_storages(session)
    if not storages:
        return {node_name: [] for node_name in node_names}, False

    shared_registry: dict[str, WorkingStoragePool] = {}
    by_node: dict[str, list[WorkingStoragePool]] = {node_name: [] for node_name in node_names}
    node_set = set(node_names)

    for storage in storages:
        node_name = str(storage.node_name or "")
        if node_name not in node_set:
            continue

        if storage.is_shared:
            pool = shared_registry.get(storage.storage)
            if pool is None:
                pool = WorkingStoragePool(
                    storage=storage.storage,
                    total_gb=float(storage.total_gb or 0.0),
                    avail_gb=float(storage.avail_gb or 0.0),
                    active=bool(storage.active),
                    enabled=bool(storage.enabled),
                    can_vm=bool(storage.can_vm),
                    can_lxc=bool(storage.can_lxc),
                    is_shared=bool(storage.is_shared),
                    speed_tier=str(storage.speed_tier or "unknown"),
                    user_priority=int(storage.user_priority or 5),
                )
                shared_registry[storage.storage] = pool
            by_node[node_name].append(pool)
            continue

        by_node[node_name].append(
            WorkingStoragePool(
                storage=storage.storage,
                total_gb=float(storage.total_gb or 0.0),
                avail_gb=float(storage.avail_gb or 0.0),
                active=bool(storage.active),
                enabled=bool(storage.enabled),
                can_vm=bool(storage.can_vm),
                can_lxc=bool(storage.can_lxc),
                is_shared=bool(storage.is_shared),
                speed_tier=str(storage.speed_tier or "unknown"),
                user_priority=int(storage.user_priority or 5),
            )
        )

    has_managed_storage = any(pools for pools in by_node.values())
    return by_node, has_managed_storage


def provisioned_current_node(request: VMRequest) -> str | None:
    if request.vmid is None:
        return None
    current = str(request.actual_node or "").strip()
    if current:
        return current
    assigned = str(request.assigned_node or "").strip()
    return assigned or None


def build_preview_vm_request(
    *,
    request: PlacementRequest,
    start_at: datetime,
    end_at: datetime,
) -> VMRequest:
    is_vm = str(request.resource_type) == "vm"
    return VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="placement-preview",
        resource_type=str(request.resource_type),
        hostname="placement-preview",
        cores=int(request.cpu_cores or 1),
        memory=int(request.memory_mb or 512),
        password="preview",
        storage="preview",
        environment_type="Preview",
        start_at=start_at,
        end_at=end_at,
        ostemplate=None if is_vm else "preview",
        rootfs_size=None if is_vm else int(request.disk_gb or 0),
        unprivileged=True,
        template_id=1 if is_vm else None,
        disk_size=int(request.disk_gb or 0) if is_vm else None,
        username="preview" if is_vm else None,
        created_at=utc_now(),
    )


def refresh_node_candidate(node: NodeCapacity) -> None:
    node.guest_pressure_ratio = placement_advisor._guest_pressure_ratio(
        int(node.running_resources),
        int(node.total_cpu_cores),
    )
    node.guest_overloaded = (
        node.guest_pressure_ratio >= placement_advisor.settings.guest_pressure_threshold
    )
    node.candidate = (
        node.status == "online"
        and node.allocatable_cpu_cores > 0
        and node.allocatable_memory_bytes > 0
        and node.allocatable_disk_bytes > 0
        and not node.guest_overloaded
    )


def node_can_host_request(
    node: NodeCapacity,
    *,
    cores: float,
    memory_bytes: int,
    disk_bytes: int,
    gpu_required: int,
    has_managed_storage: bool,
    allowed_gpu_nodes: set[str] | None = None,
    allowed_nodes: set[str] | None = None,
    allowed_affinity_nodes: set[str] | None = None,
) -> bool:
    # 模板節點白名單（None = 不受限；空集合 = 模板在任何節點都拿不到）
    if allowed_nodes is not None and node.node not in allowed_nodes:
        return False
    # 群組 affinity 白名單（None = 群組尚無錨點或不屬於任何群組）
    if allowed_affinity_nodes is not None and node.node not in allowed_affinity_nodes:
        return False
    if gpu_required > 0:
        if allowed_gpu_nodes is not None:
            if node.node not in allowed_gpu_nodes:
                return False
        elif node.gpu_count < gpu_required:
            return False
        # 白名單只回答「這個節點有沒有這張卡」，額度是否還有剩要另外算 ——
        # 否則同一張卡在同一時段可被多張申請排入並全部核准，直到建機時
        # 才由 _build_gpu_hostpci 發現額度用盡而失敗。
        if node.allocatable_gpu_slots < gpu_required:
            return False
    if (
        node.status != "online"
        or node.allocatable_cpu_cores < cores
        or node.allocatable_memory_bytes < memory_bytes
        or node.running_resources >= node.guest_soft_limit
    ):
        return False
    # 磁碟只在有受管儲存池時才判斷（由 select_best_storage_for_request 負責）。
    # 沒有受管儲存池時，節點的 maxdisk 是 PVE 自己的 root 檔案系統（常只有
    # 幾十 GB），拿它當客體磁碟容量會把每個節點都判成放不下；此時寧可不判，
    # 由 plan 的 warning 提醒管理員把儲存池納管。
    return True


def node_disk_bytes_for_capacity(*, disk_bytes: int, has_managed_storage: bool) -> int:
    """節點層要扣掉的磁碟位元組 —— 一律 0。

    有受管儲存池時磁碟記在儲存池上；沒有時節點 maxdisk 是 root fs，
    扣它只會把 allocatable_disk_bytes 誤扣成 0（節點被判成不可用）。
    參數保留讓呼叫端維持原本的語意表達。
    """
    return 0


def group_anchor_node(
    *,
    session: Session,
    placement_group_id: uuid.UUID | None,
    exclude_request_id: uuid.UUID | None = None,
) -> str | None:
    """同群組中已經定下落點的那個節點；None = 群組還沒有錨點。

    取最早建立的一台為錨點，讓同一組的每次查詢都得到同一個答案（後建的
    機器不會因為查詢順序不同而跟到不同節點）。

    預設不排除呼叫者自己，這點是刻意的：研究申請核准時
    rebuild_reserved_assignments 會重新求解整個時窗，其中包含尚未建機的
    群組成員。此時每個成員都已經有 assigned_node，會以自己為錨點而留在
    原地，整組因此被凍結成一個單位，不會在重解過程中被拆散到不同節點。
    要讓某台真的重新自由選點時，才傳入 exclude_request_id。
    """
    if placement_group_id is None:
        return None

    statement = (
        select(VMRequest)
        .where(VMRequest.placement_group_id == placement_group_id)
        .order_by(VMRequest.created_at, VMRequest.id)
    )
    for peer in session.exec(statement).all():
        if exclude_request_id is not None and peer.id == exclude_request_id:
            continue
        node = str(
            peer.actual_node or peer.assigned_node or peer.desired_node or ""
        ).strip()
        if node:
            return node
    return None


def allowed_affinity_nodes_for_request(
    *,
    session: Session,
    request: PlacementRequest,
    exclude_request_id: uuid.UUID | None = None,
) -> set[str] | None:
    """同群組已有落點時的節點白名單；None = 不受群組限制。

    約束是「同一個叢集」而不是「同一台節點」：叢集內跨節點靠同一個 bridge
    加 PVE firewall 是通的，機器不必擠在同一台。跨叢集才是真正的問題 ——
    L2 不通、firewall 規則各自獨立、IP 由全域單例網段配發但 gateway 是每個
    連線各自設定，拓樸 edge 會形同虛設。

    釘成同節點還會讓一組「範本分屬同叢集不同節點」的環境變成無解：LXC
    linked clone 不能離開自己的範本節點，兩台機器本來就不可能同節點。

    群組第一台自由選點，之後的機器限制在同一叢集；整組放不下時明確失敗，
    不會出現半組在 A 叢集、半組在 B 叢集的壞環境。
    """
    anchor = group_anchor_node(
        session=session,
        placement_group_id=request.placement_group_id,
        exclude_request_id=exclude_request_id,
    )
    if not anchor:
        return None
    return get_nodes_for_connection(get_connection_id_for_node(anchor)) or {anchor}


def allowed_gpu_nodes_for_request(request: PlacementRequest) -> set[str] | None:
    mapping_id = str(request.gpu_mapping_id or "").strip()
    if not mapping_id:
        return None
    return {
        node
        for node, count in gpu_service.get_gpu_node_counts(mapping_id=mapping_id).items()
        if count > 0
    }


_TEMPLATE_NODES_CACHE_TTL_SECONDS = 60.0
# cache value: (timestamp, 模板可見節點集合或 None, 模板是否標記需要 GPU)
_template_nodes_cache: dict[tuple[str, str], tuple[float, set[str] | None, bool]] = {}
_template_nodes_cache_lock = threading.Lock()

# 範本名稱／映像檔名以 -GPU 結尾（副檔名或版本段前）代表該作業系統需要 GPU
_GPU_MARKER_RE = re.compile(r"-gpu(?=[_.]|$)", re.IGNORECASE)


def _template_needs_gpu(name: str) -> bool:
    return bool(_GPU_MARKER_RE.search(str(name or "").strip()))


def _gpu_capable_nodes() -> set[str]:
    return {
        node
        for node, count in gpu_service.get_gpu_node_counts().items()
        if count > 0
    }


def allowed_template_nodes_for_request(request: PlacementRequest) -> set[str] | None:
    """模板決定的候選節點白名單；None = 不受模板限制。

    - LXC + ostemplate：只有 iso_storage 看得到該 vztmpl 的節點可選。
    - LXC + template_vmid（範本克隆）：linked clone 必須與範本同節點同
      storage，只有範本節點一個選擇。
    - VM + template_vmid：clone 不可跨連線，限制在範本所屬連線的節點；
      範本已不存在時回空集合（無可行節點，讓 placement 明確失敗）。
    - 名稱標記 -GPU 的模板且未指定 GPU mapping 時，再縮限到有 GPU 的
      節點（有指定 mapping 時交由 allowed_gpu_nodes 過濾，不重複處理）。
    - PVE 查詢異常時回 None（寧可放行讓後續建立報錯，也不因暫時性
      故障把整個排程判成不可行）。
    """
    resource_type = str(request.resource_type)
    if resource_type == "lxc" and request.ostemplate:
        cache_key = ("lxc", str(request.ostemplate))
    elif resource_type == "lxc" and request.template_vmid:
        cache_key = ("lxc_clone", str(request.template_vmid))
    elif resource_type == "vm" and request.template_vmid:
        cache_key = ("vm", str(request.template_vmid))
    else:
        return None

    now = time.monotonic()
    allowed: set[str] | None
    needs_gpu = False
    cached_hit = False
    with _template_nodes_cache_lock:
        cached = _template_nodes_cache.get(cache_key)
        if cached and (now - cached[0]) < _TEMPLATE_NODES_CACHE_TTL_SECONDS:
            allowed = set(cached[1]) if cached[1] is not None else None
            needs_gpu = cached[2]
            cached_hit = True

    if not cached_hit:
        try:
            if cache_key[0] == "lxc":
                node_map = proxmox_service.get_lxc_template_node_map()
                # 整張映射為空多半是所有節點查詢都失敗，視同不受限
                allowed = node_map.get(cache_key[1], set()) if node_map else None
                needs_gpu = _template_needs_gpu(cache_key[1])
            elif cache_key[0] == "lxc_clone":
                try:
                    source = proxmox_service.find_resource(int(cache_key[1]))
                except NotFoundError:
                    allowed = set()
                    source = None
                else:
                    node = str(source.get("node") or "")
                    allowed = {node} if node else None
                needs_gpu = _template_needs_gpu(
                    str((source or {}).get("name") or "")
                )
            else:
                try:
                    template = proxmox_service.find_vm_template(int(cache_key[1]))
                except NotFoundError:
                    allowed = set()
                    template = None
                else:
                    template_node = str(template.get("node") or "")
                    if template_node:
                        connection_nodes = get_nodes_for_connection(
                            get_connection_id_for_node(template_node)
                        )
                        allowed = connection_nodes or {template_node}
                    else:
                        allowed = None
                needs_gpu = _template_needs_gpu(
                    str((template or {}).get("name") or "")
                )
        except Exception as exc:
            logger.warning(
                "Unable to resolve template nodes for %s %s: %s",
                cache_key[0],
                cache_key[1],
                exc,
            )
            allowed = None
            needs_gpu = False

        with _template_nodes_cache_lock:
            _template_nodes_cache[cache_key] = (
                time.monotonic(),
                set(allowed) if allowed is not None else None,
                needs_gpu,
            )

    if needs_gpu and not str(request.gpu_mapping_id or "").strip():
        try:
            gpu_nodes = _gpu_capable_nodes()
        except Exception as exc:
            logger.warning("Unable to resolve GPU-capable nodes: %s", exc)
            gpu_nodes = None
        if gpu_nodes is not None:
            allowed = gpu_nodes if allowed is None else (allowed & gpu_nodes)

    return set(allowed) if allowed is not None else None


def reserve_request_on_capacities(
    *,
    node_capacities: list[NodeCapacity],
    db_request: VMRequest,
    node_name: str,
    request_capacity_tuple_fn,
    refresh_node_candidate_fn,
) -> None:
    node = next((item for item in node_capacities if item.node == node_name), None)
    if node is None:
        raise ValueError(f"Target node {node_name} not found in capacity list")

    cpu_cores, memory_bytes, disk_bytes = request_capacity_tuple_fn(db_request)
    node.allocatable_cpu_cores = max(round(node.allocatable_cpu_cores - cpu_cores, 2), 0.0)
    node.allocatable_memory_bytes = max(node.allocatable_memory_bytes - memory_bytes, 0)
    node.allocatable_disk_bytes = max(node.allocatable_disk_bytes - disk_bytes, 0)
    node.running_resources = int(node.running_resources) + 1
    node.allocatable_gpu_slots = max(
        int(node.allocatable_gpu_slots) - request_gpu_slots(db_request), 0
    )
    refresh_node_candidate_fn(node)


def hour_window_iter(start_at: datetime, end_at: datetime) -> list[datetime]:
    if end_at <= start_at:
        return [start_at]
    cursor = start_at.replace(minute=0, second=0, microsecond=0)
    if cursor < start_at:
        cursor += timedelta(hours=1)
    checkpoints: list[datetime] = []
    while cursor < end_at:
        checkpoints.append(cursor)
        cursor += timedelta(hours=1)
    return checkpoints or [start_at]


def apply_reserved_requests_to_capacities(
    *,
    baseline_capacities,
    reserved_requests: list[VMRequest],
    at_time: datetime,
    normalize_datetime_fn,
    request_capacity_tuple_fn,
):
    adjusted = [item.model_copy(deep=True) for item in baseline_capacities]
    by_node = {item.node: item for item in adjusted}

    for reserved in reserved_requests:
        # 已經建出機器的申請不再重複扣：它的 CPU／記憶體／磁碟／GPU 佔用
        # 已經反映在節點即時用量（baseline 由 PVE 現況算出），再扣一次
        # 等於同一台機器被算兩份，節點會提早被判成放不下。
        if getattr(reserved, "vmid", None) is not None:
            continue
        reserved_start = normalize_datetime_fn(reserved.start_at)
        reserved_end = normalize_datetime_fn(reserved.end_at)
        assigned_node = str(reserved.assigned_node or "")
        if not reserved_start or not reserved_end or not assigned_node:
            continue
        if not (reserved_start <= at_time < reserved_end):
            continue

        node = by_node.get(assigned_node)
        if not node:
            continue

        reserved_cpu, reserved_memory, reserved_disk = request_capacity_tuple_fn(reserved)
        node.allocatable_cpu_cores = max(node.allocatable_cpu_cores - reserved_cpu, 0.0)
        node.allocatable_memory_bytes = max(node.allocatable_memory_bytes - reserved_memory, 0)
        node.allocatable_disk_bytes = max(node.allocatable_disk_bytes - reserved_disk, 0)
        node.allocatable_gpu_slots = max(
            int(node.allocatable_gpu_slots) - request_gpu_slots(reserved), 0
        )
        node.candidate = (
            node.status == "online"
            and node.allocatable_cpu_cores > 0
            and node.allocatable_memory_bytes > 0
            and node.allocatable_disk_bytes > 0
        )

    return adjusted


def build_plan(
    *,
    session: Session,
    request: PlacementRequest,
    node_capacities: list[NodeCapacity],
    effective_resource_type: ResourceType,
    resource_type_reason: str,
    placement_strategy: str | None = None,
    node_priorities: dict[str, int] | None = None,
    current_node: str | None = None,
    build_storage_pool_state_fn,
    get_placement_tuning_fn,
    get_overcommit_ratios_fn,
    get_node_priorities_fn,
    placement_sort_key_fn,
) -> PlacementPlan:
    strategy = placement_strategy or placement_policy.get_placement_strategy(session)
    priorities = node_priorities or get_node_priorities_fn(session)
    tuning = get_placement_tuning_fn(session=session)
    working_nodes = [item.model_copy(deep=True) for item in node_capacities]
    storage_pools_by_node, has_managed_storage = build_storage_pool_state_fn(
        session=session,
        node_names=[item.node for item in working_nodes],
    )
    _, disk_overcommit_ratio = get_overcommit_ratios_fn(session)
    required_cpu = placement_advisor._effective_cpu_cores(request, effective_resource_type)
    required_memory = placement_advisor._effective_memory_bytes(request, effective_resource_type)
    required_disk = request.disk_gb * GIB
    node_disk_bytes = node_disk_bytes_for_capacity(
        disk_bytes=required_disk,
        has_managed_storage=has_managed_storage,
    )
    allowed_gpu_nodes = allowed_gpu_nodes_for_request(request)
    allowed_nodes = allowed_template_nodes_for_request(request)
    allowed_affinity_nodes = allowed_affinity_nodes_for_request(
        session=session, request=request
    )
    placements: dict[str, int] = {item.node: 0 for item in working_nodes}
    remaining = request.instance_count

    while remaining > 0:
        candidates: list[tuple[NodeCapacity, StorageSelection | None]] = []
        for item in working_nodes:
            if not node_can_host_request(
                item,
                cores=required_cpu,
                memory_bytes=required_memory,
                disk_bytes=required_disk,
                gpu_required=request.gpu_required,
                has_managed_storage=has_managed_storage,
                allowed_gpu_nodes=allowed_gpu_nodes,
                allowed_nodes=allowed_nodes,
                allowed_affinity_nodes=allowed_affinity_nodes,
            ):
                continue
            storage_selection: StorageSelection | None = None
            if has_managed_storage:
                storage_selection = select_best_storage_for_request(
                    storage_pools=storage_pools_by_node.get(item.node, []),
                    resource_type=str(request.resource_type),
                    disk_gb=int(request.disk_gb),
                    disk_overcommit_ratio=disk_overcommit_ratio,
                    tuning=tuning,
                )
                if storage_selection is None:
                    continue
            candidates.append((item, storage_selection))
        if not candidates:
            break

        chosen, chosen_storage = min(
            candidates,
            key=lambda candidate: placement_sort_key_fn(
                candidate[0],
                placements=placements,
                priorities=priorities,
                strategy=strategy,
                cores=required_cpu,
                memory_bytes=required_memory,
                disk_bytes=node_disk_bytes,
                storage_selection=candidate[1],
                tuning=tuning,
                current_node=current_node,
            ),
        )
        placements[chosen.node] += 1
        chosen.allocatable_cpu_cores = max(chosen.allocatable_cpu_cores - required_cpu, 0.0)
        chosen.allocatable_memory_bytes = max(chosen.allocatable_memory_bytes - required_memory, 0)
        chosen.allocatable_disk_bytes = max(chosen.allocatable_disk_bytes - node_disk_bytes, 0)
        chosen.running_resources += 1
        chosen.allocatable_gpu_slots = max(
            int(chosen.allocatable_gpu_slots) - request.gpu_required, 0
        )
        refresh_node_candidate(chosen)
        if chosen_storage is not None:
            reserve_storage_pool(
                selection=chosen_storage,
                disk_gb=int(request.disk_gb),
                disk_overcommit_ratio=disk_overcommit_ratio,
            )
        remaining -= 1

    assigned = request.instance_count - remaining
    placement_decisions = [
        PlacementDecision(
            node=item.node,
            instance_count=placements[item.node],
            cpu_cores_reserved=round(placements[item.node] * required_cpu, 2),
            memory_bytes_reserved=placements[item.node] * required_memory,
            disk_bytes_reserved=placements[item.node] * required_disk,
            remaining_cpu_cores=round(item.allocatable_cpu_cores, 2),
            remaining_memory_bytes=item.allocatable_memory_bytes,
            remaining_disk_bytes=item.allocatable_disk_bytes,
        )
        for item in working_nodes
        if placements[item.node] > 0
    ]
    placement_decisions.sort(key=lambda item: (-item.instance_count, item.node))

    warnings = placement_advisor._build_warnings(
        node_capacities=node_capacities,
        request=request,
        effective_resource_type=effective_resource_type,
        remaining=remaining,
    )
    if not has_managed_storage:
        warnings.append(
            "No managed storage pool is registered, so disk capacity was not "
            "evaluated for this placement."
        )

    return PlacementPlan(
        feasible=remaining == 0,
        requested_resource_type=request.resource_type,
        effective_resource_type=effective_resource_type,
        resource_type_reason=resource_type_reason,
        assigned_instances=assigned,
        unassigned_instances=remaining,
        recommended_node=placement_decisions[0].node if placement_decisions else None,
        summary=placement_advisor._build_summary_text(
            request=request,
            placement_decisions=placement_decisions,
            effective_resource_type=effective_resource_type,
            assigned=assigned,
            remaining=remaining,
        ),
        rationale=placement_advisor._build_rationale(
            request=request,
            placement_decisions=placement_decisions,
            effective_resource_type=effective_resource_type,
            node_capacities=node_capacities,
        ),
        warnings=warnings,
        placements=placement_decisions,
        candidate_nodes=node_capacities,
    )


def placement_sort_key(
    node: NodeCapacity,
    *,
    placements: dict[str, int],
    priorities: dict[str, int],
    strategy: str,
    cores: float,
    memory_bytes: int,
    disk_bytes: int,
    storage_selection: StorageSelection | None = None,
    tuning: PlacementTuning | None = None,
    current_node: str | None = None,
) -> tuple:
    tuning = tuning or PlacementTuning(
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
    projected_cpu_share = placement_scorer.projected_share(
        used=max(node.total_cpu_cores - node.allocatable_cpu_cores, 0.0) + cores,
        total=max(node.total_cpu_cores, 1.0),
    )
    projected_memory_share = placement_scorer.projected_share(
        used=max(node.total_memory_bytes - node.allocatable_memory_bytes, 0) + memory_bytes,
        total=max(node.total_memory_bytes, 1),
    )
    projected_disk_share = placement_scorer.projected_share(
        used=max(node.total_disk_bytes - node.allocatable_disk_bytes, 0) + disk_bytes,
        total=max(node.total_disk_bytes, 1),
    )
    w_cpu = tuning.resource_weight_cpu
    w_mem = tuning.resource_weight_memory
    w_disk = tuning.resource_weight_disk
    weighted_shares = [
        projected_cpu_share * w_cpu,
        projected_memory_share * w_mem,
        projected_disk_share * w_disk,
    ]
    dominant_share = max(weighted_shares)
    weight_sum = w_cpu + w_mem + w_disk
    average_share = sum(weighted_shares) / max(weight_sum, 0.01)
    peak_penalty = placement_scorer.peak_penalty(
        projected_cpu_share=placement_scorer.projected_share(
            used=max(node.total_cpu_cores - node.allocatable_cpu_cores, 0.0)
            + (cores * tuning.peak_cpu_margin),
            total=max(node.total_cpu_cores, 1.0),
        ),
        projected_memory_share=placement_scorer.projected_share(
            used=max(node.total_memory_bytes - node.allocatable_memory_bytes, 0)
            + int(memory_bytes * tuning.peak_memory_margin),
            total=max(node.total_memory_bytes, 1),
        ),
        tuning=tuning,
    )
    loadavg_penalty = placement_scorer.loadavg_penalty(
        placement_scorer.reference_loadavg_per_core(node),
        tuning=tuning,
    )
    cpu_contention = placement_scorer.cpu_contention_penalty(projected_cpu_share, tuning=tuning)
    cpu_contention_score = cpu_contention * tuning.cpu_contention_weight
    memory_overflow_penalty = (
        tuning.memory_overflow_weight if projected_memory_share > 1.0 + 1e-9 else 0.0
    )
    reassignment_penalty = tuning.reassignment_cost if current_node and current_node != node.node else 0.0
    disk_penalty = (
        storage_selection.contention_penalty * tuning.disk_penalty_weight
        if storage_selection is not None
        else 0.0
    )
    total_score = (
        dominant_share
        + peak_penalty
        + cpu_contention_score
        + memory_overflow_penalty
        + (loadavg_penalty * tuning.loadavg_penalty_weight)
        + reassignment_penalty
        + disk_penalty
    )
    placement_count = placements.get(node.node, 0)
    storage_speed_rank = storage_selection.speed_rank if storage_selection is not None else 99
    storage_user_priority = storage_selection.user_priority if storage_selection is not None else 99
    storage_projected_share = storage_selection.projected_share if storage_selection is not None else 1.0
    return (
        total_score,
        dominant_share,
        average_share,
        priorities.get(node.node, 5),
        placement_count,
        projected_cpu_share,
        storage_speed_rank,
        storage_user_priority,
        storage_projected_share,
        node.node,
    )


def to_placement_request(db_request: VMRequest) -> PlacementRequest:
    disk_gb = (
        int(db_request.disk_size or 0)
        if db_request.resource_type == "vm"
        else int(db_request.rootfs_size or 0)
    )
    if disk_gb <= 0:
        disk_gb = 20 if db_request.resource_type == "vm" else 8
    # LXC 帶 template_id 時走克隆路徑：不帶 ostemplate 約束，改以 template_vmid
    # 表示「必須落在範本所在節點」（linked clone 不能離開它，建機端也會強制
    # 覆寫成範本節點）。不帶這個約束的話，placement 會選出一個之後被覆寫掉的
    # 節點，群組 affinity 與容量計算都會跟著失準。
    ostemplate = (
        getattr(db_request, "ostemplate", None)
        if db_request.resource_type == "lxc"
        and not getattr(db_request, "template_id", None)
        else None
    )
    template_vmid = getattr(db_request, "template_id", None)
    return PlacementRequest(
        resource_type=db_request.resource_type,
        cpu_cores=int(db_request.cores or 1),
        memory_mb=int(db_request.memory or 512),
        disk_gb=disk_gb,
        instance_count=1,
        gpu_required=1 if bool(getattr(db_request, "gpu_mapping_id", None)) else 0,
        gpu_mapping_id=getattr(db_request, "gpu_mapping_id", None),
        ostemplate=ostemplate,
        template_vmid=template_vmid,
        placement_group_id=getattr(db_request, "placement_group_id", None),
    )
