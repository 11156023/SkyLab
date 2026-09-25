from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session

from app.domain.placement import advisor as placement_advisor
from app.domain.placement import policy as placement_policy
from app.domain.placement import scorer as placement_scorer
from app.domain.placement.models import (
    NodeScoreBreakdown,
)
from app.domain.placement.models import (
    PlacementTuning as _PlacementTuning,
)
from app.domain.placement.models import (
    StorageSelection as _StorageSelection,
)
from app.domain.placement.models import (
    WorkingStoragePool as _WorkingStoragePool,
)
from app.domain.placement.schemas import (
    NodeCapacity,
    PlacementPlan,
    PlacementRequest,
    ResourceType,
)
from app.domain.placement.storage import (
    select_best_storage_for_request as _select_best_storage_for_request,
)
from app.models import VMRequest
from app.repositories import vm_request as vm_request_repo
from app.services.vm import placement_support

GIB = 1024**3
DEFAULT_PLACEMENT_STRATEGY = placement_policy.DEFAULT_PLACEMENT_STRATEGY


@dataclass
class CurrentPlacementSelection:
    node: str | None
    strategy: str
    plan: PlacementPlan


_projected_share = placement_scorer.projected_share
_node_balance_score = placement_scorer.node_balance_score


def _utc_now() -> datetime:
    return placement_support.utc_now()


def _normalize_datetime(value: datetime | None) -> datetime | None:
    return placement_support.normalize_datetime(value)


def _request_window(db_request: VMRequest) -> tuple[datetime | None, datetime | None]:
    return placement_support.request_window(db_request)


def _request_capacity_tuple(db_request: VMRequest) -> tuple[float, int, int]:
    return placement_support.request_capacity_tuple(db_request)


def get_placement_tuning(*, session: Session) -> _PlacementTuning:
    return placement_policy.get_placement_tuning(session=session)


def _build_storage_pool_state(
    *,
    session: Session,
    node_names: list[str],
) -> tuple[dict[str, list[_WorkingStoragePool]], bool]:
    return placement_support.build_storage_pool_state(
        session=session,
        node_names=node_names,
    )


def _provisioned_current_node(request: VMRequest) -> str | None:
    return placement_support.provisioned_current_node(request)


def _build_preview_vm_request(
    *,
    request: PlacementRequest,
    start_at: datetime,
    end_at: datetime,
) -> VMRequest:
    return placement_support.build_preview_vm_request(
        request=request,
        start_at=start_at,
        end_at=end_at,
    )


def _refresh_node_candidate(node: NodeCapacity) -> None:
    placement_support.refresh_node_candidate(node)


def _reserve_request_on_capacities(
    *,
    node_capacities: list[NodeCapacity],
    db_request: VMRequest,
    node_name: str,
) -> None:
    placement_support.reserve_request_on_capacities(
        node_capacities=node_capacities,
        db_request=db_request,
        node_name=node_name,
        request_capacity_tuple_fn=_request_capacity_tuple,
        refresh_node_candidate_fn=_refresh_node_candidate,
    )


def _hour_window_iter(start_at: datetime, end_at: datetime) -> list[datetime]:
    return placement_support.hour_window_iter(start_at, end_at)


def _apply_reserved_requests_to_capacities(
    *,
    baseline_capacities,
    reserved_requests: list[VMRequest],
    at_time: datetime,
):
    return placement_support.apply_reserved_requests_to_capacities(
        baseline_capacities=baseline_capacities,
        reserved_requests=reserved_requests,
        at_time=at_time,
        normalize_datetime_fn=_normalize_datetime,
        request_capacity_tuple_fn=_request_capacity_tuple,
    )


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
) -> PlacementPlan:
    return placement_support.build_plan(
        session=session,
        request=request,
        node_capacities=node_capacities,
        effective_resource_type=effective_resource_type,
        resource_type_reason=resource_type_reason,
        placement_strategy=placement_strategy,
        node_priorities=node_priorities,
        current_node=current_node,
        build_storage_pool_state_fn=_build_storage_pool_state,
        get_placement_tuning_fn=get_placement_tuning,
        get_overcommit_ratios_fn=get_overcommit_ratios,
        get_node_priorities_fn=get_node_priorities,
        placement_sort_key_fn=_placement_sort_key,
    )


