from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlmodel import Session, select

from app.ai.pve_advisor import recommendation_service as advisor_service
from app.ai.pve_advisor.schemas import PlacementRequest
from app.core.authorizers import require_vm_request_access
from app.exceptions import BadRequestError, NotFoundError
from app.models import UserRole, VMRequest, VMRequestStatus
from app.repositories import vm_request as vm_request_repo
from app.schemas.vm_request import (
    VMRequestAvailabilityDay,
    VMRequestAvailabilityNodeSnapshot,
    VMRequestAvailabilityRequest,
    VMRequestAvailabilityResponse,
    VMRequestAvailabilitySlot,
    VMRequestAvailabilityStackItem,
    VMRequestAvailabilitySummary,
    VMRequestWindowAvailabilityRequest,
    VMRequestWindowAvailabilityResponse,
)
from app.services.vm import vm_request_placement_service

GIB = 1024**3

_ALL_DAY_POLICY_WINDOW = (0, 24)

_ROLE_LABELS: dict[UserRole, str] = {
    UserRole.student: "學生",
    UserRole.teacher: "教師",
    UserRole.admin: "管理者",
}

_STATUS_PRIORITY: dict[str, int] = {
    "available": 0,
    "limited": 1,
    "unavailable": 2,
    "policy_blocked": 3,
}


def _is_hour_within_policy(*, hour: int, allowed_start: int, allowed_end: int) -> bool:
    """Support both same-day windows (08-22) and overnight windows (22-06)."""
    if allowed_start == allowed_end:
        return True
    if allowed_start < allowed_end:
        return allowed_start <= hour < allowed_end
    return hour >= allowed_start or hour < allowed_end


def assess_request(
    *,
    session: Session,
    current_user,
    request_in: VMRequestAvailabilityRequest,
) -> VMRequestAvailabilityResponse:
    role = request_in.policy_role or cast(UserRole, current_user.role)
    return _build_availability_response(
        session=session,
        source_request=request_in,
        role=role,
        stack_label=(
            f"Requested {'VM' if request_in.resource_type == 'vm' else 'LXC'}"
        ),
    )


def assess_existing_request(
    *,
    session: Session,
    request_id,
    current_user,
    days: int,
    timezone: str,
) -> VMRequestAvailabilityResponse:
    db_request = vm_request_repo.get_vm_request_by_id(
        session=session,
        request_id=request_id,
    )
    if not db_request:
        raise NotFoundError("Request not found")

    require_vm_request_access(current_user, db_request.user_id)

    request_owner = db_request.user
    role = request_owner.role if request_owner else UserRole.student
    request_in = VMRequestAvailabilityRequest(
        resource_type=cast(str, db_request.resource_type),
        cores=int(db_request.cores or 1),
        memory=int(db_request.memory or 512),
        disk_size=int(db_request.disk_size or 0) or None,
        rootfs_size=int(db_request.rootfs_size or 0) or None,
        instance_count=1,
        gpu_required=1 if db_request.gpu_mapping_id else 0,
        days=days,
        timezone=timezone,
        policy_role=role,
    )
    return _build_availability_response(
        session=session,
        source_request=request_in,
        role=role,
        stack_label=db_request.hostname or f"request-{db_request.id}",
    )


def validate_request_window(
    *,
    session: Session,
    current_user,
    request_in,
) -> None:
    role = cast(UserRole, getattr(current_user, "role", UserRole.student))
    start_at = _normalize_datetime(getattr(request_in, "start_at", None))
    end_at = _normalize_datetime(getattr(request_in, "end_at", None))
    if not start_at or not end_at:
        raise BadRequestError("A scheduled request window is required.")
    if end_at <= start_at:
        raise BadRequestError("end_at must be later than start_at")

    placement_request = PlacementRequest(
        resource_type=cast(str, request_in.resource_type),
        cpu_cores=int(getattr(request_in, "cores", 1) or 1),
        memory_mb=int(getattr(request_in, "memory", 512) or 512),
        disk_gb=_extract_disk_gb(
            resource_type=cast(str, request_in.resource_type),
            disk_size=getattr(request_in, "disk_size", None),
            rootfs_size=getattr(request_in, "rootfs_size", None),
        ),
        instance_count=1,
        gpu_required=1 if bool(getattr(request_in, "gpu_mapping_id", None)) else 0,
    )
    selection = vm_request_placement_service.select_reserved_target_node_for_request(
        session=session,
        request=placement_request,
        start_at=start_at,
        end_at=end_at,
        allow_cohort_rebalance=(
            getattr(request_in, "mode", "") != "quick_template"
        ),
    )
    if not selection.node or not selection.plan.feasible:
        raise BadRequestError(
            selection.plan.summary
            or "No node is available for the requested time window."
        )


