from __future__ import annotations

import json
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from math import floor
from time import perf_counter
from typing import Any, cast

from sqlmodel import Session, func, select

from app.ai.pve_advisor.config import settings
from app.ai.pve_advisor.prompt import (
    build_advisor_system_prompt,
    build_advisor_user_prompt,
)
from app.ai.pve_advisor.schemas import (
    AiMetrics,
    MachineCurrentStatus,
    NodeCapacity,
    NodeSnapshot,
    PlacementAdvisorResponse,
    PlacementDecision,
    PlacementPlan,
    PlacementRequest,
    RecommendedMachine,
    ResourceSnapshot,
    ResourceType,
)
from app.ai.utils import apply_thinking_control, safe_int, strip_think_tags
from app.infrastructure.ai.pve_advisor import client
from app.models import AuditAction, AuditLog, VMRequest, VMRequestStatus
from app.repositories import proxmox_config as proxmox_config_repo
from app.services.proxmox import proxmox_service

GIB = 1024**3
MIB = 1024**2


class _BackendTrafficSnapshot:
    def __init__(
        self,
        *,
        sample_size: int = 0,
        window_minutes: int = 60,
        submitted_in_window: int = 0,
        pending_total: int = 0,
        approved_total: int = 0,
        requested_cpu_cores_total: int = 0,
        requested_memory_mb_total: int = 0,
        requested_disk_gb_total: int = 0,
    ) -> None:
        self.sample_size = sample_size
        self.window_minutes = window_minutes
        self.submitted_in_window = submitted_in_window
        self.pending_total = pending_total
        self.approved_total = approved_total
        self.requested_cpu_cores_total = requested_cpu_cores_total
        self.requested_memory_mb_total = requested_memory_mb_total
        self.requested_disk_gb_total = requested_disk_gb_total

    def model_dump(self) -> dict[str, int]:
        return {
            "sample_size": self.sample_size,
            "window_minutes": self.window_minutes,
            "submitted_in_window": self.submitted_in_window,
            "pending_total": self.pending_total,
            "approved_total": self.approved_total,
            "requested_cpu_cores_total": self.requested_cpu_cores_total,
            "requested_memory_mb_total": self.requested_memory_mb_total,
            "requested_disk_gb_total": self.requested_disk_gb_total,
        }


class _AuditSignalSnapshot:
    def __init__(
        self,
        *,
        sample_size: int = 0,
        window_minutes: int = 60,
        recent_total: int = 0,
        create_events: int = 0,
        start_events: int = 0,
        stop_events: int = 0,
        delete_events: int = 0,
        review_events: int = 0,
    ) -> None:
        self.sample_size = sample_size
        self.window_minutes = window_minutes
        self.recent_total = recent_total
        self.create_events = create_events
        self.start_events = start_events
        self.stop_events = stop_events
        self.delete_events = delete_events
        self.review_events = review_events

    def model_dump(self) -> dict[str, int]:
        return {
            "sample_size": self.sample_size,
            "window_minutes": self.window_minutes,
            "recent_total": self.recent_total,
            "create_events": self.create_events,
            "start_events": self.start_events,
            "stop_events": self.stop_events,
            "delete_events": self.delete_events,
            "review_events": self.review_events,
        }


class _ClusterCacheEntry:
    def __init__(
        self,
        cached_at: float,
        nodes: list[NodeSnapshot],
        resources: list[ResourceSnapshot],
    ) -> None:
        self.cached_at = cached_at
        self.nodes = nodes
        self.resources = resources


_cluster_cache: _ClusterCacheEntry | None = None
_cluster_cache_lock = threading.Lock()