def select_current_target_node(
    *,
    session: Session,
    db_request: VMRequest,
) -> CurrentPlacementSelection:
    request = _to_placement_request(db_request)
    nodes, resources = placement_advisor._load_cluster_state()
    cpu_overcommit_ratio, disk_overcommit_ratio = get_overcommit_ratios(session)
    node_capacities = placement_advisor._build_node_capacities(
        nodes=nodes,
        resources=resources,
        cpu_overcommit_ratio=cpu_overcommit_ratio,
        disk_overcommit_ratio=disk_overcommit_ratio,
    )
    effective_resource_type, resource_type_reason = placement_advisor._decide_resource_type(
        request
    )
    plan = build_plan(
        session=session,
        request=request,
        node_capacities=node_capacities,
        effective_resource_type=effective_resource_type,
        resource_type_reason=resource_type_reason,
    )
    return CurrentPlacementSelection(
        node=plan.recommended_node,
        strategy=get_placement_strategy(session),
        plan=plan,
    )


def select_reserved_target_node(
    *,
    session: Session,
    db_request: VMRequest,
    reserved_requests: list[VMRequest] | None = None,
) -> CurrentPlacementSelection:
    start_at, end_at = _request_window(db_request)
    return select_reserved_target_node_for_request(
        session=session,
        request=_to_placement_request(db_request),
        start_at=start_at,
        end_at=end_at,
        reserved_requests=reserved_requests,
        allow_cohort_optimization=False,
    )


def select_reserved_target_node_for_request(
    *,
    session: Session,
    request: PlacementRequest,
    start_at: datetime | None,
    end_at: datetime | None,
    reserved_requests: list[VMRequest] | None = None,
    allow_cohort_optimization: bool = True,
) -> CurrentPlacementSelection:
    if not start_at or not end_at:
        nodes, resources = placement_advisor._load_cluster_state()
        cpu_overcommit_ratio, disk_overcommit_ratio = get_overcommit_ratios(session)
        node_capacities = placement_advisor._build_node_capacities(
            nodes=nodes,
            resources=resources,
            cpu_overcommit_ratio=cpu_overcommit_ratio,
            disk_overcommit_ratio=disk_overcommit_ratio,
        )
        effective_resource_type, resource_type_reason = (
            placement_advisor._decide_resource_type(request)
        )
        plan = build_plan(
            session=session,
            request=request,
            node_capacities=node_capacities,
            effective_resource_type=effective_resource_type,
            resource_type_reason=resource_type_reason,
        )
        return CurrentPlacementSelection(
            node=plan.recommended_node,
            strategy=get_placement_strategy(session),
            plan=plan,
        )

    nodes, resources = placement_advisor._load_cluster_state()
    cpu_overcommit_ratio, disk_overcommit_ratio = get_overcommit_ratios(session)
    baseline_capacities = placement_advisor._build_node_capacities(
        nodes=nodes,
        resources=resources,
        cpu_overcommit_ratio=cpu_overcommit_ratio,
        disk_overcommit_ratio=disk_overcommit_ratio,
    )
    effective_resource_type, resource_type_reason = placement_advisor._decide_resource_type(
        request
    )
    storage_pools_by_node, has_managed_storage = _build_storage_pool_state(
        session=session,
        node_names=[item.node for item in baseline_capacities],
    )
    allowed_gpu_nodes = placement_support.allowed_gpu_nodes_for_request(request)
    allowed_template_nodes = placement_support.allowed_template_nodes_for_request(
        request
    )
    allowed_affinity_nodes = placement_support.allowed_affinity_nodes_for_request(
        session=session, request=request
    )
    if reserved_requests is None:
        reserved_requests = vm_request_repo.get_approved_vm_requests_overlapping_window(
            session=session,
            window_start=start_at,
            window_end=end_at,
        )
    checkpoints = [start_at] + [
        checkpoint
        for checkpoint in _hour_window_iter(start_at, end_at)
        if checkpoint != start_at
    ]

    feasible_nodes = {item.node for item in baseline_capacities}
    start_capacities = baseline_capacities

    for index, checkpoint in enumerate(checkpoints):
        adjusted_capacities = _apply_reserved_requests_to_capacities(
            baseline_capacities=baseline_capacities,
            reserved_requests=reserved_requests,
            at_time=checkpoint,
        )
        if index == 0:
            start_capacities = adjusted_capacities

        hour_feasible_nodes = {
            item.node
            for item in adjusted_capacities
            if placement_support.node_can_host_request(
                item,
                cores=placement_advisor._effective_cpu_cores(
                    request, effective_resource_type
                ),
                memory_bytes=placement_advisor._effective_memory_bytes(
                    request, effective_resource_type
                ),
                disk_bytes=request.disk_gb * GIB,
                gpu_required=request.gpu_required,
                has_managed_storage=has_managed_storage,
                allowed_gpu_nodes=allowed_gpu_nodes,
                allowed_nodes=allowed_template_nodes,
                allowed_affinity_nodes=allowed_affinity_nodes,
            )
            and (
                not has_managed_storage
                or storage_pools_by_node.get(item.node)
            )
        }
        feasible_nodes &= hour_feasible_nodes
        if not feasible_nodes:
            break

    strategy = get_placement_strategy(session)
    if not feasible_nodes:
        return CurrentPlacementSelection(
            node=None,
            strategy=strategy,
            plan=build_plan(
                session=session,
                request=request,
                node_capacities=[],
                effective_resource_type=effective_resource_type,
                resource_type_reason=resource_type_reason,
                placement_strategy=strategy,
                node_priorities=get_node_priorities(session),
            ),
        )

    filtered_start_capacities = [
        item for item in start_capacities if item.node in feasible_nodes
    ]
    plan = build_plan(
        session=session,
        request=request,
        node_capacities=filtered_start_capacities,
        effective_resource_type=effective_resource_type,
        resource_type_reason=resource_type_reason,
        placement_strategy=strategy,
        node_priorities=get_node_priorities(session),
    )
    return CurrentPlacementSelection(
        node=plan.recommended_node,
        strategy=strategy,
        plan=plan.model_copy(
            update={
                "summary": (
                    "Selected the best feasible node from projected capacity "
                    "for the requested rental window."
                ),
            }
        ),
    )


