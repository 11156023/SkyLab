import logging
import math
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlmodel import Session

from app.exceptions import BadRequestError, ProxmoxError
from app.models.vm_request import VMMigrationStatus, VMRequestStatus
from app.repositories import audit_log as audit_log_repo
from app.repositories import batch_provision as batch_provision_repo
from app.repositories import resource as resource_repo
from app.repositories import vm_request as vm_request_repo
from app.schemas import ResourcePublic
from app.schemas.resource import (
    BatchActionResponse,
    BatchActionResultItem,
    ExtendSessionResponse,
    SessionStatusResponse,
)
from app.services.network import firewall_service
from app.services.proxmox import proxmox_service
from app.services.scheduling.recurrence import (
    get_schedule_policy,
    is_in_window,
)
from app.services.user import audit_service

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _enforce_start_window(*, session: Session, vmid: int) -> None:
    request = vm_request_repo.get_latest_approved_vm_request_by_vmid(
        session=session,
        vmid=vmid,
    )
    if not request or not request.start_at or not request.end_at:
        return

    now = _utc_now()
    start_at = request.start_at
    end_at = request.end_at
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=UTC)
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=UTC)

    if now < start_at:
        raise BadRequestError("This resource can only be started when its approved time window begins.")
    if now >= end_at:
        raise BadRequestError("This resource can no longer be started because its approved time window has ended.")


def _from_punycode_hostname(hostname: str) -> str:
    """將 Punycode hostname 解碼回 Unicode 顯示給使用者。"""
    result_labels = []
    for label in hostname.split("."):
        if label.lower().startswith("xn--"):
            try:
                decoded = label[4:].encode("ascii").decode("punycode")
                result_labels.append(decoded)
            except Exception:
                result_labels.append(label)
        else:
            result_labels.append(label)
    return ".".join(result_labels)


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _resource_type_for_request(resource_type: str | None) -> str:
    return "qemu" if resource_type == "vm" else (resource_type or "")


def _build_resource_public(
    resource: dict, db_resource, node: str, vm_type: str,
    session: Session | None = None,
) -> ResourcePublic:
    vmid = resource.get("vmid")
    ip_address = proxmox_service.get_ip_address(node, vmid, vm_type)
    if ip_address:
        if session is not None:
            try:
                resource_repo.update_ip_address(
                    session=session, vmid=vmid, ip_address=ip_address
                )
            except Exception:
                session.rollback()
                logger.warning(
                    "Failed to update cached IP address for vmid=%s ip_address=%s",
                    vmid,
                    ip_address,
                    exc_info=True,
                )
    else:
        # VM 離線時用 DB 快取
        if session is not None:
            ip_address = resource_repo.get_cached_ip_address(session=session, vmid=vmid)
    return ResourcePublic(
        vmid=resource.get("vmid"),
        request_id=db_resource.request_id if db_resource else None,
        name=_from_punycode_hostname(resource.get("name", "")),
        status=resource.get("status", ""),
        node=node,
        type=vm_type,
        environment_type=db_resource.environment_type if db_resource else None,
        os_info=db_resource.os_info if db_resource else None,
        expiry_date=db_resource.expiry_date if db_resource else None,
        ip_address=ip_address,
        ssh_public_key=db_resource.ssh_public_key if db_resource else None,
        service_template_slug=(
            db_resource.service_template_slug if db_resource else None
        ),
        cpu=resource.get("cpu"),
        maxcpu=resource.get("maxcpu"),
        mem=resource.get("mem"),
        maxmem=resource.get("maxmem"),
        uptime=resource.get("uptime"),
    )


def get_by_vmid(
    *, session: Session, vmid: int, resource_info: dict,
) -> ResourcePublic:
    """Get a single resource with merged Proxmox + DB data."""
    vm_type = resource_info.get("type", "")
    vm_node = resource_info.get("node", "")
    db_resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    return _build_resource_public(resource_info, db_resource, vm_node, vm_type, session)