async def generate_recommendation(
    *,
    session: Session,
    request: PlacementRequest,
) -> PlacementAdvisorResponse:
    nodes, resources = _load_cluster_state()
    config = proxmox_config_repo.get_proxmox_config(session)
    cpu_overcommit_ratio = (
        float(config.cpu_overcommit_ratio) if config else 1.0
    )
    disk_overcommit_ratio = (
        float(config.disk_overcommit_ratio) if config else 1.0
    )
    backend_traffic = _load_backend_traffic_snapshot(session=session)
    audit_signals = _load_audit_signal_snapshot(session=session)
    node_capacities = _build_node_capacities(
        nodes=nodes,
        resources=resources,
        cpu_overcommit_ratio=cpu_overcommit_ratio,
        disk_overcommit_ratio=disk_overcommit_ratio,
    )
    default_resource_type, default_reason = _decide_resource_type(request)
    rule_based_plan = _build_rule_based_plan(
        request=request,
        node_capacities=node_capacities,
        effective_resource_type=default_resource_type,
        resource_type_reason=default_reason,
    )
    current_status = _build_current_status(node_capacities)

    model_name = settings.VLLM_MODEL_NAME
    if not model_name:
        return _build_response_from_plan(
            plan=rule_based_plan,
            current_status=current_status,
            ai_used=False,
            warning="AI model binding is missing in config/system-ai.json.",
        )

    try:
        ai_decision, metrics = await _generate_ai_decision(
            request=request,
            rule_based_plan=rule_based_plan,
            backend_traffic=backend_traffic,
            audit_signals=audit_signals,
            node_capacities=node_capacities,
        )
        ai_plan = _build_ai_plan_from_decision(
            request=request,
            node_capacities=node_capacities,
            decision=ai_decision,
            fallback_plan=rule_based_plan,
        )
        if ai_plan is None:
            return _build_response_from_plan(
                plan=rule_based_plan,
                current_status=current_status,
                ai_used=False,
                ai_metrics=metrics,
                warning="AI 決策不合法，因此改用基本容量規則決定。",
            )
        return _build_response_from_plan(
            plan=ai_plan,
            current_status=current_status,
            ai_used=True,
            model=model_name,
            ai_metrics=metrics,
            reply_override=str(ai_decision.get("reply") or "").strip() or None,
        )
    except Exception as exc:
        return _build_response_from_plan(
            plan=rule_based_plan,
            current_status=current_status,
            ai_used=False,
            warning=f"AI 呼叫失敗，因此改用基本容量規則決定：{exc}",
        )


async def _generate_ai_decision(
    *,
    request: PlacementRequest,
    rule_based_plan: PlacementPlan,
    backend_traffic: _BackendTrafficSnapshot,
    audit_signals: _AuditSignalSnapshot,
    node_capacities: list[NodeCapacity],
) -> tuple[dict[str, Any], AiMetrics]:
    payload = apply_thinking_control(
        {
            "model": settings.VLLM_MODEL_NAME,
            "messages": [
                {"role": "system", "content": build_advisor_system_prompt()},
                {
                    "role": "user",
                    "content": build_advisor_user_prompt(
                        request=request.model_dump(),
                        rule_based_plan=rule_based_plan.model_dump(),
                        backend_traffic=backend_traffic.model_dump(),
                        audit_signals=audit_signals.model_dump(),
                        node_capacities=[item.model_dump() for item in node_capacities],
                    ),
                },
            ],
            "max_tokens": settings.VLLM_MAX_TOKENS,
            "temperature": settings.VLLM_TEMPERATURE,
            "top_p": settings.VLLM_TOP_P,
            "top_k": settings.VLLM_TOP_K,
            "min_p": settings.VLLM_MIN_P,
            "repetition_penalty": settings.VLLM_REPETITION_PENALTY,
            "response_format": {"type": "json_object"},
        },
        settings.VLLM_ENABLE_THINKING,
    )

    started_at = perf_counter()
    data = await client.create_chat_completion(payload)
    elapsed_seconds = max(perf_counter() - started_at, 0.0)
    usage = data.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
    content = strip_think_tags(str(data["choices"][0]["message"]["content"] or ""))

    return json.loads(content), AiMetrics(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        elapsed_seconds=round(elapsed_seconds, 3),
        tokens_per_second=round(
            (completion_tokens / elapsed_seconds) if elapsed_seconds > 0 else 0.0,
            2,
        ),
    )


def _build_response_from_plan(
    *,
    plan: PlacementPlan,
    current_status: list[MachineCurrentStatus],
    ai_used: bool,
    model: str | None = None,
    warning: str | None = None,
    ai_metrics: AiMetrics | None = None,
    reply_override: str | None = None,
) -> PlacementAdvisorResponse:
    reasons = list(dict.fromkeys(plan.rationale + plan.warnings))
    return PlacementAdvisorResponse(
        reply=reply_override or _build_reply_from_plan(plan),
        machines_to_open=_build_machine_recommendations(plan),
        reasons=reasons,
        current_status=current_status,
        ai_used=ai_used,
        model=model,
        warning=warning,
        ai_metrics=ai_metrics,
    )


