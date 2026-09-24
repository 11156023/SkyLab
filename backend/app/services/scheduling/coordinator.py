from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime

from sqlmodel import Session, select

from app.core.db import engine
from app.domain.scheduling.models import ScheduledTask
from app.domain.scheduling.runner import run_polling_scheduler
from app.exceptions import NotFoundError, ProxmoxError
from app.models import (
    VMProvisioningStatus,
    VMRequest,
    VMRequestStatus,
)
from app.repositories import governance as governance_repo
from app.repositories import resource as resource_repo
from app.repositories import vm_request as vm_request_repo
from app.services.network import ip_management_service
from app.services.proxmox import provisioning_service, proxmox_service
from app.services.scheduling import policy as scheduling_policy
from app.services.scheduling import provision_pool, recurrence_scheduler
from app.services.scheduling import support as scheduling_support
from app.services.user import audit_service
from app.services.vm import vm_request_placement_service

logger = logging.getLogger(__name__)

# 這些名稱由此模組 re-export，測試以
# ``app.services.scheduling.coordinator.<name>`` 引用或 monkeypatch。
SCHEDULER_POLL_SECONDS = scheduling_policy.SCHEDULER_POLL_SECONDS

# 自動開機改為阻塞等待 PVE 任務結果，才能攔截 vGPU 開不起來這類
# 「API 接受了、QEMU 卻起不來」的失敗（qmstart 任務通常數秒內結束）
_START_TASK_WAIT_SECONDS = 60.0

# GPU 暫時開不了機時寫進 resource_warning 的訊息；開機成功後以同值比對清除
GPU_WAIT_WARNING = (
    "GPU 記憶體不足，機器暫時無法開機；系統會自動重試，等待 GPU 資源釋出"
)

# vGPU/passthrough 啟動失敗在 qmstart 任務 log 裡的特徵字串
_GPU_START_ERROR_MARKERS = (
    "nvidia-vgpu",
    "vfio",
    "mdev",
    "error getting device from group",
)


def _is_gpu_start_failure(message: str) -> bool:
    lower = message.lower()
    return any(marker in lower for marker in _GPU_START_ERROR_MARKERS)


def _utc_now() -> datetime:
    return scheduling_policy.utc_now()


def _normalize_datetime(value: datetime | None) -> datetime | None:
    return scheduling_policy.normalize_datetime(value)


def _resource_type_for_request(request: VMRequest) -> str:
    return scheduling_policy.resource_type_for_request(request)


def _sync_lxc_platform_key(
    *, session: Session, node: str, vmid: int, resource_type: str
) -> None:
    if resource_type != "lxc":
        return
    from app.services.resource import resource_service  # noqa: PLC0415

    resource_service.ensure_lxc_platform_key(
        session=session,
        node=node,
        vmid=vmid,
    )
    resource_service.ensure_lxc_login_password(
        session=session,
        node=node,
        vmid=vmid,
    )


def _find_existing_resource_for_request(
    *,
    session: Session,
    request: VMRequest,
) -> dict | None:
    return scheduling_support.find_existing_resource_for_request(
        session=session,
        request=request,
    )