def assess_request_window(
    *,
    session: Session,
    current_user,
    request_in: VMRequestWindowAvailabilityRequest,
) -> VMRequestWindowAvailabilityResponse:
    start_at = _normalize_datetime(request_in.start_at)
    end_at = _normalize_datetime(request_in.end_at)
    if not start_at or not end_at:
        raise BadRequestError("A request window is required.")
    if end_at <= start_at:
        raise BadRequestError("end_at must be later than start_at")

    duration_seconds = max((end_at - start_at).total_seconds(), 0)
    duration_hours = int(duration_seconds // 3600)
    if duration_seconds % 3600:
        duration_hours += 1
    duration_days = round(duration_seconds / 86400, 2)

    placement_request = PlacementRequest(
        resource_type=cast(str, request_in.resource_type),
        cpu_cores=int(request_in.cores or 1),
        memory_mb=int(request_in.memory or 512),
        disk_gb=_extract_disk_gb(
            resource_type=cast(str, request_in.resource_type),
            disk_size=request_in.disk_size,
            rootfs_size=request_in.rootfs_size,
        ),
        instance_count=1,
        gpu_required=int(request_in.gpu_required or 0),
    )
    selection = vm_request_placement_service.select_reserved_target_node_for_request(
        session=session,
        request=placement_request,
        start_at=start_at,
        end_at=end_at,
        allow_cohort_rebalance=request_in.mode != "quick_template",
    )
    feasible = bool(selection.node and selection.plan.feasible)
    warnings = list(selection.plan.warnings or [])

    if request_in.mode == "quick_template":
        available_summary = "目前容量可立即建立，系統會保留 3 小時。"
        unavailable_summary = "目前容量不足，暫時無法立即建立快速模板。"
    else:
        available_summary = "此研究期間預估可安排，送出後仍需管理員審核。"
        unavailable_summary = "此研究期間目前容量不足，建議縮短期間或降低規格。"

    status: Literal["available", "limited", "unavailable"]
    if feasible:
        status = "limited" if duration_days >= 30 else "available"
        if status == "limited":
            warnings.append("申請期間較長，核准前建議由管理員確認長期保留影響。")
    else:
        status = "unavailable"

    return VMRequestWindowAvailabilityResponse(
        status=status,
        feasible=feasible,
        start_at=start_at,
        end_at=end_at,
        duration_hours=duration_hours,
        duration_days=duration_days,
        summary=available_summary if feasible else unavailable_summary,
        reason=selection.plan.summary
        or (available_summary if feasible else unavailable_summary),
        selected_node=selection.node,
        placement_strategy=selection.strategy,
        checked_checkpoint_count=max(duration_hours, 1),
        warnings=warnings[:5],
    )


def _build_availability_response(
    *,
    session: Session,
    source_request: VMRequestAvailabilityRequest,
    role: UserRole,
    stack_label: str,
) -> VMRequestAvailabilityResponse:
    tz = _resolve_timezone(source_request.timezone)
    days = max(1, min(int(source_request.days), 14))
    allowed_start, allowed_end = _ALL_DAY_POLICY_WINDOW

    placement_request = _to_placement_request(source_request)
    baseline_nodes, baseline_resources = advisor_service._load_cluster_state()
    cpu_overcommit_ratio, disk_overcommit_ratio = (
        vm_request_placement_service.get_overcommit_ratios(session)
    )
    baseline_capacities = advisor_service._build_node_capacities(
        nodes=baseline_nodes,
        resources=baseline_resources,
        cpu_overcommit_ratio=cpu_overcommit_ratio,
        disk_overcommit_ratio=disk_overcommit_ratio,
    )
    resource_stack_by_node = _build_resource_stack_by_node(baseline_resources)
    hourly_demand = _load_hourly_demand_profile(session=session, timezone=tz)
    pending_pressure = _pending_pressure_ratio(
        session=session,
        baseline_capacities=baseline_capacities,
    )
    effective_resource_type, resource_type_reason = advisor_service._decide_resource_type(
        placement_request
    )
    placement_strategy = vm_request_placement_service.get_placement_strategy(session)
    node_priorities = vm_request_placement_service.get_node_priorities(session)

    now_local = datetime.now(tz)
    start_anchor = now_local.replace(minute=0, second=0, microsecond=0)
    if now_local.minute or now_local.second or now_local.microsecond:
        start_anchor += timedelta(hours=1)
    day_anchor = datetime.combine(now_local.date(), time.min, tzinfo=tz)

    reserved_requests = vm_request_repo.get_approved_vm_requests_overlapping_window(
        session=session,
        window_start=start_anchor,
        window_end=start_anchor + timedelta(days=days),
    )

    slots: list[VMRequestAvailabilitySlot] = []
    per_day: dict[date, list[VMRequestAvailabilitySlot]] = {}

    for day_offset in range(days):
        day_start = day_anchor + timedelta(days=day_offset)
        for hour in range(24):
            slot_start = day_start + timedelta(hours=hour)
            slot_end = slot_start + timedelta(hours=1)
            slot_date = slot_start.date()
            within_policy = _is_hour_within_policy(
                hour=hour,
                allowed_start=allowed_start,
                allowed_end=allowed_end,
            )
            demand_ratio = hourly_demand.get(hour, 0.0)

            if slot_start < start_anchor:
                slot = VMRequestAvailabilitySlot(
                    start_at=slot_start,
                    end_at=slot_end,
                    date=slot_date,
                    hour=hour,
                    within_policy=within_policy,
                    feasible=False,
                    status="unavailable",
                    label="已結束",
                    summary="此時段已過，請選擇目前時間之後的時段。",
                    reasons=["此時段已過，請選擇目前時間之後的時段。"],
                    recommended_nodes=[],
                    placement_strategy=placement_strategy,
                    node_snapshots=_build_slot_node_snapshots(
                        adjusted_nodes=baseline_capacities,
                        plan=None,
                        node_priorities=node_priorities,
                        resource_stack_by_node=resource_stack_by_node,
                        stack_label=stack_label,
                    ),
                )
            elif within_policy:
                reserved_adjusted_nodes = vm_request_placement_service._apply_reserved_requests_to_capacities(
                    baseline_capacities=baseline_capacities,
                    reserved_requests=reserved_requests,
                    at_time=slot_start,
                )
                adjusted_nodes = _adjust_node_capacities_for_slot(
                    baseline_capacities=reserved_adjusted_nodes,
                    demand_ratio=demand_ratio,
                    pending_pressure=pending_pressure,
                )
                plan = vm_request_placement_service.build_plan(
                    session=session,
                    request=placement_request,
                    node_capacities=adjusted_nodes,
                    effective_resource_type=effective_resource_type,
                    resource_type_reason=resource_type_reason,
                    placement_strategy=placement_strategy,
                    node_priorities=node_priorities,
                )
                slot = _slot_from_plan(
                    plan=plan,
                    slot_start=slot_start,
                    slot_end=slot_end,
                    within_policy=True,
                    role=role,
                    demand_ratio=demand_ratio,
                    pending_pressure=pending_pressure,
                    adjusted_nodes=adjusted_nodes,
                    node_priorities=node_priorities,
                    resource_stack_by_node=resource_stack_by_node,
                    stack_label=stack_label,
                    placement_strategy=placement_strategy,
                )
            else:
                slot = VMRequestAvailabilitySlot(
                    start_at=slot_start,
                    end_at=slot_end,
                    date=slot_date,
                    hour=hour,
                    within_policy=False,
                    feasible=False,
                    status="policy_blocked",
                    label="不可申請",
                    summary=_policy_block_summary(role=role, allowed_start=allowed_start, allowed_end=allowed_end),
                    reasons=[_policy_block_summary(role=role, allowed_start=allowed_start, allowed_end=allowed_end)],
                    recommended_nodes=[],
                    placement_strategy=placement_strategy,
                    node_snapshots=_build_slot_node_snapshots(
                        adjusted_nodes=baseline_capacities,
                        plan=None,
                        node_priorities=node_priorities,
                        resource_stack_by_node=resource_stack_by_node,
                        stack_label=stack_label,
                    ),
                )

            slots.append(slot)
            per_day.setdefault(slot_date, []).append(slot)

    days_summary = [_summarize_day(day, day_slots) for day, day_slots in per_day.items()]
    recommended_slots = _pick_recommended_slots(slots)
    feasible_slot_count = sum(1 for slot in slots if slot.status in {"available", "limited"})
    summary = VMRequestAvailabilitySummary(
        timezone=str(tz.key),
        role=str(role.value),
        role_label=_ROLE_LABELS.get(role, str(role.value)),
        policy_window="全天",
        checked_days=days,
        feasible_slot_count=feasible_slot_count,
        recommended_slot_count=len(recommended_slots),
        current_status=(
            "目前規格可安排時段"
            if feasible_slot_count > 0
            else "目前沒有符合容量條件的時段"
        ),
    )

    return VMRequestAvailabilityResponse(
        summary=summary,
        recommended_slots=recommended_slots,
        days=days_summary,
    )


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _resolve_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value or "Asia/Taipei")
    except ZoneInfoNotFoundError as exc:
        raise BadRequestError("Invalid timezone") from exc