def _build_ai_plan_from_decision(
    *,
    request: PlacementRequest,
    node_capacities: list[NodeCapacity],
    decision: dict[str, Any],
    fallback_plan: PlacementPlan,
) -> PlacementPlan | None:
    raw_resource_type = str(
        decision.get("effective_resource_type") or fallback_plan.effective_resource_type
    ).lower()
    if raw_resource_type not in {"lxc", "vm"}:
        return None

    effective_resource_type = cast(ResourceType, raw_resource_type)
    effective_cpu = _effective_cpu_cores(request, effective_resource_type)
    effective_memory = _effective_memory_bytes(request, effective_resource_type)
    disk_bytes = request.disk_gb * GIB
    capacity_map = {item.node: item for item in node_capacities}

    raw_machines = decision.get("machines_to_open") or []
    if not isinstance(raw_machines, list):
        return None

    placements: list[PlacementDecision] = []
    machine_reasons: list[str] = []
    total_instances = 0

    for item in raw_machines:
        if not isinstance(item, dict):
            return None

        node = str(item.get("node") or "").strip()
        instance_count = safe_int(item.get("instance_count"), minimum=0)
        reason = str(item.get("reason") or "").strip()
        if not node or instance_count <= 0:
            continue

        capacity = capacity_map.get(node)
        if capacity is None:
            return None

        node_fit = _fit_count(
            capacity,
            cores=effective_cpu,
            memory_bytes=effective_memory,
            disk_bytes=disk_bytes,
        )
        if request.gpu_required > 0 and capacity.gpu_count < request.gpu_required:
            return None
        if instance_count > node_fit:
            return None

        total_instances += instance_count
        if total_instances > request.instance_count:
            return None

        if reason:
            machine_reasons.append(reason)

        placements.append(
            PlacementDecision(
                node=node,
                instance_count=instance_count,
                cpu_cores_reserved=round(instance_count * effective_cpu, 2),
                memory_bytes_reserved=instance_count * effective_memory,
                disk_bytes_reserved=instance_count * disk_bytes,
                remaining_cpu_cores=round(
                    max(capacity.allocatable_cpu_cores - (instance_count * effective_cpu), 0.0),
                    2,
                ),
                remaining_memory_bytes=max(
                    capacity.allocatable_memory_bytes - (instance_count * effective_memory),
                    0,
                ),
                remaining_disk_bytes=max(
                    capacity.allocatable_disk_bytes - (instance_count * disk_bytes),
                    0,
                ),
            )
        )

    if total_instances == 0 and fallback_plan.assigned_instances > 0:
        return None

    raw_reasons = decision.get("reasons") or []
    reasons = [
        str(item).strip()
        for item in raw_reasons
        if str(item).strip()
    ] if isinstance(raw_reasons, list) else []
    if not reasons:
        reasons = machine_reasons or fallback_plan.rationale

    warnings = list(fallback_plan.warnings)
    remaining = max(request.instance_count - total_instances, 0)
    if remaining > 0:
        warnings.append(
            f"AI 僅分配 {total_instances} / {request.instance_count} 台，剩餘需求改由容量限制保留。"
        )

    placements.sort(key=lambda item: (-item.instance_count, item.node))
    summary = _build_summary_text(
        request=request,
        placement_decisions=placements,
        effective_resource_type=effective_resource_type,
        assigned=total_instances,
        remaining=remaining,
    )

    return PlacementPlan(
        feasible=remaining == 0,
        requested_resource_type=request.resource_type,
        effective_resource_type=effective_resource_type,
        resource_type_reason=_resource_type_reason_from_choice(
            request=request,
            effective_resource_type=effective_resource_type,
        ),
        assigned_instances=total_instances,
        unassigned_instances=remaining,
        recommended_node=placements[0].node if placements else None,
        summary=summary,
        rationale=reasons,
        warnings=warnings,
        placements=placements,
        candidate_nodes=node_capacities,
    )





