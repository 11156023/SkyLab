"""批量建立資源服務 — 包含逐一排隊邏輯"""

import json
import logging
import re
import secrets
import threading
import uuid
from datetime import date

from sqlmodel import Session, col, select

from app.core.db import engine
from app.core.i18n import t
from app.exceptions import BadRequestError
from app.models import (
    ClassCapacityReservation,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
    TeachingClassStudent,
    TeachingClassStudentMachine,
    VMTemplateStatus,
)
from app.models.batch_provision import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    BatchProvisionTask,
)
from app.repositories import batch_provision as bp_repo
from app.repositories import resource as resource_repo
from app.repositories import vm_template as vm_template_repo
from app.schemas import LXCCreateRequest, VMCreateRequest
from app.services.network import ip_management_service
from app.services.proxmox import provisioning_service, proxmox_service
from app.services.resource import quota_service
from app.services.template import clone_service

logger = logging.getLogger(__name__)


# ─── 公開 API ─────────────────────────────────────────────────────────────────


def submit_batch_job_for_users(
    *,
    session: Session,
    member_user_ids: list[uuid.UUID],
    initiated_by_id: uuid.UUID,
    resource_type: str,
    hostname_prefix: str,
    params: dict,
    teaching_class_id: uuid.UUID,
    recurrence_rule: str | None = None,
    recurrence_duration_minutes: int | None = None,
    schedule_timezone: str | None = None,
    capacity_reserved: bool = False,
) -> uuid.UUID:
    """Create a reviewed batch for an explicit class-student roster."""
    if not member_user_ids:
        raise BadRequestError(t("batch_provision.no_students"))

    # 範本系統 2.0：指定 vm_template_id 時走克隆路徑，範本必須存在且 ready
    if params.get("vm_template_id"):
        template = vm_template_repo.get_template(
            session=session,
            template_id=uuid.UUID(str(params["vm_template_id"])),
        )
        if template is None or template.status != VMTemplateStatus.ready:
            raise BadRequestError(t("batch_provision.template_not_ready"))

    # 防護：子網必須已設定
    ip_management_service.ensure_subnet_configured(session)

    # 檢查可用 IP 是否足夠
    stats = ip_management_service.get_ip_stats(session)
    if not capacity_reserved and stats["available"] < len(member_user_ids):
        raise BadRequestError(
            t(
                "batch_provision.insufficient_ip",
                needed=len(member_user_ids),
                available=stats["available"],
            )
        )

    if recurrence_rule and not recurrence_duration_minutes:
        raise BadRequestError(
            t("batch_provision.recurrence_requires_duration")
        )

    job = bp_repo.create_job(
        session=session,
        teaching_class_id=teaching_class_id,
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
        job.id,
        len(member_user_ids),
        resource_type,
        hostname_prefix,
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
        raise BadRequestError(t("batch_provision.job_not_found"))

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
            t("batch_provision.job_no_longer_pending")
        )

    worker_thread = threading.Thread(
        target=_run_queue,
        args=(job_id,),
        daemon=True,
        name=f"batch-provision-{job_id}",
    )
    worker_thread.start()

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
        raise BadRequestError(t("batch_provision.job_not_found"))

    job = bp_repo.transition_pending_review(
        session=session,
        job_id=job_id,
        reviewer_id=reviewer_id,
        decision=BatchProvisionJobStatus.rejected,
        review_comment=review_comment,
    )
    if job is None:
        raise BadRequestError(
            t("batch_provision.job_no_longer_pending")
        )
    logger.info("Batch provision job %s rejected by %s", job_id, reviewer_id)