def rebuild_reserved_assignments(
    *,
    session: Session,
    requests: list[VMRequest],
) -> dict[uuid.UUID, CurrentPlacementSelection]:
    """Rebuild node reservations for all approved requests in chronological order."""
    ordered_requests = sorted(
        requests,
        key=lambda item: (
            _normalize_datetime(item.start_at) or datetime.min.replace(tzinfo=UTC),
            _normalize_datetime(item.reviewed_at) or datetime.min.replace(tzinfo=UTC),
            _normalize_datetime(item.created_at) or datetime.min.replace(tzinfo=UTC),
            str(item.id),
        ),
    )
    reserved_so_far: list[VMRequest] = []
    selections: dict[uuid.UUID, CurrentPlacementSelection] = {}

    for request in ordered_requests:
        # 已建立的 VM/LXC 不再重新指派節點，也不進 reserved_so_far：機器已
        # 存在，節點即時用量裡本來就含它，再當成預留扣一次會雙重計算。
        current_node = _provisioned_current_node(request)
        if request.vmid is not None and current_node:
            request.assigned_node = current_node
            request.desired_node = current_node
            continue
        selection = select_reserved_target_node(
            session=session,
            db_request=request,
            reserved_requests=reserved_so_far,
        )
        if not selection.node or not selection.plan.feasible:
            raise ValueError(
                f"No feasible reservation exists for request {request.id}"
            )
        request.assigned_node = selection.node
        request.placement_strategy_used = selection.strategy
        selections[request.id] = selection
        reserved_so_far.append(request)

    return selections



