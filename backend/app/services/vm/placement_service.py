from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session

from app.domain.placement import advisor as placement_advisor
from app.domain.placement import policy as placement_policy
from app.domain.placement import scorer as placement_scorer
from app.domain.placement.models import (
    AssignmentEvaluation as _AssignmentEvaluation,
)
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
    reserve_storage_pool as _reserve_storage_pool,
)
from app.domain.placement.storage import (
    select_best_storage_for_request as _select_best_storage_for_request,
)
from app.models import VMRequest
from app.repositories import vm_request as vm_request_repo
from app.services.scheduling import policy as scheduling_policy
from app.services.scheduling import support as scheduling_support
from app.services.vm import placement_support

GIB = 1024**3
DEFAULT_PLACEMENT_STRATEGY = placement_policy.DEFAULT_PLACEMENT_STRATEGY
_MigrationPolicy = scheduling_policy.MigrationPolicy


@dataclass
class CurrentPlacementSelection:
    node: str | None
    strategy: str
    plan: PlacementPlan


_projected_share = placement_scorer.projected_share
_storage_contention_penalty = placement_scorer.storage_contention_penalty
_node_balance_score = placement_scorer.node_balance_score


def _utc_now() -> datetime:
    return placement_support.utc_now()


def _normalize_datetime(value: datetime | None) -> datetime | None:
    return placement_support.normalize_datetime(value)


def _request_window(db_request: VMRequest) -> tuple[datetime | None, datetime | None]:
    return placement_support.request_window(db_request)


def _request_capacity_tuple(db_request: VMRequest) -> tuple[float, int, int]:
    return placement_support.request_capacity_tuple(db_request)


def _get_placement_tuning(*, session: Session) -> _PlacementTuning:
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


def _is_quick_template_request(request: VMRequest) -> bool:
    return getattr(request, "request_kind", "research") == "quick_template"


def _fixed_node_for_quick_template(request: VMRequest) -> str | None:
    if not _is_quick_template_request(request):
        return None
    return (
        _provisioned_current_node(request)
        or request.desired_node
        or request.assigned_node
    )


def _build_rebalance_baseline_nodes(
    *,
    session: Session,
    requests: list[VMRequest],
) -> list[NodeCapacity]:
    return placement_support.build_rebalance_baseline_nodes(
        session=session,
        requests=requests,
        get_overcommit_ratios_fn=get_overcommit_ratios,
        release_request_from_capacities_fn=_release_request_from_capacities,
    )


def _build_active_rebalance_allowed_target_nodes(
    *,
    ordered_requests: list[VMRequest],
    baseline_nodes: list[NodeCapacity],
    policy: _MigrationPolicy,
    now: datetime,
) -> dict[uuid.UUID, set[str]]:
    candidate_nodes = [item.node for item in baseline_nodes]
    target_storages_by_node: dict[str, set[str]] = {}
    for node_name in candidate_nodes:
        try:
            target_storages_by_node[node_name] = (
                scheduling_support.shared_storage_ids_on_node(
                    node=node_name
                )
            )
        except Exception:
            target_storages_by_node[node_name] = set()

    allowed_target_nodes: dict[uuid.UUID, set[str]] = {}
    for request in ordered_requests:
        fixed_node = _fixed_node_for_quick_template(request)
        if fixed_node:
            allowed_target_nodes[request.id] = {fixed_node}
            continue
        allowed, _ = scheduling_support.migration_allowed_target_nodes_for_request(
            request=request,
            candidate_nodes=candidate_nodes,
            current_node=_provisioned_current_node(request),
            policy=policy,
            now=now,
            safe=True,
            target_storages_by_node=target_storages_by_node,
        )
        allowed_target_nodes[request.id] = allowed
    return allowed_target_nodes


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


def _release_request_from_capacities(
    *,
    node_capacities: list[NodeCapacity],
    db_request: VMRequest,
    node_name: str | None,
) -> None:
    placement_support.release_request_from_capacities(
        node_capacities=node_capacities,
        db_request=db_request,
        node_name=node_name,
        request_capacity_tuple_fn=_request_capacity_tuple,
        refresh_node_candidate_fn=_refresh_node_candidate,
    )


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
        get_placement_tuning_fn=_get_placement_tuning,
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
        allow_cohort_rebalance=not _is_quick_template_request(db_request),
    )