def list_all(
    *, session: Session, node: str | None = None
) -> list[ResourcePublic]:
    try:
        resources = proxmox_service.list_all_resources()
        result = []
        for r in resources:
            if (node and r.get("node") != node) or r.get("template") == 1:
                continue
            vmid = r.get("vmid")
            vm_type = r.get("type")
            vm_node = r.get("node")
            db_resource = resource_repo.get_resource_by_vmid(
                session=session, vmid=vmid
            )
            result.append(
                _build_resource_public(r, db_resource, vm_node, vm_type, session)
            )
        return result
    except Exception as e:
        logger.error(f"Failed to get resources: {e}")
        raise ProxmoxError(f"Failed to get resources: {e}")


DELETED_TOMBSTONE_DAYS = 30
"""How long after a user-initiated deletion to keep showing the tombstone on
their resources page. Keep it short enough that the list doesn't grow
forever; long enough that the user notices the change after returning from
a few days off."""


def list_by_user(
    *, session: Session, user_id: uuid.UUID
) -> list[ResourcePublic]:
    from sqlmodel import select

    from app.models.vm_request import VMRequest

    try:
        result: list[ResourcePublic] = []
        shown_vmids: set[int] = set()

        # 1. Live resources owned by the user (from Proxmox + DB join).
        user_resources = resource_repo.get_resources_by_user(
            session=session, user_id=user_id
        )
        if user_resources:
            owned_vmids = {r.vmid: r for r in user_resources}
            try:
                for r in proxmox_service.list_all_resources():
                    if r.get("template") == 1:
                        continue
                    vmid = r.get("vmid")
                    if vmid not in owned_vmids:
                        continue
                    result.append(
                        _build_resource_public(
                            r,
                            owned_vmids[vmid],
                            r.get("node", ""),
                            r.get("type", ""),
                            session,
                        )
                    )
                    shown_vmids.add(vmid)
            except Exception:
                logger.warning("Proxmox unavailable; marking owned resources as unknown")
                for db_r in user_resources:
                    if db_r.vmid not in shown_vmids:
                        request = (
                            session.get(VMRequest, db_r.request_id)
                            if db_r.request_id
                            else None
                        )
                        result.append(
                            ResourcePublic(
                                vmid=db_r.vmid,
                                request_id=db_r.request_id,
                                name=(
                                    _from_punycode_hostname(request.hostname)
                                    if request
                                    else f"vm-{db_r.vmid}"
                                ),
                                status="unknown",
                                node=getattr(request, "actual_node", None)
                                or getattr(request, "assigned_node", None)
                                or "",
                                type=_resource_type_for_request(
                                    getattr(request, "resource_type", None)
                                ),
                                can_control=False,
                                environment_type=db_r.environment_type,
                                os_info=db_r.os_info,
                                expiry_date=db_r.expiry_date,
                                ssh_public_key=db_r.ssh_public_key,
                                service_template_slug=db_r.service_template_slug,
                            )
                        )
                        shown_vmids.add(db_r.vmid)

        # 2. Approved requests not yet visible in Proxmox.
        now = _utc_now()
        pending_requests = list(
            session.exec(
                select(VMRequest).where(
                    VMRequest.user_id == user_id,
                    VMRequest.status == VMRequestStatus.approved,
                )
            ).all()
        )
        for req in pending_requests:
            if req.vmid and req.vmid in shown_vmids:
                continue
            if req.migration_status == VMMigrationStatus.failed or req.migration_error:
                display_status = "failed"
            else:
                start_at = _ensure_utc(req.start_at)
                display_status = (
                    "scheduled"
                    if start_at is not None and start_at > now
                    else "provisioning"
                )

            result.append(
                ResourcePublic(
                    vmid=req.vmid,
                    request_id=req.id,
                    name=_from_punycode_hostname(req.hostname),
                    status=display_status,
                    node=req.actual_node or req.assigned_node or req.desired_node or "",
                    type=_resource_type_for_request(req.resource_type),
                    is_placeholder=True,
                    can_control=False,
                    environment_type=req.environment_type,
                    os_info=req.os_info,
                    expiry_date=req.expiry_date,
                    maxcpu=req.cores,
                    maxmem=req.memory * 1024 * 1024 if req.memory else None,
                )
            )
            if req.vmid:
                shown_vmids.add(req.vmid)

        # 3. Deletion tombstones (recent self-initiated deletions).
        result.extend(
            _list_user_deletion_tombstones(
                session=session,
                user_id=user_id,
                excluded_vmids=shown_vmids,
            )
        )

        return result
    except Exception as e:
        logger.error(f"Failed to get user resources: {e}")
        raise ProxmoxError(f"Failed to get user resources: {e}")


