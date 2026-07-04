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
from app.models import VMTemplateStatus
from app.models.batch_provision import BatchProvisionJobStatus, BatchProvisionTask
from app.repositories import batch_provision as bp_repo
from app.repositories import group as group_repo
from app.repositories import vm_template as vm_template_repo
from app.schemas import LXCCreateRequest, VMCreateRequest
from app.services.network import ip_management_service
from app.services.proxmox import provisioning_service
from app.services.resource import quota_service
from app.services.template import clone_service

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

    # 範本系統 2.0：指定 vm_template_id 時走克隆路徑，範本必須存在且 ready
    if params.get("vm_template_id"):
        template = vm_template_repo.get_template(
            session=session,
            template_id=uuid.UUID(str(params["vm_template_id"])),
        )
        if template is None or template.status != VMTemplateStatus.ready:
            raise BadRequestError("指定的範本不存在或尚未就緒")

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
    """Approve a pending batch job and spawn the background worker.

    The status transition is atomic — if two admins click "approve" at the
    same time, only the one whose UPDATE wins races spawns a worker; the
    other gets a BadRequestError.
    """
    # First fail fast if the job doesn't exist at all (gives a clearer error
    # than the race-loser path).
    if bp_repo.get_job(session=session, job_id=job_id) is None:
        raise BadRequestError("Batch job not found")

    job = bp_repo.transition_pending_review(
        session=session,
        job_id=job_id,
        reviewer_id=reviewer_id,
        decision=BatchProvisionJobStatus.approved,
        review_comment=review_comment,
    )
    if job is None:
        # Either status changed under us (concurrent reviewer) or it wasn't
        # in pending_review to begin with.
        raise BadRequestError(
            "Batch job is no longer pending review (it may have been "
            "processed by another reviewer)."
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
    """Reject a pending batch job; no provisioning takes place. Same atomic
    semantics as :func:`approve_batch_job`."""
    if bp_repo.get_job(session=session, job_id=job_id) is None:
        raise BadRequestError("Batch job not found")

    job = bp_repo.transition_pending_review(
        session=session,
        job_id=job_id,
        reviewer_id=reviewer_id,
        decision=BatchProvisionJobStatus.rejected,
        review_comment=review_comment,
    )
    if job is None:
        raise BadRequestError(
            "Batch job is no longer pending review (it may have been "
            "processed by another reviewer)."
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
        start_on_create = job.recurrence_rule is None

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
                start=start_on_create,
                batch_job_id=job_id,
            )

        # E1：批量建立完成點也建初始快照（best-effort）
        from app.services.resource import reset_service  # noqa: PLC0415

        reset_service.ensure_init_snapshot(vmid)

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
    start: bool = True,
    batch_job_id: uuid.UUID | None = None,
) -> int:
    """建立單一資源，回傳 vmid。

    指定 ``vm_template_id`` 時走範本系統 2.0 統一克隆路徑
    （linked 優先退 full）；否則沿用 provisioning_service 舊路徑。
    """
    quota_service.check_quota(
        session,
        user_id,
        delta_cores=int(params.get("cores") or 0),
        delta_memory_mb=int(params.get("memory") or 0),
        delta_disk_gb=int(params.get("disk_size") or params.get("rootfs_size") or 0),
        delta_instances=1,
    )

    if params.get("vm_template_id"):
        payload = {
            "template_id": str(params["vm_template_id"]),
            "user_id": str(user_id),
            "hostname": hostname,
            "cores": params.get("cores"),
            "memory": params.get("memory"),
            "disk": params.get("disk_size"),
            "start": start,
            "batch_job_id": str(batch_job_id) if batch_job_id else None,
            "environment_type": params.get("environment_type", "批量建立"),
            "expiry_date": params.get("expiry_date"),
        }
        # 同步執行（batch 已在背景執行緒）；task_id 無對應 TaskRecord，
        # report_progress 會自動 no-op
        clone_result = clone_service.run_clone_task(uuid.uuid4(), payload)
        return int(clone_result["vmid"])

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
            start=start,
            unprivileged=True,
        )
        result = provisioning_service.create_lxc(
            session=session, lxc_data=req, user_id=user_id, batch_job_id=batch_job_id
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
            start=start,
        )
        result = provisioning_service.create_vm(
            session=session, vm_data=req, user_id=user_id, batch_job_id=batch_job_id
        )

    if result.vmid is None:
        # 批量路徑走同步 provision，正常不會沒有 vmid（202 背景克隆才會）
        raise RuntimeError(f"Provisioning for '{hostname}' did not return a vmid")
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
