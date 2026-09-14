"""規格調整申請：送單 → 管理員審核 → 申請人自行套用（關機 → 改規格 → 開機）。

核准不再直接寫 Proxmox。執行中的 QEMU 改 cores/memory 只會進 pending、
要重開機才生效，若在核准當下就寫入，系統會顯示「已套用」但機器紋風不動。
因此核准只做配額檢查與狀態變更，由申請人挑時間按「套用」，背景任務負責
整個電源循環，完成後才寫 applied_at。
"""

import logging
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Literal

from sqlmodel import Session

from app.core.authorizers import (
    can_bypass_resource_ownership,
    require_resource_access,
)
from app.core.i18n import t
from app.exceptions import (
    AppError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    ProxmoxError,
)
from app.infrastructure.worker import background_tasks
from app.models import SpecChangeRequestStatus, SpecChangeType
from app.repositories import resource as resource_repo
from app.repositories import spec_change_request as spec_request_repo
from app.schemas import (
    SpecChangeApplyAccepted,
    SpecChangeRequestCreate,
    SpecChangeRequestPublic,
    SpecChangeRequestReview,
    SpecChangeRequestsPublic,
)
from app.schemas.spec_change_request import SpecChangeApplyStatus
from app.services.proxmox import proxmox_service
from app.services.resource import quota_service, resource_service
from app.services.resource.access import require_resource_management
from app.services.user import audit_service

logger = logging.getLogger(__name__)

# 優雅關機等待；逾時改強制斷電再等一段（比照 template_service._ensure_stopped）
SHUTDOWN_TIMEOUT_SECONDS = 90.0
STOP_TIMEOUT_SECONDS = 30.0
_POLL_INTERVAL_SECONDS = 2.0

ResourceType = Literal["qemu", "lxc"]


# ---------------------------------------------------------------------------
# 狀態描述 / 序列化
# ---------------------------------------------------------------------------


def _apply_task_id(request_id: uuid.UUID) -> str:
    return f"spec-apply-{request_id}"


def _rtype(resource_info: dict[str, Any]) -> ResourceType:
    return "lxc" if str(resource_info.get("type") or "") == "lxc" else "qemu"


def _apply_status(request: Any) -> SpecChangeApplyStatus | None:
    """已核准申請的套用進度；其他狀態回 None。

    「套用中」以背景執行器實際有這個任務為準：服務重啟後 apply_started_at
    還在但任務已不存在，回 interrupted 讓申請人可以重按套用。
    """
    if request.status != SpecChangeRequestStatus.approved:
        return None
    if request.applied_at is not None:
        return "applied"
    if request.apply_error:
        return "failed"
    if request.apply_started_at is not None:
        if background_tasks.is_active(_apply_task_id(request.id)):
            return "applying"
        return "interrupted"
    return "ready"


def _describe_status(request: Any) -> str:
    if request.status == SpecChangeRequestStatus.pending:
        return t("spec_change.status_pending")
    if request.status == SpecChangeRequestStatus.rejected:
        return t("spec_change.status_rejected")
    if request.status == SpecChangeRequestStatus.cancelled:
        return t("spec_change.status_cancelled")
    apply_status = _apply_status(request)
    if apply_status == "applied":
        return t("spec_change.status_applied")
    if apply_status == "applying":
        return t("spec_change.status_applying")
    return t("spec_change.status_awaiting_apply")


def _describe_changes(request: Any) -> list[str]:
    """稽核紀錄用的英文摘要（audit log 不做在地化）。"""
    changes: list[str] = []
    if request.requested_cpu is not None:
        changes.append(f"CPU: {request.current_cpu} -> {request.requested_cpu} cores")
    if request.requested_memory is not None:
        changes.append(
            f"Memory: {request.current_memory} -> {request.requested_memory}MB"
        )
    if request.requested_disk is not None:
        changes.append(f"Disk: {request.current_disk} -> {request.requested_disk}GB")
    requested_expiry = getattr(request, "requested_expiry_date", None)
    if requested_expiry is not None:
        current_expiry = getattr(request, "current_expiry_date", None)
        changes.append(
            f"Expiry: {current_expiry.isoformat() if current_expiry else 'unlimited'} "
            f"-> {requested_expiry.isoformat()}"
        )
    return changes