def _adopt_existing_resource(
    *,
    session: Session,
    request: VMRequest,
) -> tuple[int, str, str | None, bool] | None:
    """Try to adopt an already-existing Proxmox resource for this request.

    Returns (vmid, actual_node, placement_strategy, started) or None.
    """
    resource_type = _resource_type_for_request(request)
    existing_resource = _find_existing_resource_for_request(
        session=session,
        request=request,
    )
    if existing_resource is None:
        return None

    desired_node = str(request.desired_node or request.assigned_node or "")
    placement_strategy_used = (
        request.placement_strategy_used
        or vm_request_placement_service.DEFAULT_PLACEMENT_STRATEGY
    )
    vmid = int(existing_resource["vmid"])
    actual_node = str(existing_resource["node"])
    if not resource_repo.get_resource_by_vmid(session=session, vmid=vmid):
        # 原本的 plan（連同密碼）已隨中斷的 worker 消失。LXC 可於開機後補設：
        # 申請單上還留著的密碼先存成待套用，下方 _sync_lxc_platform_key 會套進去。
        # 申請單密碼為 None ＝ 沿用範本密碼，不補。QEMU 開機後改不了 cipassword，不處理。
        pending_password = request.password if resource_type == "lxc" else None
        resource_repo.create_resource(
            session=session,
            vmid=vmid,
            user_id=request.user_id,
            environment_type=request.environment_type,
            os_info=request.os_info,
            expiry_date=request.expiry_date,
            template_id=request.template_id,
            login_password_pending_encrypted=pending_password,
            request_id=request.id,
            commit=False,
        )
    vm_request_repo.update_vm_request_provisioning(
        session=session,
        db_request=request,
        vmid=vmid,
        assigned_node=desired_node or actual_node,
        desired_node=desired_node or actual_node,
        actual_node=actual_node,
        placement_strategy_used=placement_strategy_used,
        provisioning_status=VMProvisioningStatus.completed,
        provisioning_error=None,
        commit=False,
    )
    status = proxmox_service.get_status(actual_node, vmid, resource_type)
    started = False
    if str(status.get("status") or "").lower() != "running":
        proxmox_service.control(actual_node, vmid, resource_type, "start")
        started = True
    _sync_lxc_platform_key(
        session=session,
        node=actual_node,
        vmid=vmid,
        resource_type=resource_type,
    )
    audit_service.log_action(
        session=session,
        user_id=None,
        vmid=vmid,
        action="resource_start",
        details=(
            f"Adopted existing {request.resource_type} resource for request {request.id}"
        ),
        commit=False,
    )
    logger.warning(
        "Adopted existing %s resource VMID %s for request %s",
        resource_type, vmid, request.id,
    )
    return vmid, actual_node, placement_strategy_used, started