def _to_placement_request(request_in: VMRequestAvailabilityRequest) -> PlacementRequest:
    return PlacementRequest(
        resource_type=cast(str, request_in.resource_type),
        cpu_cores=int(request_in.cores),
        memory_mb=int(request_in.memory),
        disk_gb=_extract_disk_gb(
            resource_type=cast(str, request_in.resource_type),
            disk_size=request_in.disk_size,
            rootfs_size=request_in.rootfs_size,
        ),
        instance_count=int(request_in.instance_count),
        gpu_required=int(request_in.gpu_required),
    )


def _extract_disk_gb(
    *,
    resource_type: str,
    disk_size: int | None,
    rootfs_size: int | None,
) -> int:
    disk_gb = int(disk_size or 0) if resource_type == "vm" else int(rootfs_size or 0)
    if disk_gb <= 0:
        return 20 if resource_type == "vm" else 8
    return disk_gb


def _validate_policy_window(
    *,
    role: UserRole,
    start_at: datetime,
    end_at: datetime,
) -> None:
    return None


def _load_hourly_demand_profile(*, session: Session, timezone: ZoneInfo) -> dict[int, float]:
    recent_window_start = datetime.now(UTC) - timedelta(days=30)
    rows = list(
        session.exec(
            select(VMRequest).where(VMRequest.created_at >= recent_window_start)  # type: ignore[operator]
        ).all()
    )
    counts = Counter()
    for row in rows:
        created_at = row.created_at
        if created_at is None:
            continue
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        counts[created_at.astimezone(timezone).hour] += 1

    peak = max(counts.values(), default=0)
    if peak <= 0:
        return dict.fromkeys(range(24), 0.0)
    return {
        hour: round(counts.get(hour, 0) / peak, 4)
        for hour in range(24)
    }


