import logging
import math
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlmodel import Session, select

from app.core.authorizers import can_bypass_resource_ownership
from app.exceptions import BadRequestError, PermissionDeniedError, ProxmoxError
from app.models import TeachingClass, TeachingClassStatus, User
from app.models.quick_practice import QuickPracticeSessionMachine
from app.models.vm_request import VMProvisioningStatus, VMRequest, VMRequestStatus
from app.repositories import audit_log as audit_log_repo
from app.repositories import batch_provision as batch_provision_repo
from app.repositories import resource as resource_repo
from app.repositories import resource_share as share_repo
from app.repositories import spec_change_request as spec_request_repo
from app.repositories import vm_request as vm_request_repo
from app.schemas import ResourcePublic
from app.schemas.resource import (
    BatchActionResponse,
    BatchActionResultItem,
    ExtendSessionResponse,
    ResourceStatus,
    SessionStatusResponse,
)
from app.services.network import firewall_service
from app.services.proxmox import proxmox_service
from app.services.resource.access import require_resource_management
from app.services.scheduling.recurrence import (
    get_schedule_policy,
    is_in_window,
)
from app.services.user import audit_service

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _enforce_start_window(*, session: Session, vmid: int) -> None:
    resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    if resource and resource.teaching_class_id:
        from app.models import TeachingClass, TeachingClassStatus

        teaching_class = session.get(TeachingClass, resource.teaching_class_id)
        if teaching_class is None or teaching_class.status != TeachingClassStatus.active:
            raise BadRequestError("This teaching-class resource is no longer active.")
        # The recurrence window controls automatic classroom boot/shutdown only.
        # Enrolled students may manually start their assigned machine for
        # after-class practice while the class remains active.  The start path
        # applies the normal practice-session auto-stop policy below.
        return

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


def _normalize_live_resource_status(value: object) -> ResourceStatus:
    status = str(value or "").strip().lower()
    if status in {"running", "stopped", "paused"}:
        return status  # type: ignore[return-value]
    return "unknown"


def _allocation_scope(value: object) -> Literal["personal", "teaching_class"]:
    return "teaching_class" if value == "teaching_class" else "personal"


def _control_policy(value: object) -> Literal["owner", "class_member"]:
    return "class_member" if value == "class_member" else "owner"


def _placeholder_resource_status(req) -> ResourceStatus:
    if req.provisioning_status == VMProvisioningStatus.failed or req.provisioning_error:
        return "failed"
    start_at = _ensure_utc(req.start_at)
    if start_at is not None and start_at > _utc_now():
        return "scheduled"
    return "provisioning"


def practice_request_ids(session: Session) -> set[uuid.UUID]:
    """快速練習機器對應的申請單 id，用來判斷規格是否已被環境版本鎖定。"""
    return set(
        session.exec(select(QuickPracticeSessionMachine.vm_request_id)).all()
    )


def _is_practice_resource(
    session: Session | None,
    db_resource,
    known_ids: set[uuid.UUID] | None,
) -> bool:
    request_id = getattr(db_resource, "request_id", None)
    if request_id is None:
        return False
    if known_ids is not None:
        return request_id in known_ids
    if session is None:
        return False
    return (
        session.exec(
            select(QuickPracticeSessionMachine.vm_request_id).where(
                QuickPracticeSessionMachine.vm_request_id == request_id
            )
        ).first()
        is not None
    )


