import logging
import uuid

from sqlmodel import Session

from app.core.authorizers import (
    can_bypass_resource_ownership,
    require_resource_access,
)
from app.exceptions import (
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    ProxmoxError,
)
from app.models import SpecChangeRequestStatus, SpecChangeType
from app.repositories import resource as resource_repo
from app.repositories import spec_change_request as spec_request_repo
from app.schemas import (
    SpecChangeRequestCreate,
    SpecChangeRequestPublic,
    SpecChangeRequestReview,
    SpecChangeRequestsPublic,
)
from app.services.proxmox import proxmox_service
from app.services.resource import quota_service
from app.services.user import audit_service

logger = logging.getLogger(__name__)


def _to_public(request) -> SpecChangeRequestPublic:
    return SpecChangeRequestPublic(
        id=request.id,
        vmid=request.vmid,
        user_id=request.user_id,
        user_email=request.user.email if request.user else None,
        user_full_name=request.user.full_name if request.user else None,
        change_type=request.change_type,
        reason=request.reason,
        current_cpu=request.current_cpu,
        current_memory=request.current_memory,
        current_disk=request.current_disk,
        requested_cpu=request.requested_cpu,
        requested_memory=request.requested_memory,
        requested_disk=request.requested_disk,
        status=request.status,
        reviewer_id=request.reviewer_id,
        review_comment=request.review_comment,
        reviewed_at=request.reviewed_at,
        applied_at=request.applied_at,
        created_at=request.created_at,
    )


def _check_ownership_and_get_info(
    *, session: Session, user, vmid: int
) -> dict:
    """Check resource ownership and return Proxmox resource info."""
    if not can_bypass_resource_ownership(user):
        db_resource = resource_repo.get_resource_by_vmid(
            session=session, vmid=vmid
        )
        if not db_resource:
            raise PermissionDeniedError(
                "You don't have permission to access this resource"
            )
        require_resource_access(user, db_resource.user_id)

    return proxmox_service.find_resource(vmid)


def _get_current_specs(
    node: str, vmid: int, resource_type: str
) -> dict:
    return proxmox_service.get_current_specs(node, vmid, resource_type)


def create(
    *, session: Session, request_in: SpecChangeRequestCreate, user
) -> SpecChangeRequestPublic:
    vmid = request_in.vmid
    resource_info = _check_ownership_and_get_info(
        session=session, user=user, vmid=vmid
    )

    node = resource_info["node"]
    resource_type = resource_info["type"]
    specs = _get_current_specs(node, vmid, resource_type)

    # Validate requested changes
    if (
        request_in.change_type == SpecChangeType.cpu
        and request_in.requested_cpu is None
    ):
        raise BadRequestError("requested_cpu is required for CPU change")
    if (
        request_in.change_type == SpecChangeType.memory
        and request_in.requested_memory is None
    ):
        raise BadRequestError("requested_memory is required for memory change")
    if request_in.change_type == SpecChangeType.disk:
        if request_in.requested_disk is None:
            raise BadRequestError(
                "requested_disk is required for disk change"
            )
        if specs["disk"] and request_in.requested_disk <= specs["disk"]:
            raise BadRequestError(
                f"Disk size can only be increased. Current: {specs['disk']}GB"
            )
    if request_in.change_type == SpecChangeType.combined:
        if not any(
            [
                request_in.requested_cpu,
                request_in.requested_memory,
                request_in.requested_disk,
            ]
        ):
            raise BadRequestError(
                "At least one specification must be requested for combined change"
            )

    db_request = spec_request_repo.create_spec_change_request(
        session=session,
        user_id=user.id,
        vmid=vmid,
        change_type=request_in.change_type,
        reason=request_in.reason,
        current_cpu=specs["cpu"],
        current_memory=specs["memory"],
        current_disk=specs["disk"],
        requested_cpu=request_in.requested_cpu,
        requested_memory=request_in.requested_memory,
        requested_disk=request_in.requested_disk,
        commit=False,
    )

    audit_service.log_action(
        session=session,
        user_id=user.id,
        vmid=vmid,
        action="spec_change_request",
        details=(
            f"Requested {request_in.change_type.value} change: "
            f"CPU={request_in.requested_cpu}, "
            f"Memory={request_in.requested_memory}MB, "
            f"Disk={request_in.requested_disk}GB. "
            f"Reason: {request_in.reason}"
        ),
        commit=False,
    )
    session.commit()

    logger.info(
        f"User {user.email} created spec change request for VMID {vmid}"
    )
    return _to_public(db_request)


def list_by_user(
    *, session: Session, user_id: uuid.UUID, skip: int = 0, limit: int = 100
) -> SpecChangeRequestsPublic:
    requests, count = spec_request_repo.get_spec_change_requests_by_user(
        session=session, user_id=user_id, skip=skip, limit=limit
    )
    return SpecChangeRequestsPublic(
        data=[_to_public(r) for r in requests], count=count
    )


def list_all(
    *,
    session: Session,
    skip: int = 0,
    limit: int = 100,
    status: SpecChangeRequestStatus | None = None,
    vmid: int | None = None,
) -> SpecChangeRequestsPublic:
    requests, count = spec_request_repo.get_all_spec_change_requests(
        session=session, skip=skip, limit=limit, status=status, vmid=vmid
    )
    return SpecChangeRequestsPublic(
        data=[_to_public(r) for r in requests], count=count
    )


