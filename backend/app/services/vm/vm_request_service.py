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
from app.core.i18n import t
from app.core.permissions import Permission, has_permission, is_admin
from app.core.security import encrypt_value
from app.exceptions import (
    BadRequestError,
    NotFoundError,
    ProvisioningError,
)
from app.infrastructure.worker import submit_sync
from app.models import (
    User,
    VMProvisioningStatus,
    VMRequest,
    VMRequestStatus,
    VMTemplate,
    VMTemplateStatus,
    VMTemplateVisibility,
)
from app.repositories import governance as governance_repo
from app.repositories import vm_request as vm_request_repo
from app.repositories import vm_template as vm_template_repo
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
from app.services.resource import quota_service
from app.services.scheduling import vm_request_schedule_service
from app.services.user import audit_service
from app.services.vm import (
    vm_request_availability_service,
    vm_request_placement_service,
    workload_advisor,
)
from app.services.vm.placement_service import CurrentPlacementSelection

logger = logging.getLogger(__name__)



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
        gpu_mapping_id=req.gpu_mapping_id,
        gpu_mdev_profile=req.gpu_mdev_profile,
        requested_mode=req.requested_mode,
        auto_decision_reason=req.auto_decision_reason,
        status=req.status,
        reviewer_id=req.reviewer_id,
        review_comment=req.review_comment,
        reviewed_at=req.reviewed_at,
        vmid=req.vmid,
        assigned_node=req.assigned_node,
        desired_node=req.desired_node,
        actual_node=req.actual_node,
        placement_strategy_used=req.placement_strategy_used,
        provisioning_status=req.provisioning_status,
        provisioning_error=req.provisioning_error,
        resource_warning=req.resource_warning,
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
        provisioning_status=VMProvisioningStatus.idle,
        provisioning_error=None,
        commit=False,
    )

    if db_request.request_kind in {"quick_template", "course"}:
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
                t("vm_request.no_node_for_window")
            )
        vm_request_repo.update_vm_request_provisioning(
            session=session,
            db_request=db_request,
            vmid=db_request.vmid,
            assigned_node=selection.node,
            desired_node=selection.node,
            actual_node=db_request.actual_node,
            placement_strategy_used=selection.strategy,
            provisioning_status=(
                VMProvisioningStatus.pending
                if db_request.vmid is not None
                and db_request.actual_node
                and db_request.actual_node != selection.node
                else VMProvisioningStatus.idle
            ),
            provisioning_error=None,
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
        if request.vmid is not None:
            current_node = request.actual_node or request.assigned_node
            if not current_node:
                raise BadRequestError(
                    t("vm_request.provisioned_request_no_node", request_id=request.id)
                )
            vm_request_repo.update_vm_request_provisioning(
                session=session,
                db_request=request,
                vmid=request.vmid,
                assigned_node=current_node,
                desired_node=current_node,
                actual_node=current_node,
                placement_strategy_used=request.placement_strategy_used,
                provisioning_status=VMProvisioningStatus.completed,
                provisioning_error=None,
                commit=False,
            )
            continue
        selection = selections.get(request.id)
        if not selection or not selection.node:
            raise BadRequestError(
                t("vm_request.no_node_after_reservations")
            )
        vm_request_repo.update_vm_request_provisioning(
            session=session,
            db_request=request,
            vmid=request.vmid,
            assigned_node=selection.node,
            desired_node=selection.node,
            actual_node=request.actual_node,
            placement_strategy_used=selection.strategy,
            provisioning_status=(
                VMProvisioningStatus.pending
                if request.vmid is not None
                and request.actual_node
                and request.actual_node != selection.node
                else VMProvisioningStatus.idle
            ),
            provisioning_error=None,
            commit=False,
        )

    return selections.get(db_request.id)


def _validate_template_source(
    *, session: Session, template_vmid: int, user: User, resource_type: str
) -> VMTemplate | None:
    """驗證申請所選的來源，回傳對應的平台母範本（基礎映像則回 None）。

    母範本同時也是 PVE template，所以不能只靠前端清單擋：任何帶
    template_id 的申請都要在建立當下確認範本存在、ready，且申請者確實有
    權限使用它。教師依可見範圍，其他角色則必須是「全部可見」的範本。
    provision 時仍會再查一次範本節點，這裡是為了不讓審核通過後才失敗。
    """
    template = vm_template_repo.get_template_by_pve_vmid(
        session=session, pve_vmid=template_vmid
    )
    if template is None:
        if resource_type == "lxc":
            raise BadRequestError(t("vm_request.lxc_template_not_registered"))
        # 未註冊的 PVE template 就是平台基礎映像，任何人都能申請。
        return None
    # 其他模組一律把「非 lxc」視為 qemu，這裡沿用同一個判定
    template_kind = "lxc" if template.resource_type.lower() == "lxc" else "qemu"
    if template_kind != ("lxc" if resource_type == "lxc" else "qemu"):
        raise BadRequestError(t("vm_request.template_type_mismatch"))
    if template.status != VMTemplateStatus.ready:
        raise BadRequestError(t("vm_request.template_not_ready"))
    if is_admin(user):
        return template
    if has_permission(user, Permission.TEMPLATE_MANAGE):
        if not vm_template_repo.is_template_visible_to_user(
            template=template, user_id=user.id
        ):
            raise BadRequestError(t("vm_request.template_not_accessible"))
        return template
    if template.visibility != VMTemplateVisibility.global_:
        raise BadRequestError(t("vm_request.template_not_open_to_students"))
    return template


def _apply_template_floor(request_in: VMRequestCreate, template: VMTemplate) -> None:
    """套用範本後仍可自訂規格，但磁碟不得小於範本本身。

    CPU 與記憶體由申請者決定（配額仍會把關）；磁碟是物理限制：克隆出來的
    機器天生就是範本的大小，PVE 只能放大不能縮小，所以低於範本的值一律
    提高到範本大小，再進配額計算。
    """
    floor = template.default_disk
    if not floor:
        return
    if request_in.resource_type == "lxc":
        request_in.rootfs_size = max(int(request_in.rootfs_size or 0), floor)
    else:
        request_in.disk_size = max(int(request_in.disk_size or 0), floor)


def _apply_source_disk_floor(session: Session, request_in: VMRequestCreate) -> None:
    """課程 / 快速練習路徑的磁碟下限。

    這兩條路的規格來自課程模板而非公開申請表單，不會經過 ``create`` 的
    ``_apply_template_floor``；但克隆機一樣天生就是來源範本的大小，不先拉
    高就會發生「配額記 20 GB、實際開出 40 GB」的超賣。來源已註冊時用資料
    庫的 default_disk，未註冊（自訂規格挑的 PVE 範本）才去問 PVE。
    """
    template_vmid = getattr(request_in, "template_id", None)
    if not template_vmid:
        return

    template = vm_template_repo.get_template_by_pve_vmid(
        session=session, pve_vmid=int(template_vmid)
    )
    if template is not None:
        _apply_template_floor(request_in, template)
        return

    if request_in.resource_type == "lxc":
        return
    # 頂層 import 會與 provisioning_service 互相相依
    from app.services.proxmox import provisioning_service  # noqa: PLC0415

    floor = provisioning_service.template_disk_floor_gb(int(template_vmid))
    if floor:
        request_in.disk_size = max(int(request_in.disk_size or 0), floor)


def _require_template_gpu(request_in: VMRequestCreate, template: VMTemplate) -> None:
    """範本政策 requires_gpu：用這個範本申請時必須配置 GPU。

    前端在範本要求 GPU 時才顯示 GPU 區塊並強制選擇；這裡再守一次，
    避免直接打 API 或舊表單繞過。
    """
    if not template.requires_gpu:
        return
    if not str(getattr(request_in, "gpu_mapping_id", "") or "").strip():
        raise BadRequestError(t("vm_request.template_requires_gpu"))


def create(
    *, session: Session, request_in: VMRequestCreate, user
) -> VMRequestPublic:
    if request_in.resource_type not in ("lxc", "vm"):
        raise BadRequestError(t("vm_request.invalid_resource_type"))

    # ---------- 來源範本：先驗證再算配額（磁碟下限會提高用量） ----------
    source_template: VMTemplate | None = None
    source_vmid = getattr(request_in, "template_id", None)
    if source_vmid:
        source_template = _validate_template_source(
            session=session,
            template_vmid=source_vmid,
            user=user,
            resource_type=request_in.resource_type,
        )
        if source_template is not None:
            _apply_template_floor(request_in, source_template)
            _require_template_gpu(request_in, source_template)

    # ---------- 配額執法（E7）：寫入前先擋 ----------
    quota_service.check_quota(
        session,
        user.id,
        delta_cores=int(request_in.cores or 0),
        delta_memory_mb=int(request_in.memory or 0),
        delta_disk_gb=int(request_in.disk_size or request_in.rootfs_size or 0),
        delta_instances=1,
    )

    # ---------- auto mode: 伺服器端重跑規則引擎記錄判斷理由 ----------
    auto_decision_reason: str | None = None
    if getattr(request_in, "requested_mode", "manual") == "auto":
        governance = governance_repo.get_governance_config(session=session)
        if not governance.workload_advisor_enabled:
            raise BadRequestError(t("vm_request.auto_mode_disabled"))
        advice = workload_advisor.advise(
            environment_type=request_in.environment_type,
            os_info=request_in.os_info,
            reason=request_in.reason,
            cores=request_in.cores,
            memory=request_in.memory,
            gpu_mapping_id=request_in.gpu_mapping_id,
        )
        auto_decision_reason = "；".join(advice.reasons)
        if advice.resource_type != request_in.resource_type:
            auto_decision_reason += "（提交值與伺服器建議不同）"
    if request_in.resource_type == "lxc":
        if not request_in.template_id and not request_in.ostemplate:
            raise BadRequestError(t("vm_request.lxc_requires_template"))
    if request_in.resource_type == "vm":
        if not request_in.template_id:
            raise BadRequestError(t("vm_request.vm_requires_template"))
        # Windows 範本帳號由 cloudbase-init 設定檔固定，前端不送 username
        if not request_in.username:
            from app.services.proxmox import provisioning_service  # noqa: PLC0415

            if not provisioning_service.is_windows_template(request_in.template_id):
                raise BadRequestError(t("vm_request.vm_requires_username"))

    # ---------- GPU / vGPU 規格 ----------
    if request_in.gpu_mdev_profile and not request_in.gpu_mapping_id:
        raise BadRequestError(t("vm_request.vgpu_requires_gpu"))
    if request_in.gpu_mapping_id and request_in.gpu_mdev_profile:
        from app.services.proxmox import gpu_service  # noqa: PLC0415

        try:
            gpu_detail = gpu_service.get_gpu_mapping(request_in.gpu_mapping_id)
        except Exception:
            gpu_detail = None  # PVE 暫時查不到時不擋單，provision 前會再驗
        if gpu_detail is not None and gpu_detail.profiles:
            known = {p.mdev_type for p in gpu_detail.profiles}
            if request_in.gpu_mdev_profile not in known:
                raise BadRequestError(
                    t(
                        "vm_request.gpu_profile_not_found",
                        gpu=request_in.gpu_mapping_id,
                        profile=request_in.gpu_mdev_profile,
                    )
                )

    # ---------- mode validation ----------
    mode = getattr(request_in, "mode", "scheduled") or "scheduled"

    if mode == "quick_template":
        # 舊的學生自助路徑會自動核准，繞過本次建立的目錄與審核治理。
        # 快速練習改由 quick_practice 服務整組建立（仍用同一個 request_kind）。
        raise BadRequestError(
            t("vm_request.quick_template_mode_disabled")
        )
    if mode == "immediate":
        require_immediate_vm_request_access(user)
        # Set start_at to now; end_at can be None (infinite) or user-specified.
        request_in.start_at = _utc_now()
        if request_in.end_at is not None:
            end_at = request_in.end_at
            if end_at.tzinfo is None:
                end_at = end_at.replace(tzinfo=UTC)
            if end_at <= request_in.start_at:
                raise BadRequestError(t("vm_request.end_before_start"))
    else:
        # scheduled mode -- both start_at and end_at are required
        if request_in.start_at is None or request_in.end_at is None:
            raise BadRequestError(
                t("vm_request.scheduled_requires_window")
            )
        start_at = request_in.start_at
        end_at = request_in.end_at
        if start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=UTC)
        if end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=UTC)
        if end_at <= start_at:
            raise BadRequestError(t("vm_request.end_before_start"))

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
        auto_decision_reason=auto_decision_reason,
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