def _pending_pressure_ratio(
    *,
    session: Session,
    baseline_capacities,
) -> float:
    pending_requests = list(
        session.exec(
            select(VMRequest).where(VMRequest.status == VMRequestStatus.pending)
        ).all()
    )
    if not pending_requests or not baseline_capacities:
        return 0.0

    total_alloc_cpu = sum(max(item.allocatable_cpu_cores, 0.0) for item in baseline_capacities)
    total_alloc_mem = sum(max(item.allocatable_memory_bytes, 0) for item in baseline_capacities)
    total_alloc_disk = sum(max(item.allocatable_disk_bytes, 0) for item in baseline_capacities)

    requested_cpu = sum(max(float(item.cores or 0), 0.0) for item in pending_requests)
    requested_mem = sum(max(int(item.memory or 0), 0) * 1024 * 1024 for item in pending_requests)
    requested_disk = sum(
        max(int(item.disk_size or item.rootfs_size or 0), 0) * 1024**3
        for item in pending_requests
    )

    ratios = [
        (requested_cpu / total_alloc_cpu) if total_alloc_cpu > 0 else 0.0,
        (requested_mem / total_alloc_mem) if total_alloc_mem > 0 else 0.0,
        (requested_disk / total_alloc_disk) if total_alloc_disk > 0 else 0.0,
    ]
    return round(min(max(ratios, default=0.0), 1.0), 4)


def _adjust_node_capacities_for_slot(
    *,
    baseline_capacities,
    demand_ratio: float,
    pending_pressure: float,
):
    reserve_ratio = min(0.55, (demand_ratio * 0.28) + (pending_pressure * 0.22))
    adjusted = []
    for item in baseline_capacities:
        clone = item.model_copy(deep=True)
        cpu_scale = max(1.0 - reserve_ratio, 0.45)
        mem_scale = max(1.0 - reserve_ratio, 0.45)
        disk_scale = max(1.0 - (reserve_ratio * 0.7), 0.55)
        clone.allocatable_cpu_cores = round(clone.allocatable_cpu_cores * cpu_scale, 2)
        clone.allocatable_memory_bytes = int(clone.allocatable_memory_bytes * mem_scale)
        clone.allocatable_disk_bytes = int(clone.allocatable_disk_bytes * disk_scale)
        clone.candidate = (
            clone.candidate
            and clone.allocatable_cpu_cores > 0
            and clone.allocatable_memory_bytes > 0
            and clone.allocatable_disk_bytes > 0
        )
        adjusted.append(clone)
    return adjusted