def _list_user_deletion_tombstones(
    *,
    session: Session,
    user_id: uuid.UUID,
    excluded_vmids: set[int] | None = None,
) -> list[ResourcePublic]:
    """Build ResourcePublic tombstones for the user's recent self-initiated
    deletions, so the resources page can render a "已刪除" badge alongside
    live resources."""
    from sqlmodel import col, select

    from app.models.deletion_request import (
        DeletionRequest,
        DeletionRequestStatus,
    )

    cutoff = _utc_now() - timedelta(days=DELETED_TOMBSTONE_DAYS)
    rows = list(
        session.exec(
            select(DeletionRequest)
            .where(
                DeletionRequest.user_id == user_id,
                DeletionRequest.status == DeletionRequestStatus.completed,
                col(DeletionRequest.completed_at) >= cutoff,
            )
            .order_by(col(DeletionRequest.completed_at).desc())
        ).all()
    )
    excluded_vmids = excluded_vmids or set()
    return [
        ResourcePublic(
            vmid=req.vmid,
            name=req.name or f"vm-{req.vmid}",
            status="deleted",
            node=req.node or "",
            type=req.resource_type or "",
            can_control=False,
        )
        for req in rows
        if req.vmid not in excluded_vmids
    ]


def get_config(*, vmid: int, resource_info: dict) -> dict:
    try:
        node = resource_info["node"]
        resource_type = resource_info["type"]
        return proxmox_service.get_config(node, vmid, resource_type)
    except Exception as e:
        logger.error(f"Failed to get config for {vmid}: {e}")
        raise ProxmoxError(f"Failed to get config for resource {vmid}: {e}")


def control(
    *,
    session: Session,
    vmid: int,
    action: str,
    resource_info: dict,
    user_id: uuid.UUID,
) -> dict:
    """Control a resource: start, stop, reboot, shutdown, reset."""
    valid_actions = {"start", "stop", "reboot", "shutdown", "reset"}
    if action not in valid_actions:
        raise BadRequestError(f"Invalid action: {action}")

    try:
        node = resource_info["node"]
        resource_type = resource_info["type"]

        if action == "start":
            _enforce_start_window(session=session, vmid=vmid)

        proxmox_service.control(node, vmid, resource_type, action)

        # 啟動時確保防火牆仍為啟用狀態
        if action == "start":
            firewall_service.ensure_firewall_enabled(node, vmid, resource_type)
            _set_auto_stop_for_user_start(session=session, vmid=vmid)
        elif action in ("stop", "shutdown"):
            # 學生主動關機 → 清除 auto_stop_at，不會被排程器再啟動
            resource_repo.set_auto_stop(
                session=session,
                vmid=vmid,
                auto_stop_at=None,
                auto_stop_reason=None,
            )

        action_map = {
            "start": "resource_start",
            "stop": "resource_stop",
            "reboot": "resource_reboot",
            "shutdown": "resource_shutdown",
            "reset": "resource_reset",
        }
        audit_service.log_action(
            session=session,
            user_id=user_id,
            vmid=vmid,
            action=action_map[action],
            details=f"{action.capitalize()} {resource_type} {resource_info.get('name', vmid)}",
        )

        logger.info(f"Resource {vmid} {action}")
        return {"message": f"Resource {vmid} {action}"}
    except BadRequestError:
        raise
    except Exception as e:
        logger.error(f"Failed to {action} resource {vmid}: {e}")
        raise ProxmoxError(f"Failed to {action} resource {vmid}: {e}")