def create_course_request(
    *,
    session: Session,
    request_in: VMRequestCreate,
    user,
    placement_group_id: uuid.UUID | None = None,
) -> VMRequest:
    """Course Lab 內部專用：免審核建立課程實驗機申請。

    僅供 ``services/course/deployment_service`` 呼叫 —— 不暴露於公開 API
    （公開 schema 的 mode 不含 course，避免繞過房間限制直接開機）。
    房間/單人單機/發布狀態檢查由 deployment_service 負責；本函式重用
    配額檢查、審核核准 + 節點保留（quick_template 同款輕量路徑）與 audit。

    呼叫端負責 commit 與 commit 後的背景 provision 觸發。
    """
    _apply_source_disk_floor(session, request_in)
    quota_service.check_quota(
        session,
        user.id,
        delta_cores=int(request_in.cores or 0),
        delta_memory_mb=int(request_in.memory or 0),
        delta_disk_gb=int(request_in.disk_size or request_in.rootfs_size or 0),
        delta_instances=1,
    )

    db_request = vm_request_repo.create_vm_request(
        session=session,
        vm_request_in=request_in,
        user_id=user.id,
        encrypted_password=encrypt_value(request_in.password),
        request_kind="course",
        placement_group_id=placement_group_id,
        commit=False,
    )
    _approve_and_place(
        session=session,
        db_request=db_request,
        reviewer_id=user.id,
    )
    audit_service.log_action(
        session=session,
        user_id=user.id,
        action="course_lab_deploy",
        details=(
            f"Course lab deploy: {request_in.resource_type} "
            f"{request_in.hostname}, {request_in.cores} cores, "
            f"{request_in.memory}MB RAM. Auto-approved."
        ),
        commit=False,
    )
    return db_request