def _to_public(
    request: Any, *, resource_name: str | None = None
) -> SpecChangeRequestPublic:
    return SpecChangeRequestPublic(
        id=request.id,
        vmid=request.vmid,
        resource_name=resource_name,
        resource_exists=request.resource_vmid is not None,
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
        current_expiry_date=getattr(request, "current_expiry_date", None),
        requested_expiry_date=getattr(request, "requested_expiry_date", None),
        status=request.status,
        reviewer_id=request.reviewer_id,
        review_comment=request.review_comment,
        reviewed_at=request.reviewed_at,
        applied_at=request.applied_at,
        apply_started_at=request.apply_started_at,
        apply_error=request.apply_error,
        apply_status=_apply_status(request),
        created_at=request.created_at,
    )


def _resource_names() -> dict[int, str]:
    """vmid → 機器名稱，給列表顯示用。best-effort：Proxmox 掛了列表也不能 500。"""
    try:
        listing = proxmox_service.list_all_resources()
    except Exception:
        logger.warning("Failed to list resources for spec change names", exc_info=True)
        return {}
    names: dict[int, str] = {}
    for item in listing:
        vmid = item.get("vmid")
        if vmid is None:
            continue
        names[int(vmid)] = str(item.get("name") or "")
    return names


def _to_public_list(requests: list[Any]) -> list[SpecChangeRequestPublic]:
    names = _resource_names() if requests else {}
    return [_to_public(r, resource_name=names.get(r.vmid) or None) for r in requests]


# ---------------------------------------------------------------------------
# 建立
# ---------------------------------------------------------------------------


def _check_ownership_and_get_info(
    *, session: Session, user: Any, vmid: int
) -> dict[str, Any]:
    """Check resource ownership and return Proxmox resource info."""
    if not can_bypass_resource_ownership(user):
        require_resource_management(session=session, user=user, vmid=vmid)

    return proxmox_service.find_resource(vmid)


def _reject_fixed_spec_resource(*, session: Session, vmid: int) -> None:
    """課堂與快速練習的機器照課程環境版本建立，規格不接受個別調整。

    ``ResourcePublic.can_request_spec_change`` 一直有算這件事，但沒有任何地方
    讀它——所以旗標說不行、API 還是收單。守衛放在這裡才算數。
    """
    resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    if resource is None:
        return
    if resource.allocation_scope == "teaching_class":
        raise BadRequestError(t("spec_change.class_machine_spec_fixed"))
    if resource_service._is_practice_resource(session, resource, None):
        raise BadRequestError(t("spec_change.practice_machine_spec_fixed"))


def _get_current_specs(
    node: str, vmid: int, resource_type: ResourceType
) -> dict[str, Any]:
    return proxmox_service.get_current_specs(node, vmid, resource_type)


# 到期日最多一次延到一年後，超過的交給管理員直接改
EXPIRY_MAX_EXTENSION_DAYS = 366


def _validate_expiry_request(
    *, session: Session, vmid: int, request_in: SpecChangeRequestCreate
) -> date | None:
    """延期申請：要有日期、要在今天之後、要比目前到期日晚、不能超過上限。回傳目前到期日。"""
    requested = request_in.requested_expiry_date
    if requested is None:
        raise BadRequestError(t("spec_change.expiry_value_required"))
    resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    current_expiry = resource.expiry_date if resource else None
    today = datetime.now(timezone.utc).date()
    if requested <= today:
        raise BadRequestError(t("spec_change.expiry_must_be_future"))
    if current_expiry is not None and requested <= current_expiry:
        raise BadRequestError(
            t("spec_change.expiry_must_extend", current=current_expiry.isoformat())
        )
    if requested > today + timedelta(days=EXPIRY_MAX_EXTENSION_DAYS):
        raise BadRequestError(
            t("spec_change.expiry_too_far", days=EXPIRY_MAX_EXTENSION_DAYS)
        )
    return current_expiry