def _provision_new_resource(
    *,
    session: Session,
    request: VMRequest,
) -> tuple[int, str, str | None] | None:
    """Lock, mark provisioning running, clone outside txn, then record VMID.

    This is the core anti-duplication pattern:
    1. SELECT FOR UPDATE SKIP LOCKED; if locked, bail
    2. provisioning_status = running, commit (visible to other sessions)
    3. plan_provision (resolve storage etc.) in a short txn
    4. commit / close session
    5. execute_provision (clone VM) with no open transaction
    6. Open new session, record vmid and provisioning_status, commit
    """
    desired_node = str(request.desired_node or request.assigned_node or "")

    # --- Phase 1: mark provisioning running + plan (short transaction) ----
    request.provisioning_status = VMProvisioningStatus.running
    request.provisioning_started_at = _utc_now()
    request.provisioning_error = None
    session.add(request)
    session.commit()
    logger.info("Marked request %s as provisioning", request.id)

    try:
        # 送單時的配額檢查看不到後來一起核准的其他申請；真正要開機器前再驗
        # 一次（這張單自己會被排除、PVE 查詢失敗 fail-open）。
        from app.services.resource import (  # noqa: PLC0415 — 避免 import cycle
            quota_service,
        )

        quota_service.check_quota_for_provision(session, request)
        # plan 內的 next_vmid() 與 IP 配發要在跨 worker 的 VMID 鎖內完成並
        # commit，否則併發的 plan 會讀到同一個 cluster.nextid、兩張單撞 VMID。
        with proxmox_service.vmid_allocation_lock(db_engine=session.get_bind()):
            plan = provisioning_service.plan_provision(
                session=session,
                db_request=request,
            )
            session.commit()
    except Exception as plan_exc:
        # Plan failed — revert to approved so scheduler can retry.
        # IP allocated during plan_provision is already flushed to session;
        # rollback first, then revert status cleanly.
        session.rollback()
        request = vm_request_repo.get_vm_request_by_id(
            session=session, request_id=request.id, for_update=True,
        )
        if request:
            request.provisioning_status = VMProvisioningStatus.failed
            request.provisioning_error = (
                f"Failed to plan provisioning: {plan_exc}"[:500]
            )
            session.add(request)
            session.commit()
        raise

    request_id = request.id
    request_user_id = request.user_id
    request_env_type = request.environment_type
    request_os_info = request.os_info
    request_expiry_date = request.expiry_date
    request_template_id = request.template_id
    request_resource_type = request.resource_type

    # Close session so clone runs outside any transaction.
    session.commit()

    # --- Phase 2: execute clone (NO open transaction) ---------------------
    try:
        new_vmid, actual_node = provisioning_service.execute_provision(plan)
    except Exception as provision_exc:
        # Clone failed — revert to approved and release allocated IP.
        with Session(engine) as rollback_session:
            # Release IP allocated during planning
            try:
                ip_management_service.release_ip(
                    rollback_session,
                    plan["vmid"],
                    restore_reservation=bool(plan.get("ip_reservation_key")),
                    reservation_key=plan.get("ip_reservation_key"),
                )
                rollback_session.commit()
            except Exception:
                logger.warning("Failed to release IP for VMID %s during rollback", plan["vmid"])

            req = vm_request_repo.get_vm_request_by_id(
                session=rollback_session, request_id=request_id, for_update=True,
            )
            if req and req.vmid is None:
                req.provisioning_status = VMProvisioningStatus.failed
                req.provisioning_error = (
                    f"Failed to execute provisioning: {provision_exc}"[:500]
                )
                rollback_session.add(req)
                rollback_session.commit()
                logger.warning("Reverted request %s to approved after provision failure", request_id)
        raise

    # --- Phase 3: record result (new short txn) ---------------------------
    with Session(engine) as finish_session:
        req = vm_request_repo.get_vm_request_by_id(
            session=finish_session, request_id=request_id, for_update=True,
        )
        if req is None:
            logger.error("Request %s vanished after provisioning VMID %s", request_id, new_vmid)
            raise NotFoundError(f"Request {request_id} no longer exists")

        if req.status != VMRequestStatus.approved:
            # clone 期間申請單被取消／駁回：不能把機器掛到非 approved 的單上
            # （會變成沒有 auto-stop、沒有 TTL 回收的孤兒），直接收回。
            logger.warning(
                "Request %s is %s after provisioning VMID %s; removing the orphan",
                request_id, req.status.value, new_vmid,
            )
            finish_session.commit()
            try:
                provisioning_service.cleanup_failed_resource(
                    actual_node, new_vmid, plan["resource_type"]
                )
            except Exception:
                logger.exception("Failed to remove orphan VMID %s", new_vmid)
            try:
                ip_management_service.release_ip(
                    finish_session,
                    new_vmid,
                    restore_reservation=bool(plan.get("ip_reservation_key")),
                    reservation_key=plan.get("ip_reservation_key"),
                )
                finish_session.commit()
            except Exception:
                logger.warning("Failed to release IP for orphan VMID %s", new_vmid)
            return None

        resource_repo.create_resource(
            session=finish_session,
            vmid=new_vmid,
            user_id=request_user_id,
            environment_type=request_env_type,
            os_info=request_os_info,
            expiry_date=request_expiry_date,
            template_id=request_template_id,
            ssh_private_key_encrypted=plan.get("ssh_private_key_encrypted"),
            ssh_public_key=plan.get("ssh_public_key"),
            login_password_encrypted=(
                provisioning_service.applied_login_password_encrypted(plan)
            ),
            login_password_pending_encrypted=(
                provisioning_service.pending_login_password_encrypted(plan)
            ),
            request_id=req.id,
            commit=False,
        )
        ip_management_service.link_ip_to_resource(
            finish_session,
            new_vmid,
            reservation_key=plan.get("ip_reservation_key"),
        )
        vm_request_repo.update_vm_request_provisioning(
            session=finish_session,
            db_request=req,
            vmid=new_vmid,
            assigned_node=desired_node or actual_node,
            desired_node=desired_node or actual_node,
            actual_node=actual_node,
            placement_strategy_used=plan["placement_strategy"],
            provisioning_status=VMProvisioningStatus.completed,
            provisioning_error=None,
            commit=False,
        )
        # 密碼已隨機器存進 resources.login_password_encrypted，
        # 申請單不再保留一份可逆加密的副本
        req.password = None
        finish_session.add(req)

        audit_service.log_action(
            session=finish_session,
            user_id=request_user_id,
            vmid=new_vmid,
            action="lxc_create" if request_resource_type == "lxc" else "vm_create",
            details=f"Provisioned {request_resource_type} for request {request_id} on {actual_node}",
            commit=False,
        )
        finish_session.commit()

    # E1：provision 完成即建受保護初始快照（best-effort，不阻斷）
    from app.services.resource import reset_service  # noqa: PLC0415 — 避免 import cycle

    reset_service.ensure_init_snapshot(new_vmid)

    logger.info(
        "Provisioned request %s → VMID %s on node %s",
        request_id, new_vmid, actual_node,
    )
    return new_vmid, actual_node, plan["placement_strategy"]