def delete(
    *,
    session: Session,
    vmid: int,
    resource_info: dict,
    user_id: uuid.UUID,
    purge: bool = True,
    force: bool = False,
) -> dict:
    try:
        node = resource_info["node"]
        resource_type = resource_info["type"]

        # Force stop if running
        current_status = resource_info.get("status", "")
        if current_status == "running":
            if not force:
                raise BadRequestError(
                    f"Resource {vmid} is running. Use force=true to stop and delete."
                )
            proxmox_service.control(node, vmid, resource_type, "stop")

            # Wait for stop
            for _ in range(30):
                time.sleep(1)
                try:
                    status = proxmox_service.get_status(node, vmid, resource_type)
                    if status.get("status") == "stopped":
                        break
                except Exception:
                    break

        # Delete the resource
        delete_params = {}
        if purge:
            delete_params["purge"] = 1
            if resource_type == "qemu":
                delete_params["destroy-unreferenced-disks"] = 1

        proxmox_service.delete_resource(node, vmid, resource_type, **delete_params)

        # Clean up reverse proxy rules and Cloudflare DNS records for this VM
        try:
            from app.services.network import reverse_proxy_service  # noqa: PLC0415
            reverse_proxy_service.remove_reverse_proxy_rules_for_vmid(session, vmid)
        except Exception as exc:
            logger.warning("Failed to clean up reverse proxy rules for VM %s: %s", vmid, exc)

        # Release IP allocation
        try:
            from app.services.network import ip_management_service  # noqa: PLC0415
            ip_management_service.release_ip(session, vmid)
        except Exception as exc:
            logger.warning("Failed to release IP for VM %s: %s", vmid, exc)

        # Unlink deleted VMID from historical batch tasks so group status won't
        # accidentally match a future resource that reuses the same VMID.
        try:
            cleared_count = batch_provision_repo.clear_task_vmid_references(
                session=session,
                vmid=vmid,
                commit=False,
            )
            if cleared_count:
                logger.info(
                    "Cleared VMID %s from %s batch task(s)",
                    vmid,
                    cleared_count,
                )
        except Exception as exc:
            session.rollback()
            logger.warning(
                "Failed to clear batch task VMID references for VM %s: %s",
                vmid,
                exc,
            )

        # Remove from database (resource record + all associated audit logs)
        resource_repo.delete_resource(session=session, vmid=vmid)
        audit_log_repo.delete_audit_logs_by_vmid(session=session, vmid=vmid)

        # Cancel the associated VM request so the scheduler won't re-provision
        linked_request = vm_request_repo.get_latest_approved_vm_request_by_vmid(
            session=session, vmid=vmid,
        )
        if linked_request is not None:
            linked_request.status = VMRequestStatus.cancelled
            linked_request.review_comment = "Resource deleted by user"
            session.add(linked_request)

        audit_service.log_action(
            session=session,
            user_id=user_id,
            vmid=vmid,
            action="resource_delete",
            details=(
                f"Deleted {resource_type} {resource_info.get('name', vmid)} "
                f"(purge={purge}, force={force})"
            ),
        )

        logger.info(f"Resource {vmid} deleted")
        return {"message": f"Resource {vmid} deleted successfully"}
    except (BadRequestError, ProxmoxError):
        raise
    except Exception as e:
        logger.error(f"Failed to delete resource {vmid}: {e}")
        raise ProxmoxError(f"Failed to delete resource {vmid}: {e}")