def _apply_expiry_extension(session: Session, db_request: Any) -> None:
    """核准即生效：改到期日、清掉 TTL 已發出的通知與刪除排程，讓治理重新起算。"""
    from app.models import Resource  # noqa: PLC0415

    resource = session.get(Resource, db_request.resource_vmid)
    if resource is None:
        raise BadRequestError(t("spec_change.resource_gone_review"))
    resource.expiry_date = db_request.requested_expiry_date
    resource.expiry_notified_at = None
    resource.scheduled_deletion_at = None
    if resource.auto_stop_reason == "ttl_expired":
        resource.auto_stop_at = None
        resource.auto_stop_reason = None
    session.add(resource)
    db_request.applied_at = datetime.now(timezone.utc)
    db_request.apply_error = None
    session.add(db_request)


def create(
    *, session: Session, request_in: SpecChangeRequestCreate, user: Any
) -> SpecChangeRequestPublic:
    vmid = request_in.vmid
    resource_info = _check_ownership_and_get_info(
        session=session, user=user, vmid=vmid
    )
    _reject_fixed_spec_resource(session=session, vmid=vmid)

    # 同一台機器同時只能有一張處理中的申請：磁碟增量以建立時的快照計算，
    # 兩張都核准會疊加（A 100→200 套用後，B 100→150 再 +50 變 250）。
    existing = spec_request_repo.get_open_spec_change_request_by_vmid(
        session=session, vmid=vmid
    )
    if existing is not None:
        raise ConflictError(
            t("spec_change.duplicate_open_request", status=_describe_status(existing))
        )

    node = resource_info["node"]
    specs = _get_current_specs(node, vmid, _rtype(resource_info))

    # 延長到期日：不動 Proxmox，只驗日期；核准時直接寫回 resources.expiry_date
    current_expiry: date | None = None
    if request_in.change_type == SpecChangeType.expiry:
        current_expiry = _validate_expiry_request(session=session, vmid=vmid, request_in=request_in)

    # Validate requested changes
    if (
        request_in.change_type == SpecChangeType.cpu
        and request_in.requested_cpu is None
    ):
        raise BadRequestError(t("spec_change.cpu_value_required"))
    if (
        request_in.change_type == SpecChangeType.memory
        and request_in.requested_memory is None
    ):
        raise BadRequestError(t("spec_change.memory_value_required"))
    if request_in.change_type == SpecChangeType.disk:
        if request_in.requested_disk is None:
            raise BadRequestError(t("spec_change.disk_value_required"))
    if request_in.change_type == SpecChangeType.combined:
        if not any(
            [
                request_in.requested_cpu,
                request_in.requested_memory,
                request_in.requested_disk,
            ]
        ):
            raise BadRequestError(t("spec_change.combined_requires_one"))
    # 磁碟只能增加，combined 帶磁碟時也要擋（以前只有 disk 類型檢查）
    if (
        request_in.requested_disk is not None
        and specs["disk"]
        and request_in.requested_disk <= specs["disk"]
    ):
        raise BadRequestError(
            t("spec_change.disk_increase_only", current=specs["disk"])
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
        current_expiry_date=current_expiry,
        requested_expiry_date=(
            request_in.requested_expiry_date
            if request_in.change_type == SpecChangeType.expiry
            else None
        ),
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
            f"Disk={request_in.requested_disk}GB, "
            f"Expiry={request_in.requested_expiry_date}. "
            f"Reason: {request_in.reason}"
        ),
        commit=False,
    )
    session.commit()

    logger.info(
        f"User {user.email} created spec change request for VMID {vmid}"
    )
    return _to_public(db_request, resource_name=resource_info.get("name"))