def _preview_breakdown_for_node(
    *,
    candidate_node: str,
    projected_baseline: list[NodeCapacity],
    preview_request: VMRequest,
    request: PlacementRequest,
    storage_pools_by_node: dict[str, list[_WorkingStoragePool]],
    has_managed_storage: bool,
    disk_overcommit_ratio: float,
    tuning: _PlacementTuning,
    priorities: dict[str, int],
    current_node: str | None,
    is_selected: bool,
) -> NodeScoreBreakdown | None:
    """把本次申請放上 candidate_node 之後，該節點的投影狀態。

    回傳 None 代表該節點在儲存層面放不下（節點層的可行性已由呼叫端過濾）。
    """
    working = [item.model_copy(deep=True) for item in projected_baseline]
    node = next((item for item in working if item.node == candidate_node), None)
    if node is None:
        return None

    storage_selection: _StorageSelection | None = None
    if has_managed_storage:
        storage_selection = _select_best_storage_for_request(
            storage_pools=storage_pools_by_node.get(candidate_node, []),
            resource_type=str(request.resource_type),
            disk_gb=int(request.disk_gb),
            disk_overcommit_ratio=disk_overcommit_ratio,
            tuning=tuning,
        )
        if storage_selection is None:
            return None

    _reserve_request_on_capacities(
        node_capacities=working,
        db_request=preview_request,
        node_name=candidate_node,
    )

    cpu_share = _projected_share(
        used=max(node.total_cpu_cores - node.allocatable_cpu_cores, 0.0),
        total=max(node.total_cpu_cores, 1.0),
    )
    memory_share = _projected_share(
        used=max(node.total_memory_bytes - node.allocatable_memory_bytes, 0),
        total=max(node.total_memory_bytes, 1),
    )
    disk_share = _projected_share(
        used=max(node.total_disk_bytes - node.allocatable_disk_bytes, 0),
        total=max(node.total_disk_bytes, 1),
    )
    storage_penalty = (
        storage_selection.contention_penalty if storage_selection is not None else 0.0
    )
    return NodeScoreBreakdown(
        node=candidate_node,
        balance_score=round(_node_balance_score(node, tuning=tuning), 4),
        cpu_share=round(cpu_share, 4),
        memory_share=round(memory_share, 4),
        disk_share=round(disk_share, 4),
        peak_penalty=round(
            placement_scorer.peak_penalty(
                projected_cpu_share=cpu_share,
                projected_memory_share=memory_share,
                tuning=tuning,
            ),
            4,
        ),
        loadavg_penalty=round(
            placement_scorer.loadavg_penalty(
                placement_scorer.reference_loadavg_per_core(node),
                tuning=tuning,
            ),
            4,
        ),
        storage_penalty=round(storage_penalty * tuning.disk_penalty_weight, 4),
        reassignment_cost=round(
            tuning.reassignment_cost
            if current_node and current_node != candidate_node
            else 0.0,
            4,
        ),
        priority=priorities.get(candidate_node, 5),
        is_selected=is_selected,
        reason="Selected node" if is_selected else "Feasible alternative",
    )