def _load_cluster_state() -> tuple[list[NodeSnapshot], list[ResourceSnapshot]]:
    cached = _get_cached_cluster_state()
    if cached is not None:
        return cached.nodes, cached.resources

    gpu_map = settings.parsed_backend_node_gpu_map
    nodes = [
        NodeSnapshot(
            node=str(item.get("node") or "unknown"),
            status=str(item.get("status") or "unknown").lower(),
            cpu_ratio=float(item.get("cpu") or 0.0),
            maxcpu=int(item.get("maxcpu") or 0),
            mem_bytes=int(item.get("mem") or 0),
            maxmem_bytes=int(item.get("maxmem") or 0),
            disk_bytes=int(item.get("disk") or 0),
            maxdisk_bytes=int(item.get("maxdisk") or 0),
            uptime=_optional_int(item.get("uptime")),
            gpu_count=gpu_map.get(str(item.get("node") or "unknown"), 0),
            current_loadavg_1=_parse_loadavg_1(item.get("loadavg")),
            average_loadavg_1=_parse_loadavg_1(
                item.get("avg_load")
                or item.get("avgload")
                or item.get("average_loadavg")
            ),
        )
        for item in proxmox_service.list_nodes()
    ]
    resources = [
        ResourceSnapshot(
            vmid=int(item.get("vmid") or 0),
            name=str(item.get("name") or ""),
            resource_type=str(item.get("type") or "unknown"),
            node=str(item.get("node") or "unknown"),
            status=str(item.get("status") or "unknown").lower(),
        )
        for item in proxmox_service.list_all_resources()
        if item.get("template") != 1 and str(item.get("type") or "") in {"lxc", "qemu", "vm"}
    ]

    _set_cached_cluster_state(nodes=nodes, resources=resources)
    return nodes, resources


def _load_backend_traffic_snapshot(*, session: Session) -> _BackendTrafficSnapshot:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=settings.backend_traffic_window_minutes)
    recent_requests = list(
        session.exec(
            select(VMRequest)
            .order_by(VMRequest.created_at.desc())  # type: ignore[union-attr]
            .limit(settings.backend_traffic_sample_limit)
        ).all()
    )
    pending_total = session.exec(
        select(func.count()).select_from(VMRequest).where(VMRequest.status == VMRequestStatus.pending)
    ).one()
    approved_total = session.exec(
        select(func.count()).select_from(VMRequest).where(
            VMRequest.status == VMRequestStatus.approved
        )
    ).one()

    submitted = 0
    requested_cpu = 0
    requested_memory_mb = 0
    requested_disk_gb = 0
    for item in recent_requests:
        created_at = item.created_at
        if created_at is not None:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if created_at >= window_start:
                submitted += 1
        requested_cpu += max(int(item.cores or 0), 0)
        requested_memory_mb += max(int(item.memory or 0), 0)
        requested_disk_gb += max(int(item.disk_size or item.rootfs_size or 0), 0)

    return _BackendTrafficSnapshot(
        sample_size=len(recent_requests),
        window_minutes=settings.backend_traffic_window_minutes,
        submitted_in_window=submitted,
        pending_total=int(pending_total or 0),
        approved_total=int(approved_total or 0),
        requested_cpu_cores_total=requested_cpu,
        requested_memory_mb_total=requested_memory_mb,
        requested_disk_gb_total=requested_disk_gb,
    )


def _load_audit_signal_snapshot(*, session: Session) -> _AuditSignalSnapshot:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=settings.audit_log_window_minutes)
    rows = list(
        session.exec(
            select(AuditLog)
            .where(AuditLog.created_at >= window_start)  # type: ignore[operator]
            .order_by(AuditLog.created_at.desc())
            .limit(settings.audit_log_sample_limit)
        ).all()
    )
    counts = Counter(
        str(row.action.value if isinstance(row.action, AuditAction) else row.action)
        for row in rows
    )
    return _AuditSignalSnapshot(
        sample_size=len(rows),
        window_minutes=settings.audit_log_window_minutes,
        recent_total=len(rows),
        create_events=counts["vm_create"] + counts["lxc_create"],
        start_events=counts["resource_start"],
        stop_events=counts["resource_stop"] + counts["resource_shutdown"] + counts["resource_reset"],
        delete_events=counts["resource_delete"],
        review_events=counts["vm_request_review"],
    )


