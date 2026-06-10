import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from app.core.authorizers import (
    can_auto_approve_vm_request,
    require_immediate_vm_request_access,
    require_vm_request_access,
    require_vm_request_cancel,
    require_vm_request_review,
)
from app.core.security import encrypt_value
from app.exceptions import (
    BadRequestError,
    NotFoundError,
    ProvisioningError,
)
from app.infrastructure.worker import submit_sync
from app.models import VMMigrationStatus, VMRequest, VMRequestStatus
from app.repositories import vm_request as vm_request_repo
from app.schemas import (
    VMRequestCreate,
    VMRequestPublic,
    VMRequestReview,
    VMRequestReviewContext,
    VMRequestReviewNodeScore,
    VMRequestReviewOverlapItem,
    VMRequestReviewProjectedNode,
    VMRequestReviewRuntimeResource,
    VMRequestsPublic,
)
from app.services.proxmox import proxmox_service
from app.services.scheduling import vm_request_schedule_service
from app.services.user import audit_service
from app.services.vm import (
    vm_request_availability_service,
    vm_request_placement_service,
)
from app.services.vm.placement_service import CurrentPlacementSelection

logger = logging.getLogger(__name__)

QUICK_TEMPLATE_DURATION_HOURS = 3
QUICK_TEMPLATE_MAX_ACTIVE_PER_USER = 1
QUICK_TEMPLATE_MAX_CREATED_PER_24H = 3
QUICK_TEMPLATE_MAX_CORES = 2
QUICK_TEMPLATE_MAX_MEMORY_MB = 4096
QUICK_TEMPLATE_MAX_DISK_GB = 32
QUICK_TEMPLATE_ALLOWED_SLUGS = frozenset(
    {
        "postgresql",
        "mongodb",
        "grafana",
        "homepage",
        "n8n",
        "wordpress",
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _to_public(req: VMRequest, user_override=None) -> VMRequestPublic:
    user = user_override or req.user
    return VMRequestPublic(
        id=req.id,
        user_id=req.user_id,
        user_email=user.email if user else None,
        user_full_name=user.full_name if user else None,
        reason=req.reason,
        resource_type=req.resource_type,
        request_kind=req.request_kind,
        hostname=req.hostname,
        cores=req.cores,
        memory=req.memory,
        storage=req.storage,
        environment_type=req.environment_type,
        os_info=req.os_info,
        expiry_date=req.expiry_date,
        start_at=req.start_at,
        end_at=req.end_at,
        ostemplate=req.ostemplate,
        rootfs_size=req.rootfs_size,
        template_id=req.template_id,
        disk_size=req.disk_size,
        username=req.username,
        status=req.status,
        service_template_slug=req.service_template_slug,
        service_template_script_path=req.service_template_script_path,
        reviewer_id=req.reviewer_id,
        review_comment=req.review_comment,
        reviewed_at=req.reviewed_at,
        vmid=req.vmid,
        assigned_node=req.assigned_node,
        desired_node=req.desired_node,
        actual_node=req.actual_node,
        placement_strategy_used=req.placement_strategy_used,
        migration_status=req.migration_status,
        migration_error=req.migration_error,
        migration_pinned=req.migration_pinned,
        resource_warning=req.resource_warning,
        rebalance_epoch=req.rebalance_epoch,
        last_rebalanced_at=req.last_rebalanced_at,
        last_migrated_at=req.last_migrated_at,
        created_at=req.created_at,
    )


def _approve_and_place(
    *,
    session: Session,
    db_request: VMRequest,
    reviewer_id: uuid.UUID,
) -> CurrentPlacementSelection | None:
    """Approve a request and compute its placement.

    Shared helper used by both ``create()`` (auto-approve) and ``review()``.
    The caller is responsible for committing the session.
    """
    start_at = db_request.start_at
    end_at = db_request.end_at
    if start_at and start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=UTC)
    if end_at and end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=UTC)

    # For requests with a finite end_at, lock overlapping requests for the window.
    if start_at and end_at:
        locked_requests = vm_request_repo.lock_overlapping_vm_requests_for_window(
            session=session,
            window_start=start_at,
            window_end=end_at,
        )
    elif start_at:
        # Infinite end_at -- use a far-future sentinel so that overlapping lock
        # captures everything from start_at onward.
        far_future = start_at + timedelta(days=3650)
        locked_requests = vm_request_repo.lock_overlapping_vm_requests_for_window(
            session=session,
            window_start=start_at,
            window_end=far_future,
        )
    else:
        locked_requests = []

    vm_request_repo.update_vm_request_status(
        session=session,
        db_request=db_request,
        status=VMRequestStatus.approved,
        reviewer_id=reviewer_id,
        review_comment=None,
        assigned_node=None,
        desired_node=None,
        actual_node=None,
        placement_strategy_used=None,
        migration_status=VMMigrationStatus.idle,
        migration_error=None,
        commit=False,
    )

    if db_request.request_kind == "quick_template":
        reserved_requests = [
            item
            for item in locked_requests
            if item.id != db_request.id and item.status == VMRequestStatus.approved
        ]
        selection = vm_request_placement_service.select_reserved_target_node(
            session=session,
            db_request=db_request,
            reserved_requests=reserved_requests,
        )
        if not selection or not selection.node:
            raise BadRequestError(
                "No node is available for the quick template time window."
            )
        vm_request_repo.update_vm_request_provisioning(
            session=session,
            db_request=db_request,
            vmid=db_request.vmid,
            assigned_node=selection.node,
            desired_node=selection.node,
            actual_node=db_request.actual_node,
            placement_strategy_used=selection.strategy,
            migration_status=(
                VMMigrationStatus.pending
                if db_request.vmid is not None
                and db_request.actual_node
                and db_request.actual_node != selection.node
                else VMMigrationStatus.idle
            ),
            migration_error=None,
            commit=False,
        )
        return selection

    approved_requests = [
        item
        for item in locked_requests
        if item.id != db_request.id and item.status == VMRequestStatus.approved
    ]
    approved_requests.append(db_request)

    selections = vm_request_placement_service.rebuild_reserved_assignments(
        session=session,
        requests=approved_requests,
    )
    for request in approved_requests:
        selection = selections.get(request.id)
        if not selection or not selection.node:
            raise BadRequestError(
                "No node is available for the requested time window after applying reservations."
            )
        vm_request_repo.update_vm_request_provisioning(
            session=session,
            db_request=request,
            vmid=request.vmid,
            assigned_node=selection.node,
            desired_node=selection.node,
            actual_node=request.actual_node,
            placement_strategy_used=selection.strategy,
            migration_status=(
                VMMigrationStatus.pending
                if request.vmid is not None
                and request.actual_node
                and request.actual_node != selection.node
                else VMMigrationStatus.idle
            ),
            migration_error=None,
            commit=False,
        )

    return selections.get(db_request.id)