def _mark_request_runtime_error(
    *,
    session: Session,
    request_id,
    message: str,
) -> None:
    scheduling_support.mark_request_runtime_error(
        session=session,
        request_id=request_id,
        message=message,
    )


def _refresh_actual_node(
    *,
    session: Session,
    request: VMRequest,
) -> tuple[str, dict]:
    db_request = vm_request_repo.get_vm_request_by_id(
        session=session,
        request_id=request.id,
        for_update=True,
    ) or request
    if request.vmid is None:
        raise NotFoundError(f"Request {request.id} has no provisioned VMID")
    resource = proxmox_service.find_resource(request.vmid)
    resource_name = str(resource.get("name") or "")
    # hostname is stored as punycode in DB since creation, so a direct
    # comparison is sufficient.
    expected_hostname = str(request.hostname or "")
    if resource_name != expected_hostname:
        raise NotFoundError(
            f"Provisioned resource {request.vmid} name '{resource_name}' "
            f"does not match request hostname '{expected_hostname}'"
        )
    actual_node = str(resource["node"])
    vm_request_repo.update_vm_request_provisioning(
        session=session,
        db_request=db_request,
        vmid=request.vmid,
        assigned_node=actual_node,
        desired_node=actual_node,
        actual_node=actual_node,
        placement_strategy_used=db_request.placement_strategy_used,
        provisioning_status=VMProvisioningStatus.completed,
        provisioning_error=None,
        commit=False,
    )
    return actual_node, resource


def _adopt_or_provision_due_request(
    *,
    session: Session,
    request: VMRequest,
) -> tuple[int, str | None, str | None, bool] | None:
    """Acquire lock, then adopt existing Proxmox resource or fully provision.

    Returns ``(vmid, actual_node, strategy, started)`` on success, or ``None``
    if the lock cannot be acquired (another worker has it) or the request has
    already been handled.
    """
    # SELECT FOR UPDATE SKIP LOCKED — skip if another session holds it.
    locked = vm_request_repo.get_vm_request_by_id(
        session=session,
        request_id=request.id,
        for_update=True,
        skip_locked=True,
    )
    if locked is None:
        return None
    # Re-check: another process may have set vmid or changed status.
    if locked.vmid is not None:
        return None
    if locked.provisioning_status == VMProvisioningStatus.running:
        if not scheduling_policy.is_provisioning_stale(
            locked.provisioning_started_at, now=_utc_now()
        ):
            return None
        # running 超過上限：多半是 clone 中容器重啟或寫回 DB 失敗。先試著
        # 認領 PVE 上可能已建好的機器，認不到再重新 provision。
        logger.warning(
            "Request %s stuck in provisioning since %s; taking over",
            locked.id, locked.provisioning_started_at,
        )

    # Try adopting an existing Proxmox resource first.
    adopted = _adopt_existing_resource(session=session, request=locked)
    if adopted is not None:
        vmid, actual_node, strategy, started = adopted
        session.commit()
        return vmid, actual_node, strategy, started

    # Full provision: mark provisioning → clone outside txn → mark running.
    # _provision_new_resource manages its own sessions/commits.
    _provision_new_resource(session=session, request=locked)
    refreshed = vm_request_repo.get_vm_request_by_id(
        session=session,
        request_id=locked.id,
    )
    if refreshed is None or refreshed.vmid is None:
        return None
    started = (
        refreshed.vmid is not None
        or refreshed.provisioning_status == VMProvisioningStatus.running
    )
    return (
        refreshed.vmid,
        refreshed.actual_node,
        refreshed.placement_strategy_used,
        started,
    )