def select_reserved_target_node_for_request(
    *,
    session: Session,
    request: PlacementRequest,
    start_at: datetime | None,
    end_at: datetime | None,
    reserved_requests: list[VMRequest] | None = None,
    allow_cohort_rebalance: bool = True,
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
    if not allow_cohort_rebalance:
        return CurrentPlacementSelection(
            node=plan.recommended_node,
            strategy=strategy,
            plan=plan.model_copy(
                update={
                    "summary": (
                        "Selected from currently available reserved capacity "
                        "without previewing cohort rebalance."
                    ),
                }
            ),
        )

    overlapping_start_requests = [
        item
        for item in reserved_requests
        if (window := _request_window(item))[0] is not None
        and window[1] is not None
        and window[0] <= start_at < window[1]
    ]
    preview_request = _build_preview_vm_request(
        request=request,
        start_at=start_at,
        end_at=end_at,
    )
    preview_cohort = overlapping_start_requests + [preview_request]
    preview_ordered_requests = sorted(
        preview_cohort,
        key=lambda item: (
            _normalize_datetime(item.start_at) or datetime.min.replace(tzinfo=UTC),
            _normalize_datetime(item.reviewed_at) or datetime.min.replace(tzinfo=UTC),
            _normalize_datetime(item.created_at) or datetime.min.replace(tzinfo=UTC),
            str(item.id),
        ),
    )
    preview_baseline_nodes = _build_rebalance_baseline_nodes(
        session=session,
        requests=preview_ordered_requests,
    )
    preview_baseline_nodes = [
        item.model_copy(deep=True)
        for item in preview_baseline_nodes
        if item.node in feasible_nodes
    ]
    priorities = get_node_priorities(session)
    tuning = _get_placement_tuning(session=session)
    best_preview_node = plan.recommended_node
    best_preview_objective: tuple[float, float, float, int] | None = None
    candidate_evals: dict[str, _AssignmentEvaluation] = {}
    for candidate_node in sorted(feasible_nodes):
        try:
            preview_assignments = _solve_rebalance_assignments(
                session=session,
                ordered_requests=preview_ordered_requests,
                baseline_nodes=preview_baseline_nodes,
                strategy=strategy,
                priorities=priorities,
                tuning=tuning,
                fixed_assignments={preview_request.id: candidate_node},
            )
            preview_eval = _evaluate_active_assignment_map(
                session=session,
                ordered_requests=preview_ordered_requests,
                baseline_nodes=preview_baseline_nodes,
                assignments=preview_assignments,
                priorities=priorities,
                tuning=tuning,
            )
        except ValueError:
            continue
        if not preview_eval.feasible:
            continue
        candidate_evals[candidate_node] = preview_eval
        if (
            best_preview_objective is None
            or preview_eval.objective < best_preview_objective
        ):
            best_preview_objective = preview_eval.objective
            best_preview_node = candidate_node
    preview_reasons = (
        _build_preview_selection_reasons(
            selected_node=best_preview_node,
            selected_eval=candidate_evals[best_preview_node],
            candidate_evals=candidate_evals,
            priorities=priorities,
        )
        if best_preview_node and best_preview_node in candidate_evals
        else list(plan.rationale or [])
    )
    return CurrentPlacementSelection(
        node=best_preview_node,
        strategy=strategy,
        plan=plan.model_copy(
            update={
                "recommended_node": best_preview_node,
                "summary": (
                    "Reservation preview selected the best feasible node "
                    "using the same active-window rebalance objective."
                ),
                "rationale": preview_reasons,
            }
        ),
    )


def _evaluate_active_assignment_map(
    *,
    session: Session,
    ordered_requests: list[VMRequest],
    baseline_nodes: list[NodeCapacity],
    assignments: dict[uuid.UUID, str],
    priorities: dict[str, int],
    tuning: _PlacementTuning,
    allowed_target_nodes_by_request: dict[uuid.UUID, set[str]] | None = None,
    max_migrations: int | None = None,
) -> _AssignmentEvaluation:
    working_nodes = [item.model_copy(deep=True) for item in baseline_nodes]
    by_node = {item.node: item for item in working_nodes}
    storage_pools_by_node, has_managed_storage = _build_storage_pool_state(
        session=session,
        node_names=[item.node for item in working_nodes],
    )
    _, disk_overcommit_ratio = get_overcommit_ratios(session)
    storage_penalty_total = 0.0
    priority_total = 0.0
    movement_count = 0
    storage_speed_rank_total = 0.0
    storage_user_priority_total = 0.0
    _INFEASIBLE_OBJECTIVE: tuple[float, ...] = (
        float("inf"),
        float("inf"),
        10**9,
        float("inf"),
        float("inf"),
        float("inf"),
    )

    for request in ordered_requests:
        target_node = assignments.get(request.id)
        if not target_node:
            return _AssignmentEvaluation(
                feasible=False,
                objective=_INFEASIBLE_OBJECTIVE,
            )
        allowed_targets = (allowed_target_nodes_by_request or {}).get(request.id)
        if allowed_targets is not None and target_node not in allowed_targets:
            return _AssignmentEvaluation(
                feasible=False,
                objective=_INFEASIBLE_OBJECTIVE,
            )
        node = by_node.get(target_node)
        if node is None:
            return _AssignmentEvaluation(
                feasible=False,
                objective=_INFEASIBLE_OBJECTIVE,
            )

        placement_request = _to_placement_request(request)
        effective_resource_type, _ = placement_advisor._decide_resource_type(
            placement_request
        )
        required_cpu = placement_advisor._effective_cpu_cores(
            placement_request,
            effective_resource_type,
        )
        required_memory = placement_advisor._effective_memory_bytes(
            placement_request,
            effective_resource_type,
        )
        required_disk = placement_request.disk_gb * GIB
        if not placement_support.node_can_host_request(
            node,
            cores=required_cpu,
            memory_bytes=required_memory,
            disk_bytes=required_disk,
            gpu_required=placement_request.gpu_required,
            has_managed_storage=has_managed_storage,
            allowed_gpu_nodes=placement_support.allowed_gpu_nodes_for_request(
                placement_request
            ),
        ):
            return _AssignmentEvaluation(
                feasible=False,
                objective=_INFEASIBLE_OBJECTIVE,
            )

        storage_selection: _StorageSelection | None = None
        if has_managed_storage:
            storage_selection = _select_best_storage_for_request(
                storage_pools=storage_pools_by_node.get(target_node, []),
                resource_type=str(placement_request.resource_type),
                disk_gb=int(placement_request.disk_gb),
                disk_overcommit_ratio=disk_overcommit_ratio,
                tuning=tuning,
            )
            if storage_selection is None:
                return _AssignmentEvaluation(
                    feasible=False,
                    objective=_INFEASIBLE_OBJECTIVE,
                )

        _reserve_request_on_capacities(
            node_capacities=working_nodes,
            db_request=request,
            node_name=target_node,
        )
        if storage_selection is not None:
            _reserve_storage_pool(
                selection=storage_selection,
                disk_gb=int(placement_request.disk_gb),
                disk_overcommit_ratio=disk_overcommit_ratio,
            )
            storage_penalty_total += storage_selection.contention_penalty
            storage_speed_rank_total += float(storage_selection.speed_rank)
            storage_user_priority_total += float(storage_selection.user_priority)
        priority_total += float(priorities.get(target_node, 5))
        if _provisioned_current_node(request) not in {None, target_node}:
            movement_count += 1

    if max_migrations is not None and movement_count > max(max_migrations, 0):
        return _AssignmentEvaluation(
            feasible=False,
            objective=_INFEASIBLE_OBJECTIVE,
        )

    node_score_map = {
        node.node: _node_balance_score(node, tuning=tuning) for node in working_nodes
    }
    max_node_score = max(node_score_map.values(), default=0.0)
    total_score = (
        sum(node_score_map.values())
        + (storage_penalty_total * tuning.disk_penalty_weight)
        + (movement_count * tuning.migration_cost)
    )
    return _AssignmentEvaluation(
        feasible=True,
        objective=(
            max_node_score,
            total_score,
            priority_total,
            float(movement_count),
            storage_speed_rank_total,
            storage_user_priority_total,
        ),
        max_node_score=max_node_score,
        total_score=total_score,
        priority_total=priority_total,
        movement_count=movement_count,
        node_scores=node_score_map,
        storage_penalties={
            node_name: sum(
                _storage_contention_penalty(
                    projected_share=_projected_share(
                        used=max(pool.total_gb - pool.avail_gb, 0.0),
                        total=max(pool.total_gb, 1.0),
                    ),
                    placed_count=pool.placed_count,
                    overcommit_placed_count=pool.overcommit_placed_count,
                    tuning=tuning,
                    overcommit=pool.overcommit_placed_count > 0,
                )
                for pool in storage_pools_by_node.get(node_name, [])
            )
            for node_name in by_node
        },
    )


def _initial_active_assignment_map(
    *,
    session: Session,
    ordered_requests: list[VMRequest],
    baseline_nodes: list[NodeCapacity],
    strategy: str,
    priorities: dict[str, int],
    tuning: _PlacementTuning,
    fixed_assignments: dict[uuid.UUID, str] | None = None,
    allowed_target_nodes_by_request: dict[uuid.UUID, set[str]] | None = None,
    max_migrations: int | None = None,
) -> dict[uuid.UUID, str]:
    working_nodes = [item.model_copy(deep=True) for item in baseline_nodes]
    storage_pools_by_node, has_managed_storage = _build_storage_pool_state(
        session=session,
        node_names=[item.node for item in working_nodes],
    )
    _, disk_overcommit_ratio = get_overcommit_ratios(session)
    placements: dict[str, int] = {item.node: 0 for item in working_nodes}
    assignments: dict[uuid.UUID, str] = {}
    locked_nodes = fixed_assignments or {}
    movement_count = 0

    for request in ordered_requests:
        placement_request = _to_placement_request(request)
        effective_resource_type, resource_type_reason = placement_advisor._decide_resource_type(
            placement_request
        )
        required_cpu = placement_advisor._effective_cpu_cores(
            placement_request,
            effective_resource_type,
        )
        required_memory = placement_advisor._effective_memory_bytes(
            placement_request,
            effective_resource_type,
        )
        required_disk = placement_request.disk_gb * GIB
        node_disk_bytes = placement_support.node_disk_bytes_for_capacity(
            disk_bytes=required_disk,
            has_managed_storage=has_managed_storage,
        )
        allowed_gpu_nodes = placement_support.allowed_gpu_nodes_for_request(
            placement_request
        )
        current_node = _provisioned_current_node(request)
        movement_budget_exhausted = (
            max_migrations is not None
            and current_node is not None
            and movement_count >= max(max_migrations, 0)
        )
        candidates: list[tuple[NodeCapacity, _StorageSelection | None]] = []
        for item in working_nodes:
            forced_node = locked_nodes.get(request.id)
            if forced_node and item.node != forced_node:
                continue
            if movement_budget_exhausted and item.node != current_node:
                continue
            allowed_targets = (allowed_target_nodes_by_request or {}).get(request.id)
            if allowed_targets is not None and item.node not in allowed_targets:
                continue
            if not placement_support.node_can_host_request(
                item,
                cores=required_cpu,
                memory_bytes=required_memory,
                disk_bytes=required_disk,
                gpu_required=placement_request.gpu_required,
                has_managed_storage=has_managed_storage,
                allowed_gpu_nodes=allowed_gpu_nodes,
            ):
                continue

            storage_selection: _StorageSelection | None = None
            if has_managed_storage:
                storage_selection = _select_best_storage_for_request(
                    storage_pools=storage_pools_by_node.get(item.node, []),
                    resource_type=str(placement_request.resource_type),
                    disk_gb=int(placement_request.disk_gb),
                    disk_overcommit_ratio=disk_overcommit_ratio,
                    tuning=tuning,
                )
                if storage_selection is None:
                    continue
            candidates.append((item, storage_selection))

        if not candidates:
            # Try relief relocation before giving up
            relief = _try_relief_relocation(
                session=session,
                stuck_request=request,
                ordered_requests_so_far=[r for r in ordered_requests if r.id in assignments],
                current_assignments=assignments,
                working_nodes=working_nodes,
                storage_pools_by_node=storage_pools_by_node,
                has_managed_storage=has_managed_storage,
                strategy=strategy,
                priorities=priorities,
                tuning=tuning,
                locked_request_ids=set(locked_nodes.keys()),
                disk_overcommit_ratio=disk_overcommit_ratio,
                allowed_target_nodes_by_request=allowed_target_nodes_by_request,
                max_migrations=max_migrations,
            )
            if relief is not None:
                # Adopt the relief assignments and continue
                assignments = relief
                # Re-build working state for the relief assignments
                working_nodes_copy = [item.model_copy(deep=True) for item in baseline_nodes]
                for r in ordered_requests:
                    if r.id in assignments:
                        _reserve_request_on_capacities(
                            node_capacities=working_nodes_copy,
                            db_request=r,
                            node_name=assignments[r.id],
                        )
                working_nodes = working_nodes_copy
                placements = {item.node: 0 for item in working_nodes}
                for node_name in assignments.values():
                    placements[node_name] = placements.get(node_name, 0) + 1
                continue
            raise ValueError(f"No feasible active rebalance exists for request {request.id}")

        chosen, chosen_storage = min(
            candidates,
            key=lambda candidate: _placement_sort_key(
                candidate[0],
                placements=placements,
                priorities=priorities,
                strategy=strategy,
                cores=required_cpu,
                memory_bytes=required_memory,
                disk_bytes=node_disk_bytes,
                storage_selection=candidate[1],
                tuning=tuning,
                current_node=_provisioned_current_node(request),
            ),
        )
        assignments[request.id] = chosen.node
        placements[chosen.node] += 1
        if current_node is not None and chosen.node != current_node:
            movement_count += 1
        _reserve_request_on_capacities(
            node_capacities=working_nodes,
            db_request=request,
            node_name=chosen.node,
        )
        if chosen_storage is not None:
            _reserve_storage_pool(
                selection=chosen_storage,
                disk_gb=int(placement_request.disk_gb),
                disk_overcommit_ratio=disk_overcommit_ratio,
            )

    return assignments


def _run_local_rebalance_search(
    *,
    session: Session,
    ordered_requests: list[VMRequest],
    baseline_nodes: list[NodeCapacity],
    initial_assignments: dict[uuid.UUID, str],
    priorities: dict[str, int],
    tuning: _PlacementTuning,
    locked_request_ids: set[uuid.UUID] | None = None,
    allowed_target_nodes_by_request: dict[uuid.UUID, set[str]] | None = None,
    max_migrations: int | None = None,
) -> dict[uuid.UUID, str]:
    if tuning.search_depth <= 0 or tuning.search_max_relocations <= 0:
        return initial_assignments

    current_assignments = dict(initial_assignments)
    locked_ids = set(locked_request_ids or ())
    # Also lock requests that are migration-pinned
    for req in ordered_requests:
        if getattr(req, 'migration_pinned', False):
            locked_ids.add(req.id)
    current_eval = _evaluate_active_assignment_map(
        session=session,
        ordered_requests=ordered_requests,
        baseline_nodes=baseline_nodes,
        assignments=current_assignments,
        priorities=priorities,
        tuning=tuning,
        allowed_target_nodes_by_request=allowed_target_nodes_by_request,
        max_migrations=max_migrations,
    )
    if not current_eval.feasible:
        return initial_assignments

    node_names = [item.node for item in baseline_nodes]
    used_moves = 0

    for _ in range(tuning.search_depth):
        if used_moves >= tuning.search_max_relocations:
            break

        best_assignments: dict[uuid.UUID, str] | None = None
        best_eval: _AssignmentEvaluation | None = None
        best_move_cost = 0

        for request in ordered_requests:
            if request.id in locked_ids:
                continue
            current_node = current_assignments.get(request.id)
            if not current_node:
                continue
            allowed_targets = (allowed_target_nodes_by_request or {}).get(request.id)
            for candidate_node in node_names:
                if candidate_node == current_node:
                    continue
                if allowed_targets is not None and candidate_node not in allowed_targets:
                    continue
                trial_assignments = dict(current_assignments)
                trial_assignments[request.id] = candidate_node
                trial_eval = _evaluate_active_assignment_map(
                    session=session,
                    ordered_requests=ordered_requests,
                    baseline_nodes=baseline_nodes,
                    assignments=trial_assignments,
                    priorities=priorities,
                    tuning=tuning,
                    allowed_target_nodes_by_request=allowed_target_nodes_by_request,
                    max_migrations=max_migrations,
                )
                if not trial_eval.feasible or trial_eval.objective >= current_eval.objective:
                    continue
                if best_eval is None or trial_eval.objective < best_eval.objective:
                    best_assignments = trial_assignments
                    best_eval = trial_eval
                    best_move_cost = 1

        if used_moves + 2 <= tuning.search_max_relocations:
            for index, request_a in enumerate(ordered_requests):
                if request_a.id in locked_ids:
                    continue
                node_a = current_assignments.get(request_a.id)
                if not node_a:
                    continue
                for request_b in ordered_requests[index + 1 :]:
                    if request_b.id in locked_ids:
                        continue
                    node_b = current_assignments.get(request_b.id)
                    if not node_b or node_a == node_b:
                        continue
                    trial_assignments = dict(current_assignments)
                    trial_assignments[request_a.id] = node_b
                    trial_assignments[request_b.id] = node_a
                    trial_eval = _evaluate_active_assignment_map(
                        session=session,
                        ordered_requests=ordered_requests,
                        baseline_nodes=baseline_nodes,
                        assignments=trial_assignments,
                        priorities=priorities,
                        tuning=tuning,
                        allowed_target_nodes_by_request=allowed_target_nodes_by_request,
                        max_migrations=max_migrations,
                    )
                    if not trial_eval.feasible or trial_eval.objective >= current_eval.objective:
                        continue
                    if best_eval is None or trial_eval.objective < best_eval.objective:
                        best_assignments = trial_assignments
                        best_eval = trial_eval
                        best_move_cost = 2

        if best_assignments is None or best_eval is None:
            break
        current_assignments = best_assignments
        current_eval = best_eval
        used_moves += best_move_cost

    return current_assignments


_RELIEF_MAX_EVALUATIONS = 50


def _try_relief_relocation(
    *,
    session: Session,
    stuck_request: VMRequest,
    ordered_requests_so_far: list[VMRequest],
    current_assignments: dict[uuid.UUID, str],
    working_nodes: list[NodeCapacity],
    storage_pools_by_node: dict[str, list[_WorkingStoragePool]],
    has_managed_storage: bool,
    strategy: str,
    priorities: dict[str, int],
    tuning: _PlacementTuning,
    locked_request_ids: set[uuid.UUID],
    disk_overcommit_ratio: float,
    allowed_target_nodes_by_request: dict[uuid.UUID, set[str]] | None = None,
    max_migrations: int | None = None,
) -> dict[uuid.UUID, str] | None:
    """Try 1-move or 2-move relief to make room for a stuck request.

    When direct placement fails, this function tries moving existing requests
    to other nodes to free capacity for the stuck request.
    Returns updated assignment map or None if no relief found.
    """
    if tuning.search_max_relocations <= 0:
        return None

    node_names = [n.node for n in working_nodes]
    evaluations = 0
    best_result: dict[uuid.UUID, str] | None = None
    best_score: tuple | None = None

    # 1-move relief: move one request away from a node, then check if stuck_request fits
    for req in ordered_requests_so_far:
        if req.id in locked_request_ids:
            continue
        if evaluations >= _RELIEF_MAX_EVALUATIONS:
            break

        current_node = current_assignments.get(req.id)
        if not current_node:
            continue
        req_allowed_targets = (allowed_target_nodes_by_request or {}).get(req.id)
        stuck_allowed_targets = (allowed_target_nodes_by_request or {}).get(stuck_request.id)
        if stuck_allowed_targets is not None and current_node not in stuck_allowed_targets:
            continue

        for target_node in node_names:
            if target_node == current_node:
                continue
            if req_allowed_targets is not None and target_node not in req_allowed_targets:
                continue
            if evaluations >= _RELIEF_MAX_EVALUATIONS:
                break
            evaluations += 1

            # Trial: move req from current_node to target_node
            trial = dict(current_assignments)
            trial[req.id] = target_node

            # Check if stuck_request now fits on current_node (freed capacity)
            trial[stuck_request.id] = current_node

            # Validate the entire assignment
            all_requests = ordered_requests_so_far + [stuck_request]
            try:
                trial_eval = _evaluate_active_assignment_map(
                    session=session,
                    ordered_requests=all_requests,
                    baseline_nodes=working_nodes,
                    assignments=trial,
                    priorities=priorities,
                    tuning=tuning,
                    allowed_target_nodes_by_request=allowed_target_nodes_by_request,
                    max_migrations=max_migrations,
                )
            except (ValueError, KeyError):
                continue

            if not trial_eval.feasible:
                continue

            if best_score is None or trial_eval.objective < best_score:
                best_score = trial_eval.objective
                best_result = trial

    return best_result


def _solve_rebalance_assignments(
    *,
    session: Session,
    ordered_requests: list[VMRequest],
    baseline_nodes: list[NodeCapacity],
    strategy: str,
    priorities: dict[str, int],
    tuning: _PlacementTuning,
    fixed_assignments: dict[uuid.UUID, str] | None = None,
    allowed_target_nodes_by_request: dict[uuid.UUID, set[str]] | None = None,
    max_migrations: int | None = None,
) -> dict[uuid.UUID, str]:
    initial_assignments = _initial_active_assignment_map(
        session=session,
        ordered_requests=ordered_requests,
        baseline_nodes=baseline_nodes,
        strategy=strategy,
        priorities=priorities,
        tuning=tuning,
        fixed_assignments=fixed_assignments,
        allowed_target_nodes_by_request=allowed_target_nodes_by_request,
        max_migrations=max_migrations,
    )
    final_assignments = _run_local_rebalance_search(
        session=session,
        ordered_requests=ordered_requests,
        baseline_nodes=baseline_nodes,
        initial_assignments=initial_assignments,
        priorities=priorities,
        tuning=tuning,
        locked_request_ids=(
            set((fixed_assignments or {}).keys())
            | {r.id for r in ordered_requests if getattr(r, 'migration_pinned', False)}
        ),
        allowed_target_nodes_by_request=allowed_target_nodes_by_request,
        max_migrations=max_migrations,
    )
    final_eval = _evaluate_active_assignment_map(
        session=session,
        ordered_requests=ordered_requests,
        baseline_nodes=baseline_nodes,
        assignments=final_assignments,
        priorities=priorities,
        tuning=tuning,
        allowed_target_nodes_by_request=allowed_target_nodes_by_request,
        max_migrations=max_migrations,
    )
    if not final_eval.feasible:
        raise ValueError("No feasible active rebalance exists for the current request cohort")
    return final_assignments


def _build_preview_selection_reasons(
    *,
    selected_node: str,
    selected_eval: _AssignmentEvaluation,
    candidate_evals: dict[str, _AssignmentEvaluation],
    priorities: dict[str, int],
) -> list[str]:
    alternatives = [
        (node, evaluation)
        for node, evaluation in candidate_evals.items()
        if node != selected_node and evaluation.feasible
    ]
    if not alternatives:
        return [f"{selected_node} is the only feasible node for this reservation window."]

    runner_up_node, runner_up_eval = min(alternatives, key=lambda item: item[1].objective)
    reasons = [
        (
            f"{selected_node} has the best feasible active-window objective "
            "for the reservation cohort."
        )
    ]

    if selected_eval.max_node_score + 0.01 < runner_up_eval.max_node_score:
        bottleneck_node = max(
            (runner_up_eval.node_scores or {}).items(),
            key=lambda item: item[1],
            default=(runner_up_node, runner_up_eval.max_node_score),
        )[0]
        reasons.append(
            f"It leaves less projected pressure on {bottleneck_node} than the runner-up."
        )

    selected_storage_penalty = (selected_eval.storage_penalties or {}).get(selected_node, 0.0)
    runner_up_storage_penalty = (runner_up_eval.storage_penalties or {}).get(
        runner_up_node,
        0.0,
    )
    if selected_storage_penalty + 0.08 < runner_up_storage_penalty:
        reasons.append(
            f"{selected_node} has lower storage contention than {runner_up_node}."
        )

    if selected_eval.movement_count < runner_up_eval.movement_count:
        delta = runner_up_eval.movement_count - selected_eval.movement_count
        reasons.append(f"It avoids {delta} extra VM movement(s).")

    selected_priority = priorities.get(selected_node, 5)
    runner_up_priority = priorities.get(runner_up_node, 5)
    if (
        selected_priority < runner_up_priority
        and abs(selected_eval.total_score - runner_up_eval.total_score) <= 0.15
    ):
        reasons.append(
            f"{selected_node} wins the tie-breaker by node priority."
        )

    return reasons[:4]


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


def rebalance_active_assignments(
    *,
    session: Session,
    requests: list[VMRequest],
) -> dict[uuid.UUID, CurrentPlacementSelection]:
    ordered_requests = sorted(
        requests,
        key=lambda item: (
            _normalize_datetime(item.start_at) or datetime.min.replace(tzinfo=UTC),
            _normalize_datetime(item.reviewed_at) or datetime.min.replace(tzinfo=UTC),
            _normalize_datetime(item.created_at) or datetime.min.replace(tzinfo=UTC),
            str(item.id),
        ),
    )
    working_nodes = _build_rebalance_baseline_nodes(
        session=session,
        requests=ordered_requests,
    )
    migration_policy = scheduling_policy.get_migration_policy(session=session)
    strategy = get_placement_strategy(session)
    priorities = get_node_priorities(session)
    tuning = _get_placement_tuning(session=session)

    baseline_nodes = [item.model_copy(deep=True) for item in working_nodes]
    allowed_target_nodes_by_request = _build_active_rebalance_allowed_target_nodes(
        ordered_requests=ordered_requests,
        baseline_nodes=baseline_nodes,
        policy=migration_policy,
        now=_utc_now(),
    )
    fixed_assignments = {
        request.id: fixed_node
        for request in ordered_requests
        if (fixed_node := _fixed_node_for_quick_template(request))
    }
    final_assignments = _solve_rebalance_assignments(
        session=session,
        ordered_requests=ordered_requests,
        baseline_nodes=baseline_nodes,
        strategy=strategy,
        priorities=priorities,
        tuning=tuning,
        fixed_assignments=fixed_assignments,
        allowed_target_nodes_by_request=allowed_target_nodes_by_request,
        max_migrations=migration_policy.max_per_rebalance,
    )

    selections: dict[uuid.UUID, CurrentPlacementSelection] = {}
    for request in ordered_requests:
        placement_request = _to_placement_request(request)
        effective_resource_type, resource_type_reason = placement_advisor._decide_resource_type(
            placement_request
        )
        chosen_node = final_assignments.get(request.id)
        if not chosen_node:
            raise ValueError(f"No feasible active rebalance exists for request {request.id}")
        selections[request.id] = CurrentPlacementSelection(
            node=chosen_node,
            strategy=strategy,
            plan=PlacementPlan(
                feasible=True,
                requested_resource_type=placement_request.resource_type,
                effective_resource_type=effective_resource_type,
                resource_type_reason=resource_type_reason,
                assigned_instances=1,
                unassigned_instances=0,
                recommended_node=chosen_node,
                summary=(
                    "Active window rebalance selected the best feasible node "
                    "after greedy placement and local rebalance search."
                ),
                rationale=[],
                warnings=[],
                placements=[],
                candidate_nodes=baseline_nodes,
            ),
        )

    return selections

def compute_node_score_breakdown(
    *,
    session: Session,
    candidate_evals: dict[str, _AssignmentEvaluation],
    selected_node: str | None,
    priorities: dict[str, int] | None = None,
) -> list[NodeScoreBreakdown]:
    if not candidate_evals:
        return []
    tuning = _get_placement_tuning(session=session)
    priorities = priorities or get_node_priorities(session)
    breakdowns: list[NodeScoreBreakdown] = []
    for node_name, evaluation in sorted(candidate_evals.items()):
        node_score = (evaluation.node_scores or {}).get(node_name, 0.0)
        storage_pen = (evaluation.storage_penalties or {}).get(node_name, 0.0)
        breakdowns.append(NodeScoreBreakdown(
            node=node_name,
            balance_score=round(node_score, 4),
            cpu_share=round(evaluation.objective[0], 4) if evaluation.feasible else 0.0,
            memory_share=0.0,
            disk_share=0.0,
            peak_penalty=0.0,
            loadavg_penalty=0.0,
            storage_penalty=round(storage_pen * tuning.disk_penalty_weight, 4),
            migration_cost=round(evaluation.movement_count * tuning.migration_cost, 4),
            priority=priorities.get(node_name, 5),
            is_selected=node_name == selected_node,
            reason=(
                "Selected node"
                if node_name == selected_node
                else ("Feasible alternative" if evaluation.feasible else "Not feasible")
            ),
        ))
    breakdowns.sort(key=lambda b: (not b.is_selected, b.balance_score, b.priority))
    return breakdowns


def get_preview_node_scores(
    *,
    session: Session,
    db_request: VMRequest,
    reserved_requests: list[VMRequest] | None = None,
) -> list[NodeScoreBreakdown]:
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

    overlapping_start_requests = [
        item for item in reserved_requests
        if (w := _request_window(item))[0] is not None
        and w[1] is not None
        and w[0] <= start_at < w[1]
    ]
    preview_request = _build_preview_vm_request(
        request=request, start_at=start_at, end_at=end_at,
    )
    preview_cohort = overlapping_start_requests + [preview_request]
    preview_ordered = sorted(
        preview_cohort,
        key=lambda item: (
            _normalize_datetime(item.start_at) or datetime.min.replace(tzinfo=UTC),
            _normalize_datetime(item.reviewed_at) or datetime.min.replace(tzinfo=UTC),
            _normalize_datetime(item.created_at) or datetime.min.replace(tzinfo=UTC),
            str(item.id),
        ),
    )
    preview_baseline = _build_rebalance_baseline_nodes(
        session=session, requests=preview_ordered,
    )
    preview_baseline = [
        item.model_copy(deep=True) for item in preview_baseline
        if item.node in feasible_nodes
    ]

    priorities = get_node_priorities(session)
    strategy = get_placement_strategy(session)
    tuning = _get_placement_tuning(session=session)

    candidate_evals: dict[str, _AssignmentEvaluation] = {}
    best_node: str | None = None
    best_obj = None
    for candidate_node in sorted(feasible_nodes):
        try:
            assignments = _solve_rebalance_assignments(
                session=session,
                ordered_requests=preview_ordered,
                baseline_nodes=preview_baseline,
                strategy=strategy,
                priorities=priorities,
                tuning=tuning,
                fixed_assignments={preview_request.id: candidate_node},
            )
            evaluation = _evaluate_active_assignment_map(
                session=session,
                ordered_requests=preview_ordered,
                baseline_nodes=preview_baseline,
                assignments=assignments,
                priorities=priorities,
                tuning=tuning,
            )
        except ValueError:
            continue
        if not evaluation.feasible:
            continue
        candidate_evals[candidate_node] = evaluation
        if best_obj is None or evaluation.objective < best_obj:
            best_obj = evaluation.objective
            best_node = candidate_node

    return compute_node_score_breakdown(
        session=session,
        candidate_evals=candidate_evals,
        selected_node=best_node,
        priorities=priorities,
    )


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
        tuning=_get_placement_tuning(session=session),
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


def _normalize_strategy(strategy: str | None) -> str:
    return placement_policy.normalize_strategy(strategy)


def _to_placement_request(db_request: VMRequest) -> PlacementRequest:
    return placement_support.to_placement_request(db_request)