def _prepare_quick_template_request(
    *,
    session: Session,
    request_in: VMRequestCreate,
    user,
    now: datetime,
) -> None:
    if request_in.resource_type != "lxc":
        raise BadRequestError("Quick templates currently support LXC only")

    slug = str(request_in.service_template_slug or "").strip().lower()
    if not slug:
        raise BadRequestError("Quick template mode requires a service template")
    if slug not in QUICK_TEMPLATE_ALLOWED_SLUGS:
        raise BadRequestError("This service template is not enabled for quick use")

    if request_in.gpu_mapping_id:
        raise BadRequestError("Quick template mode does not support GPU allocation")
    if not request_in.rootfs_size:
        raise BadRequestError("Quick template mode requires rootfs_size")
    if int(request_in.cores or 0) > QUICK_TEMPLATE_MAX_CORES:
        raise BadRequestError("Quick template CPU exceeds the allowed limit")
    if int(request_in.memory or 0) > QUICK_TEMPLATE_MAX_MEMORY_MB:
        raise BadRequestError("Quick template memory exceeds the allowed limit")
    if int(request_in.rootfs_size or 0) > QUICK_TEMPLATE_MAX_DISK_GB:
        raise BadRequestError("Quick template disk size exceeds the allowed limit")

    active_count = vm_request_repo.count_quick_template_requests_for_user(
        session=session,
        user_id=user.id,
        active_at=now,
    )
    if active_count >= QUICK_TEMPLATE_MAX_ACTIVE_PER_USER:
        raise BadRequestError("You already have an active quick template")

    recent_count = vm_request_repo.count_quick_template_requests_for_user(
        session=session,
        user_id=user.id,
        since=now - timedelta(hours=24),
    )
    if recent_count >= QUICK_TEMPLATE_MAX_CREATED_PER_24H:
        raise BadRequestError("Quick template daily limit reached")

    request_in.service_template_slug = slug
    request_in.service_template_script_path = (
        request_in.service_template_script_path or f"ct/{slug}.sh"
    )
    request_in.start_at = now
    request_in.end_at = now + timedelta(hours=QUICK_TEMPLATE_DURATION_HOURS)
    request_in.gpu_mapping_id = None