def review_batch_jobs(
    *,
    session: Session,
    job_ids: list[uuid.UUID],
    reviewer_id: uuid.UUID,
    decision: BatchProvisionJobStatus,
    review_comment: str | None = None,
) -> list[BatchProvisionJob]:
    """Review all current jobs of a class as one atomic decision."""
    if not job_ids:
        raise BadRequestError(t("batch_provision.class_no_pending_jobs"))
    jobs = bp_repo.transition_pending_reviews(
        session=session,
        job_ids=job_ids,
        reviewer_id=reviewer_id,
        decision=decision,
        review_comment=review_comment,
    )
    if len(jobs) != len(set(job_ids)):
        raise BadRequestError(
            t("batch_provision.class_jobs_not_all_pending")
        )
    if decision == BatchProvisionJobStatus.approved:
        for job in jobs:
            thread = threading.Thread(
                target=_run_queue,
                args=(job.id,),
                daemon=True,
                name=f"batch-provision-{job.id}",
            )
            thread.start()
    logger.info(
        "Teaching class batch jobs %s reviewed as %s by %s",
        ",".join(str(job.id) for job in jobs),
        decision.value,
        reviewer_id,
    )
    return jobs


# ─── 背景排隊執行 ──────────────────────────────────────────────────────────────


def _run_queue(job_id: uuid.UUID) -> None:
    """背景執行緒：逐一建立每個成員的資源。"""
    with Session(engine) as session:
        if not bp_repo.transition_job_to_running(
            session=session, job_id=job_id
        ):
            return

    with Session(engine) as session:
        tasks = bp_repo.get_pending_tasks(session=session, job_id=job_id)
        task_ids = [t.id for t in tasks]

    for task_id in task_ids:
        _process_task(job_id=job_id, task_id=task_id)

    with Session(engine) as session:
        job = bp_repo.get_job(session=session, job_id=job_id)
        if job is None:
            return
        if job.status == BatchProvisionJobStatus.cancelled:
            return
        final = (
            BatchProvisionJobStatus.failed
            if job.failed_count > 0 and job.done == 0
            else BatchProvisionJobStatus.completed
        )
        bp_repo.update_job_status(session=session, job_id=job_id, status=final)
        teaching_class_id = job.teaching_class_id
        logger.info(
            "Batch provision job %s finished: done=%d failed=%d",
            job_id,
            job.done,
            job.failed_count,
        )

    if teaching_class_id is not None:
        _refresh_teaching_class_status(teaching_class_id)