def review(
    *,
    session: Session,
    request_id: uuid.UUID,
    review_data: SpecChangeRequestReview,
    reviewer,
) -> SpecChangeRequestPublic:
    db_request = spec_request_repo.get_spec_change_request_by_id(
        session=session, request_id=request_id, for_update=True
    )
    if not db_request:
        raise NotFoundError("Request not found")
    if db_request.status != SpecChangeRequestStatus.pending:
        raise BadRequestError(
            f"Request already {db_request.status.value}"
        )

    try:
        if review_data.status == SpecChangeRequestStatus.approved:
            quota_service.check_quota(
                session,
                db_request.user_id,
                delta_cores=max(
                    0,
                    int(db_request.requested_cpu or db_request.current_cpu or 0)
                    - int(db_request.current_cpu or 0),
                ),
                delta_memory_mb=max(
                    0,
                    int(db_request.requested_memory or db_request.current_memory or 0)
                    - int(db_request.current_memory or 0),
                ),
                delta_disk_gb=max(
                    0,
                    int(db_request.requested_disk or db_request.current_disk or 0)
                    - int(db_request.current_disk or 0),
                ),
            )
            changes = _apply_spec_changes(db_request=db_request)
            db_request = spec_request_repo.update_spec_change_request_status(
                session=session,
                request_id=request_id,
                status=review_data.status,
                reviewer_id=reviewer.id,
                review_comment=review_data.review_comment,
                commit=False,
            )
            db_request = spec_request_repo.mark_spec_change_applied(
                session=session, request_id=db_request.id, commit=False
            )
            audit_service.log_action(
                session=session,
                user_id=reviewer.id,
                vmid=db_request.vmid,
                action="spec_change_apply",
                details=f"Applied approved spec changes: {', '.join(changes)}",
                commit=False,
            )
            logger.info(
                "Admin %s approved and applied spec change request %s",
                reviewer.email,
                request_id,
            )
        else:
            db_request = spec_request_repo.update_spec_change_request_status(
                session=session,
                request_id=request_id,
                status=review_data.status,
                reviewer_id=reviewer.id,
                review_comment=review_data.review_comment,
                commit=False,
            )
            audit_service.log_action(
                session=session,
                user_id=reviewer.id,
                vmid=db_request.vmid,
                action="spec_change_request",
                details=(
                    f"Rejected spec change request {request_id}: "
                    f"{review_data.review_comment or 'No comment'}"
                ),
                commit=False,
            )
            logger.info(
                f"Admin {reviewer.email} rejected spec change request {request_id}"
            )

        session.commit()
    except Exception:
        session.rollback()
        raise

    refreshed = spec_request_repo.get_spec_change_request_by_id(
        session=session, request_id=db_request.id
    )
    return _to_public(refreshed)


def _apply_spec_changes(*, db_request) -> list[str]:
    """Apply approved spec changes to the Proxmox resource and return summaries."""
    try:
        resource_info = proxmox_service.find_resource(db_request.vmid)

        node = resource_info["node"]
        resource_type = resource_info["type"]

        config_params = {}
        changes = []

        if db_request.requested_cpu is not None:
            config_params["cores"] = db_request.requested_cpu
            changes.append(
                f"CPU: {db_request.current_cpu} -> {db_request.requested_cpu} cores"
            )
        if db_request.requested_memory is not None:
            config_params["memory"] = db_request.requested_memory
            changes.append(
                f"Memory: {db_request.current_memory} -> {db_request.requested_memory}MB"
            )

        if config_params:
            proxmox_service.update_config(
                node, db_request.vmid, resource_type, **config_params
            )

        if db_request.requested_disk is not None:
            # Proxmox resize 只接受「增量」。current_disk 解析失敗（None）時
            # 不能把 requested 整個當增量套上去，那會把磁碟擴成
            # current + requested；增量 <= 0（combined 類型建立時未驗證）
            # 也必須擋下。
            if db_request.current_disk is None:
                raise ProxmoxError(
                    f"Cannot apply disk change for VMID {db_request.vmid}: "
                    "current disk size is unknown"
                )
            disk_increase = db_request.requested_disk - db_request.current_disk
            if disk_increase <= 0:
                raise ProxmoxError(
                    f"Disk size can only be increased "
                    f"(current={db_request.current_disk}GB, "
                    f"requested={db_request.requested_disk}GB)"
                )
            size_param = f"+{disk_increase}G"
            disk_name = "scsi0" if resource_type == "qemu" else "rootfs"
            proxmox_service.resize_disk(
                node, db_request.vmid, resource_type, disk_name, size_param
            )
            changes.append(
                f"Disk: {db_request.current_disk} -> {db_request.requested_disk}GB"
            )

        return changes
    except (ProxmoxError, NotFoundError):
        raise
    except Exception as e:
        logger.error(f"Failed to apply spec changes: {e}")
        raise ProxmoxError(
            f"Failed to apply requested changes before approval persistence: {e}"
        )