def _slot_from_plan(
    *,
    plan,
    slot_start: datetime,
    slot_end: datetime,
    within_policy: bool,
    role: UserRole,
    demand_ratio: float,
    pending_pressure: float,
    adjusted_nodes,
    node_priorities: dict[str, int],
    resource_stack_by_node: dict[str, Counter[str]],
    stack_label: str,
    placement_strategy: str,
) -> VMRequestAvailabilitySlot:
    reasons = list(plan.rationale or plan.warnings or [])
    recommended_nodes = [item.node for item in plan.placements]
    if plan.feasible:
        status = "limited" if demand_ratio >= 0.6 or pending_pressure >= 0.35 else "available"
        label = "可申請" if status == "available" else "可申請但熱門"
        summary = plan.summary
    elif plan.assigned_instances > 0:
        status = "limited"
        label = "部分可行"
        summary = plan.summary
    else:
        status = "unavailable"
        label = "容量不足"
        summary = plan.summary or "此時段沒有足夠容量。"

    if not reasons:
        reasons = [summary]

    if status == "limited":
        reasons = reasons + [
            f"熱門時段壓力係數 {demand_ratio:.2f}，待審核壓力 {pending_pressure:.2f}。",
        ]

    return VMRequestAvailabilitySlot(
        start_at=slot_start,
        end_at=slot_end,
        date=slot_start.date(),
        hour=slot_start.hour,
        within_policy=within_policy,
        feasible=plan.feasible,
        status=status,
        label=label,
        summary=summary,
        reasons=reasons[:4],
        recommended_nodes=recommended_nodes[:3],
        target_node=plan.recommended_node,
        placement_strategy=placement_strategy,
        node_snapshots=_build_slot_node_snapshots(
            adjusted_nodes=adjusted_nodes,
            plan=plan,
            node_priorities=node_priorities,
            resource_stack_by_node=resource_stack_by_node,
            stack_label=stack_label,
        ),
    )


def _build_resource_stack_by_node(resources) -> dict[str, Counter[str]]:
    stacks: dict[str, Counter[str]] = {}
    for resource in resources:
        node = getattr(resource, "node", None) or "unknown"
        name = getattr(resource, "name", None) or f"{resource.resource_type}-{resource.vmid}"
        counter = stacks.setdefault(node, Counter())
        counter[str(name)] += 1
    return stacks