def create(
    *, session: Session, request_in: VMRequestCreate, user
) -> VMRequestPublic:
    if request_in.resource_type not in ("lxc", "vm"):
        raise BadRequestError("resource_type must be 'lxc' or 'vm'")
    if request_in.resource_type == "lxc" and not request_in.ostemplate:
        raise BadRequestError("LXC request requires ostemplate")
    if request_in.resource_type == "vm" and (
        not request_in.template_id or not request_in.username
    ):
        raise BadRequestError("VM request requires template_id and username")

    # ---------- mode validation ----------
    mode = getattr(request_in, "mode", "scheduled") or "scheduled"

    if mode == "quick_template":
        now = _utc_now()
        _prepare_quick_template_request(
            session=session,
            request_in=request_in,
            user=user,
            now=now,
        )
    elif mode == "immediate":
        require_immediate_vm_request_access(user)
        # Set start_at to now; end_at can be None (infinite) or user-specified.
        request_in.start_at = _utc_now()
        if request_in.end_at is not None:
            end_at = request_in.end_at
            if end_at.tzinfo is None:
                end_at = end_at.replace(tzinfo=UTC)
            if end_at <= request_in.start_at:
                raise BadRequestError("end_at must be later than start_at")
    else:
        # scheduled mode -- both start_at and end_at are required
        if request_in.start_at is None or request_in.end_at is None:
            raise BadRequestError(
                "Scheduled mode requires both start_at and end_at"
            )
        start_at = request_in.start_at
        end_at = request_in.end_at
        if start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=UTC)
        if end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=UTC)
        if end_at <= start_at:
            raise BadRequestError("end_at must be later than start_at")

    # Only validate window when both start_at and end_at are present.
    # Immediate mode with end_at=None (infinite) skips window validation
    # since it's restricted to admin/teacher.
    if request_in.start_at is not None and request_in.end_at is not None:
        vm_request_availability_service.validate_request_window(
            session=session,
            current_user=user,
            request_in=request_in,
        )

    db_request = vm_request_repo.create_vm_request(
        session=session,
        vm_request_in=request_in,
        user_id=user.id,
        encrypted_password=encrypt_value(request_in.password),
        commit=False,
    )

    # ---------- role branching ----------
    auto_approved = False
    if can_auto_approve_vm_request(user, mode=mode):
        _approve_and_place(
            session=session,
            db_request=db_request,
            reviewer_id=user.id,
        )
        auto_approved = True
    # Otherwise (student, or teacher+scheduled): stays pending

    action_label = "vm_request_submit_auto_approved" if auto_approved else "vm_request_submit"
    audit_service.log_action(
        session=session,
        user_id=user.id,
        action=action_label,
        details=(
            f"Submitted {request_in.resource_type} request: {request_in.hostname}, "
            f"{request_in.cores} cores, {request_in.memory}MB RAM. "
            f"Mode: {mode}. "
            f"Reason: {request_in.reason}"
            + (". Auto-approved." if auto_approved else "")
        ),
        commit=False,
    )
    session.commit()

    # For immediate or quick-template auto-approved requests, trigger
    # provisioning right away in the background so the HTTP request returns
    # immediately (a VM clone can take 30+ seconds and must not block the
    # request handler).
    if auto_approved and mode in {"immediate", "quick_template"}:
        submit_sync(
            vm_request_schedule_service.process_single_request_start,
            db_request.id,
            name=f"provision_vm_request:{db_request.id}",
            task_id=f"vm_request:{db_request.id}",
            max_retries=1,
            retry_delay=15.0,
        )

    logger.info(f"User {user.email} submitted VM request {db_request.id}")
    return _to_public(db_request, user_override=user)