def _ensure_request_running(
    *,
    session: Session,
    request: VMRequest,
    now: datetime,
) -> bool:
    """Make sure an approved request has a live VM.

    For requests without a vmid: lock, mark provisioning running, clone, record VMID.
    For requests with a vmid: ensure the VM is started.
    """
    resource_type = _resource_type_for_request(request)

    # ---- No VMID yet → need to provision ---------------------------------
    if request.vmid is None:
        outcome = _adopt_or_provision_due_request(session=session, request=request)
        if outcome is None:
            return False
        _vmid, outcome_actual_node, _strategy, started = outcome
        # A freshly provisioned guest is complete once its actual node is recorded.
        refreshed_after = vm_request_repo.get_vm_request_by_id(
            session=session, request_id=request.id,
        )
        if (
            refreshed_after is not None
            and refreshed_after.vmid is not None
            and refreshed_after.desired_node
            and outcome_actual_node
            and refreshed_after.desired_node == outcome_actual_node
            and refreshed_after.provisioning_status
            in (VMProvisioningStatus.idle, VMProvisioningStatus.pending)
        ):
            vm_request_repo.update_vm_request_provisioning(
                session=session,
                db_request=refreshed_after,
                vmid=refreshed_after.vmid,
                assigned_node=refreshed_after.assigned_node or outcome_actual_node,
                desired_node=refreshed_after.desired_node,
                actual_node=outcome_actual_node,
                placement_strategy_used=refreshed_after.placement_strategy_used,
                provisioning_status=VMProvisioningStatus.completed,
                provisioning_error=None,
                commit=False,
            )
            session.commit()
        return started

    # ---- Already provisioned → ensure VM is started ----------------------
    actual_node, _ = _refresh_actual_node(session=session, request=request)
    request = vm_request_repo.get_vm_request_by_id(
        session=session, request_id=request.id, for_update=True,
    ) or request
    # 鎖定後再確認一次：本 tick 撈單之後，使用者刪機流程可能已把申請單標成
    # 已消耗（provisioning_status=failed）。這時不能再把它寫回 completed，
    # 否則下個 tick 會把它當成活單、發現機器不見而重新 clone 出來。
    if (
        request.status != VMRequestStatus.approved
        or request.provisioning_status == VMProvisioningStatus.failed
    ):
        logger.info(
            "Skipping auto-start for request %s: consumed or deactivated "
            "after this tick began",
            request.id,
        )
        return False

    pve_status = proxmox_service.get_status(actual_node, request.vmid, resource_type)
    is_running = str(pve_status.get("status") or "").lower() == "running"
    if not is_running:
        try:
            proxmox_service.control(
                actual_node,
                request.vmid,
                resource_type,
                "start",
                wait_timeout_seconds=_START_TASK_WAIT_SECONDS,
            )
        except ProxmoxError as exc:
            if not _is_gpu_start_failure(str(exc)):
                raise
            # GPU 記憶體不足屬暫時性：寫警示供前端顯示「等待 GPU 釋出」，
            # 不標 failed——申請單留在 active 清單，之後的 tick 會繼續重試
            request.resource_warning = GPU_WAIT_WARNING
            session.add(request)
            session.commit()
            logger.warning(
                "Auto-start blocked by GPU capacity for request %s (VMID %s): %s",
                request.id, request.vmid, exc,
            )
            return False
        except TimeoutError as exc:
            # 任務還在 PVE 端跑（未失敗）：視為已觸發，下一個 tick 再對帳
            logger.warning(
                "Auto-start task still running for request %s (VMID %s): %s",
                request.id, request.vmid, exc,
            )

    if not is_running:
        _sync_lxc_platform_key(
            session=session,
            node=actual_node,
            vmid=int(request.vmid),
            resource_type=resource_type,
        )

    # 成功開機（或已在跑）代表 GPU 已擠得進去，清掉先前的等待警示
    if request.resource_warning == GPU_WAIT_WARNING:
        request.resource_warning = None

    vm_request_repo.update_vm_request_provisioning(
        session=session,
        db_request=request,
        vmid=request.vmid,
        assigned_node=actual_node,
        desired_node=actual_node,
        actual_node=actual_node,
        placement_strategy_used=request.placement_strategy_used,
        provisioning_status=VMProvisioningStatus.completed,
        provisioning_error=None,
        commit=False,
    )
    if not is_running:
        audit_service.log_action(
            session=session,
            user_id=None,
            vmid=request.vmid,
            action="resource_start",
            details=f"Auto-started {request.resource_type} request {request.id}",
            commit=False,
        )
        logger.info(
            "Auto-started request %s on node %s with VMID %s",
            request.id, actual_node, request.vmid,
        )
    return not is_running


def process_single_request_start(request_id: uuid.UUID) -> bool:
    """Immediately trigger provisioning for a single approved request."""
    with Session(engine) as session:
        request = vm_request_repo.get_vm_request_by_id(
            session=session,
            request_id=request_id,
            for_update=True,
            skip_locked=True,
        )
        if not request or request.status != VMRequestStatus.approved:
            return False
        try:
            started = _ensure_request_running(
                session=session,
                request=request,
                now=_utc_now(),
            )
            # A quick-practice environment becomes ready only after every
            # machine is provisioned and its published network topology has
            # been materialized. This callback is idempotent and row-locked.
            from app.services import quick_practice  # noqa: PLC0415

            session.expire_all()
            quick_practice.reconcile_for_request(
                session,
                request_id=request_id,
            )
            session.commit()
            return started
        except Exception:
            session.rollback()
            logger.exception(
                "Failed to immediately provision request %s", request_id
            )
            return False


