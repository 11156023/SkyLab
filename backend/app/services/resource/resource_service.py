import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from app.exceptions import BadRequestError, ProxmoxError
from app.models.vm_request import VMRequestStatus
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
        if db_resource and db_resource.ip_address:
            ip_address = db_resource.ip_address
    return ResourcePublic(
        vmid=resource.get("vmid"),
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


def list_by_user(
    *, session: Session, user_id: uuid.UUID
) -> list[ResourcePublic]:
    try:
        user_resources = resource_repo.get_resources_by_user(
            session=session, user_id=user_id
        )
        if not user_resources:
            return []

        owned_vmids = {r.vmid: r for r in user_resources}
        resources = proxmox_service.list_all_resources()
        result = []
        for r in resources:
            if r.get("template") == 1:
                continue
            vmid = r.get("vmid")
            if vmid not in owned_vmids:
                continue
            vm_type = r.get("type")
            vm_node = r.get("node")
            result.append(
                _build_resource_public(
                    r, owned_vmids[vmid], vm_node, vm_type, session
                )
            )
        return result
    except Exception as e:
        logger.error(f"Failed to get user resources: {e}")
        raise ProxmoxError(f"Failed to get user resources: {e}")


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
            linked_request.status = VMRequestStatus.rejected
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
    """Live session info for the student UI (polled every ~30s)."""
    resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    running = resource_info.get("status") == "running"
    auto_stop_at = resource.auto_stop_at if resource else None
    auto_stop_reason = resource.auto_stop_reason if resource else None

    minutes_until_stop: int | None = None
    should_warn = False
    if running and auto_stop_at:
        delta = auto_stop_at - _utc_now()
        minutes_until_stop = max(int(delta.total_seconds() // 60), 0)
        policy = get_schedule_policy(session=session)
        should_warn = minutes_until_stop <= policy.practice_warning_minutes

    return SessionStatusResponse(
        vmid=vmid,
        running=running,
        auto_stop_at=auto_stop_at,
        auto_stop_reason=auto_stop_reason,
        minutes_until_stop=minutes_until_stop,
        should_warn=should_warn,
        # Only practice-quota sessions can be extended via the button.
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