def _public_for_personal_view(req: VMRequest) -> VMRequestPublic:
    """Return the personal-view request payload with the real backend status."""
    return _to_public(req)


def list_by_user(
    *, session: Session, user_id: uuid.UUID, skip: int = 0, limit: int = 100
) -> VMRequestsPublic:
    requests, count = vm_request_repo.get_vm_requests_by_user(
        session=session, user_id=user_id, skip=skip, limit=limit
    )
    return VMRequestsPublic(
        data=[_public_for_personal_view(r) for r in requests], count=count
    )


def list_all(
    *,
    session: Session,
    status: VMRequestStatus | None = None,
    skip: int = 0,
    limit: int = 100,
) -> VMRequestsPublic:
    requests, count = vm_request_repo.get_all_vm_requests(
        session=session, status=status, skip=skip, limit=limit
    )
    return VMRequestsPublic(
        data=[_to_public(r) for r in requests], count=count
    )


def get(
    *, session: Session, request_id: uuid.UUID, current_user
) -> VMRequestPublic:
    db_request = vm_request_repo.get_vm_request_by_id(
        session=session, request_id=request_id
    )
    if not db_request:
        raise NotFoundError("Request not found")
    require_vm_request_access(current_user, db_request.user_id)
    return _to_public(db_request)