def delete_orphan_db_record(
    *,
    session: Session,
    vmid: int,
    user_id: uuid.UUID,
) -> None:
    """Clean up a DB resource record whose Proxmox VM no longer exists.

    Runs only the non-Proxmox cleanup steps (IP release, reverse proxy,
    batch task unlinking, DB row deletion, VM request cancellation, audit log).
    Safe to call when the VM is already gone from Proxmox.
    """
    try:
        from app.services.network import reverse_proxy_service  # noqa: PLC0415
        reverse_proxy_service.remove_reverse_proxy_rules_for_vmid(session, vmid)
    except Exception as exc:
        logger.warning("Orphan cleanup: failed to remove reverse proxy rules for vmid=%s: %s", vmid, exc)

    try:
        from app.services.network import ip_management_service  # noqa: PLC0415
        ip_management_service.release_ip(session, vmid)
    except Exception as exc:
        logger.warning("Orphan cleanup: failed to release IP for vmid=%s: %s", vmid, exc)

    try:
        batch_provision_repo.clear_task_vmid_references(session=session, vmid=vmid, commit=False)
    except Exception as exc:
        session.rollback()
        logger.warning("Orphan cleanup: failed to clear batch task refs for vmid=%s: %s", vmid, exc)

    resource_repo.delete_resource(session=session, vmid=vmid)
    audit_log_repo.delete_audit_logs_by_vmid(session=session, vmid=vmid)

    linked_request = vm_request_repo.get_latest_approved_vm_request_by_vmid(session=session, vmid=vmid)
    if linked_request is not None:
        linked_request.status = VMRequestStatus.rejected
        linked_request.review_comment = "Resource deleted (orphan DB cleanup)"
        session.add(linked_request)

    audit_service.log_action(
        session=session,
        user_id=user_id,
        vmid=vmid,
        action="resource_delete",
        details=f"Orphan DB cleanup for vmid={vmid} (VM not found in Proxmox)",
    )
    logger.info("Orphan DB record for vmid=%s cleaned up", vmid)


def get_current_stats(*, vmid: int, resource_info: dict) -> dict:
    try:
        node = resource_info["node"]
        resource_type = resource_info["type"]
        s = proxmox_service.get_status(node, vmid, resource_type)
        return {
            "cpu": s.get("cpu"),
            "maxcpu": s.get("cpus") or s.get("maxcpu"),
            "mem": s.get("mem"),
            "maxmem": s.get("maxmem"),
            "disk": s.get("disk"),
            "maxdisk": s.get("maxdisk"),
            "netin": s.get("netin"),
            "netout": s.get("netout"),
            "uptime": s.get("uptime"),
            "status": s.get("status", "unknown"),
        }
    except Exception as e:
        logger.error(f"Failed to get current stats for {vmid}: {e}")
        raise ProxmoxError(f"Failed to get stats for resource {vmid}: {e}")


def get_rrd_stats(
    *, vmid: int, resource_info: dict, timeframe: str
) -> list[dict]:
    valid_timeframes = ["hour", "day", "week", "month", "year"]
    if timeframe not in valid_timeframes:
        raise BadRequestError(
            f"Invalid timeframe. Must be one of: {valid_timeframes}"
        )
    try:
        node = resource_info["node"]
        resource_type = resource_info["type"]
        return proxmox_service.get_rrd_data(node, vmid, resource_type, timeframe)
    except BadRequestError:
        raise
    except Exception as e:
        logger.error(f"Failed to get RRD stats for {vmid}: {e}")
        raise ProxmoxError(f"Failed to get RRD stats for resource {vmid}: {e}")