def get_preview_node_scores(
    *,
    session: Session,
    db_request: VMRequest,
    reserved_requests: list[VMRequest] | None = None,
    selected_node: str | None = None,
) -> list[NodeScoreBreakdown]:
    """審核畫面的候選節點評分。

    `selected_node` 由呼叫端傳入核准路徑（rebuild_reserved_assignments）算出的
    投影落點 —— 預覽標示的「選定節點」因此與按下核准後的實際落點同源，不會
    出現兩套答案。未傳入時就地問一次核准會用的同一個函式。
    """
    start_at, end_at = _request_window(db_request)
    if not start_at or not end_at:
        return []

    request = _to_placement_request(db_request)
    effective_resource_type, _ = placement_advisor._decide_resource_type(request)

    nodes, resources = placement_advisor._load_cluster_state()
    cpu_overcommit_ratio, disk_overcommit_ratio = get_overcommit_ratios(session)
    baseline_capacities = placement_advisor._build_node_capacities(
        nodes=nodes,
        resources=resources,
        cpu_overcommit_ratio=cpu_overcommit_ratio,
        disk_overcommit_ratio=disk_overcommit_ratio,
    )

    if reserved_requests is None:
        reserved_requests = vm_request_repo.get_approved_vm_requests_overlapping_window(
            session=session,
            window_start=start_at,
            window_end=end_at,
        )

    checkpoints = [start_at] + [
        checkpoint
        for checkpoint in _hour_window_iter(start_at, end_at)
        if checkpoint != start_at
    ]

    feasible_nodes = {item.node for item in baseline_capacities}
    storage_pools_by_node, has_managed_storage = _build_storage_pool_state(
        session=session,
        node_names=[item.node for item in baseline_capacities],
    )
    allowed_gpu_nodes = placement_support.allowed_gpu_nodes_for_request(request)
    allowed_template_nodes = placement_support.allowed_template_nodes_for_request(
        request
    )
    allowed_affinity_nodes = placement_support.allowed_affinity_nodes_for_request(
        session=session, request=request
    )
    for checkpoint in checkpoints:
        adjusted = _apply_reserved_requests_to_capacities(
            baseline_capacities=baseline_capacities,
            reserved_requests=reserved_requests,
            at_time=checkpoint,
        )
        hour_feasible = {
            item.node for item in adjusted
            if placement_support.node_can_host_request(
                item,
                cores=placement_advisor._effective_cpu_cores(request, effective_resource_type),
                memory_bytes=placement_advisor._effective_memory_bytes(request, effective_resource_type),
                disk_bytes=request.disk_gb * GIB,
                gpu_required=request.gpu_required,
                has_managed_storage=has_managed_storage,
                allowed_gpu_nodes=allowed_gpu_nodes,
                allowed_nodes=allowed_template_nodes,
                allowed_affinity_nodes=allowed_affinity_nodes,
            )
            and (
                not has_managed_storage
                or storage_pools_by_node.get(item.node)
            )
        }
        feasible_nodes &= hour_feasible
        if not feasible_nodes:
            break

    if not feasible_nodes:
        return []

    if selected_node is None:
        selected_node = select_reserved_target_node_for_request(
            session=session,
            request=request,
            start_at=start_at,
            end_at=end_at,
            reserved_requests=reserved_requests,
            allow_cohort_optimization=False,
        ).node

    preview_request = _build_preview_vm_request(
        request=request, start_at=start_at, end_at=end_at,
    )
    # 其他 approved 申請的落點已經固定，baseline 扣掉它們的占用後，預覽只需
    # 回答「把這一台放到各候選節點，該節點會變成什麼樣子」—— 不必也不該重解
    # 整個 cohort（重解會得出與核准不同的答案，且成本是 O(節點²×申請²)）。
    projected_baseline = _apply_reserved_requests_to_capacities(
        baseline_capacities=baseline_capacities,
        reserved_requests=reserved_requests,
        at_time=start_at,
    )
    priorities = get_node_priorities(session)
    tuning = get_placement_tuning(session=session)
    current_node = _provisioned_current_node(db_request)

    breakdowns: list[NodeScoreBreakdown] = []
    for candidate_node in sorted(feasible_nodes):
        breakdown = _preview_breakdown_for_node(
            candidate_node=candidate_node,
            projected_baseline=projected_baseline,
            preview_request=preview_request,
            request=request,
            storage_pools_by_node=storage_pools_by_node,
            has_managed_storage=has_managed_storage,
            disk_overcommit_ratio=disk_overcommit_ratio,
            tuning=tuning,
            priorities=priorities,
            current_node=current_node,
            is_selected=candidate_node == selected_node,
        )
        if breakdown is not None:
            breakdowns.append(breakdown)

    breakdowns.sort(key=lambda b: (not b.is_selected, b.balance_score, b.priority))
    return breakdowns


def get_placement_strategy(session: Session) -> str:
    return placement_policy.get_placement_strategy(session)


def get_overcommit_ratios(session: Session) -> tuple[float, float]:
    return placement_policy.get_overcommit_ratios(session)


def get_node_priorities(session: Session) -> dict[str, int]:
    return placement_policy.get_node_priorities(session)


def select_best_storage_name(
    *,
    session: Session,
    node_name: str,
    resource_type: str,
    disk_gb: int,
    fallback_storage: str | None = None,
) -> str | None:
    storage_pools_by_node, has_managed_storage = _build_storage_pool_state(
        session=session,
        node_names=[node_name],
    )
    if not has_managed_storage:
        return fallback_storage

    _, disk_overcommit_ratio = get_overcommit_ratios(session)
    selection = _select_best_storage_for_request(
        storage_pools=storage_pools_by_node.get(node_name, []),
        resource_type=resource_type,
        disk_gb=disk_gb,
        disk_overcommit_ratio=disk_overcommit_ratio,
        tuning=get_placement_tuning(session=session),
    )
    if selection is None:
        return None
    return selection.pool.storage


def _placement_sort_key(
    node: NodeCapacity,
    *,
    placements: dict[str, int],
    priorities: dict[str, int],
    strategy: str,
    cores: float,
    memory_bytes: int,
    disk_bytes: int,
    storage_selection: _StorageSelection | None = None,
    tuning: _PlacementTuning | None = None,
    current_node: str | None = None,
) -> tuple:
    return placement_support.placement_sort_key(
        node,
        placements=placements,
        priorities=priorities,
        strategy=strategy,
        cores=cores,
        memory_bytes=memory_bytes,
        disk_bytes=disk_bytes,
        storage_selection=storage_selection,
        tuning=tuning,
        current_node=current_node,
    )


def _to_placement_request(db_request: VMRequest) -> PlacementRequest:
    return placement_support.to_placement_request(db_request)