def _refresh_teaching_class_status(teaching_class_id: uuid.UUID) -> None:
    """Advance the owning class once a node job reaches a terminal state.

    Without this the class list keeps showing "建立中" until a teacher happens
    to open the class workspace, which is the only other caller that recomputes
    the status.
    """
    from app.services.teaching import class_status_service  # noqa: PLC0415

    try:
        with Session(engine) as session:
            class_status_service.recompute(
                session=session, class_id=teaching_class_id
            )
            session.commit()
    except Exception:
        logger.exception(
            "Failed to refresh teaching class status class_id=%s", teaching_class_id
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
        teaching_class_id = job.teaching_class_id
        hostname = _build_hostname(job.hostname_prefix, member_index)
        start_on_create = job.recurrence_rule is None
        teaching_class = session.get(TeachingClass, teaching_class_id)
        class_archived = (
            teaching_class is None
            or teaching_class.status == TeachingClassStatus.archived
        )
        job_cancelled = job.status == BatchProvisionJobStatus.cancelled
        initiated_by_id = job.initiated_by

    if class_archived or job_cancelled:
        with Session(engine) as session:
            bp_repo.update_task_failed(
                session=session,
                task_id=task_id,
                error="Teaching class is archived or provisioning was cancelled",
            )
            bp_repo.increment_job_failed(session=session, job_id=job_id)
        return

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
                teaching_class_id=teaching_class_id,
                target_node=_class_target_node(
                    session=session, job_id=job_id, user_id=user_id
                ),
            )

        # E1：批量建立完成點也建初始快照（best-effort）
        with Session(engine) as session:
            resource = resource_repo.assign_to_teaching_class(
                session=session,
                vmid=vmid,
                teaching_class_id=teaching_class_id,
            )
            if resource is None:
                raise RuntimeError(
                    f"Provisioned class resource {vmid} has no database record"
                )
            teaching_class = session.get(TeachingClass, teaching_class_id)
            if (
                teaching_class is None
                or teaching_class.status == TeachingClassStatus.archived
            ):
                resource_info = proxmox_service.find_resource(vmid)
                from app.services.resource import resource_service  # noqa: PLC0415

                resource_service.delete(
                    session=session,
                    vmid=vmid,
                    resource_info=resource_info,
                    user_id=initiated_by_id,
                    purge=True,
                    force=True,
                )
                raise RuntimeError(
                    "Teaching class was archived while resource was provisioning"
                )

        # Snapshot failure must not turn a successfully created resource into
        # a failed task, otherwise retrying can create a duplicate machine.
        try:
            from app.services.resource import reset_service  # noqa: PLC0415

            reset_service.ensure_init_snapshot(vmid)
        except Exception:
            logger.warning("Initial snapshot failed for class vmid=%s", vmid)

        with Session(engine) as session:
            bp_repo.update_task_done(session=session, task_id=task_id, vmid=vmid)
            _sync_class_machine_mapping(
                session=session,
                job_id=job_id,
                task_id=task_id,
                user_id=user_id,
                vmid=vmid,
                status="completed",
            )
            bp_repo.increment_job_done(session=session, job_id=job_id)

        logger.info("Batch task %s done: vmid=%d user=%s", task_id, vmid, user_id)

    except Exception:
        logger.exception("Batch task failed task=%s user=%s", task_id, user_id)
        error_msg = "Provisioning failed. Retry or contact an administrator."
        with Session(engine) as session:
            bp_repo.update_task_failed(
                session=session, task_id=task_id, error=error_msg
            )
            _sync_class_machine_mapping(
                session=session,
                job_id=job_id,
                task_id=task_id,
                user_id=user_id,
                vmid=None,
                status="failed",
                error=error_msg,
            )
            bp_repo.increment_job_failed(session=session, job_id=job_id)


def _class_target_node(
    *, session: Session, job_id: uuid.UUID, user_id: uuid.UUID
) -> str | None:
    """這位學生的這台課程機器該建在哪個節點。

    與容量預留共用 class_capacity_service 的同一份分配：先查該學生在預留階段
    被分到哪個叢集，再在該叢集內決定節點。這確保同一位學生的每一台機器都落
    在同一個叢集（跨叢集 L2 不通、拓樸形同虛設），同時允許不同學生分屬不同
    叢集 —— 例如 25 位在 A、10 位在 B。

    自訂 LXC 原本走 pick_target_node，會在所有連線之間自由挑選。

    解析不出來時回 None，沿用既有的預設節點行為（不讓建機因此中斷）。
    """
    from app.services.teaching import class_capacity_service  # noqa: PLC0415

    machine_node = session.exec(
        select(TeachingClassMachineNode).where(
            TeachingClassMachineNode.batch_job_id == job_id
        )
    ).first()
    if machine_node is None:
        return None
    try:
        return class_capacity_service.target_node_for_machine(
            session, machine_node=machine_node, user_id=user_id
        )
    except Exception:
        logger.warning(
            "Failed to resolve class target node for job=%s; falling back to default",
            job_id,
            exc_info=True,
        )
        return None


