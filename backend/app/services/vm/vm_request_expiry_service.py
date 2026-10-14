"""申請逾時自動過期。

``vm_request_service.review()`` 早已擋掉「end_at 已過不得核准」，卻沒有任何
機制清理這些申請，於是它們永遠停在 pending：管理員看得到卻核准不了，使用者
看到的「審核中」也是假的。本模組由 scheduler 每個 tick 呼叫，把它們標為
``expired``。

``process_expired_requests`` 只動 pending —— 已開出機器（有 vmid）的
``approved`` 申請由 ``coordinator.process_due_request_stops`` 負責到期關機，
改它的狀態會讓已開通的機器脫離排程管理變成孤兒。

``process_expired_unprovisioned_requests`` 處理另一種：已核准、時段結束卻
始終沒開出機器（vmid 為空）的申請。排程只在時段內佈建，這些單不會再變成
機器，卻會一直佔著申請人的配額；同樣標成 ``expired``。clone 正在跑的不動，
沒有結束時間的（immediate 模式）也不動。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlmodel import Session

from app.core.db import engine
from app.models import AuditAction, VMProvisioningStatus
from app.repositories import vm_request as vm_request_repo
from app.services.scheduling import policy as scheduling_policy
from app.services.user import audit_service

logger = logging.getLogger(__name__)

# 單一 tick 最多處理的筆數，避免長交易佔住 scheduler。
EXPIRY_BATCH_SIZE = 100


def _utc_now() -> datetime:
    return datetime.now(UTC)


def process_expired_requests() -> int:
    """把已過使用時段的 pending 申請標為 expired，回傳處理筆數。"""
    now = _utc_now()

    with Session(engine) as session:
        due = vm_request_repo.list_expired_pending_vm_requests(
            session=session,
            at_time=now,
            limit=EXPIRY_BATCH_SIZE,
        )
        if not due:
            return 0

        # commit 後 ORM 物件會被 expire，屆時讀 id 會觸發額外查詢；先取好。
        expired_ids = [request.id for request in due]

        try:
            for request in due:
                vm_request_repo.mark_vm_request_expired(
                    session=session,
                    db_request=request,
                    commit=False,
                )
                audit_service.log_action(
                    session=session,
                    user_id=None,
                    action=AuditAction.vm_request_expired,
                    details=(
                        f"Auto-expired VM request {request.id}: "
                        f"usage window ended at {request.end_at}"
                    ),
                    commit=False,
                )
            # 整批一次 commit：這裡沒有外部 I/O，逐筆 commit 反而會在第一次
            # commit 後釋放 FOR UPDATE 鎖，讓剩下的列失去併發保護。
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("Failed to expire %d VM request(s)", len(expired_ids))
            return 0

    logger.info("Auto-expired %d VM request(s): %s", len(expired_ids), expired_ids)
    return len(expired_ids)


def _provisioning_in_flight(request: object, *, now: datetime) -> bool:
    """clone 是否正在 worker 上跑（running 且未超過 stale 門檻）。"""
    if getattr(request, "provisioning_status", None) != VMProvisioningStatus.running:
        return False
    return not scheduling_policy.is_provisioning_stale(
        getattr(request, "provisioning_started_at", None), now=now
    )


def process_expired_unprovisioned_requests() -> int:
    """把時段已結束、卻沒開出機器的 approved 申請標為 expired，回傳處理筆數。"""
    now = _utc_now()

    with Session(engine) as session:
        due = [
            request
            for request in vm_request_repo.list_expired_unprovisioned_approved_vm_requests(
                session=session,
                at_time=now,
                limit=EXPIRY_BATCH_SIZE,
            )
            # 時段結束前就開始的 clone 還在跑：機器隨時會掛上來，留給它收尾
            if not _provisioning_in_flight(request, now=now)
        ]
        if not due:
            return 0

        expired_ids = [request.id for request in due]

        try:
            for request in due:
                vm_request_repo.mark_vm_request_expired(
                    session=session,
                    db_request=request,
                    commit=False,
                )
                audit_service.log_action(
                    session=session,
                    user_id=None,
                    action=AuditAction.vm_request_expired,
                    details=(
                        f"Auto-expired approved VM request {request.id}: usage "
                        f"window ended at {request.end_at} before a machine "
                        "was provisioned"
                    ),
                    commit=False,
                )
            session.commit()
        except Exception:
            session.rollback()
            logger.exception(
                "Failed to expire %d unprovisioned approved VM request(s)",
                len(expired_ids),
            )
            return 0

    logger.info(
        "Auto-expired %d unprovisioned approved VM request(s): %s",
        len(expired_ids),
        expired_ids,
    )
    return len(expired_ids)