def get_review_context(
    *,
    session: Session,
    request_id: uuid.UUID,
    current_user,
) -> VMRequestReviewContext:
    db_request = vm_request_repo.get_vm_request_by_id(
        session=session,
        request_id=request_id,
    )
    if not db_request:
        raise NotFoundError("Request not found")

    require_vm_request_review(current_user)

    start_at = db_request.start_at
    end_at = db_request.end_at
    if not start_at:
        raise BadRequestError("A scheduled request window is required for review context")
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=UTC)
    # Use a far-future sentinel when end_at is None (infinite request).
    effective_end_at = end_at
    if effective_end_at is None:
        effective_end_at = _utc_now() + timedelta(days=3650)
    elif effective_end_at.tzinfo is None:
        effective_end_at = effective_end_at.replace(tzinfo=UTC)

    overlapping_requests = [
        item
        for item in vm_request_repo.get_approved_vm_requests_overlapping_window(
            session=session,
            window_start=start_at,
            window_end=effective_end_at,
        )
        if item.id != db_request.id
    ]

    projection_request = VMRequest.model_validate(db_request.model_dump())
    projection_request.status = VMRequestStatus.approved
    projection_request.assigned_node = None
    projection_request.desired_node = None
    projection_request.placement_strategy_used = None
    projection_request.reviewed_at = projection_request.reviewed_at or _utc_now()
    projected_requests = overlapping_requests + [projection_request]
    selections = vm_request_placement_service.rebuild_reserved_assignments(
        session=session,
        requests=projected_requests,
    )
    request_selection = selections.get(projection_request.id)
    if not request_selection or not request_selection.node:
        raise BadRequestError("No projected node is available for this request window")

    now = _utc_now()
    active_requests = vm_request_repo.list_active_approved_vm_requests(
        session=session,
        at_time=now,
    )
    active_by_vmid = {
        int(item.vmid): item
        for item in active_requests
        if item.vmid is not None
    }

    current_running_resources: list[VMRequestReviewRuntimeResource] = []
    cluster_nodes = sorted(
        {
            str(item.get("node") or item.get("name") or "").strip()
            for item in proxmox_service.list_nodes()
            if str(item.get("node") or item.get("name") or "").strip()
        }
    )
    for resource in proxmox_service.list_all_resources():
        status = str(resource.get("status") or "").lower()
        if status != "running":
            continue
        vmid = int(resource.get("vmid"))
        linked_request = active_by_vmid.get(vmid)
        current_running_resources.append(
            VMRequestReviewRuntimeResource(
                vmid=vmid,
                name=str(resource.get("name") or f"vm-{vmid}"),
                node=str(resource.get("node") or "unknown"),
                resource_type=str(resource.get("type") or "unknown"),
                status=status,
                linked_request_id=linked_request.id if linked_request else None,
                linked_hostname=linked_request.hostname if linked_request else None,
                linked_actual_node=linked_request.actual_node if linked_request else None,
                linked_desired_node=linked_request.desired_node if linked_request else None,
            )
        )
    current_running_resources.sort(
        key=lambda item: (item.node, item.name, item.vmid)
    )

    running_vmids = {item.vmid for item in current_running_resources}
    overlap_items: list[VMRequestReviewOverlapItem] = []
    projected_by_node: dict[str, list[str]] = {}
    for request in projected_requests:
        selection = selections.get(request.id)
        projected_node = selection.node if selection else None
        if projected_node:
            projected_by_node.setdefault(projected_node, []).append(request.hostname)
        overlap_items.append(
            VMRequestReviewOverlapItem(
                request_id=request.id,
                hostname=request.hostname,
                resource_type=request.resource_type,
                start_at=request.start_at,
                end_at=request.end_at,
                vmid=request.vmid,
                status=db_request.status if request.id == db_request.id else request.status,
                assigned_node=request.assigned_node,
                desired_node=request.desired_node,
                actual_node=request.actual_node,
                projected_node=projected_node,
                projected_strategy=selection.strategy if selection else None,
                migration_status=request.migration_status,
                is_current_request=request.id == db_request.id,
                is_running_now=bool(request.vmid is not None and request.vmid in running_vmids),
                is_provisioned=request.vmid is not None,
            )
        )
    overlap_items.sort(
        key=lambda item: (
            not item.is_current_request,
            item.start_at or datetime.min.replace(tzinfo=UTC),
            item.hostname,
        )
    )

    projected_nodes = [
        VMRequestReviewProjectedNode(
            node=node,
            request_count=len(hostnames),
            includes_current_request=db_request.hostname in hostnames,
            hostnames=sorted(hostnames),
        )
        for node, hostnames in sorted(
            projected_by_node.items(),
            key=lambda item: (-len(item[1]), item[0]),
        )
    ]

    node_score_breakdowns: list[VMRequestReviewNodeScore] = []
    try:
        breakdowns = vm_request_placement_service.get_preview_node_scores(
            session=session,
            db_request=projection_request,
            reserved_requests=overlapping_requests,
        )
        node_score_breakdowns = [
            VMRequestReviewNodeScore(
                node=b.node,
                balance_score=b.balance_score,
                cpu_share=b.cpu_share,
                memory_share=b.memory_share,
                disk_share=b.disk_share,
                peak_penalty=b.peak_penalty,
                loadavg_penalty=b.loadavg_penalty,
                storage_penalty=b.storage_penalty,
                migration_cost=b.migration_cost,
                priority=b.priority,
                is_selected=b.is_selected,
                reason=b.reason,
            )
            for b in breakdowns
        ]
    except Exception:
        logger.debug("Could not compute node score breakdown for review context", exc_info=True)

    # --- gather resource warnings ---
    resource_warnings: list[str] = []
    if not request_selection.plan.feasible:
        resource_warnings.append(
            "投影的資源容量不足以在請求的時段內部署。"
        )
    if request_selection.plan.warnings:
        resource_warnings.extend(request_selection.plan.warnings)
    if db_request.resource_warning is not None:
        resource_warnings.append(db_request.resource_warning)

    return VMRequestReviewContext(
        request=_to_public(db_request),
        window_start=start_at,
        window_end=effective_end_at,
        window_active_now=start_at <= now < effective_end_at,
        feasible=bool(request_selection.plan.feasible),
        placement_strategy=request_selection.strategy,
        projected_node=request_selection.node,
        summary=request_selection.plan.summary,
        reasons=list(request_selection.plan.rationale or []),
        warnings=list(request_selection.plan.warnings or []),
        resource_warnings=resource_warnings,
        cluster_nodes=sorted(
            {
                *cluster_nodes,
                *(item.node for item in current_running_resources),
                *(item.node for item in projected_nodes),
            }
        ),
        current_running_resources=current_running_resources,
        overlapping_approved_requests=overlap_items,
        projected_nodes=projected_nodes,
        node_scores=node_score_breakdowns,
    )