def _build_slot_node_snapshots(
    *,
    adjusted_nodes,
    plan,
    node_priorities: dict[str, int],
    resource_stack_by_node: dict[str, Counter[str]],
    stack_label: str,
) -> list[VMRequestAvailabilityNodeSnapshot]:
    decisions_by_node = {
        item.node: item for item in (plan.placements if plan else [])
    }
    snapshots: list[VMRequestAvailabilityNodeSnapshot] = []

    for node in adjusted_nodes:
        decision = decisions_by_node.get(node.node)
        remaining_cpu = (
            float(decision.remaining_cpu_cores)
            if decision
            else float(node.allocatable_cpu_cores)
        )
        remaining_memory = (
            int(decision.remaining_memory_bytes)
            if decision
            else int(node.allocatable_memory_bytes)
        )
        remaining_disk = (
            int(decision.remaining_disk_bytes)
            if decision
            else int(node.allocatable_disk_bytes)
        )
        projected_running = int(node.running_resources) + int(
            decision.instance_count if decision else 0
        )
        base_stack = resource_stack_by_node.get(node.node, Counter()).copy()
        if decision and decision.instance_count > 0:
            base_stack[stack_label] += int(decision.instance_count)

        snapshots.append(
            VMRequestAvailabilityNodeSnapshot(
                node=node.node,
                status=node.status,
                candidate=bool(node.candidate),
                priority=int(node_priorities.get(node.node, 5)),
                is_target=bool(decision and decision.instance_count > 0),
                placement_count=int(decision.instance_count if decision else 0),
                running_resources=int(node.running_resources),
                projected_running_resources=projected_running,
                dominant_share=round(
                    _dominant_share(
                        total_cpu=float(node.total_cpu_cores),
                        remaining_cpu=remaining_cpu,
                        total_memory=int(node.total_memory_bytes),
                        remaining_memory=remaining_memory,
                        total_disk=int(node.total_disk_bytes),
                        remaining_disk=remaining_disk,
                    ),
                    4,
                ),
                average_share=round(
                    _average_share(
                        total_cpu=float(node.total_cpu_cores),
                        remaining_cpu=remaining_cpu,
                        total_memory=int(node.total_memory_bytes),
                        remaining_memory=remaining_memory,
                        total_disk=int(node.total_disk_bytes),
                        remaining_disk=remaining_disk,
                    ),
                    4,
                ),
                cpu_share=round(
                    _usage_share(
                        total=float(node.total_cpu_cores),
                        remaining=remaining_cpu,
                    ),
                    4,
                ),
                memory_share=round(
                    _usage_share(
                        total=float(node.total_memory_bytes),
                        remaining=float(remaining_memory),
                    ),
                    4,
                ),
                disk_share=round(
                    _usage_share(
                        total=float(node.total_disk_bytes),
                        remaining=float(remaining_disk),
                    ),
                    4,
                ),
                remaining_cpu_cores=round(remaining_cpu, 2),
                remaining_memory_gb=round(remaining_memory / GIB, 2),
                remaining_disk_gb=round(remaining_disk / GIB, 2),
                vm_stack=[
                    VMRequestAvailabilityStackItem(
                        name=name,
                        count=count,
                        pending=name == stack_label and bool(decision and decision.instance_count > 0),
                    )
                    for name, count in sorted(
                        base_stack.items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                ],
            )
        )

    snapshots.sort(
        key=lambda item: (
            not item.is_target,
            item.priority,
            item.dominant_share,
            item.node,
        )
    )
    return snapshots


def _usage_share(*, total: float, remaining: float) -> float:
    if total <= 0:
        return 0.0
    used = max(total - max(remaining, 0.0), 0.0)
    return min(used / total, 1.0)


def _dominant_share(
    *,
    total_cpu: float,
    remaining_cpu: float,
    total_memory: int,
    remaining_memory: int,
    total_disk: int,
    remaining_disk: int,
) -> float:
    return max(
        _usage_share(total=total_cpu, remaining=remaining_cpu),
        _usage_share(total=float(total_memory), remaining=float(remaining_memory)),
        _usage_share(total=float(total_disk), remaining=float(remaining_disk)),
    )


def _average_share(
    *,
    total_cpu: float,
    remaining_cpu: float,
    total_memory: int,
    remaining_memory: int,
    total_disk: int,
    remaining_disk: int,
) -> float:
    values = [
        _usage_share(total=total_cpu, remaining=remaining_cpu),
        _usage_share(total=float(total_memory), remaining=float(remaining_memory)),
        _usage_share(total=float(total_disk), remaining=float(remaining_disk)),
    ]
    return sum(values) / len(values)


def _policy_block_summary(*, role: UserRole, allowed_start: int, allowed_end: int) -> str:
    return "目前不限制申請時段。"


def _policy_hint(*, role: UserRole) -> str:
    return "此評估未套用時段限制。"


def _summarize_day(day: date, slots: list[VMRequestAvailabilitySlot]) -> VMRequestAvailabilityDay:
    available_hours = [item.hour for item in slots if item.status == "available"]
    limited_hours = [item.hour for item in slots if item.status == "limited"]
    blocked_hours = [item.hour for item in slots if item.status == "policy_blocked"]
    unavailable_hours = [item.hour for item in slots if item.status == "unavailable"]
    best_slots = _pick_recommended_slots(slots)[:3]
    return VMRequestAvailabilityDay(
        date=day,
        available_hours=available_hours,
        limited_hours=limited_hours,
        blocked_hours=blocked_hours,
        unavailable_hours=unavailable_hours,
        slots=slots,
        best_hours=[item.hour for item in best_slots],
    )


def _pick_recommended_slots(slots: list[VMRequestAvailabilitySlot]) -> list[VMRequestAvailabilitySlot]:
    candidates = [item for item in slots if item.status in {"available", "limited"}]
    return sorted(
        candidates,
        key=lambda item: (
            _STATUS_PRIORITY.get(item.status, 99),
            item.hour,
            item.start_at,
        ),
    )[:6]