# ---------------------------------------------------------------------------
# 查詢
# ---------------------------------------------------------------------------


def list_by_user(
    *, session: Session, user_id: uuid.UUID, skip: int = 0, limit: int = 100
) -> SpecChangeRequestsPublic:
    requests, count = spec_request_repo.get_spec_change_requests_by_user(
        session=session, user_id=user_id, skip=skip, limit=limit
    )
    return SpecChangeRequestsPublic(data=_to_public_list(requests), count=count)


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
    return SpecChangeRequestsPublic(data=_to_public_list(requests), count=count)


# ---------------------------------------------------------------------------
# 審核
# ---------------------------------------------------------------------------


def _refresh_current_specs(
    session: Session,
    db_request: Any,
    *,
    resource_info: dict[str, Any] | None = None,
    strict: bool,
) -> Any:
    """用 Proxmox 實際生效的值覆寫「目前規格」快照。

    建立到審核／套用之間管理員可能直接改過規格，用舊快照算出來的配額增量
    與磁碟增量都會錯。``strict=False`` 時 Proxmox 暫時連不上就沿用快照
    （比照 quota fail-open）；找不到機器一律視為錯誤。
    """
    try:
        info = resource_info or proxmox_service.find_resource(db_request.vmid)
        specs = proxmox_service.get_current_specs(
            info["node"], db_request.vmid, _rtype(info)
        )
    except NotFoundError:
        raise BadRequestError(
            t("spec_change.resource_not_in_proxmox", vmid=db_request.vmid)
        )
    except Exception:
        if strict:
            raise
        logger.warning(
            "Failed to refresh current specs for VMID %s; keeping snapshot",
            db_request.vmid,
            exc_info=True,
        )
        return db_request
    return spec_request_repo.update_spec_change_current_specs(
        session=session,
        request_id=db_request.id,
        current_cpu=specs["cpu"],
        current_memory=specs["memory"],
        current_disk=specs["disk"],
        commit=False,
    )


def _check_quota_delta(session: Session, db_request: Any) -> None:
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