def _build_node_capacities(
    *,
    nodes: list[NodeSnapshot],
    resources: list[ResourceSnapshot],
    cpu_overcommit_ratio: float = 1.0,
    disk_overcommit_ratio: float = 1.0,
) -> list[NodeCapacity]:
    running_counter = Counter(
        resource.node for resource in resources if resource.status == "running"
    )
    capacities: list[NodeCapacity] = []
    for node in nodes:
        running_resources = running_counter.get(node.node, 0)
        guest_soft_limit = _guest_soft_limit(node.maxcpu)
        guest_pressure_ratio = _guest_pressure_ratio(running_resources, node.maxcpu)
        used_cpu = max(float(node.maxcpu) * node.cpu_ratio, 0.0)
        effective_total_cpu = max(float(node.maxcpu) * max(cpu_overcommit_ratio, 1.0), 0.0)
        raw_available_cpu = max(effective_total_cpu - used_cpu, 0.0)
        raw_available_memory = _raw_available_bytes(node.mem_bytes, node.maxmem_bytes)
        effective_total_disk = max(
            int(float(node.maxdisk_bytes) * max(disk_overcommit_ratio, 1.0)),
            0,
        )
        raw_available_disk = max(effective_total_disk - node.disk_bytes, 0)
        allocatable_cpu = _safe_available_float(raw_available_cpu, int(effective_total_cpu))
        allocatable_memory = _safe_available_int(raw_available_memory, node.maxmem_bytes)
        allocatable_disk = _safe_available_int(raw_available_disk, effective_total_disk)
        guest_overloaded = guest_pressure_ratio >= settings.guest_pressure_threshold

        capacities.append(
            NodeCapacity(
                node=node.node,
                status=node.status,
                gpu_count=node.gpu_count,
                running_resources=running_resources,
                guest_soft_limit=guest_soft_limit,
                guest_pressure_ratio=guest_pressure_ratio,
                guest_overloaded=guest_overloaded,
                candidate=(
                    node.status == "online"
                    and allocatable_cpu > 0
                    and allocatable_memory > 0
                    and allocatable_disk > 0
                    and not guest_overloaded
                ),
                cpu_ratio=node.cpu_ratio,
                memory_ratio=_ratio(node.mem_bytes, node.maxmem_bytes),
                disk_ratio=_ratio(node.disk_bytes, node.maxdisk_bytes),
                total_cpu_cores=round(effective_total_cpu, 2),
                allocatable_cpu_cores=allocatable_cpu,
            total_memory_bytes=node.maxmem_bytes,
            allocatable_memory_bytes=allocatable_memory,
            total_disk_bytes=effective_total_disk,
            allocatable_disk_bytes=allocatable_disk,
            current_loadavg_1=node.current_loadavg_1,
            average_loadavg_1=node.average_loadavg_1,
        )
        )

    return sorted(capacities, key=lambda item: item.node)