def process_due_request_starts() -> int:
    started_count = 0
    now = _utc_now()

    with Session(engine) as session:
        active_requests = vm_request_repo.list_active_approved_vm_requests(
            session=session,
            at_time=now,
        )
        governance_config = governance_repo.get_governance_config(session=session)

        for request in active_requests:
            if request.vmid is None:
                if (
                    request.provisioning_status == VMProvisioningStatus.running
                    and not scheduling_policy.is_provisioning_stale(
                        request.provisioning_started_at, now=now
                    )
                ):
                    # worker 正在 clone：不用再送，job id 去重也擋得住，但這裡
                    # 先跳過省一次 Redis 往返
                    continue
                # 尚未 provision — 入列到 arq worker 並行 clone（worker 內
                # semaphore 限流），tick 不再同步等待重 I/O。防重複由 job id
                # 去重 + DB SKIP LOCKED + provisioning_status 再檢查三層保障。
                provision_pool.submit_provision(
                    session,
                    request_id=request.id,
                    user_id=request.user_id,
                    concurrency=governance_config.provision_max_concurrency,
                )
                continue

            try:
                started = _ensure_request_running(
                    session=session,
                    request=request,
                    now=now,
                )
                if started:
                    started_count += 1
                session.commit()
            except NotFoundError:
                stale_vmid = request.vmid
                session.rollback()
                # Retry find_resource up to 3 times with a short delay
                # to tolerate transient Proxmox API hiccups.
                if stale_vmid is not None:
                    confirmed_gone = True
                    for attempt in range(3):
                        try:
                            proxmox_service.find_resource(stale_vmid)
                            confirmed_gone = False
                            break
                        except NotFoundError:
                            if attempt < 2:
                                time.sleep(2)
                    if not confirmed_gone:
                        logger.info(
                            "VMID %s still exists on Proxmox; "
                            "skipping recovery for request %s",
                            stale_vmid, request.id,
                        )
                        continue
                # VMID confirmed absent — clear and re-provision.
                try:
                    # 與「使用者刪機」的競態防護：SkyLab 刪除流程會以
                    # mark_linked_request_consumed 把申請單標成 failed。
                    # 本 tick 撈單在前、刪除 commit 在後時，這裡拿到的是
                    # 舊資料——必須重讀 DB；已標 failed 或已非 approved
                    # 代表刪除是使用者意圖，不是異常消失，不得重建。
                    fresh = vm_request_repo.get_vm_request_by_id(
                        session=session, request_id=request.id,
                    )
                    if (
                        fresh is None
                        or fresh.status != VMRequestStatus.approved
                        or fresh.provisioning_status == VMProvisioningStatus.failed
                    ):
                        logger.info(
                            "Skipping stale-VMID recovery for request %s: "
                            "request was consumed or deactivated during this tick",
                            request.id,
                        )
                        continue
                    request = fresh
                    if stale_vmid is not None:
                        vm_request_repo.clear_vm_request_provisioning(
                            session=session,
                            db_request=request,
                            commit=False,
                        )
                        request.status = VMRequestStatus.approved
                        session.add(request)
                        session.commit()
                    started = _ensure_request_running(
                        session=session,
                        request=request,
                        now=now,
                    )
                    if started:
                        started_count += 1
                    session.commit()
                    logger.warning(
                        "Recovered request %s from stale VMID %s",
                        request.id, stale_vmid,
                    )
                except Exception as exc:
                    session.rollback()
                    _mark_request_runtime_error(
                        session=session,
                        request_id=request.id,
                        message=str(exc),
                    )
                    logger.exception(
                        "Failed to recover request %s from stale VMID %s",
                        request.id, stale_vmid,
                    )
            except Exception as exc:
                session.rollback()
                _mark_request_runtime_error(
                    session=session,
                    request_id=request.id,
                    message=str(exc),
                )
                logger.exception(
                    "Failed to reconcile approved request %s with VMID %s",
                    request.id,
                    request.vmid,
                )

    return started_count


