"""批量建立資源服務 — 包含逐一排隊邏輯"""

import json
import logging
import re
import threading
import uuid
from datetime import date

from sqlmodel import Session

from app.core.db import engine
from app.exceptions import BadRequestError
from app.models.batch_provision import BatchProvisionJobStatus, BatchProvisionTask
from app.repositories import batch_provision as bp_repo
from app.repositories import group as group_repo
from app.schemas import LXCCreateRequest, VMCreateRequest
from app.services.network import ip_management_service
from app.services.proxmox import provisioning_service

logger = logging.getLogger(__name__)


# ─── 公開 API ─────────────────────────────────────────────────────────────────


def submit_batch_job(
    *,
    session: Session,
    group_id: uuid.UUID,
    initiated_by_id: uuid.UUID,
    resource_type: str,
    hostname_prefix: str,
    params: dict,
    recurrence_rule: str | None = None,
    recurrence_duration_minutes: int | None = None,
    schedule_timezone: str | None = None,
) -> uuid.UUID:
    """Create a BatchProvisionJob in ``pending_review`` state.

    The job (and its per-member tasks) are persisted but no provisioning is
    started — an admin must call :func:`approve_batch_job` first.

    Validates that the IP subnet is configured and that there is enough free
    capacity for every group member before persisting anything.
    """
    member_rows = group_repo.get_member_rows(session=session, group_id=group_id)
    if not member_rows:
        raise BadRequestError("群組沒有成員，無法執行批量建立")

    # 防護：子網必須已設定
    ip_management_service.ensure_subnet_configured(session)

    # 檢查可用 IP 是否足夠
    stats = ip_management_service.get_ip_stats(session)
    if stats["available"] < len(member_rows):
        raise BadRequestError(
            f"可用 IP 不足：需要 {len(member_rows)} 個，"
            f"但僅剩 {stats['available']} 個可用"
        )

    member_user_ids = [row.user_id for row in member_rows]

    if recurrence_rule and not recurrence_duration_minutes:
        raise BadRequestError(
            "排程必須同時指定 recurrence_rule 與 recurrence_duration_minutes"
        )

    job = bp_repo.create_job(
        session=session,
        group_id=group_id,
        initiated_by=initiated_by_id,
        resource_type=resource_type,
        hostname_prefix=hostname_prefix,
        template_params=json.dumps(params),
        member_user_ids=member_user_ids,
        initial_status=BatchProvisionJobStatus.pending_review,
        recurrence_rule=recurrence_rule,
        recurrence_duration_minutes=recurrence_duration_minutes,
        schedule_timezone=schedule_timezone,
    )

    logger.info(
        "Batch provision job %s submitted (pending_review): %d members, type=%s prefix=%s",
        job.id, len(member_user_ids), resource_type, hostname_prefix,
    )
    return job.id


def approve_batch_job(
    *,
    session: Session,
    job_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    review_comment: str | None = None,
) -> None:
    """Approve a pending batch job and spawn the background worker."""
    job = bp_repo.get_job(session=session, job_id=job_id)
    if job is None:
        raise BadRequestError("Batch job not found")
    if job.status != BatchProvisionJobStatus.pending_review:
        raise BadRequestError(
            f"Batch job is not pending review (current status: {job.status})"
        )

    bp_repo.mark_reviewed(
        session=session,
        job_id=job_id,
        reviewer_id=reviewer_id,
        decision=BatchProvisionJobStatus.approved,
        review_comment=review_comment,
    )

    t = threading.Thread(
        target=_run_queue,
        args=(job_id,),
        daemon=True,
        name=f"batch-provision-{job_id}",
    )
    t.start()

    logger.info("Batch provision job %s approved by %s", job_id, reviewer_id)


def reject_batch_job(
    *,
    session: Session,
    job_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    review_comment: str | None = None,
) -> None:
    """Reject a pending batch job; no provisioning takes place."""
    job = bp_repo.get_job(session=session, job_id=job_id)
    if job is None:
        raise BadRequestError("Batch job not found")
    if job.status != BatchProvisionJobStatus.pending_review:
        raise BadRequestError(
            f"Batch job is not pending review (current status: {job.status})"
        )
    bp_repo.mark_reviewed(
        session=session,
        job_id=job_id,
        reviewer_id=reviewer_id,
        decision=BatchProvisionJobStatus.rejected,
        review_comment=review_comment,
    )
    logger.info("Batch provision job %s rejected by %s", job_id, reviewer_id)


# Backwards-compat shim — older callers still pass through ``start_batch_job``.
# The scheduling feature wraps batches in a review step; immediate provisioning
# is no longer the default but can be opted into for non-recurring jobs that
# bypass review (e.g. a future "instant batch" admin tool).
def start_batch_job(
    *,
    session: Session,
    group_id: uuid.UUID,
    initiated_by_id: uuid.UUID,
    resource_type: str,
    hostname_prefix: str,
    params: dict,
    **schedule_kwargs,
) -> uuid.UUID:
    return submit_batch_job(
        session=session,
        group_id=group_id,
        initiated_by_id=initiated_by_id,
        resource_type=resource_type,
        hostname_prefix=hostname_prefix,
        params=params,
        **schedule_kwargs,
    )