def direct_update_spec(
    *,
    session: Session,
    vmid: int,
    resource_info: dict,
    user_id: uuid.UUID,
    cores: int | None = None,
    memory: int | None = None,
    disk_size: str | None = None,
) -> dict:
    """Admin direct spec update (no approval needed)."""
    try:
        node = resource_info["node"]
        resource_type = resource_info["type"]

        changes = []
        config_params = {}

        if cores is not None:
            config_params["cores"] = cores
            changes.append(f"CPU: {cores} cores")
        if memory is not None:
            config_params["memory"] = memory
            changes.append(f"Memory: {memory}MB")

        if not config_params and not disk_size:
            raise BadRequestError(
                "At least one specification must be provided"
            )

        if config_params:
            proxmox_service.update_config(
                node, vmid, resource_type, **config_params
            )

        if disk_size:
            disk_name = "scsi0" if resource_type == "qemu" else "rootfs"
            proxmox_service.resize_disk(
                node, vmid, resource_type, disk_name, disk_size
            )
            changes.append(f"Disk: {disk_size}")

        audit_service.log_action(
            session=session,
            user_id=user_id,
            vmid=vmid,
            action="spec_direct_update",
            details=f"Direct spec update: {', '.join(changes)}",
        )

        return {"message": f"Spec updated: {', '.join(changes)}"}
    except (BadRequestError, ProxmoxError):
        raise
    except Exception as e:
        logger.error(f"Failed to update spec for {vmid}: {e}")
        raise ProxmoxError(f"Failed to update spec for resource {vmid}: {e}")


# ─── Auto-stop / practice session helpers ─────────────────────────────────────


def _set_auto_stop_for_user_start(*, session: Session, vmid: int) -> None:
    """Decide the ``auto_stop_at`` to write when a student manually starts a VM.

    Inside a course window: keep the window-grace stop (until window_end + grace).
    Outside any window: practice quota (now + practice_session_hours).
    """
    policy = get_schedule_policy(session=session)
    now = _utc_now()

    request = vm_request_repo.get_latest_approved_vm_request_by_vmid(
        session=session, vmid=vmid
    )
    if request and is_in_window(
        request.next_window_start, request.next_window_end, now
    ):
        # In course window — the scheduler already set window_grace; don't shorten it.
        return

    # Outside any window: enforce practice-quota auto-stop.
    auto_stop_at = now + timedelta(hours=policy.practice_session_hours)
    resource_repo.set_auto_stop(
        session=session,
        vmid=vmid,
        auto_stop_at=auto_stop_at,
        auto_stop_reason="practice_quota",
    )


def extend_session(
    *,
    session: Session,
    vmid: int,
    user_id: uuid.UUID,
) -> ExtendSessionResponse:
    """Extend a practice-quota session by another ``practice_session_hours``.

    Only allowed when:
    - Caller owns the resource
    - VM is currently running
    - ``auto_stop_reason == "practice_quota"`` (window-grace stops are not
      extendable — the course window dictates that)
    """
    resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    if resource is None:
        raise BadRequestError("Resource not found")
    if resource.user_id != user_id:
        raise BadRequestError("Not the owner of this resource")
    if resource.auto_stop_reason != "practice_quota":
        raise BadRequestError(
            "Session can only be extended during a practice (out-of-window) session."
        )

    policy = get_schedule_policy(session=session)
    now = _utc_now()
    new_stop = now + timedelta(hours=policy.practice_session_hours)
    resource_repo.set_auto_stop(
        session=session,
        vmid=vmid,
        auto_stop_at=new_stop,
        auto_stop_reason="practice_quota",
    )
    audit_service.log_action(
        session=session,
        user_id=user_id,
        vmid=vmid,
        action="resource_extend_session",
        details=f"Extended practice session to {new_stop.isoformat()}",
    )
    return ExtendSessionResponse(
        vmid=vmid,
        auto_stop_at=new_stop,
        extended_minutes=policy.practice_session_hours * 60,
    )