def review(
    *,
    session: Session,
    request_id: uuid.UUID,
    review_data: VMRequestReview,
    reviewer,
) -> VMRequestPublic:
    db_request = vm_request_repo.get_vm_request_by_id(
        session=session, request_id=request_id, for_update=True
    )
    if not db_request:
        raise NotFoundError("Request not found")
    if db_request.status != VMRequestStatus.pending:
        raise BadRequestError("This request has already been reviewed")

    reservation = None
    try:
        if review_data.status == "approved":
            if not db_request.start_at:
                raise BadRequestError(
                    "A scheduled request window is required before approval."
                )
            end_at = db_request.end_at
            if end_at is not None:
                if end_at.tzinfo is None:
                    end_at = end_at.replace(tzinfo=UTC)
                if end_at <= _utc_now():
                    raise BadRequestError(
                        "This request window has already ended and can no longer be approved."
                    )

            reservation = _approve_and_place(
                session=session,
                db_request=db_request,
                reviewer_id=reviewer.id,
            )
            # Apply reviewer comment if provided
            if review_data.review_comment:
                db_request.review_comment = review_data.review_comment
                session.add(db_request)
                session.flush()
        else:
            vm_request_repo.update_vm_request_status(
                session=session,
                db_request=db_request,
                status=VMRequestStatus.rejected,
                reviewer_id=reviewer.id,
                review_comment=review_data.review_comment,
                assigned_node=None,
                desired_node=None,
                actual_node=None,
                placement_strategy_used=None,
                migration_status=VMMigrationStatus.idle,
                migration_error=None,
                commit=False,
            )

        action = (
            "approved"
            if review_data.status == "approved"
            else "rejected"
        )
        details = f"Reviewed VM request {request_id}: {action}"
        if review_data.status == "approved":
            details += (
                ", reserved node "
                f"{reservation.node if reservation else db_request.assigned_node} for the approved time window"
            )
        if review_data.review_comment:
            details += f". Comment: {review_data.review_comment}"

        audit_service.log_action(
            session=session,
            user_id=reviewer.id,
            vmid=db_request.vmid,
            action="vm_request_review",
            details=details,
            commit=False,
        )
        session.commit()

        logger.info(
            f"Admin {reviewer.email} {action} VM request {request_id}"
        )
    except BadRequestError:
        session.rollback()
        raise
    except ValueError as exc:
        session.rollback()
        raise BadRequestError(str(exc)) from exc
    except Exception:
        logger.exception(
            "Failed to process review for VM request %s", request_id
        )
        session.rollback()

        raise ProvisioningError(
            "Failed to process review; scheduled provisioning setup may have failed."
        )

    refreshed = vm_request_repo.get_vm_request_by_id(
        session=session, request_id=db_request.id
    )
    # If the approved request's start window is already open, kick off
    # provisioning in the background so we don't wait for the next scheduler
    # tick (which can be up to SCHEDULER_POLL_SECONDS away).
    if (
        refreshed is not None
        and refreshed.status == VMRequestStatus.approved
        and refreshed.start_at is not None
    ):
        start_at = refreshed.start_at
        if start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=UTC)
        if start_at <= _utc_now():
            submit_sync(
                vm_request_schedule_service.process_single_request_start,
                refreshed.id,
                name=f"provision_vm_request:{refreshed.id}",
                task_id=f"vm_request:{refreshed.id}",
                max_retries=1,
                retry_delay=15.0,
            )
    return _to_public(refreshed)