# ─── 背景排隊執行 ──────────────────────────────────────────────────────────────


def _run_queue(job_id: uuid.UUID) -> None:
    """背景執行緒：逐一建立每個成員的資源。"""
    with Session(engine) as session:
        bp_repo.update_job_status(
            session=session,
            job_id=job_id,
            status=BatchProvisionJobStatus.running,
        )

    with Session(engine) as session:
        tasks = bp_repo.get_pending_tasks(session=session, job_id=job_id)
        task_ids = [t.id for t in tasks]

    for task_id in task_ids:
        _process_task(job_id=job_id, task_id=task_id)

    with Session(engine) as session:
        job = bp_repo.get_job(session=session, job_id=job_id)
        if job is None:
            return
        final = (
            BatchProvisionJobStatus.failed
            if job.failed_count > 0 and job.done == 0
            else BatchProvisionJobStatus.completed
        )
        bp_repo.update_job_status(session=session, job_id=job_id, status=final)
        logger.info(
            "Batch provision job %s finished: done=%d failed=%d",
            job_id, job.done, job.failed_count,
        )


def _process_task(*, job_id: uuid.UUID, task_id: uuid.UUID) -> None:
    """執行單一成員的建立，並更新 task / job 計數。"""
    # 讀取必要資訊
    with Session(engine) as session:
        task = session.get(BatchProvisionTask, task_id)
        if task is None:
            return
        job = bp_repo.get_job(session=session, job_id=job_id)
        if job is None:
            return
        params = json.loads(job.template_params)
        member_index = task.member_index
        user_id = task.user_id
        resource_type = job.resource_type
        hostname = _build_hostname(job.hostname_prefix, member_index)

    with Session(engine) as session:
        bp_repo.update_task_running(session=session, task_id=task_id)

    try:
        with Session(engine) as session:
            vmid = _provision_one(
                session=session,
                resource_type=resource_type,
                hostname=hostname,
                user_id=user_id,
                params=params,
            )

        with Session(engine) as session:
            bp_repo.update_task_done(session=session, task_id=task_id, vmid=vmid)
            bp_repo.increment_job_done(session=session, job_id=job_id)

        logger.info("Batch task %s done: vmid=%d user=%s", task_id, vmid, user_id)

    except Exception as exc:
        error_msg = str(exc)[:500]
        with Session(engine) as session:
            bp_repo.update_task_failed(
                session=session, task_id=task_id, error=error_msg
            )
            bp_repo.increment_job_failed(session=session, job_id=job_id)
        logger.error("Batch task %s failed user=%s: %s", task_id, user_id, error_msg)


def _provision_one(
    *,
    session: Session,
    resource_type: str,
    hostname: str,
    user_id: uuid.UUID,
    params: dict,
) -> int:
    """呼叫 provisioning_service 建立單一資源，回傳 vmid。"""
    if resource_type == "lxc":
        req = LXCCreateRequest(
            hostname=hostname,
            ostemplate=params["ostemplate"],
            cores=params["cores"],
            memory=params["memory"],
            rootfs_size=params.get("rootfs_size", 8),
            password=params["password"],
            storage=params.get("storage", "local-lvm"),
            environment_type=params.get("environment_type", "批量建立"),
            os_info=params.get("os_info"),
            expiry_date=_parse_date(params.get("expiry_date")),
            start=True,
            unprivileged=True,
        )
        result = provisioning_service.create_lxc(
            session=session, lxc_data=req, user_id=user_id
        )
    else:
        req = VMCreateRequest(
            hostname=hostname,
            template_id=params["template_id"],
            username=params["username"],
            password=params["password"],
            cores=params["cores"],
            memory=params["memory"],
            disk_size=params.get("disk_size", 20),
            storage=params.get("storage", "local-lvm"),
            environment_type=params.get("environment_type", "批量建立"),
            os_info=params.get("os_info"),
            expiry_date=_parse_date(params.get("expiry_date")),
            start=True,
        )
        result = provisioning_service.create_vm(
            session=session, vm_data=req, user_id=user_id
        )

    return result.vmid


# ─── 工具函式 ──────────────────────────────────────────────────────────────────


def _build_hostname(prefix: str, index: int) -> str:
    # Replace any character that isn't a letter, digit, or hyphen with a hyphen
    sanitized = re.sub(r"[^a-zA-Z0-9-]", "-", prefix)
    # Collapse consecutive hyphens and strip leading/trailing hyphens
    sanitized = re.sub(r"-{2,}", "-", sanitized).strip("-") or "vm"
    suffix = str(index)
    max_prefix = 63 - 1 - len(suffix)
    return f"{sanitized[:max_prefix]}-{suffix}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None