def _build_rule_based_plan(
    *,
    request: PlacementRequest,
    node_capacities: list[NodeCapacity],
    effective_resource_type: ResourceType,
    resource_type_reason: str,
) -> PlacementPlan:
    working_nodes = [item.model_copy(deep=True) for item in node_capacities]
    required_cpu = _effective_cpu_cores(request, effective_resource_type)
    required_memory = _effective_memory_bytes(request, effective_resource_type)
    required_disk = request.disk_gb * GIB
    placements: dict[str, int] = {item.node: 0 for item in working_nodes}
    remaining = request.instance_count

    while remaining > 0:
        candidates = [
            item
            for item in working_nodes
            if item.candidate
            and _can_fit(
                item,
                cores=required_cpu,
                memory_bytes=required_memory,
                disk_bytes=required_disk,
                gpu_required=request.gpu_required,
            )
        ]
        if not candidates:
            break

        chosen = _choose_node(
            nodes=candidates,
            placements=placements,
            cores=required_cpu,
            memory_bytes=required_memory,
            disk_bytes=required_disk,
        )
        placements[chosen.node] += 1
        chosen.allocatable_cpu_cores = max(chosen.allocatable_cpu_cores - required_cpu, 0.0)
        chosen.allocatable_memory_bytes = max(
            chosen.allocatable_memory_bytes - required_memory,
            0,
        )
        chosen.allocatable_disk_bytes = max(chosen.allocatable_disk_bytes - required_disk, 0)
        chosen.running_resources += 1
        chosen.guest_pressure_ratio = _guest_pressure_ratio(
            chosen.running_resources,
            int(chosen.total_cpu_cores),
        )
        chosen.guest_overloaded = (
            chosen.guest_pressure_ratio >= settings.guest_pressure_threshold
        )
        chosen.candidate = (
            chosen.status == "online"
            and chosen.allocatable_cpu_cores > 0
            and chosen.allocatable_memory_bytes > 0
            and chosen.allocatable_disk_bytes > 0
            and not chosen.guest_overloaded
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

    return PlacementPlan(
        feasible=remaining == 0,
        requested_resource_type=request.resource_type,
        effective_resource_type=effective_resource_type,
        resource_type_reason=resource_type_reason,
        assigned_instances=assigned,
        unassigned_instances=remaining,
        recommended_node=placement_decisions[0].node if placement_decisions else None,
        summary=_build_summary_text(
            request=request,
            placement_decisions=placement_decisions,
            effective_resource_type=effective_resource_type,
            assigned=assigned,
            remaining=remaining,
        ),
        rationale=_build_rationale(
            request=request,
            placement_decisions=placement_decisions,
            effective_resource_type=effective_resource_type,
            node_capacities=node_capacities,
        ),
        warnings=_build_warnings(
            node_capacities=node_capacities,
            request=request,
            effective_resource_type=effective_resource_type,
            remaining=remaining,
        ),
        placements=placement_decisions,
        candidate_nodes=node_capacities,
    )


def _build_machine_recommendations(plan: PlacementPlan) -> list[RecommendedMachine]:
    machines: list[RecommendedMachine] = []
    primary_reason = plan.rationale[0] if plan.rationale else plan.resource_type_reason
    for item in plan.placements:
        reason = next(
            (entry for entry in plan.rationale if item.node in entry),
            primary_reason,
        )
        machines.append(
            RecommendedMachine(
                node=item.node,
                resource_type=plan.effective_resource_type,
                instance_count=item.instance_count,
                reason=reason,
            )
        )
    return machines


def _build_current_status(node_capacities: list[NodeCapacity]) -> list[MachineCurrentStatus]:
    return [
        MachineCurrentStatus(
            node=item.node,
            status=item.status,
            candidate=item.candidate,
            running_resources=item.running_resources,
            cpu_usage_ratio=round(item.cpu_ratio, 4),
            memory_usage_ratio=round(item.memory_ratio, 4),
            disk_usage_ratio=round(item.disk_ratio, 4),
            allocatable_cpu_cores=round(item.allocatable_cpu_cores, 2),
            allocatable_memory_gb=round(item.allocatable_memory_bytes / GIB, 2),
            allocatable_disk_gb=round(item.allocatable_disk_bytes / GIB, 2),
            gpu_count=item.gpu_count,
        )
        for item in node_capacities
    ]


def _build_reply_from_plan(plan: PlacementPlan) -> str:
    if not plan.placements:
        return plan.summary
    distribution = ", ".join(f"{item.node} x{item.instance_count}" for item in plan.placements)
    return f"{plan.summary} 建議開啟 {distribution}。"


def _build_warnings(
    *,
    node_capacities: list[NodeCapacity],
    request: PlacementRequest,
    effective_resource_type: ResourceType,
    remaining: int,
) -> list[str]:
    warnings: list[str] = []
    overloaded = [item.node for item in node_capacities if item.guest_overloaded]
    if overloaded:
        warnings.append(f"以下節點 guest 壓力偏高，已降低優先權：{', '.join(overloaded)}。")
    if request.gpu_required > 0 and not any(
        item.gpu_count >= request.gpu_required for item in node_capacities
    ):
        warnings.append(f"目前沒有節點滿足每台至少 {request.gpu_required} 張 GPU 的需求。")
    if request.resource_type != effective_resource_type:
        warnings.append(
            f"原始需求為 {request.resource_type.upper()}，已改用 {effective_resource_type.upper()} 進行評估。"
        )
    if remaining > 0:
        warnings.append(f"仍有 {remaining} 台尚未分配，表示目前叢集剩餘容量不足。")
    return warnings


def _build_rationale(
    *,
    request: PlacementRequest,
    placement_decisions: list[PlacementDecision],
    effective_resource_type: ResourceType,
    node_capacities: list[NodeCapacity],
) -> list[str]:
    capacity_map = {item.node: item for item in node_capacities}
    reasons = [
        _resource_type_summary(
            requested_type=request.resource_type,
            effective_type=effective_resource_type,
            gpu_required=request.gpu_required,
        )
    ]
    for item in placement_decisions:
        baseline = capacity_map.get(item.node)
        if baseline is None:
            continue
        reasons.append(
            f"節點 {item.node} 分配後仍保留 "
            f"{item.remaining_cpu_cores:.2f} vCPU、"
            f"{item.remaining_memory_bytes / GIB:.1f} GiB RAM、"
            f"{item.remaining_disk_bytes / GIB:.1f} GiB Disk，"
            f"目前狀態為 {baseline.status}。"
        )
    return reasons


def _build_summary_text(
    *,
    request: PlacementRequest,
    placement_decisions: list[PlacementDecision],
    effective_resource_type: ResourceType,
    assigned: int,
    remaining: int,
) -> str:
    request_label = _request_label(request)
    if not placement_decisions:
        return f"{request_label} 目前找不到可承載的 PVE 節點。"

    distribution = ", ".join(f"{item.node} x{item.instance_count}" for item in placement_decisions)
    if remaining == 0:
        return (
            f"{request_label} 可完整分配 {assigned} 台 "
            f"{effective_resource_type.upper()}，建議節點分布為 {distribution}。"
        )
    return (
        f"{request_label} 目前只能分配 {assigned} / {request.instance_count} 台 "
        f"{effective_resource_type.upper()}，暫定節點分布為 {distribution}。"
    )


def _decide_resource_type(request: PlacementRequest) -> tuple[ResourceType, str]:
    if request.resource_type == "lxc":
        if request.gpu_required > 0:
            return (
                "vm",
                "需求包含 GPU，為避免 LXC 對驅動與裝置直通的限制，改以 VM 評估。",
            )
        return (
            "lxc",
            "需求為 Linux 容器且未要求 GPU，優先以 LXC 評估以保留較高密度。",
        )
    return (
        "vm",
        "需求指定 VM，將以較完整隔離與作業系統相容性進行評估。",
    )


def _resource_type_summary(
    *,
    requested_type: ResourceType,
    effective_type: ResourceType,
    gpu_required: int,
) -> str:
    if requested_type != effective_type:
        return (
            f"原始需求為 {requested_type.upper()}，因 GPU 需求為 {gpu_required}，"
            f"本次改以 {effective_type.upper()} 進行分配。"
        )
    if effective_type == "lxc":
        return "本次以 LXC 評估，因為資源密度較高且符合一般 Linux 容器需求。"
    return "本次以 VM 評估，因為隔離性與相容性較完整。"


def _resource_type_reason_from_choice(
    *,
    request: PlacementRequest,
    effective_resource_type: ResourceType,
) -> str:
    if request.resource_type == effective_resource_type:
        return _decide_resource_type(request)[1]
    return f"AI 改用 {effective_resource_type.upper()}，以符合整體相容性與容量條件。"


def _request_label(request: PlacementRequest) -> str:
    return f"{request.resource_type.upper()} 需求"


def _get_cached_cluster_state() -> _ClusterCacheEntry | None:
    with _cluster_cache_lock:
        if _cluster_cache is None:
            return None
        age = time.monotonic() - _cluster_cache.cached_at
        if age > settings.source_cache_ttl_seconds:
            return None
        return _cluster_cache


def _set_cached_cluster_state(
    *,
    nodes: list[NodeSnapshot],
    resources: list[ResourceSnapshot],
) -> None:
    if settings.source_cache_ttl_seconds <= 0:
        return

    with _cluster_cache_lock:
        global _cluster_cache
        _cluster_cache = _ClusterCacheEntry(
            cached_at=time.monotonic(),
            nodes=nodes,
            resources=resources,
        )


def _choose_node(
    *,
    nodes: list[NodeCapacity],
    placements: dict[str, int],
    cores: float,
    memory_bytes: int,
    disk_bytes: int,
) -> NodeCapacity:
    return max(
        nodes,
        key=lambda item: (
            _fit_count(item, cores=cores, memory_bytes=memory_bytes, disk_bytes=disk_bytes),
            _weighted_headroom_score(
                item,
                cores=cores,
                memory_bytes=memory_bytes,
                disk_bytes=disk_bytes,
            ),
            -placements[item.node],
            -item.guest_pressure_ratio,
        ),
    )


def _fit_count(
    node: NodeCapacity,
    *,
    cores: float,
    memory_bytes: int,
    disk_bytes: int,
) -> int:
    cpu_fit = floor(node.allocatable_cpu_cores / float(cores)) if cores > 0 else 0
    memory_fit = floor(node.allocatable_memory_bytes / memory_bytes) if memory_bytes > 0 else 0
    disk_fit = floor(node.allocatable_disk_bytes / disk_bytes) if disk_bytes > 0 else 0
    guest_fit = max(node.guest_soft_limit - node.running_resources, 0)
    return max(min(cpu_fit, memory_fit, disk_fit, guest_fit), 0)


def _weighted_headroom_score(
    node: NodeCapacity,
    *,
    cores: float,
    memory_bytes: int,
    disk_bytes: int,
) -> float:
    cpu_total = max(node.total_cpu_cores, 1.0)
    memory_total = max(float(node.total_memory_bytes), 1.0)
    disk_total = max(float(node.total_disk_bytes), 1.0)
    guest_total = max(float(node.guest_soft_limit), 1.0)

    cpu_headroom = max(node.allocatable_cpu_cores - cores, 0.0) / cpu_total
    memory_headroom = max(node.allocatable_memory_bytes - memory_bytes, 0) / memory_total
    disk_headroom = max(node.allocatable_disk_bytes - disk_bytes, 0) / disk_total
    guest_headroom = max(node.guest_soft_limit - node.running_resources - 1, 0) / guest_total

    return (
        (settings.placement_weight_cpu * cpu_headroom)
        + (settings.placement_weight_memory * memory_headroom)
        + (settings.placement_weight_disk * disk_headroom)
        + (settings.placement_weight_guest * guest_headroom)
    )


def _can_fit(
    node: NodeCapacity,
    *,
    cores: float,
    memory_bytes: int,
    disk_bytes: int,
    gpu_required: int,
) -> bool:
    return (
        node.allocatable_cpu_cores >= cores
        and node.allocatable_memory_bytes >= memory_bytes
        and node.allocatable_disk_bytes >= disk_bytes
        and node.gpu_count >= gpu_required
        and node.running_resources < node.guest_soft_limit
    )


def _effective_cpu_cores(request: PlacementRequest, resource_type: ResourceType) -> float:
    requested = float(request.cpu_cores)
    hypervisor_overhead = 0.25 if resource_type == "vm" else 0.0
    return round(requested + hypervisor_overhead, 2)


def _effective_memory_bytes(request: PlacementRequest, resource_type: ResourceType) -> int:
    base = request.memory_mb * MIB
    hypervisor_overhead = 256 * MIB if resource_type == "vm" else 0
    return base + hypervisor_overhead


def _guest_soft_limit(maxcpu: int) -> int:
    return max(int(maxcpu * settings.guest_per_core_limit), 1)


def _guest_pressure_ratio(running_resources: int, maxcpu: int) -> float:
    guest_limit = _guest_soft_limit(maxcpu)
    if guest_limit <= 0:
        return 0.0
    return max(float(running_resources) / float(guest_limit), 0.0)


def _raw_available_cpu(node: NodeSnapshot) -> float:
    used_cpu = max(float(node.maxcpu) * node.cpu_ratio, 0.0)
    return max(float(node.maxcpu) - used_cpu, 0.0)


def _raw_available_bytes(used: int, total: int) -> int:
    return max(total - used, 0)


def _safe_available_float(raw_available: float, total: int) -> float:
    reserve = float(total) * settings.placement_headroom_ratio
    return max(raw_available - reserve, 0.0)


def _safe_available_int(raw_available: int, total: int) -> int:
    reserve = int(total * settings.placement_headroom_ratio)
    return max(raw_available - reserve, 0)


def _ratio(used: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return max(float(used) / float(total), 0.0)


def _optional_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_loadavg_1(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if parsed >= 0 else None
    if isinstance(value, (list, tuple)) and value:
        return _parse_loadavg_1(value[0])
    text = str(value).strip()
    if not text:
        return None
    separators = [",", " ", "/"]
    for separator in separators:
        if separator in text:
            first = next((part for part in text.split(separator) if part.strip()), "")
            return _parse_loadavg_1(first)
    try:
        parsed = float(text)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