def process_due_request_stops() -> int:
    stopped_count = 0
    now = _utc_now()

    with Session(engine) as session:
        due_requests = list(
            session.exec(
                select(VMRequest).where(
                    VMRequest.status == VMRequestStatus.approved,
                    # failed = 已消耗（使用者刪機／轉範本）或機器異常，排程器
                    # 不再接管。不排除的話，刪機後這裡會把 vmid 清掉，申請單
                    # 在前端就變回「建立中／開通失敗」的 placeholder。
                    VMRequest.provisioning_status != VMProvisioningStatus.failed,
                    VMRequest.vmid.is_not(None),
                    VMRequest.end_at.is_not(None),
                    VMRequest.end_at <= now,
                )
            ).all()
        )

        for request in due_requests:
            vmid = request.vmid
            if vmid is None:
                continue

            resource_type = _resource_type_for_request(request)

            try:
                resource = proxmox_service.find_resource(vmid)
                node = str(resource["node"])
                status = proxmox_service.get_status(node, vmid, resource_type)
                current_status = str(status.get("status") or "").lower()
                if current_status in {"stopped", "paused"}:
                    continue

                proxmox_service.control(node, vmid, resource_type, "shutdown")
                audit_service.log_action(
                    session=session,
                    user_id=None,
                    vmid=vmid,
                    action="resource_shutdown",
                    details=(
                        "Scheduled auto-shutdown for approved "
                        f"{request.resource_type} request {request.id}"
                    ),
                    commit=False,
                )
                stopped_count += 1
                logger.info(
                    "Auto-shutdown triggered for approved request %s on node %s with VMID %s",
                    request.id,
                    node,
                    vmid,
                )
            except NotFoundError:
                # 機器已不在 Proxmox（例如在 PVE 端被直接刪掉）。保留 vmid 供
                # 稽核，標 failed 讓下個 tick 不再撈到；不要清 vmid——approved
                # 且 vmid 為空在前端會被當成「建立中」placeholder。
                logger.warning(
                    "Scheduled shutdown skipped: resource %s not found for "
                    "request %s; marking request failed",
                    vmid,
                    request.id,
                )
                request.provisioning_status = VMProvisioningStatus.failed
                request.provisioning_error = (
                    f"Resource {vmid} no longer exists on Proxmox; "
                    "scheduled shutdown skipped"
                )
                session.add(request)
                session.commit()
            except Exception:
                logger.exception(
                    "Failed to auto-shutdown approved request %s with VMID %s",
                    request.id,
                    vmid,
                )

        if stopped_count > 0:
            session.commit()

    return stopped_count


async def run_scheduler(stop_event: asyncio.Event) -> None:
    from app.services.scheduling.leader import (  # noqa: PLC0415 — 避免 import cycle
        scheduler_leader_lock,
    )

    logger.info("VM request scheduler is running")
    await run_polling_scheduler(
        stop_event=stop_event,
        interval_seconds=SCHEDULER_POLL_SECONDS,
        leader_gate=scheduler_leader_lock,
        tasks=[
            ScheduledTask(name="process_due_request_starts", handler=process_due_request_starts),
            ScheduledTask(name="process_due_request_stops", handler=process_due_request_stops),
            ScheduledTask(
                name="process_expired_requests",
                handler=process_expired_requests_task,
            ),
            ScheduledTask(name="process_pending_deletions", handler=process_pending_deletions_task),
            ScheduledTask(
                name="reap_stale_script_runs", handler=reap_stale_script_runs_task
            ),
            ScheduledTask(
                name="reap_stale_task_records", handler=reap_stale_task_records_task
            ),


            ScheduledTask(
                name="process_recurrence_windows",
                handler=recurrence_scheduler.process_recurrence_windows,
            ),
            ScheduledTask(
                name="process_scheduled_boot",
                handler=recurrence_scheduler.process_scheduled_boot,
            ),
            ScheduledTask(
                name="process_auto_stops",
                handler=recurrence_scheduler.process_auto_stops,
            ),
            ScheduledTask(
                name="process_quick_practice_lifecycle",
                handler=process_quick_practice_lifecycle_task,
            ),
            ScheduledTask(
                name="process_resource_alerts",
                handler=process_resource_alerts_task,
            ),
            ScheduledTask(
                name="process_ttl_lifecycle",
                handler=process_ttl_lifecycle_task,
            ),
            ScheduledTask(
                name="process_idle_detection",
                handler=process_idle_detection_task,
            ),
            ScheduledTask(
                name="process_mining_detection",
                handler=process_mining_detection_task,
            ),
            ScheduledTask(
                name="process_snapshot_cleanup",
                handler=process_snapshot_cleanup_task,
            ),
            ScheduledTask(
                name="reap_stale_batch_jobs",
                handler=reap_stale_batch_jobs_task,
            ),
        ],
    )
    logger.info("VM request scheduler stopped")


