"""Resource deletion request service.

提供「將刪除請求加入佇列」、「取消佇列中的刪除」、「scheduler 處理 pending」三組能力。
實際刪除邏輯仍委派給 `resource_service.delete`，本 service 只負責生命週期管理與 audit。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.exceptions import AppError
from app.models import (
    DeletionRequest,
    DeletionRequestStatus,
    Resource,
    User,
)
from app.services.resource import resource_service

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
    existing = session.exec(
        select(DeletionRequest).where(
            DeletionRequest.vmid == vmid,
            DeletionRequest.status.in_(  # type: ignore[union-attr]
                [DeletionRequestStatus.pending, DeletionRequestStatus.running]
            ),
        )
    ).first()
    if existing is not None:
        return existing

    req = DeletionRequest(
        user_id=user_id,
        vmid=vmid,
        resource_vmid=vmid if session.get(Resource, vmid) is not None else None,
        name=resource_info.get("name"),
        node=resource_info.get("node"),
        resource_type=resource_info.get("type"),
        purge=purge,
        force=force,
        status=DeletionRequestStatus.pending,
        created_at=_utc_now(),
    )
    session.add(req)
    session.commit()
    session.refresh(req)
    logger.info("Queued deletion request %s for vmid=%s", req.id, vmid)
    return req


def cancel_deletion_request(
    *,
    session: Session,
    request_id: uuid.UUID,
    user_id: uuid.UUID,
    is_admin: bool,
) -> DeletionRequest:
    """Cancel a pending or in-flight deletion request.

    - ``pending``: simply mark cancelled.
    - ``running``: best-effort cancel the underlying background task and
      mark the request cancelled. Note that if the worker thread is in
      the middle of a Proxmox API call the call will run to completion;
      the cancel only prevents further retries / follow-up work.
    - terminal (completed/failed/cancelled): rejected with 409.
    """
    from app.infrastructure.worker import cancel as _cancel_bg_task  # noqa: PLC0415

    req = session.get(DeletionRequest, request_id)
    if req is None:
        raise AppError(404, "Deletion request not found")
    if not is_admin and req.user_id != user_id:
        raise AppError(403, "Not allowed to cancel this deletion request")
    if req.status not in (DeletionRequestStatus.pending, DeletionRequestStatus.running):
        raise AppError(
            409,
            f"Cannot cancel deletion request in status={req.status.value}",
        )

    was_running = req.status == DeletionRequestStatus.running
    if was_running:
        # Best-effort: cancel the asyncio task; effective if it's still
        # waiting for the semaphore or sleeping between retries.
        cancelled_in_runner = _cancel_bg_task(str(req.id))
        logger.info(
            "Best-effort cancel for running deletion request %s: runner_cancelled=%s",
            req.id, cancelled_in_runner,
        )

    req.status = DeletionRequestStatus.cancelled
    req.completed_at = _utc_now()
    if was_running:
        req.error_message = "Cancelled by user while running"
    session.add(req)
    session.commit()
    session.refresh(req)
    logger.info("Cancelled deletion request %s (vmid=%s)", req.id, req.vmid)
    return req


def list_for_user(
    *,
    session: Session,
    user_id: uuid.UUID,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[DeletionRequest], int]:
    rows = session.exec(
        select(DeletionRequest)
        .where(DeletionRequest.user_id == user_id)
        .order_by(DeletionRequest.created_at.desc())  # type: ignore[union-attr]
        .offset(skip)
        .limit(limit)
    ).all()
    total = len(
        session.exec(
            select(DeletionRequest.id).where(DeletionRequest.user_id == user_id)
        ).all()
    )
    return list(rows), total


def list_all(
    *,
    session: Session,
    status: DeletionRequestStatus | None = None,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[DeletionRequest], int]:
    stmt = select(DeletionRequest)
    if status is not None:
        stmt = stmt.where(DeletionRequest.status == status)
    stmt = stmt.order_by(DeletionRequest.created_at.desc()).offset(skip).limit(limit)  # type: ignore[union-attr]
    rows = session.exec(stmt).all()

    count_stmt = select(DeletionRequest.id)
    if status is not None:
        count_stmt = count_stmt.where(DeletionRequest.status == status)
    total = len(session.exec(count_stmt).all())
    return list(rows), total


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
            DeletionRequest.status.in_(  # type: ignore[union-attr]
                [DeletionRequestStatus.pending, DeletionRequestStatus.running]
            ),
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
        req.status = DeletionRequestStatus.running
        req.started_at = _utc_now()
        session.add(req)
        session.commit()
        session.refresh(req)
    # else: already running → retry path; reuse existing started_at

    session.exec(
        select(Resource).where(Resource.vmid == req.vmid)
    ).first()
    # resource may be None for admin-initiated deletion of orphan resources
    # (machines that exist in Proxmox but have no DB record). In that case
    # we still attempt the Proxmox deletion using the snapshot data.

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
        from app.services.proxmox import proxmox_service  # noqa: PLC0415

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
    import time  # noqa: PLC0415 — keep import local

    from app.core.db import engine  # noqa: PLC0415 — keep import local to avoid cycles

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


def retry_failed_request(
    *,
    session: Session,
    request_id: uuid.UUID,
    user_id: uuid.UUID,
    is_admin: bool,
) -> DeletionRequest:
    """Manually re-queue a failed DeletionRequest for another attempt.

    Resets status to ``pending`` and clears ``error_message`` /
    ``completed_at`` so the standard pipeline (background task or
    scheduler tick) picks it up again.
    """
    req = session.get(DeletionRequest, request_id)
    if req is None:
        raise AppError(404, "Deletion request not found")
    if not is_admin and req.user_id != user_id:
        raise AppError(403, "Not allowed to retry this deletion request")
    if req.status != DeletionRequestStatus.failed:
        raise AppError(
            409,
            f"Only failed deletion requests can be retried (current={req.status.value})",
        )
    req.status = DeletionRequestStatus.pending
    req.error_message = None
    req.started_at = None
    req.completed_at = None
    session.add(req)
    session.commit()
    session.refresh(req)
    logger.info("Re-queued failed deletion request %s for retry", req.id)
    return req


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
            # （之後的 tick 只撈 pending）。標記 failed，留給使用者手動 retry。
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


# ──────────────────────────────────────────────────────────────────────────────
# Helpers for jobs/UI
# ──────────────────────────────────────────────────────────────────────────────


def to_public_with_user(
    *,
    session: Session,
    req: DeletionRequest,
) -> dict:
    user = session.get(User, req.user_id)
    return {
        "id": req.id,
        "user_id": req.user_id,
        "vmid": req.vmid,
        "name": req.name,
        "node": req.node,
        "resource_type": req.resource_type,
        "purge": req.purge,
        "force": req.force,
        "status": req.status,
        "error_message": req.error_message,
        "created_at": req.created_at,
        "started_at": req.started_at,
        "completed_at": req.completed_at,
        "user_email": user.email if user is not None else None,
        "user_full_name": user.full_name if user is not None else None,
    }