def review(
    *,
    session: Session,
    request_id: uuid.UUID,
    review_data: SpecChangeRequestReview,
    reviewer: Any,
) -> SpecChangeRequestPublic:
    db_request = spec_request_repo.get_spec_change_request_by_id(
        session=session, request_id=request_id, for_update=True
    )
    if not db_request:
        raise NotFoundError(t("spec_change.not_found"))
    if db_request.status != SpecChangeRequestStatus.pending:
        raise BadRequestError(
            t("spec_change.already_reviewed", status=db_request.status.value)
        )
    # schema 已擋非 approved/rejected，這裡是最後防線：以前送 pending 會走進
    # 駁回分支，狀態寫回待審卻蓋上審核人與「Rejected」稽核。
    if review_data.status not in (
        SpecChangeRequestStatus.approved,
        SpecChangeRequestStatus.rejected,
    ):
        raise BadRequestError(t("spec_change.decision_only"))

    try:
        if review_data.status == SpecChangeRequestStatus.approved:
            # resource_vmid 隨資源刪除 SET NULL；VMID 會被新機器回收，
            # 不能拿 vmid 直接去 Proxmox 找（會改到別人的機器）。
            if db_request.resource_vmid is None:
                raise BadRequestError(t("spec_change.resource_gone_review"))
            is_expiry = (
                getattr(db_request, "change_type", None) == SpecChangeType.expiry
            )
            if not is_expiry:
                db_request = _refresh_current_specs(session, db_request, strict=False)
                _check_quota_delta(session, db_request)
            db_request = spec_request_repo.update_spec_change_request_status(
                session=session,
                request_id=request_id,
                status=review_data.status,
                reviewer_id=reviewer.id,
                review_comment=review_data.review_comment,
                commit=False,
            )
            if is_expiry:
                # 延期不需要動機器，核准當下就寫回到期日
                _apply_expiry_extension(session, db_request)
            audit_service.log_action(
                session=session,
                user_id=reviewer.id,
                vmid=db_request.vmid,
                action="spec_change_request",
                details=(
                    f"Approved spec change request {request_id} "
                    f"({', '.join(_describe_changes(db_request))}); "
                    + ("applied immediately" if is_expiry else "awaiting requester to apply")
                ),
                commit=False,
            )
            logger.info(
                "Admin %s approved spec change request %s (awaiting apply)",
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


# ---------------------------------------------------------------------------
# 撤銷
# ---------------------------------------------------------------------------


def cancel(
    *, session: Session, request_id: uuid.UUID, user: Any
) -> SpecChangeRequestPublic:
    """申請人（或管理員）撤銷：待審核、或已核准但尚未套用的申請。"""
    db_request = spec_request_repo.get_spec_change_request_by_id(
        session=session, request_id=request_id, for_update=True
    )
    if not db_request:
        raise NotFoundError(t("spec_change.not_found"))
    require_resource_access(
        user, db_request.user_id, detail=t("spec_change.cancel_forbidden")
    )

    if (
        db_request.status == SpecChangeRequestStatus.approved
        and db_request.applied_at is None
    ):
        if background_tasks.is_active(_apply_task_id(db_request.id)):
            raise ConflictError(t("spec_change.cancel_in_progress"))
    elif db_request.status != SpecChangeRequestStatus.pending:
        raise BadRequestError(
            t("spec_change.cannot_cancel_status", status=_describe_status(db_request))
        )

    by_requester = db_request.user_id == user.id
    db_request = spec_request_repo.update_spec_change_request_status(
        session=session,
        request_id=request_id,
        status=SpecChangeRequestStatus.cancelled,
        reviewer_id=user.id,
        review_comment="Cancelled by requester" if by_requester else "Cancelled by admin",
        commit=False,
    )
    audit_service.log_action(
        session=session,
        user_id=user.id,
        vmid=db_request.vmid,
        action="spec_change_request",
        details=f"Cancelled spec change request {request_id}",
        commit=False,
    )
    session.commit()

    refreshed = spec_request_repo.get_spec_change_request_by_id(
        session=session, request_id=db_request.id
    )
    return _to_public(refreshed)


# ---------------------------------------------------------------------------
# 套用（申請人觸發，背景執行）
# ---------------------------------------------------------------------------


def needs_power_cycle(resource_type: str, was_running: bool, request: Any) -> bool:
    """執行中的 QEMU 改 cores/memory 只會進 pending，必須關機再開才生效。

    LXC 的 cores/memory 是 cgroup 限制、線上即生效；磁碟 resize 兩種類型
    都可線上進行。只有真的需要時才打斷使用者。
    """
    if resource_type != "qemu" or not was_running:
        return False
    return request.requested_cpu is not None or request.requested_memory is not None


def apply(
    *, session: Session, request_id: uuid.UUID, user: Any
) -> SpecChangeApplyAccepted:
    db_request = spec_request_repo.get_spec_change_request_by_id(
        session=session, request_id=request_id, for_update=True
    )
    if not db_request:
        raise NotFoundError(t("spec_change.not_found"))
    require_resource_access(
        user, db_request.user_id, detail=t("spec_change.apply_forbidden")
    )
    if db_request.status != SpecChangeRequestStatus.approved:
        raise BadRequestError(
            t(
                "spec_change.apply_requires_approved",
                status=_describe_status(db_request),
            )
        )
    if db_request.applied_at is not None:
        raise BadRequestError(t("spec_change.already_applied"))
    if db_request.resource_vmid is None:
        raise BadRequestError(t("spec_change.resource_gone_apply"))
    task_id = _apply_task_id(db_request.id)
    if background_tasks.is_active(task_id):
        raise ConflictError(t("spec_change.apply_in_progress"))

    resource_info = proxmox_service.find_resource(db_request.vmid)
    db_request = _refresh_current_specs(
        session, db_request, resource_info=resource_info, strict=True
    )
    # 核准到套用之間使用者可能又拿到新機器，套用才是真正消耗資源的時點
    _check_quota_delta(session, db_request)

    db_request = spec_request_repo.mark_spec_change_apply_started(
        session=session, request_id=db_request.id, commit=False
    )
    audit_service.log_action(
        session=session,
        user_id=user.id,
        vmid=db_request.vmid,
        action="spec_change_apply",
        details=(
            f"Started applying spec change request {request_id}: "
            f"{', '.join(_describe_changes(db_request))}"
        ),
        commit=False,
    )
    session.commit()

    submitted = background_tasks.submit_sync(
        _run_apply,
        db_request.id,
        _snapshot(db_request),
        dict(resource_info),
        user.id,
        name=f"spec-apply:{db_request.vmid}",
        task_id=task_id,
    )
    if not submitted:
        spec_request_repo.mark_spec_change_apply_failed(
            session=session,
            request_id=db_request.id,
            error=t("spec_change.runner_unavailable"),
        )
        raise AppError(status_code=503, message=t("spec_change.runner_unavailable"))

    refreshed = spec_request_repo.get_spec_change_request_by_id(
        session=session, request_id=db_request.id
    )
    return SpecChangeApplyAccepted(
        message=t("spec_change.apply_accepted"),
        task_id=submitted,
        request=_to_public(refreshed, resource_name=resource_info.get("name")),
    )


def _snapshot(db_request: Any) -> SimpleNamespace:
    """背景執行緒不共用 request 的 session，先把要用的欄位抄成普通物件。"""
    return SimpleNamespace(
        id=db_request.id,
        vmid=db_request.vmid,
        current_cpu=db_request.current_cpu,
        current_memory=db_request.current_memory,
        current_disk=db_request.current_disk,
        requested_cpu=db_request.requested_cpu,
        requested_memory=db_request.requested_memory,
        requested_disk=db_request.requested_disk,
    )


def _wait_until_stopped(
    node: str, vmid: int, resource_type: ResourceType, timeout: float
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL_SECONDS)
        try:
            status = proxmox_service.get_status(node, vmid, resource_type)
        except Exception:
            logger.debug("Waiting for %s %s to stop: status check failed", resource_type, vmid)
            continue
        if str(status.get("status") or "").lower() == "stopped":
            return True
    return False


def _ensure_stopped(node: str, vmid: int, resource_type: ResourceType) -> None:
    """優雅關機，逾時強制斷電；仍停不下來就放棄（不能在開機狀態下改 QEMU 規格）。"""
    proxmox_service.control(node, vmid, resource_type, "shutdown")
    if _wait_until_stopped(node, vmid, resource_type, SHUTDOWN_TIMEOUT_SECONDS):
        return
    logger.warning(
        "Graceful shutdown of %s %s timed out; forcing stop", resource_type, vmid
    )
    proxmox_service.control(node, vmid, resource_type, "stop")
    if _wait_until_stopped(node, vmid, resource_type, STOP_TIMEOUT_SECONDS):
        return
    raise ProxmoxError(t("spec_change.stop_timeout", vmid=vmid))


def _run_apply(
    request_id: uuid.UUID,
    request: Any,
    resource_info: dict[str, Any],
    user_id: uuid.UUID,
) -> None:
    """背景任務本體：查電源 →（需要時）關機 → 改規格 →（需要時）開機 → 寫回結果。

    透過 ``asyncio.to_thread`` 執行時會帶著申請人請求的 contextvars，
    所以 ``t()`` 產生的錯誤／警告文字會是申請人當時的語言。
    """
    node = str(resource_info["node"])
    vmid = int(request.vmid)
    resource_type = _rtype(resource_info)

    power_cycle = False
    stopped_by_us = False
    try:
        status = proxmox_service.get_status(node, vmid, resource_type)
        was_running = str(status.get("status") or "").lower() == "running"
        power_cycle = needs_power_cycle(resource_type, was_running, request)
        if power_cycle:
            _ensure_stopped(node, vmid, resource_type)
            stopped_by_us = True
        changes = _apply_spec_changes(db_request=request, resource_info=resource_info)
    except Exception as exc:
        error = str(exc)
        if stopped_by_us:
            # 是我們把機器關掉的，套用失敗也要把機器還給使用者
            try:
                proxmox_service.control(node, vmid, resource_type, "start")
                error += t("spec_change.restarted_after_failure")
            except Exception as start_exc:
                error += t(
                    "spec_change.restart_failed_after_failure", error=start_exc
                )
        _finish_apply(request_id, user_id, vmid, error=error)
        raise

    warning: str | None = None
    if power_cycle:
        try:
            proxmox_service.control(node, vmid, resource_type, "start")
        except Exception as exc:
            warning = t("spec_change.applied_but_start_failed", error=exc)
    _finish_apply(
        request_id,
        user_id,
        vmid,
        changes=changes,
        warning=warning,
        power_cycled=power_cycle,
    )


def _open_session() -> Session:
    """背景執行緒用的獨立 DB session（測試可替換成自己的 engine）。"""
    from app.core.db import engine  # noqa: PLC0415 — 測試環境不一定有 DB

    return Session(engine)


def _finish_apply(
    request_id: uuid.UUID,
    user_id: uuid.UUID,
    vmid: int,
    *,
    changes: list[str] | None = None,
    error: str | None = None,
    warning: str | None = None,
    power_cycled: bool = False,
) -> None:
    """背景任務寫回結果（獨立 session）。DB 寫失敗只記 log：規格已在 Proxmox 生效。"""
    if error is None:
        detail = (
            f"Applied spec change request {request_id}: "
            f"{', '.join(changes or [])} (power_cycled={power_cycled})"
        )
        if warning:
            detail += f"; warning: {warning}"
        logger.info(detail)
    else:
        detail = f"Failed to apply spec change request {request_id}: {error}"
        logger.warning(detail)

    try:
        with _open_session() as session:
            if error is None:
                spec_request_repo.mark_spec_change_applied(
                    session=session,
                    request_id=request_id,
                    warning=warning,
                    commit=False,
                )
            else:
                spec_request_repo.mark_spec_change_apply_failed(
                    session=session, request_id=request_id, error=error, commit=False
                )
            audit_service.log_action(
                session=session,
                user_id=user_id,
                vmid=vmid,
                action="spec_change_apply",
                details=detail,
                commit=False,
            )
            session.commit()
    except Exception:
        logger.exception(
            "Failed to record apply result for spec change request %s", request_id
        )


def _apply_spec_changes(
    *, db_request: Any, resource_info: dict[str, Any] | None = None
) -> list[str]:
    """Apply spec changes to the Proxmox resource and return summaries.

    呼叫端負責電源狀態（執行中的 QEMU 要先關機，否則 cores/memory 只會進
    pending）。這裡只做設定寫入與磁碟 resize。
    """
    try:
        info = resource_info or proxmox_service.find_resource(db_request.vmid)

        node = info["node"]
        resource_type = _rtype(info)

        config_params: dict[str, Any] = {}
        changes: list[str] = []

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
                    t("spec_change.disk_current_unknown", vmid=db_request.vmid)
                )
            disk_increase = db_request.requested_disk - db_request.current_disk
            if disk_increase <= 0:
                raise ProxmoxError(
                    t(
                        "spec_change.disk_increase_only_detail",
                        current=db_request.current_disk,
                        requested=db_request.requested_disk,
                    )
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
        raise ProxmoxError(t("spec_change.apply_failed", error=e))