def _sync_class_machine_mapping(
    *,
    session: Session,
    job_id: uuid.UUID,
    task_id: uuid.UUID,
    user_id: uuid.UUID,
    vmid: int | None,
    status: str,
    error: str | None = None,
) -> None:
    """Persist the class-to-student machine grant as soon as provisioning ends."""
    job = session.get(BatchProvisionJob, job_id)
    if job is None:
        return
    node = session.exec(
        select(TeachingClassMachineNode).where(
            TeachingClassMachineNode.batch_job_id == job_id
        )
    ).first()
    enrollment = session.exec(
        select(TeachingClassStudent).where(
            TeachingClassStudent.class_id == job.teaching_class_id,
            TeachingClassStudent.user_id == user_id,
        )
    ).first()
    if node is None or enrollment is None:
        return
    mapping = session.exec(
        select(TeachingClassStudentMachine).where(
            TeachingClassStudentMachine.class_student_id == enrollment.id,
            TeachingClassStudentMachine.machine_node_id == node.id,
        )
    ).first()
    if mapping is None:
        mapping = TeachingClassStudentMachine(
            class_student_id=enrollment.id,
            machine_node_id=node.id,
        )
    mapping.batch_task_id = task_id
    mapping.vmid = vmid
    mapping.status = status
    mapping.error = error
    session.add(mapping)
    session.commit()


def _provision_one(
    *,
    session: Session,
    resource_type: str,
    hostname: str,
    user_id: uuid.UUID,
    params: dict,
    start: bool = True,
    batch_job_id: uuid.UUID | None = None,
    teaching_class_id: uuid.UUID | None = None,
    target_node: str | None = None,
) -> int:
    """建立單一資源，回傳 vmid。

    指定 ``vm_template_id`` 時走範本系統 2.0 統一克隆路徑
    （linked 優先退 full）；否則沿用 provisioning_service 舊路徑。
    """
    # Formal classes use the administrator-approved whole-class capacity
    # reservation and must not consume each student's personal quota.
    if teaching_class_id is not None:
        reservation = session.exec(
            select(ClassCapacityReservation).where(
                ClassCapacityReservation.class_id == teaching_class_id,
                col(ClassCapacityReservation.status).in_(["reserved", "consumed"]),
            )
        ).first()
        if reservation is None:
            raise BadRequestError(
                t("batch_provision.class_no_capacity_reservation")
            )
    else:
        quota_service.check_quota(
            session,
            user_id,
            delta_cores=int(params.get("cores") or 0),
            delta_memory_mb=int(params.get("memory") or 0),
            delta_disk_gb=int(
                params.get("disk_size") or params.get("rootfs_size") or 0
            ),
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
            "ip_reservation_key": (
                f"{params['ip_reservation_prefix']}:{user_id}"
                if params.get("ip_reservation_prefix")
                else None
            ),
        }
        # 同步執行（batch 已在背景執行緒）；task_id 無對應 TaskRecord，
        # report_progress 會自動 no-op
        clone_result = clone_service.run_clone_task(uuid.uuid4(), payload)
        return int(clone_result["vmid"])

    if resource_type == "lxc":
        reservation_key = (
            f"{params['ip_reservation_prefix']}:{user_id}"
            if params.get("ip_reservation_prefix")
            else None
        )
        req = LXCCreateRequest(
            hostname=hostname,
            ostemplate=params["ostemplate"],
            cores=params["cores"],
            memory=params["memory"],
            rootfs_size=params.get("rootfs_size", 8),
            password=params.get("password") or secrets.token_urlsafe(18),
            storage=params.get("storage", "local-lvm"),
            environment_type=params.get("environment_type", "批量建立"),
            os_info=params.get("os_info"),
            expiry_date=_parse_date(params.get("expiry_date")),
            start=start,
            unprivileged=bool(params.get("unprivileged", True)),
        )
        result = provisioning_service.create_lxc(
            session=session,
            lxc_data=req,
            user_id=user_id,
            batch_job_id=batch_job_id,
            ip_reservation_key=reservation_key,
            target_node=target_node,
        )
    else:
        reservation_key = (
            f"{params['ip_reservation_prefix']}:{user_id}"
            if params.get("ip_reservation_prefix")
            else None
        )
        req = VMCreateRequest(
            hostname=hostname,
            template_id=params["template_id"],
            username=params.get("username") or "student",
            password=params.get("password") or secrets.token_urlsafe(18),
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
            session=session,
            vm_data=req,
            user_id=user_id,
            batch_job_id=batch_job_id,
            ip_reservation_key=reservation_key,
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
