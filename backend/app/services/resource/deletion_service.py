"""Resource deletion request service.

提供「將刪除請求加入佇列並交給 arq worker」、「worker 端執行（含重試）」、
「scheduler tick 安全網（撿回 pending 與殭屍 running）」三組能力。
實際刪除邏輯仍委派給 `resource_service.delete`，本 service 只負責生命週期管理與 audit。
進度與結果由 Jobs 顯示；``cancelled`` 狀態已無寫入端，只保留讀取端的判斷。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.authorizers import can_bypass_resource_ownership
from app.core.i18n import t
from app.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ProxmoxError,
)
from app.infrastructure.queue import enqueue_task_sync
from app.models import (
    DeletionRequest,
    DeletionRequestStatus,
    Resource,
    User,
)
from app.repositories import proxmox_connection as proxmox_connection_repo
from app.repositories import resource as resource_repo
from app.repositories import vm_template as template_repo
from app.schemas.deletion_request import DeletionRequestCreated
from app.services.proxmox import proxmox_service
from app.services.resource import resource_service
from app.services.resource.access import require_resource_management

logger = logging.getLogger(__name__)

TASK_DELETE = "resource.delete"

# 仍在處理中的刪除單；同一 vmid 同時只能有一張（partial unique index 把關）
_ACTIVE_STATUSES = (DeletionRequestStatus.pending, DeletionRequestStatus.running)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _find_active(session: Session, vmid: int) -> DeletionRequest | None:
    return session.exec(
        select(DeletionRequest).where(
            DeletionRequest.vmid == vmid,
            DeletionRequest.status.in_(_ACTIVE_STATUSES),  # type: ignore[union-attr]
        )
    ).first()


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────


def create_deletion_request(
    *,
    session: Session,
    user_id: uuid.UUID,
    vmid: int,
    resource_info: dict,
    purge: bool = True,
    force: bool = False,
) -> DeletionRequest:
    """建立一筆 pending DeletionRequest。

    若該 vmid 已有 pending/running 的請求，直接回傳該請求避免重複佇列。
    """
    existing = _find_active(session, vmid)
    if existing is not None:
        return existing

    req = DeletionRequest(
        user_id=user_id,
        vmid=vmid,
        resource_vmid=resource_repo.linked_resource_vmid(session, vmid),
        name=resource_info.get("name"),
        node=resource_info.get("node"),
        resource_type=resource_info.get("type"),
        purge=purge,
        force=force,
        status=DeletionRequestStatus.pending,
        created_at=_utc_now(),
    )
    session.add(req)
    try:
        session.commit()
    except IntegrityError:
        # Another scheduler/API replica may have queued the same VM between
        # our read and insert. The partial unique index is the final arbiter.
        session.rollback()
        existing = _find_active(session, vmid)
        if existing is not None:
            return existing
        raise
    session.refresh(req)
    logger.info("Queued deletion request %s for vmid=%s", req.id, vmid)
    return req


def _ensure_vm_absent_everywhere(session: Session, vmid: int) -> None:
    """確認 VM 真的已經不在任何 PVE 上，才允許清掉孤兒 DB 記錄。

    ``find_resource`` 只看啟用中的連線、只看各連線自己的 pool，且只要還有
    一個連線答得出來就會默默略過連不上的連線；拿它的 NotFound 當「機器已不
    存在」會在連線停用／斷線時把活著的機器的 DB 記錄、IP 與稽核紀錄一併刪掉。
    這裡改為逐一詢問所有連線（含停用中的）、不套 pool 篩選：任何一個連線
    列不出清單就丟 ProxmoxError（502，不動 DB）；在 pool 外找到同一個 vmid
    就回 409，交給管理員處理。
    """
    connection_ids: list[int | None] = [
        conn.id
        for conn in proxmox_connection_repo.get_all_connections(session)
        if conn.id is not None
    ] or [None]
    try:
        hit = proxmox_service.find_vmid_on_connections(vmid, connection_ids)
    except ProxmoxError as exc:
        # find_vmid_on_connections 的訊息已含連線 id
        logger.warning("Cannot confirm resource %s is gone: %s", vmid, exc)
        raise ProxmoxError(t("resource.delete_presence_unverified")) from exc
    if hit is not None:
        logger.warning(
            "Resource %s is outside its pool or on a disabled "
            "connection (%s); refusing orphan cleanup",
            vmid, hit[0],
        )
        raise ConflictError(t("resource.delete_outside_pool"))


def _ensure_not_template_update_temp_vm(session: Session, resource: Resource) -> None:
    """範本更新循環進行中的暫存母機不能從資源頁刪，要從範本頁取消更新。

    直接刪掉的話，範本會一直卡在 updating，直到使用者自己去按取消。
    """
    from app.services.template.template_service import UPDATE_TEMP_ENVIRONMENT_TYPE

    if getattr(resource, "environment_type", None) != UPDATE_TEMP_ENVIRONMENT_TYPE:
        return
    template = template_repo.get_updating_template_by_source_vmid(
        session=session, source_vmid=resource.vmid
    )
    if template is not None:
        raise ConflictError(
            t("resource.delete_template_update_temp", name=template.name)
        )


def request_deletion(
    *,
    session: Session,
    user: User,
    vmid: int,
    purge: bool = True,
    force: bool = False,
) -> DeletionRequestCreated:
    """``DELETE /resources/{vmid}`` 的本體：權限檢查、孤兒清理或排進刪除佇列。

    - 主路徑：寫入 DeletionRequest 後入列 ``resource.delete`` 任務，worker
      呼叫 ``process_one_request``，無需等 scheduler tick。
    - 兜底：scheduler 每隔 ``SCHEDULER_POLL_SECONDS`` 仍會掃描 pending request，
      涵蓋入列失敗 / worker 重啟的情況；pending→running 是條件式認領，不會重複執行。
    - 孤兒清理：若 VM 在 Proxmox 已不存在但 DB 仍有記錄，直接清理 DB 並回
      completed 的合成回應。
    """
    # Check DB ownership first (without requiring Proxmox to be available)
    db_resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    is_admin = can_bypass_resource_ownership(user)

    if db_resource is None:
        if not is_admin:
            raise NotFoundError(f"Resource {vmid} not found")
        # Admin deleting an orphan resource (exists in Proxmox but not in DB).
        # Fall through to locate it in Proxmox below.
        logger.info(
            "Admin %s deleting orphan resource %s (no DB record)",
            user.email, vmid,
        )
    elif not is_admin:
        try:
            require_resource_management(session=session, user=user, vmid=vmid)
        except PermissionDeniedError:
            logger.warning(
                "User %s attempted to delete resource %s without management rights",
                user.email, vmid,
            )
            raise

    if db_resource is not None:
        _ensure_not_template_update_temp_vm(session, db_resource)

    # Try to locate the VM in Proxmox.
    # - If gone and DB record exists → clean up orphan DB record.
    # - If gone and no DB record → nothing to do.
    try:
        resource_info = proxmox_service.find_resource(vmid)
    except NotFoundError:
        if db_resource is not None:
            # find_resource 的 NotFound 不夠嚴格（見 helper 說明），清 DB 前再確認一次
            _ensure_vm_absent_everywhere(session, vmid)
            logger.warning(
                "Resource %s not found in Proxmox; cleaning up orphan DB record", vmid
            )
            resource_service.delete_orphan_db_record(
                session=session, vmid=vmid, user_id=user.id
            )
        else:
            logger.info(
                "Resource %s not found in Proxmox and no DB record; nothing to clean up",
                vmid,
            )
        return DeletionRequestCreated(
            id=uuid.uuid4(),
            vmid=vmid,
            status=DeletionRequestStatus.completed,
            message="Orphan DB record cleaned up (VM already removed from Proxmox)",
        )

    req = create_deletion_request(
        session=session,
        user_id=user.id,
        vmid=vmid,
        resource_info=resource_info,
        purge=purge,
        force=force,
    )
    # 只對剛建立的 pending 單入列；去重回傳的既有 pending/running 單已在處理中
    enqueue_processing(session=session, req=req)
    return DeletionRequestCreated(
        id=req.id,
        vmid=req.vmid,
        status=req.status,
        message="Deletion request queued",
    )


def list_active_for_vmids(
    *,
    session: Session,
    vmids: list[int],
) -> dict[int, DeletionRequest]:
    """回傳 vmid → 仍進行中（pending/running）的 DeletionRequest 的 mapping。"""
    if not vmids:
        return {}
    rows = session.exec(
        select(DeletionRequest).where(
            DeletionRequest.vmid.in_(vmids),  # type: ignore[union-attr]
            DeletionRequest.status.in_(_ACTIVE_STATUSES),  # type: ignore[union-attr]
        )
    ).all()
    return {r.vmid: r for r in rows}


# ──────────────────────────────────────────────────────────────────────────────
# Execution
# ──────────────────────────────────────────────────────────────────────────────


def _execute_deletion(session: Session, req: DeletionRequest) -> None:
    """Execute one DeletionRequest end-to-end (claim → delete → finalize).

    Caller has already loaded the row; this function transitions
    pending → running → completed and commits at each transition.

    On failure: rolls back the session and **re-raises** so the caller's
    retry loop can decide whether to retry or finalize as ``failed``.
    Status stays ``running`` between retry attempts; the caller is
    responsible for flipping to ``failed`` after retries are exhausted.
    """
    if req.status == DeletionRequestStatus.cancelled:
        logger.info("Deletion request %s already cancelled; skipping", req.id)
        return
    if req.status in (
        DeletionRequestStatus.completed,
        DeletionRequestStatus.failed,
    ):
        logger.debug(
            "Deletion request %s in terminal status=%s; skipping",
            req.id, req.status.value,
        )
        return
    if req.status == DeletionRequestStatus.pending:
        # 條件式 UPDATE 認領：API 背景任務與排程 tick 可能同時拿到同一張
        # pending 單，只有把 pending 翻成 running 的那個能繼續，另一個直接退出，
        # 不會對同一台機器下兩次刪除。
        claimed = session.execute(
            update(DeletionRequest)
            .where(
                DeletionRequest.id == req.id,  # type: ignore[arg-type]
                DeletionRequest.status == DeletionRequestStatus.pending,  # type: ignore[arg-type]
            )
            .values(status=DeletionRequestStatus.running, started_at=_utc_now())
        )
        session.commit()
        session.refresh(req)
        if claimed.rowcount == 0:
            logger.info(
                "Deletion request %s already claimed by another worker; skipping",
                req.id,
            )
            return
    # else: already running → retry path; reuse existing started_at

    resource = session.exec(
        select(Resource).where(Resource.vmid == req.vmid)
    ).first()
    # resource may be None for admin-initiated deletion of orphan resources
    # (machines that exist in Proxmox but have no DB record). In that case
    # we still attempt the Proxmox deletion using the snapshot data.
    if (
        resource is not None
        and req.resource_vmid is None
        and resource.created_at is not None
        and resource.created_at > req.created_at
    ):
        # 這張單原本指的機器已被別的途徑刪掉（resource_vmid 已被 SET NULL）、
        # VMID 又配給了新機器：絕不能拿舊單的快照去刪現在這台。
        # 原本的條件要求 resource_vmid 不為 NULL，但那代表原資源列還在，
        # created_at 不可能晚於申請，這道防線從來不會觸發。
        req.status = DeletionRequestStatus.failed
        req.error_message = (
            f"VMID {req.vmid} now belongs to a different resource created after this "
            "request; refusing to delete it"
        )
        req.completed_at = _utc_now()
        session.add(req)
        session.commit()
        logger.warning(
            "Deletion request %s aborted: vmid=%s was reassigned to user %s",
            req.id, req.vmid, resource.user_id,
        )
        return

    # The Resource model only stores user/business metadata (env type, owner,
    # SSH keys, etc.). Live Proxmox info (node / type / status) must come from
    # the DeletionRequest snapshot (captured at request time) and a live
    # status query.
    node = req.node
    resource_type = req.resource_type
    if not node or not resource_type:
        req.status = DeletionRequestStatus.failed
        req.error_message = (
            f"Deletion request {req.id} missing node/resource_type snapshot"
        )
        req.completed_at = _utc_now()
        session.add(req)
        session.commit()
        logger.error(
            "Deletion request %s aborted: snapshot missing node=%s type=%s",
            req.id, node, resource_type,
        )
        return

    try:
        from app.services.proxmox import proxmox_service

        live_status = proxmox_service.get_status(node, req.vmid, resource_type).get(
            "status", ""
        )
    except Exception as exc:
        logger.warning(
            "Deletion request %s: failed to fetch live status for vmid=%s on node=%s: %s",
            req.id, req.vmid, node, exc,
        )
        live_status = ""

    resource_info = {
        "vmid": req.vmid,
        "node": node,
        "type": resource_type,
        "name": req.name,
        "status": live_status,
    }

    try:
        resource_service.delete(
            session=session,
            vmid=req.vmid,
            resource_info=resource_info,
            user_id=req.user_id,
            purge=req.purge,
            force=req.force,
        )
        # Re-check whether the user cancelled while we were running.
        fresh = session.get(DeletionRequest, req.id)
        if fresh is None:
            return
        if fresh.status == DeletionRequestStatus.cancelled:
            logger.info(
                "Deletion request %s was cancelled during execution; "
                "deletion still completed on Proxmox",
                req.id,
            )
            return
        fresh.status = DeletionRequestStatus.completed
        fresh.completed_at = _utc_now()
        session.add(fresh)
        session.commit()
        logger.info("Deletion request %s completed (vmid=%s)", req.id, req.vmid)
    except Exception:
        try:
            session.rollback()
        except Exception:
            # rollback 失敗不影響錯誤回報
            pass
        # Surface the error to the caller's retry loop. We deliberately do
        # NOT mark the row as ``failed`` here so the row stays ``running``
        # between retry attempts — the wrapper finalizes on exhaustion.
        raise


def enqueue_processing(*, session: Session, req: DeletionRequest) -> None:
    """把剛建立的 pending 刪除單交給 arq worker（``resource.delete``）。

    只對 ``pending`` 的單入列：``create_deletion_request`` 去重回傳既有的
    pending/running 單時，那張單已經有人在處理。入列失敗會拋出，呼叫端
    不用補救：排程 tick 的 ``process_pending_deletions`` 會撿起 pending 單。
    """
    if req.status != DeletionRequestStatus.pending:
        return
    enqueue_task_sync(
        session=session,
        task_type=TASK_DELETE,
        user_id=req.user_id,
        payload={"request_id": str(req.id), "vmid": req.vmid},
    )


def run_delete_task(task_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
    """worker 端 handler：解包 payload 後跑 ``process_one_request``。"""
    request_id = uuid.UUID(str(payload["request_id"]))
    logger.info("Deletion task %s started for request %s", task_id, request_id)
    process_one_request(request_id)
    return {"request_id": str(request_id), "vmid": payload.get("vmid")}


def process_one_request(
    request_id: uuid.UUID,
    *,
    max_retries: int = 2,
    retry_delay: float = 10.0,
    retry_backoff: float = 2.0,
) -> None:
    """Background entrypoint: process a single DeletionRequest by id.


    Opens its own DB session per attempt so it's safe to run as a
    fire-and-forget task. On failure, retries up to ``max_retries`` times
    with exponential backoff. Only after all attempts fail does the
    request transition to ``failed``. Cancelled requests are honoured
    immediately at the start of each attempt.
    """
    import time

    from app.core.db import engine

    delay = max(0.0, retry_delay)
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 2):
        # Honour cancellation between retries.
        with Session(engine) as session:
            req = session.get(DeletionRequest, request_id)
            if req is None:
                logger.warning(
                    "process_one_request: DeletionRequest %s not found", request_id
                )
                return
            if req.status in (
                DeletionRequestStatus.cancelled,
                DeletionRequestStatus.completed,
                DeletionRequestStatus.failed,
            ):
                logger.info(
                    "process_one_request: %s already in terminal status=%s; aborting",
                    request_id, req.status.value,
                )
                return

            try:
                _execute_deletion(session, req)
                return  # success
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Deletion request %s attempt %d/%d failed: %s",
                    request_id, attempt, max_retries + 1, exc,
                )

        if attempt > max_retries:
            break
        time.sleep(delay)
        delay = min(delay * retry_backoff, 300.0)

    # All attempts exhausted — finalize as failed (unless cancelled meanwhile).
    with Session(engine) as session:
        req = session.get(DeletionRequest, request_id)
        if req is None:
            return
        if req.status == DeletionRequestStatus.cancelled:
            return
        if req.status in (
            DeletionRequestStatus.completed,
            DeletionRequestStatus.failed,
        ):
            return
        req.status = DeletionRequestStatus.failed
        req.error_message = (
            (str(last_exc)[:2000]) if last_exc is not None else "Deletion failed"
        )
        req.completed_at = _utc_now()
        session.add(req)
        session.commit()
        logger.error(
            "Deletion request %s permanently failed after %d attempt(s): %s",
            request_id, max_retries + 1, last_exc,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Scheduler tick (safety net — picks up requests dropped by background path)
# ──────────────────────────────────────────────────────────────────────────────


def process_pending_deletions(session: Session) -> None:
    """Scheduler tick: process pending DeletionRequests as a safety net.

    Most deletions are kicked off via background task right after the API call.
    This tick exists to recover from server restarts or background-task failures.
    Processes up to a small batch per tick to avoid blocking the scheduler loop.
    """
    pending = list(
        session.exec(
            select(DeletionRequest)
            .where(DeletionRequest.status == DeletionRequestStatus.pending)
            .order_by(DeletionRequest.created_at.asc())  # type: ignore[union-attr]
            .limit(5)
        ).all()
    )

    # 回收殭屍 running：伺服器重啟或背景任務消失會把請求永遠留在 running，
    # 而 create_deletion_request 看到 running 會擋掉同 vmid 的新請求。
    stale_cutoff = _utc_now() - timedelta(minutes=30)
    stale_running = list(
        session.exec(
            select(DeletionRequest)
            .where(
                DeletionRequest.status == DeletionRequestStatus.running,
                DeletionRequest.started_at.is_not(None),  # type: ignore[union-attr]
                DeletionRequest.started_at <= stale_cutoff,
            )
            .limit(5)
        ).all()
    )

    for req in pending + stale_running:
        try:
            _execute_deletion(session, req)
        except Exception as exc:
            logger.exception(
                "process_pending_deletions: unhandled error for request %s", req.id
            )
            # 安全網路徑沒有重試 wrapper：若不收尾，請求會永遠卡在 running
            # （之後的 tick 只撈 pending）。標記 failed，使用者可再送一次刪除（failed 不算進行中）。
            try:
                session.rollback()
                fresh = session.get(DeletionRequest, req.id)
                if fresh is not None and fresh.status == DeletionRequestStatus.running:
                    fresh.status = DeletionRequestStatus.failed
                    fresh.error_message = str(exc)[:2000]
                    fresh.completed_at = _utc_now()
                    session.add(fresh)
                    session.commit()
            except Exception:
                logger.exception(
                    "process_pending_deletions: failed to finalize request %s", req.id
                )