def create_quick_practice_request(
    *,
    session: Session,
    request_in: VMRequestCreate,
    user,
    placement_group_id: uuid.UUID | None = None,
) -> VMRequest:
    """Create one machine request inside an already validated quick-practice session.

    The quick-practice orchestrator validates the whole environment, enforces
    aggregate quota and active-session limits before calling this function.
    Keeping the request creation here preserves the same approval, placement,
    audit and provisioning path used by the legacy quick-template flow.
    Disk floors are resolved by the orchestrator (``quick_practice``) so that
    the quota it enforces and the request written here use the same value.
    """
    db_request = vm_request_repo.create_vm_request(
        session=session,
        vm_request_in=request_in,
        user_id=user.id,
        encrypted_password=encrypt_value(request_in.password),
        request_kind="quick_template",
        placement_group_id=placement_group_id,
        commit=False,
    )
    _approve_and_place(
        session=session,
        db_request=db_request,
        reviewer_id=user.id,
    )
    audit_service.log_action(
        session=session,
        user_id=user.id,
        action="quick_practice_machine_create",
        details=(
            f"Quick practice machine: {request_in.resource_type} "
            f"{request_in.hostname}, {request_in.cores} cores, "
            f"{request_in.memory}MB RAM. Auto-approved."
        ),
        commit=False,
    )
    return db_request