def get_session_status(
    *,
    session: Session,
    vmid: int,
    resource_info: dict,
) -> SessionStatusResponse:
    """Live session info for the student UI (polled every ~30s).

    Reports whichever warning is more urgent:
    - ``warn_reason="auto_stop"``: VM has an ``auto_stop_at`` within the
      configured warning window (group practice quota or course-window grace).
    - ``warn_reason="expiry"``: VM's ``expiry_date`` is within
      ``policy.expiry_warning_hours`` (admin-configurable, defaults to 24 h).

    auto_stop wins when both apply, since it's typically minutes away while
    expiry is at least hours.
    """
    resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    running = resource_info.get("status") == "running"
    auto_stop_at = resource.auto_stop_at if resource else None
    auto_stop_reason = resource.auto_stop_reason if resource else None

    policy = get_schedule_policy(session=session)

    minutes_until_stop: int | None = None
    auto_stop_warn = False
    if running and auto_stop_at:
        delta = auto_stop_at - _utc_now()
        minutes_until_stop = max(int(delta.total_seconds() // 60), 0)
        auto_stop_warn = minutes_until_stop <= policy.practice_warning_minutes

    expiry_at: datetime | None = None
    hours_until_expiry: int | None = None
    expiry_warn = False
    if running and resource and resource.expiry_date and resource.batch_job_id is None:
        expiry_at = datetime.combine(
            resource.expiry_date, datetime.min.time(), tzinfo=UTC
        ) + timedelta(days=1)
        delta_h = (expiry_at - _utc_now()).total_seconds() / 3600
        hours_until_expiry = max(math.ceil(delta_h), 0)
        expiry_warn = 0 < delta_h <= policy.expiry_warning_hours

    # auto_stop is more urgent (minutes vs hours), so it takes priority.
    if auto_stop_warn:
        warn_reason: Literal["auto_stop", "expiry"] | None = "auto_stop"
    elif expiry_warn:
        warn_reason = "expiry"
    else:
        warn_reason = None

    return SessionStatusResponse(
        vmid=vmid,
        running=running,
        auto_stop_at=auto_stop_at,
        auto_stop_reason=auto_stop_reason,
        minutes_until_stop=minutes_until_stop,
        expiry_at=expiry_at,
        hours_until_expiry=hours_until_expiry,
        should_warn=warn_reason is not None,
        warn_reason=warn_reason,
        # Only practice-quota sessions can be extended via the button.
        # Expiry extensions go through spec_change_requests, not this endpoint.
        can_extend=running and auto_stop_reason == "practice_quota",
    )


def batch_action(
    *,
    session: Session,
    vmids: list[int],
    action: str,
    user_id: uuid.UUID,
    is_admin: bool,
) -> BatchActionResponse:
    """Batch control/delete for multiple resources."""
    results: list[BatchActionResultItem] = []

    for vmid in vmids:
        try:
            resource_info = proxmox_service.find_resource(vmid)

            # Non-admin users must own the resource
            if not is_admin:
                db_resource = resource_repo.get_resource_by_vmid(
                    session=session, vmid=vmid
                )
                if not db_resource or db_resource.user_id != user_id:
                    results.append(
                        BatchActionResultItem(
                            vmid=vmid,
                            success=False,
                            message="Permission denied",
                        )
                    )
                    continue

            if action == "delete":
                delete(
                    session=session,
                    vmid=vmid,
                    resource_info=resource_info,
                    user_id=user_id,
                    purge=True,
                    force=True,
                )
            else:
                control(
                    session=session,
                    vmid=vmid,
                    action=action,
                    resource_info=resource_info,
                    user_id=user_id,
                )

            results.append(
                BatchActionResultItem(
                    vmid=vmid,
                    success=True,
                    message=f"Resource {vmid} {action} succeeded",
                )
            )
        except Exception as e:
            logger.warning(f"Batch {action} failed for vmid={vmid}: {e}")
            results.append(
                BatchActionResultItem(
                    vmid=vmid,
                    success=False,
                    message=str(e),
                )
            )

    succeeded = sum(1 for r in results if r.success)
    return BatchActionResponse(
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results=results,
    )