def _build_resource_public(
    resource: dict, db_resource, node: str, vm_type: str,
    session: Session | None = None,
    known_practice_ids: set[uuid.UUID] | None = None,
) -> ResourcePublic:
    vmid = resource.get("vmid")
    class_governed = bool(
        db_resource and db_resource.allocation_scope == "teaching_class"
    )
    # 課堂與快速練習的機器都照課程環境版本建立，規格不接受個別調整。
    spec_fixed = class_governed or _is_practice_resource(
        session, db_resource, known_practice_ids
    )
    class_available = not class_governed
    if class_governed and session is not None and db_resource.teaching_class_id:
        teaching_class = session.get(TeachingClass, db_resource.teaching_class_id)
        class_available = bool(
            teaching_class
            and teaching_class.status == TeachingClassStatus.active
        )
    ip_address = proxmox_service.get_ip_address(node, vmid, vm_type)
    if session is not None:
        # 線上：寫回快取；離線：回退 DB 快取。DB 出錯時 sync_ip_cache 會 rollback。
        ip_address = resource_repo.sync_ip_cache(
            session=session, vmid=vmid, live_ip=ip_address
        )
    quick_practice_limited = False
    if session is not None and db_resource and db_resource.request_id:
        source_request = session.get(VMRequest, db_resource.request_id)
        quick_practice_limited = bool(
            source_request and source_request.request_kind == "quick_template"
        )
    return ResourcePublic(
        vmid=resource.get("vmid"),
        request_id=db_resource.request_id if db_resource else None,
        teaching_class_id=db_resource.teaching_class_id if db_resource else None,
        allocation_scope=_allocation_scope(
            db_resource.allocation_scope if db_resource else None
        ),
        control_policy=_control_policy(
            db_resource.control_policy if db_resource else None
        ),
        name=_from_punycode_hostname(resource.get("name", "")),
        status=_normalize_live_resource_status(resource.get("status")),
        node=node,
        type=vm_type,
        can_control=class_available,
        can_delete=not class_governed,
        can_request_spec_change=not spec_fixed,
        can_extend=class_available and not quick_practice_limited,
        environment_type=db_resource.environment_type if db_resource else None,
        os_info=db_resource.os_info if db_resource else None,
        expiry_date=db_resource.expiry_date if db_resource else None,
        ip_address=ip_address,
        ssh_public_key=db_resource.ssh_public_key if db_resource else None,
        has_login_password=bool(
            db_resource.login_password_encrypted if db_resource else None
        ),
        cpu=resource.get("cpu"),
        maxcpu=resource.get("maxcpu"),
        mem=resource.get("mem"),
        maxmem=resource.get("maxmem"),
        uptime=resource.get("uptime"),
        auto_stop_at=(
            _ensure_utc(getattr(db_resource, "auto_stop_at", None)) if db_resource else None
        ),
        auto_stop_reason=(
            getattr(db_resource, "auto_stop_reason", None) if db_resource else None
        ),
        idle_since=_ensure_utc(db_resource.idle_since) if db_resource else None,
        scheduled_deletion_at=(
            _ensure_utc(getattr(db_resource, "scheduled_deletion_at", None))
            if db_resource
            else None
        ),
        mining_exempt=bool(db_resource.mining_exempt) if db_resource else False,
        tags=_parse_tags(resource.get("tags")),
    )


def _parse_tags(raw: object) -> list[str]:
    """cluster/resources 的 tags 是 ``;`` 分隔字串。"""
    if not raw:
        return []
    return [tag for tag in str(raw).replace(",", ";").split(";") if tag]


def _mark_shared(public: ResourcePublic, db_resource, session: Session) -> None:
    """被分享的機器只能「用」，擁有者層級的動作全部關掉。"""
    owner = session.get(User, db_resource.user_id)
    public.access_role = "shared"
    public.can_manage = False
    public.can_delete = False
    public.can_request_spec_change = False
    public.can_extend = False
    public.owner_email = owner.email if owner else None


def annotate_access_for_user(
    *, session: Session, public: ResourcePublic, user
) -> ResourcePublic:
    """替單筆資源標上目前使用者的關係（擁有者／被分享／課堂成員／管理員）與可管理與否。"""
    if public.vmid is None:
        return public
    db_resource = resource_repo.get_resource_by_vmid(session=session, vmid=public.vmid)
    if db_resource is None:
        public.access_role = "admin" if can_bypass_resource_ownership(user) else "owner"
        return public

    if db_resource.user_id == user.id:
        public.access_role = (
            "class_member"
            if db_resource.allocation_scope == "teaching_class"
            else "owner"
        )
    elif can_bypass_resource_ownership(user):
        public.access_role = "admin"
    elif share_repo.get_share(session=session, vmid=public.vmid, user_id=user.id):
        _mark_shared(public, db_resource, session)
        return public
    else:
        # 例如課堂老師：不是擁有者但有管理權
        public.access_role = "admin"

    try:
        require_resource_management(session=session, user=user, vmid=public.vmid)
        public.can_manage = True
    except PermissionDeniedError:
        public.can_manage = False
    return public


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
        known_practice_ids = practice_request_ids(session)
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
                _build_resource_public(
                    r, db_resource, vm_node, vm_type, session, known_practice_ids
                )
            )
        return result
    except Exception as e:
        logger.error(f"Failed to get resources: {e}")
        raise ProxmoxError(f"Failed to get resources: {e}")