def reap_stale_batch_jobs_task() -> int:
    """Scheduler tick：批次佈建跑在 daemon thread，重啟後會永遠停在 running；
    超過 STALE_BATCH_JOB_HOURS 沒進度的工作標 failed 讓老師能重試。"""
    from app.services.vm import (
        batch_provision_service,  # noqa: PLC0415 — 避免 import cycle
    )

    return batch_provision_service.reap_stale_batch_jobs()


def process_expired_requests_task() -> int:
    """Scheduler tick：已過使用時段仍未審核的申請自動過期。"""
    from app.services.vm import (
        vm_request_expiry_service,  # noqa: PLC0415 — 避免 import cycle
    )

    return vm_request_expiry_service.process_expired_requests()


def process_quick_practice_lifecycle_task() -> int:
    """Finalize multi-machine topology and reclaim expired practice groups."""
    from app.services import quick_practice  # noqa: PLC0415

    return quick_practice.process_lifecycle()


def process_resource_alerts_task() -> int:
    """Scheduler tick：資源閾值警告評估（間隔由 GovernanceConfig 控制）。"""
    from app.services.monitoring import (
        alert_service,  # noqa: PLC0415 — 避免 import cycle
    )

    return alert_service.process_resource_alerts()


def process_ttl_lifecycle_task() -> int:
    """Scheduler tick：TTL 漸進回收（通知 → 關機 → 寬限期 → 刪除佇列）。"""
    from app.services.governance import (
        lifecycle_service,  # noqa: PLC0415 — 避免 import cycle
    )

    return lifecycle_service.process_ttl_lifecycle()


def process_idle_detection_task() -> int:
    """Scheduler tick：閒置偵測（CPU 長期低於閾值 → 通知 → 自動關機）。"""
    from app.services.governance import (
        lifecycle_service,  # noqa: PLC0415 — 避免 import cycle
    )

    return lifecycle_service.process_idle_detection()


def process_mining_detection_task() -> int:
    """Scheduler tick：挖礦偵測（CPU 長期滿載 → 存證 → 暫停 → 通知）。"""
    from app.services.security import (
        mining_service,  # noqa: PLC0415 — 避免 import cycle
    )

    return mining_service.process_mining_detection()


def process_snapshot_cleanup_task() -> int:
    """Scheduler tick：快照自動清理（超過保留天數的一般快照）。"""
    from app.services.governance import (
        snapshot_cleanup_service,  # noqa: PLC0415 — 避免 import cycle
    )

    return snapshot_cleanup_service.process_snapshot_cleanup()


def reap_stale_task_records_task() -> int:
    """Scheduler tick：把 worker 被硬殺後永遠停在 running／queued 的 TaskRecord 收成 failed。"""
    from app.repositories import task_record as task_record_repo  # noqa: PLC0415

    try:
        with Session(engine) as session:
            return task_record_repo.reap_stale_task_records(session=session)
    except Exception:
        logger.exception("reap_stale_task_records_task failed")
        return 0


def reap_stale_script_runs_task() -> int:
    """Scheduler tick：把被硬殺的 Teacher Judge script run 從 running 收成 failed。"""
    from app.ai.teacher_judge import (
        script_executor_service,  # noqa: PLC0415 — 避免 import cycle
    )

    try:
        with Session(engine) as session:
            reaped = script_executor_service.reap_stale_script_runs(session)
        return reaped
    except Exception:
        logger.exception("reap_stale_script_runs_task failed")
        return 0


def process_pending_deletions_task() -> int:
    """Scheduler tick：處理一筆 pending DeletionRequest（每 tick 最多一筆，避免長阻塞）。"""
    from app.services.resource import (
        deletion_service,  # noqa: PLC0415 — 避免 import cycle
    )

    try:
        with Session(engine) as session:
            deletion_service.process_pending_deletions(session)
        return 0
    except Exception:
        logger.exception("process_pending_deletions_task failed")
        return 0