def submit_course_provision(request_id: uuid.UUID) -> None:
    """課程實驗機 provision 背景觸發（commit 後呼叫）。"""
    submit_sync(
        vm_request_schedule_service.process_single_request_start,
        request_id,
        name=f"provision_vm_request:{request_id}",
        task_id=f"vm_request:{request_id}",
        max_retries=1,
        retry_delay=15.0,
    )


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
        raise NotFoundError(t("vm_request.not_found"))
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
        raise NotFoundError(t("vm_request.not_found"))

    require_vm_request_review(current_user)

    start_at = db_request.start_at
    end_at = db_request.end_at
    if not start_at:
        raise BadRequestError(t("vm_request.window_required_for_review"))
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
        raise BadRequestError(t("vm_request.no_projected_node"))

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
                provisioning_status=request.provisioning_status,
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
            # 直接沿用核准路徑（rebuild_reserved_assignments）算出的落點，
            # 讓畫面標示的選定節點與按下核准後的結果必然一致。
            selected_node=request_selection.node,
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
                reassignment_cost=b.reassignment_cost,
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
        raise NotFoundError(t("vm_request.not_found"))
    if db_request.status != VMRequestStatus.pending:
        raise BadRequestError(t("vm_request.already_reviewed"))

    reservation = None
    try:
        if review_data.status == "approved":
            if not db_request.start_at:
                raise BadRequestError(
                    t("vm_request.window_required_before_approval")
                )
            end_at = db_request.end_at
            if end_at is not None:
                if end_at.tzinfo is None:
                    end_at = end_at.replace(tzinfo=UTC)
                if end_at <= _utc_now():
                    raise BadRequestError(
                        t("vm_request.window_already_ended")
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
                provisioning_status=VMProvisioningStatus.idle,
                provisioning_error=None,
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
            t("vm_request.review_processing_failed")
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
    - ``approved`` without ``vmid``: cancel before the resource becomes
      controllable. If the worker is mid-Proxmox-clone the clone may still
      complete; the scheduler reconciliation will then expose the live machine
      in resources. We surface 409 in that case rather than silently lying
      about the state.
    - ``approved`` with ``vmid``: rejected. The machine is already provisioned
      and the scheduler manages it through the active-approved-request list;
      cancelling here would orphan the live VM (no start window, auto-shutdown
      or lifecycle management). The resource deletion flow keeps the approval record
      intact and marks the request as no longer schedulable.
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
        raise NotFoundError(t("vm_request.not_found"))

    require_vm_request_cancel(current_user, db_request.user_id)

    cancellable = (
        VMRequestStatus.pending,
        VMRequestStatus.approved,
    )
    if db_request.status not in cancellable:
        raise BadRequestError(
            t("vm_request.cannot_cancel_status", status=db_request.status.value)
        )

    if (
        db_request.status == VMRequestStatus.approved
        and db_request.vmid is not None
    ):
        raise BadRequestError(
            t("vm_request.cancel_already_provisioned")
        )

    if db_request.status == VMRequestStatus.approved and db_request.vmid is None:
        bg_task_id = f"vm_request:{db_request.id}"
        cancelled_in_runner = _cancel_bg_task(bg_task_id)
        if not cancelled_in_runner and _is_bg_task_active(bg_task_id):
            # Active but cancel returned False — race. Be honest about it.
            raise BadRequestError(
                t("vm_request.cancel_provisioning_in_progress")
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
        provisioning_status=VMProvisioningStatus.idle,
        provisioning_error=None,
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
        raise NotFoundError(t("vm_request.not_found"))

    require_vm_request_cancel(current_user, db_request.user_id)

    if db_request.status != VMRequestStatus.approved:
        raise BadRequestError(
            t("vm_request.retry_requires_approved", status=db_request.status.value)
        )
    if db_request.vmid is not None:
        raise BadRequestError(
            t("vm_request.retry_already_provisioned")
        )
    if db_request.provisioning_status != VMProvisioningStatus.failed:
        raise BadRequestError(
            t("vm_request.retry_requires_failed")
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