DELETED_TOMBSTONE_DAYS = 30

# Marker written onto a VMRequest's resource_warning / provisioning_error /
# review_comment when the user explicitly deletes the live resource. Used
# by list_by_user to suppress the now-defunct approved request from being
# resurrected as a "failed" placeholder, and by the frontend to hide the
# consumed request from the applications list.
RESOURCE_DELETED_BY_USER_MARKER = "Resource deleted by user"
RESOURCE_DELETED_ORPHAN_MARKER = "Resource deleted (orphan DB cleanup)"
RESOURCE_CONVERTED_TO_TEMPLATE_MARKER = "Resource converted to template"
_RESOURCE_DELETED_MARKERS = frozenset(
    {
        RESOURCE_DELETED_BY_USER_MARKER,
        RESOURCE_DELETED_ORPHAN_MARKER,
        RESOURCE_CONVERTED_TO_TEMPLATE_MARKER,
    }
)


def mark_linked_request_consumed(
    *, session: Session, vmid: int, marker: str, commit: bool = False
) -> dict[str, Any] | None:
    """把連結到 vmid 的 approved 申請單標為已消耗（機器已刪除或轉為範本）。

    provisioning_status=failed 讓排程器不再接管該申請單；marker 寫入
    resource_warning / review_comment 讓資源頁與審核頁不再顯示它。

    回傳標記前的欄位快照（供 ``restore_linked_request`` 在 Proxmox 端刪除
    失敗時還原）；沒有連結的申請單時回傳 None。
    """
    linked_request = vm_request_repo.get_latest_approved_vm_request_by_vmid(
        session=session, vmid=vmid,
    )
    if linked_request is None:
        return None
    snapshot: dict[str, Any] = {
        "request": linked_request,
        "provisioning_status": linked_request.provisioning_status,
        "provisioning_error": linked_request.provisioning_error,
        "resource_warning": linked_request.resource_warning,
        "review_comment": linked_request.review_comment,
    }
    linked_request.provisioning_status = VMProvisioningStatus.failed
    linked_request.provisioning_error = marker
    linked_request.resource_warning = marker
    linked_request.review_comment = marker
    session.add(linked_request)
    if commit:
        session.commit()
    return snapshot


def _cancel_open_spec_change_requests(
    *, session: Session, vmid: int, marker: str
) -> None:
    """機器刪除時作廢該 vmid 處理中的規格調整申請；失敗不阻斷刪除。"""
    try:
        cancelled = spec_request_repo.cancel_open_spec_change_requests_for_vmid(
            session=session, vmid=vmid, comment=marker, commit=False
        )
        if cancelled:
            logger.info(
                "Cancelled %s open spec change request(s) for vmid=%s", cancelled, vmid
            )
    except Exception as exc:
        session.rollback()
        logger.warning(
            "Failed to cancel spec change requests for vmid=%s: %s", vmid, exc
        )


def restore_linked_request(
    *, session: Session, snapshot: dict[str, Any] | None
) -> None:
    """還原 ``mark_linked_request_consumed`` 寫入的標記（刪除在 Proxmox 端失敗時用）。"""
    if not snapshot:
        return
    request = snapshot["request"]
    request.provisioning_status = snapshot["provisioning_status"]
    request.provisioning_error = snapshot["provisioning_error"]
    request.resource_warning = snapshot["resource_warning"]
    request.review_comment = snapshot["review_comment"]
    session.add(request)
    session.commit()