def cancel(
    *,
    session: Session,
    request_id: uuid.UUID,
    current_user,
) -> VMRequestPublic:
    """Cancel a VM request.

    - ``pending``: standard flow.
    - ``approved``: cancel before the resource becomes controllable.
      If the worker is mid-Proxmox-clone the clone may still complete; the
      scheduler reconciliation will then expose the live machine in resources. We
      surface 409 in that case rather than silently lying about the state.
    """
    from app.infrastructure.worker import (  # noqa: PLC0415
        cancel as _cancel_bg_task,
    )
    from app.infrastructure.worker import (
        is_active as _is_bg_task_active,
    )

    db_request = vm_request_repo.get_vm_request_by_id(
        session=session,
        request_id=request_id,
        for_update=True,
    )
    if not db_request:
        raise NotFoundError("Request not found")

    require_vm_request_cancel(current_user, db_request.user_id)

    cancellable = (
        VMRequestStatus.pending,
        VMRequestStatus.approved,
    )
    if db_request.status not in cancellable:
        raise BadRequestError(
            f"Cannot cancel VM request in status={db_request.status.value}"
        )

    if db_request.status == VMRequestStatus.approved and db_request.vmid is None:
        bg_task_id = f"vm_request:{db_request.id}"
        cancelled_in_runner = _cancel_bg_task(bg_task_id)
        if not cancelled_in_runner and _is_bg_task_active(bg_task_id):
            # Active but cancel returned False — race. Be honest about it.
            raise BadRequestError(
                "Provisioning is in progress and could not be cancelled cleanly; "
                "please retry in a few seconds"
            )

    vm_request_repo.update_vm_request_status(
        session=session,
        db_request=db_request,
        status=VMRequestStatus.cancelled,
        reviewer_id=current_user.id,
        review_comment=(
            "Cancelled by requester"
            if db_request.user_id == current_user.id
            else "Cancelled by admin"
        ),
        assigned_node=None,
        desired_node=None,
        actual_node=None,
        placement_strategy_used=None,
        migration_status=VMMigrationStatus.idle,
        migration_error=None,
        commit=False,
    )

    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action="vm_request_review",
        details=f"Cancelled VM request {request_id}",
        commit=False,
    )
    session.commit()

    refreshed = vm_request_repo.get_vm_request_by_id(
        session=session,
        request_id=request_id,
    )
    return _to_public(refreshed)


def retry(
    *,
    session: Session,
    request_id: uuid.UUID,
    current_user,
) -> VMRequestPublic:
    """Re-fire provisioning for an approved VM request.

    Useful when the previous attempt failed (status reverted to ``approved``)
    and the user doesn't want to wait for the next scheduler tick.
    """
    db_request = vm_request_repo.get_vm_request_by_id(
        session=session, request_id=request_id, for_update=True,
    )
    if not db_request:
        raise NotFoundError("Request not found")

    require_vm_request_cancel(current_user, db_request.user_id)

    if db_request.status != VMRequestStatus.approved:
        raise BadRequestError(
            f"Only approved VM requests can be retried (current={db_request.status.value})"
        )

    submit_sync(
        vm_request_schedule_service.process_single_request_start,
        db_request.id,
        name=f"provision_vm_request:{db_request.id}",
        task_id=f"vm_request:{db_request.id}",
        max_retries=1,
        retry_delay=15.0,
    )
    return _to_public(db_request)