def _restore_after_failed_delete(
    *, session: Session, snapshot: dict[str, Any] | None, vmid: int
) -> None:
    if snapshot is None:
        return
    try:
        session.rollback()
        restore_linked_request(session=session, snapshot=snapshot)
    except Exception as exc:
        logger.warning(
            "Failed to restore linked request after aborted delete of %s: %s",
            vmid, exc,
        )


def list_by_user(
    *, session: Session, user_id: uuid.UUID
) -> list[ResourcePublic]:
    from sqlmodel import select

    from app.models.vm_request import VMRequest

    try:
        result: list[ResourcePublic] = []
        shown_vmids: set[int] = set()

        # 1. Live resources owned by the user (from Proxmox + DB join),
        #    plus machines other owners shared with them (use-only access).
        user_resources = resource_repo.get_resources_by_user(
            session=session, user_id=user_id
        )
        owned_vmids = {r.vmid: r for r in user_resources}
        shared_rows: dict[int, Any] = {}
        for share in share_repo.list_shares_for_user(session=session, user_id=user_id):
            if share.resource_vmid in owned_vmids:
                continue
            shared_db = resource_repo.get_resource_by_vmid(
                session=session, vmid=share.resource_vmid
            )
            if shared_db is not None:
                shared_rows[share.resource_vmid] = shared_db
        if user_resources or shared_rows:
            known_practice_ids = practice_request_ids(session)
            try:
                for r in proxmox_service.list_all_resources():
                    if r.get("template") == 1:
                        continue
                    vmid = r.get("vmid")
                    db_row = owned_vmids.get(vmid) or shared_rows.get(vmid)
                    if db_row is None:
                        continue
                    public = _build_resource_public(
                        r,
                        db_row,
                        r.get("node", ""),
                        r.get("type", ""),
                        session,
                        known_practice_ids,
                    )
                    if vmid in shared_rows:
                        _mark_shared(public, db_row, session)
                    elif db_row.allocation_scope == "teaching_class":
                        public.access_role = "class_member"
                        public.can_manage = False
                    result.append(public)
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
                                teaching_class_id=db_r.teaching_class_id,
                                allocation_scope=_allocation_scope(
                                    db_r.allocation_scope
                                ),
                                control_policy=_control_policy(db_r.control_policy),
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
                                can_delete=False,
                                can_request_spec_change=False,
                                can_extend=False,
                                environment_type=db_r.environment_type,
                                os_info=db_r.os_info,
                                expiry_date=db_r.expiry_date,
                                ssh_public_key=db_r.ssh_public_key,
                                has_login_password=bool(
                                    db_r.login_password_encrypted
                                ),
                            )
                        )
                        shown_vmids.add(db_r.vmid)

        # 2. Approved requests not yet visible in Proxmox.
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
            # Approved requests whose live resource was deleted by the user
            # are kept on disk for audit but must NOT resurrect as "failed"
            # placeholders in the resources list.
            if req.resource_warning in _RESOURCE_DELETED_MARKERS:
                continue
            result.append(
                ResourcePublic(
                    vmid=req.vmid,
                    request_id=req.id,
                    name=_from_punycode_hostname(req.hostname),
                    status=_placeholder_resource_status(req),
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

        # 3. Overlay in-progress deletions. Deletion runs from a background
        # queue (shutdown → wait → destroy), so the VM stays visible in
        # Proxmox as "stopped" for a while after the user hits delete;
        # without this overlay the card would reappear as 已關機.
        if shown_vmids:
            from app.services.resource import deletion_service  # noqa: PLC0415

            deleting_map = deletion_service.list_active_for_vmids(
                session=session, vmids=list(shown_vmids)
            )
            for item in result:
                if item.vmid in deleting_map:
                    item.status = "deleting"
                    item.can_control = False

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


def _wait_until_stopped(
    node: str,
    vmid: int,
    resource_type: str,
    *,
    timeout_seconds: int,
    poll_interval: float = 2.0,
) -> bool:
    """Poll resource status until it reports ``stopped`` or the timeout elapses.

    Transient status-query errors are tolerated (keep polling); they must NOT
    be treated as "stopped", otherwise we would attempt to delete a VM that is
    still running.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            status = proxmox_service.get_status(node, vmid, resource_type)
            if status.get("status") == "stopped":
                return True
        except Exception as exc:
            logger.warning(
                "Status poll failed for resource %s while waiting for stop: %s",
                vmid, exc,
            )
        time.sleep(poll_interval)
    return False


def _ensure_stopped_before_delete(
    node: str,
    vmid: int,
    resource_type: str,
    *,
    force: bool,
    shutdown_timeout: int = 60,
    stop_timeout: int = 30,
) -> None:
    """Shut down a running resource before deletion.

    Graceful ``shutdown`` first; if it doesn't stop within ``shutdown_timeout``
    (e.g. no guest agent / hung OS) fall back to a hard ``stop``. With
    ``force=True`` the graceful phase is skipped. Raises ``ProxmoxError`` if
    the resource is still running afterwards — deletion must never proceed on
    a running resource.
    """
    if not force:
        try:
            proxmox_service.control(node, vmid, resource_type, "shutdown")
            if _wait_until_stopped(
                node, vmid, resource_type, timeout_seconds=shutdown_timeout
            ):
                return
            logger.warning(
                "Resource %s did not stop within %ss after graceful shutdown; "
                "falling back to hard stop",
                vmid, shutdown_timeout,
            )
        except Exception as exc:
            logger.warning(
                "Graceful shutdown of resource %s failed (%s); "
                "falling back to hard stop",
                vmid, exc,
            )

    proxmox_service.control(node, vmid, resource_type, "stop")
    if not _wait_until_stopped(
        node, vmid, resource_type, timeout_seconds=stop_timeout
    ):
        raise ProxmoxError(
            f"Resource {vmid} is still running after shutdown/stop; "
            "aborting deletion"
        )


def delete(
    *,
    session: Session,
    vmid: int,
    resource_info: dict,
    user_id: uuid.UUID,
    purge: bool = True,
    force: bool = False,
) -> dict:
    tracked_resource = resource_repo.get_resource_by_vmid(
        session=session, vmid=vmid
    )
    teaching_class_id = (
        tracked_resource.teaching_class_id if tracked_resource else None
    )
    try:
        node = resource_info["node"]
        resource_type = resource_info["type"]

        # 先把連結的申請單標成已消耗並 commit，再碰 Proxmox。排程器每個 tick
        # 都以「approved 且未 failed」撈單；若機器先在 Proxmox 消失而標記還沒
        # 落地，stale-VMID 回復路徑會把同名機器重新 clone 出來（會復活）。
        # 標記先落地，任何找不到機器的 tick 重讀 DB 都會看到 failed 而放手；
        # 同時也避免關機等待期間被排程器自動開機。Proxmox 端失敗時再還原。
        consumed = mark_linked_request_consumed(
            session=session,
            vmid=vmid,
            marker=RESOURCE_DELETED_BY_USER_MARKER,
            commit=True,
        )

        try:
            # Re-check live status: deletion runs from a queue, so the snapshot
            # in resource_info may be stale by the time we execute.
            try:
                current_status = proxmox_service.get_status(
                    node, vmid, resource_type
                ).get("status", "")
            except Exception as exc:
                logger.warning(
                    "Failed to fetch live status for resource %s before delete: %s",
                    vmid, exc,
                )
                current_status = resource_info.get("status", "")

            if current_status == "running":
                _ensure_stopped_before_delete(
                    node, vmid, resource_type, force=force
                )

            # Delete the resource
            delete_params = {}
            if purge:
                delete_params["purge"] = 1
                if resource_type == "qemu":
                    delete_params["destroy-unreferenced-disks"] = 1

            proxmox_service.delete_resource(
                node, vmid, resource_type, **delete_params
            )
        except Exception:
            # 機器還在：把申請單還原成可排程狀態，否則使用者會看到機器活著
            # 但申請單已被隱藏、也不再自動關機。
            _restore_after_failed_delete(
                session=session, snapshot=consumed, vmid=vmid
            )
            raise

        # Clean up reverse proxy rules and Cloudflare DNS records for this VM
        try:
            from app.services.network import reverse_proxy_service  # noqa: PLC0415
            reverse_proxy_service.remove_reverse_proxy_rules_for_vmid(session, vmid)
        except Exception as exc:
            logger.warning("Failed to clean up reverse proxy rules for VM %s: %s", vmid, exc)

        # NAT 規則的 vmid 外鍵會連帶刪除 DB 紀錄，但不會重寫 Gateway 上的
        # haproxy 設定；不明確清一次，轉發會留在原地指向已釋放的 IP。
        try:
            from app.services.network import nat_service  # noqa: PLC0415
            nat_service.remove_nat_rules_for_vmid(session, vmid)
        except Exception as exc:
            logger.warning("Failed to clean up NAT rules for VM %s: %s", vmid, exc)

        # Release IP allocation
        try:
            from app.services.network import ip_management_service  # noqa: PLC0415
            ip_management_service.release_ip(session, vmid)
        except Exception as exc:
            logger.warning("Failed to release IP for VM %s: %s", vmid, exc)

        # Unlink deleted VMID from historical batch tasks so class job status won't
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

        # 規格調整申請也要跟著作廢：VMID 會被新機器回收，留著會核准／套用到
        # 別人的機器上。resource_vmid 的 SET NULL 只擋審核，這裡把狀態收掉。
        _cancel_open_spec_change_requests(
            session=session, vmid=vmid, marker=RESOURCE_DELETED_BY_USER_MARKER
        )

        if teaching_class_id is not None:
            _mark_class_machine_reclaimed(session=session, vmid=vmid)

        # Remove from database (resource record + all associated audit logs)
        resource_repo.delete_resource(session=session, vmid=vmid)
        audit_log_repo.delete_audit_logs_by_vmid(session=session, vmid=vmid)
        _mark_class_reclaimed_if_empty(
            session=session, teaching_class_id=teaching_class_id
        )

        # The linked approval record was already marked consumed (and
        # committed) before touching Proxmox; it stays on disk for
        # audit/review reporting but is no longer schedulable.
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
    batch task unlinking, DB row deletion, VM request scheduling stop, audit log).
    Safe to call when the VM is already gone from Proxmox.
    """
    tracked_resource = resource_repo.get_resource_by_vmid(
        session=session, vmid=vmid
    )
    teaching_class_id = (
        tracked_resource.teaching_class_id if tracked_resource else None
    )

    try:
        from app.services.network import reverse_proxy_service  # noqa: PLC0415
        reverse_proxy_service.remove_reverse_proxy_rules_for_vmid(session, vmid)
    except Exception as exc:
        logger.warning("Orphan cleanup: failed to remove reverse proxy rules for vmid=%s: %s", vmid, exc)

    try:
        from app.services.network import nat_service  # noqa: PLC0415
        nat_service.remove_nat_rules_for_vmid(session, vmid)
    except Exception as exc:
        logger.warning("Orphan cleanup: failed to remove NAT rules for vmid=%s: %s", vmid, exc)

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

    if teaching_class_id is not None:
        _mark_class_machine_reclaimed(session=session, vmid=vmid)
    _cancel_open_spec_change_requests(
        session=session, vmid=vmid, marker=RESOURCE_DELETED_ORPHAN_MARKER
    )
    resource_repo.delete_resource(session=session, vmid=vmid)
    audit_log_repo.delete_audit_logs_by_vmid(session=session, vmid=vmid)
    _mark_class_reclaimed_if_empty(
        session=session, teaching_class_id=teaching_class_id
    )

    mark_linked_request_consumed(
        session=session, vmid=vmid, marker=RESOURCE_DELETED_ORPHAN_MARKER,
    )

    audit_service.log_action(
        session=session,
        user_id=user_id,
        vmid=vmid,
        action="resource_delete",
        details=f"Orphan DB cleanup for vmid={vmid} (VM not found in Proxmox)",
    )
    logger.info("Orphan DB record for vmid=%s cleaned up", vmid)


def _mark_class_reclaimed_if_empty(
    *, session: Session, teaching_class_id: uuid.UUID | None
) -> None:
    if teaching_class_id is None:
        return
    from app.models import TeachingClass, TeachingClassStatus

    teaching_class = session.get(TeachingClass, teaching_class_id)
    if teaching_class is None:
        return
    if teaching_class.status != TeachingClassStatus.archived:
        teaching_class.status = TeachingClassStatus.partial_failed
        teaching_class.updated_at = _utc_now()
    if resource_repo.get_resources_by_teaching_class(
        session=session, teaching_class_id=teaching_class_id
    ):
        session.add(teaching_class)
        session.commit()
        return
    teaching_class.resources_reclaimed_at = _utc_now()
    session.add(teaching_class)
    session.commit()


def _mark_class_machine_reclaimed(*, session: Session, vmid: int) -> None:
    from sqlmodel import select

    from app.models import TeachingClassStudentMachine

    mappings = session.exec(
        select(TeachingClassStudentMachine).where(
            TeachingClassStudentMachine.vmid == vmid
        )
    ).all()
    for mapping in mappings:
        mapping.vmid = None
        mapping.status = "reclaimed"
        mapping.error = None
        session.add(mapping)
    if mappings:
        session.flush()


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

    resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    if resource and resource.teaching_class_id and resource.batch_job_id:
        from app.models import BatchProvisionJob

        job = session.get(BatchProvisionJob, resource.batch_job_id)
        if job and is_in_window(job.next_window_start, job.next_window_end, now):
            resource_repo.set_auto_stop(
                session=session,
                vmid=vmid,
                auto_stop_at=job.next_window_end,
                auto_stop_reason="window_grace",
            )
            return

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
    """Extend an active personal or teaching-class machine session.

    Only allowed when:
    - Caller owns the resource
    - An auto-stop is scheduled for either a practice quota or course window

    The configured practice-session duration is added after the later of now
    and the current stop time, so extending early never shortens the session.
    """
    resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    if resource is None:
        raise BadRequestError("Resource not found")
    if resource.user_id != user_id:
        raise BadRequestError("Not the owner of this resource")
    request_id = getattr(resource, "request_id", None)
    request = session.get(VMRequest, request_id) if request_id else None
    if request and request.request_kind == "quick_template":
        raise BadRequestError("Quick-practice sessions have a fixed time limit.")
    if resource.auto_stop_reason not in {"practice_quota", "window_grace"}:
        raise BadRequestError(
            "Session can only be extended while an automatic stop is scheduled."
        )

    policy = get_schedule_policy(session=session)
    now = _utc_now()
    current_stop = _ensure_utc(resource.auto_stop_at)
    extension_base = max(now, current_stop) if current_stop else now
    new_stop = extension_base + timedelta(hours=policy.practice_session_hours)
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
      configured warning window (class practice quota or course-window grace).
    - ``warn_reason="expiry"``: VM's ``expiry_date`` is within
      ``policy.expiry_warning_hours`` (admin-configurable, defaults to 24 h).

    auto_stop wins when both apply, since it's typically minutes away while
    expiry is at least hours.
    """
    resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    running = resource_info.get("status") == "running"
    auto_stop_at = resource.auto_stop_at if resource else None
    auto_stop_reason = resource.auto_stop_reason if resource else None
    request_id = getattr(resource, "request_id", None) if resource else None
    request = session.get(VMRequest, request_id) if request_id else None
    quick_practice_limited = bool(
        request and request.request_kind == "quick_template"
    )

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
        # Practice and teaching-window sessions can both be extended by the owner.
        # Expiry extensions go through spec_change_requests, not this endpoint.
        can_extend=(
            running
            and not quick_practice_limited
            and auto_stop_reason in {"practice_quota", "window_grace"}
        ),
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
