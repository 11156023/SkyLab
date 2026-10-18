"""批量建立資源服務 — 包含逐一排隊邏輯"""

import asyncio
import logging
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_
from sqlmodel import Session, col, select

from app.core.db import engine
from app.core.i18n import t
from app.exceptions import BadRequestError
from app.infrastructure.queue import enqueue_task_sync
from app.models import (
    ClassCapacityReservation,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
    TeachingClassStudent,
    User,
    VMTemplate,
    VMTemplateStatus,
)
from app.models.batch_provision import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    BatchProvisionTask,
    BatchProvisionTaskStatus,
)
from app.repositories import batch_provision as bp_repo
from app.repositories import resource as resource_repo
from app.repositories import vm_template as vm_template_repo
from app.schemas import LXCCreateRequest, VMCreateRequest
from app.schemas.batch_provision import (
    BatchProvisionJobPublic,
    BatchProvisionJobSpec,
    BatchProvisionTaskPublic,
)
from app.services.network import ip_management_service
from app.services.proxmox import provisioning_service, proxmox_service
from app.services.resource import quota_service
from app.services.teaching.student_machine_mapping import upsert_student_machine_mapping
from app.services.template import clone_service, password_policy
from app.utils.login_password import generate_login_password
from app.utils.timeutil import normalize_datetime

logger = logging.getLogger(__name__)

TASK_RUN_BATCH_JOB = "batch_provision.run"

# running 超過這個時數且毫無進度的 job 視為 worker 已死（例如被 OOM kill）
STALE_BATCH_JOB_HOURS = 2.0

# worker 等 PVE clone 時多久記一次心跳（刷新 running task 的 started_at）
BATCH_HEARTBEAT_SECONDS = 60

# 已核准超過這麼久仍沒有 worker 接手（入列失敗、Redis 掉資料）就由排程重新入列
APPROVED_JOB_REQUEUE_MINUTES = 5


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
        template_params=params,
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
    """Approve a pending batch job and enqueue it for the arq worker.

    The status transition is atomic — if two admins click "approve" at the
    same time, only the one whose UPDATE wins races enqueues the job; the
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

    _enqueue_after_approval(session=session, job_id=job_id, reviewer_id=reviewer_id)

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
            _enqueue_after_approval(
                session=session, job_id=job.id, reviewer_id=reviewer_id
            )
    logger.info(
        "Teaching class batch jobs %s reviewed as %s by %s",
        ",".join(str(job.id) for job in jobs),
        decision.value,
        reviewer_id,
    )
    return jobs


def to_public(session: Session, job: BatchProvisionJob) -> BatchProvisionJobPublic:
    """把 job 連同 task、發起人／審核人與班級名稱組成審核頁用的回應。"""
    tasks = bp_repo.get_job_tasks(session=session, job_id=job.id)

    # Collect every user we want to display: task owners + initiator + reviewer.
    user_ids: set[uuid.UUID] = {task.user_id for task in tasks}
    if job.initiated_by:
        user_ids.add(job.initiated_by)
    if job.reviewer_id:
        user_ids.add(job.reviewer_id)

    users: dict[uuid.UUID, User] = {}
    if user_ids:
        rows = session.exec(select(User).where(col(User.id).in_(list(user_ids)))).all()
        users = {user.id: user for user in rows}

    teaching_class = (
        session.get(TeachingClass, job.teaching_class_id)
        if job.teaching_class_id
        else None
    )

    # Parse the JSON-encoded spec snapshot.
    params = bp_repo.job_params(job)
    spec = BatchProvisionJobSpec(
        cores=params.get("cores"),
        memory=params.get("memory"),
        disk_size=params.get("disk_size"),
        rootfs_size=params.get("rootfs_size"),
        ostemplate=params.get("ostemplate"),
        template_id=params.get("template_id"),
        vm_template_id=params.get("vm_template_id"),
        username=params.get("username"),
        environment_type=params.get("environment_type"),
        os_info=params.get("os_info"),
        expiry_date=params.get("expiry_date"),
    )

    task_publics = [
        BatchProvisionTaskPublic(
            id=task.id,
            user_id=task.user_id,
            user_email=users[task.user_id].email if task.user_id in users else None,
            user_name=users[task.user_id].full_name if task.user_id in users else None,
            member_index=task.member_index,
            vmid=task.vmid,
            status=task.status,
            error=task.error,
            started_at=task.started_at,
            finished_at=task.finished_at,
        )
        for task in tasks
    ]

    initiator = users.get(job.initiated_by) if job.initiated_by else None
    reviewer = users.get(job.reviewer_id) if job.reviewer_id else None

    return BatchProvisionJobPublic(
        id=job.id,
        teaching_class_id=job.teaching_class_id,
        teaching_class_name=teaching_class.name if teaching_class else None,
        resource_type=job.resource_type,
        hostname_prefix=job.hostname_prefix,
        status=job.status,
        total=job.total,
        done=job.done,
        failed_count=job.failed_count,
        created_at=job.created_at,
        finished_at=job.finished_at,
        initiated_by=job.initiated_by,
        initiated_by_email=initiator.email if initiator else None,
        initiated_by_name=initiator.full_name if initiator else None,
        reviewer_id=job.reviewer_id,
        reviewer_email=reviewer.email if reviewer else None,
        reviewed_at=job.reviewed_at,
        review_comment=job.review_comment,
        recurrence_rule=job.recurrence_rule,
        recurrence_duration_minutes=job.recurrence_duration_minutes,
        schedule_timezone=job.schedule_timezone,
        next_window_start=job.next_window_start,
        next_window_end=job.next_window_end,
        spec=spec,
        tasks=task_publics,
    )


# ─── 背景排隊執行 ──────────────────────────────────────────────────────────────


def batch_run_task_id(job_id: uuid.UUID) -> str:
    """一個 job 的 arq job id；核准入列與排程補入列共用，排隊中不會重複入列。"""
    return f"batch_provision:{job_id}"


def _enqueue_job_run(
    *, session: Session, job_id: uuid.UUID, reviewer_id: uuid.UUID
) -> Any:
    """把已核准的 job 交給 arq worker 執行；同一 job 已在排隊／執行中時回 None。

    之前用 daemon thread 跑在 API 行程裡：API 重啟整批就消失，只能等
    ``reap_stale_batch_jobs`` 兩小時後標成 failed。arq 的 job 存在 Redis，
    worker 重啟後會續跑；``_run_queue`` 起手的 approved→running 條件式轉換
    則擋掉重複執行。
    """
    return enqueue_task_sync(
        session=session,
        task_type=TASK_RUN_BATCH_JOB,
        user_id=reviewer_id,
        payload={"job_id": str(job_id)},
        job_id=batch_run_task_id(job_id),
    )


def _enqueue_after_approval(
    *, session: Session, job_id: uuid.UUID, reviewer_id: uuid.UUID
) -> None:
    """核准後入列；失敗不讓審核失敗。

    走到這裡 job 已經以 approved 寫進 DB，入列失敗時回錯誤反而讓審核者以為
    沒核准成功（再按一次只會得到「已不是待審」）。排程的
    ``requeue_stalled_approved_jobs`` 會在幾分鐘內把它重新入列，所以這裡只記
    ERROR，審核照常回成功。
    """
    try:
        _enqueue_job_run(session=session, job_id=job_id, reviewer_id=reviewer_id)
    except Exception:
        session.rollback()
        logger.exception(
            "Batch provision job %s approved but enqueue failed; the scheduler "
            "will re-enqueue it within %s minutes",
            job_id,
            APPROVED_JOB_REQUEUE_MINUTES,
        )


def requeue_stalled_approved_jobs(
    *, stalled_minutes: float = APPROVED_JOB_REQUEUE_MINUTES
) -> int:
    """排程安全網：已核准、超過 ``stalled_minutes`` 仍沒有 worker 接手的 job 重新入列。

    核准時入列失敗（Redis 斷線、逾時）或排隊中的 arq job 遺失，job 會永遠停在
    「已核准」。arq job id 固定（``batch_run_task_id``），還在排隊／執行中的不會
    重複入列；就算真的多跑一次，``_run_queue`` 起手的 approved→running 條件式
    轉換也只會讓其中一個執行。回傳本輪重新入列的 job 數。
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=stalled_minutes)
    requeued = 0
    try:
        with Session(engine) as session:
            jobs = list(
                session.exec(
                    select(BatchProvisionJob).where(
                        BatchProvisionJob.status == BatchProvisionJobStatus.approved,
                        or_(
                            col(BatchProvisionJob.reviewed_at) <= cutoff,
                            and_(
                                col(BatchProvisionJob.reviewed_at).is_(None),
                                col(BatchProvisionJob.created_at) <= cutoff,
                            ),
                        ),
                    )
                ).all()
            )
            for job in jobs:
                user_id = job.reviewer_id or job.initiated_by
                if user_id is None:
                    teaching_class = session.get(TeachingClass, job.teaching_class_id)
                    user_id = teaching_class.owner_id if teaching_class else None
                if user_id is None:
                    logger.error(
                        "Approved batch job %s has no reviewer, initiator or class "
                        "owner to enqueue it as; it cannot be started",
                        job.id,
                    )
                    continue
                try:
                    record = _enqueue_job_run(
                        session=session, job_id=job.id, reviewer_id=user_id
                    )
                except Exception:
                    session.rollback()
                    logger.exception(
                        "Failed to re-enqueue approved batch provision job %s", job.id
                    )
                    continue
                if record is not None:
                    requeued += 1
                    logger.warning(
                        "Approved batch provision job %s had no worker; re-enqueued",
                        job.id,
                    )
    except Exception:
        logger.exception("requeue_stalled_approved_jobs failed")
    return requeued


def record_heartbeat(job_id: uuid.UUID) -> int:
    """刷新 job 內 running task 的心跳；回傳更新的 task 數。"""
    with Session(engine) as session:
        return bp_repo.touch_running_task_heartbeat(session=session, job_id=job_id)


async def heartbeat_loop(
    job_id: uuid.UUID, *, interval_seconds: float = BATCH_HEARTBEAT_SECONDS
) -> None:
    """worker 端心跳：每 ``interval_seconds`` 秒刷新一次，直到被取消。

    跟著 arq job 的 coroutine 走：job 超過 6 小時上限被 arq 取消時心跳一起
    停，卡住的 clone 之後仍會被 ``reap_stale_batch_jobs`` 回收。
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await asyncio.to_thread(record_heartbeat, job_id)
        except Exception:
            logger.exception("Batch provision heartbeat failed for job %s", job_id)


def run_batch_job_task(
    task_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """worker 端 handler：解包 payload 後跑 ``_run_queue``。"""
    job_id = uuid.UUID(str(payload["job_id"]))
    logger.info("Batch provision task %s started for job %s", task_id, job_id)
    _run_queue(job_id)
    return {"job_id": str(job_id)}


def _run_queue(job_id: uuid.UUID) -> None:
    """逐一建立每個成員的資源（在 arq worker 內執行）。"""
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
        job = bp_repo.lock_job(session=session, job_id=job_id)
        if job is None:
            return
        if job.status == BatchProvisionJobStatus.cancelled:
            return
        teaching_class_id = job.teaching_class_id
        if job.status == BatchProvisionJobStatus.running:
            bp_repo.recount_job(session=session, job_id=job_id)
            # 部分失敗仍標 completed：週期性課程的自動開機與清單只認 completed，
            # 一台失敗就把整個工作標 failed 會讓全班都不再自動開機。失敗台數由
            # failed_count 呈現，前端據此顯示重試入口；全部失敗才標 failed。
            final = (
                BatchProvisionJobStatus.failed
                if job.failed_count > 0 and job.done == 0
                else BatchProvisionJobStatus.completed
            )
            job.status = final
            job.finished_at = datetime.now(UTC)
            session.add(job)
            session.commit()
            log = logger.warning if job.failed_count > 0 else logger.info
            log(
                "Batch provision job %s finished as %s: done=%d failed=%d total=%d",
                job_id,
                final.value,
                job.done,
                job.failed_count,
                job.total,
            )
        else:
            # 已被回收（reap）結案：終態以回收結果為準，晚到的成功已在
            # _record_task_success 補記
            closed_as = job.status.value
            session.rollback()
            logger.warning(
                "Batch provision job %s was closed as %s while the worker was "
                "still running; keeping that result",
                job_id,
                closed_as,
            )

    if teaching_class_id is not None:
        _refresh_teaching_class_status(teaching_class_id)


def _refresh_teaching_class_status(teaching_class_id: uuid.UUID) -> None:
    """Advance the owning class once a node job reaches a terminal state.

    Without this the class list keeps showing "建立中" until a teacher happens
    to open the class workspace, which is the only other caller that recomputes
    the status.
    """
    from app.services.teaching import class_status_service

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
        params = bp_repo.job_params(job)
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
            bp_repo.lock_job(session=session, job_id=job_id)
            bp_repo.mark_task_failed_if_open(
                session=session,
                task_id=task_id,
                error="Teaching class is archived or provisioning was cancelled",
            )
            bp_repo.recount_job(session=session, job_id=job_id)
            session.commit()
        return

    with Session(engine) as session:
        if not bp_repo.claim_pending_task(
            session=session, job_id=job_id, task_id=task_id
        ):
            # job 已被回收（reap）或結案、task 已標 failed：不要再替它建機器
            logger.warning(
                "Batch task %s of job %s is no longer pending; skipping",
                task_id,
                job_id,
            )
            return

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
                # 機器建出來了但沒有 resources 列可以掛班級：留著它只會變成
                # 沒人管得到的孤兒，重試還會再開一台。與「班級已封存」走同
                # 一條清理路徑（刪機、刪 DB 列、釋放 IP）後才讓 task 失敗。
                _discard_provisioned_machine(
                    vmid=vmid,
                    initiated_by_id=initiated_by_id,
                    reason=f"resource {vmid} has no database record",
                )
                raise RuntimeError(
                    f"Provisioned class resource {vmid} has no database record"
                )
            teaching_class = session.get(TeachingClass, teaching_class_id)
            if (
                teaching_class is None
                or teaching_class.status == TeachingClassStatus.archived
            ):
                _discard_provisioned_machine(
                    vmid=vmid,
                    initiated_by_id=initiated_by_id,
                    reason="teaching class archived during provisioning",
                )
                raise RuntimeError(
                    "Teaching class was archived while resource was provisioning"
                )
            if _superseded_by_retry(session=session, task_id=task_id):
                # 等 clone 的期間被回收、老師也已按重試：新的 job 會替這位學生
                # 再建一台，這台留著就是重複的機器
                _discard_provisioned_machine(
                    vmid=vmid,
                    initiated_by_id=initiated_by_id,
                    reason="task was reaped and superseded by a class retry",
                )
                raise RuntimeError(
                    "Batch task was superseded by a class retry while provisioning"
                )

        # Snapshot failure must not turn a successfully created resource into
        # a failed task, otherwise retrying can create a duplicate machine.
        try:
            from app.services.resource import reset_service

            reset_service.ensure_init_snapshot(vmid)
        except Exception:
            logger.warning("Initial snapshot failed for class vmid=%s", vmid)

        try:
            late = _record_task_success(
                job_id=job_id, task_id=task_id, user_id=user_id, vmid=vmid
            )
        except Exception:
            # 授權紀錄寫不進去 = 學生拿不到這台機器；留著它，重試時又會再
            # 開一台。同樣先清掉機器再讓 task 失敗。
            _discard_provisioned_machine(
                vmid=vmid,
                initiated_by_id=initiated_by_id,
                reason=f"failed to record class machine grant for task {task_id}",
            )
            raise

        if late:
            logger.warning(
                "Batch task %s finished after it was reaped; recorded the late "
                "success: vmid=%s user=%s",
                task_id,
                vmid,
                user_id,
            )
            if teaching_class_id is not None:
                _refresh_teaching_class_status(teaching_class_id)
        else:
            logger.info("Batch task %s done: vmid=%d user=%s", task_id, vmid, user_id)

    except Exception:
        logger.exception("Batch task failed task=%s user=%s", task_id, user_id)
        error_msg = "Provisioning failed. Retry or contact an administrator."
        with Session(engine) as session:
            bp_repo.lock_job(session=session, job_id=job_id)
            # 已被回收標 failed 的 task 不再覆寫、不重複計數
            bp_repo.mark_task_failed_if_open(
                session=session, task_id=task_id, error=error_msg
            )
            _sync_class_machine_mapping(
                session=session,
                job_id=job_id,
                task_id=task_id,
                user_id=user_id,
                commit=False,
            )
            bp_repo.recount_job(session=session, job_id=job_id)
            session.commit()


def _record_task_success(
    *, job_id: uuid.UUID, task_id: uuid.UUID, user_id: uuid.UUID, vmid: int
) -> bool:
    """把建好的機器記到 task 上（同一個交易內：task、學生對應、job 計數）。

    回傳 True 代表這是晚到的成功：worker 等 clone 的期間 task 已被回收標成
    failed。機器最後還是建好了，照樣記成 completed；計數依 task 現況重算，
    不會重複計算，job 若已被標成 failed 也會改回 completed。
    """
    with Session(engine) as session:
        bp_repo.lock_job(session=session, job_id=job_id)
        previous = bp_repo.mark_task_completed(
            session=session, task_id=task_id, vmid=vmid
        )
        _sync_class_machine_mapping(
            session=session,
            job_id=job_id,
            task_id=task_id,
            user_id=user_id,
            commit=False,
        )
        bp_repo.recount_job(session=session, job_id=job_id)
        session.commit()
    return previous is not None and previous != BatchProvisionTaskStatus.running


def _superseded_by_retry(*, session: Session, task_id: uuid.UUID) -> bool:
    """被回收的 task 之後，班級重試是否已替同一位學生送出新的建機 task。

    只看「被標 failed 之後才建立」的同班 job：同一位學生在其他機器節點、
    當初一起送出的 job 不算。新 task 又失敗了也不算，這台晚到的機器照常記入。
    """
    task = session.get(BatchProvisionTask, task_id)
    if task is None or task.status != BatchProvisionTaskStatus.failed:
        return False
    failed_at = normalize_datetime(task.finished_at)
    job = session.get(BatchProvisionJob, task.job_id)
    if failed_at is None or job is None:
        return False
    newer = session.exec(
        select(BatchProvisionTask.id)
        .join(
            BatchProvisionJob,
            col(BatchProvisionJob.id) == col(BatchProvisionTask.job_id),
        )
        .where(
            BatchProvisionJob.teaching_class_id == job.teaching_class_id,
            BatchProvisionJob.id != job.id,
            col(BatchProvisionJob.created_at) >= failed_at,
            col(BatchProvisionJob.status).not_in(
                [
                    BatchProvisionJobStatus.rejected,
                    BatchProvisionJobStatus.cancelled,
                ]
            ),
            BatchProvisionTask.user_id == task.user_id,
            BatchProvisionTask.status != BatchProvisionTaskStatus.failed,
        )
    ).first()
    return newer is not None


def _discard_provisioned_machine(
    *, vmid: int, initiated_by_id: uuid.UUID, reason: str
) -> None:
    """把已經建出來、但接不上班級的機器整台收掉（best-effort）。

    走 ``resource_service.delete``：刪 Proxmox 機器、刪 resources 列、釋放
    IP 與相關規則。清不掉也不再往外丟例外 —— 呼叫端本來就要讓 task 失敗，
    再丟一次只會蓋掉原本的失敗原因。
    """
    try:
        with Session(engine) as session:
            resource_info = proxmox_service.find_resource(vmid)
            from app.services.resource import resource_service

            resource_service.delete(
                session=session,
                vmid=vmid,
                resource_info=resource_info,
                user_id=initiated_by_id,
                purge=True,
                force=True,
            )
        logger.warning(
            "Discarded orphaned batch machine vmid=%s (%s)", vmid, reason
        )
    except Exception:
        logger.exception(
            "Failed to discard orphaned batch machine vmid=%s (%s); "
            "manual cleanup required",
            vmid,
            reason,
        )


def _last_progress_at(
    job: BatchProvisionJob, tasks: list[BatchProvisionTask]
) -> datetime | None:
    """這個 job 最後一次有動靜的時間（任一 task 開始/結束，否則審核/建立時間）。

    running task 的 ``started_at`` 會被 worker 心跳（``heartbeat_loop``）持續
    刷新，單台 clone 等再久，只要 worker 還在等就不會被當成卡死。
    """
    stamps = [
        stamp
        for task in tasks
        for stamp in (
            normalize_datetime(task.finished_at),
            normalize_datetime(task.started_at),
        )
        if stamp is not None
    ]
    stamps.extend(
        stamp
        for stamp in (
            normalize_datetime(job.reviewed_at),
            normalize_datetime(job.created_at),
        )
        if stamp is not None
    )
    return max(stamps) if stamps else None


def reap_stale_batch_jobs(
    *, max_running_hours: float = STALE_BATCH_JOB_HOURS
) -> int:
    """回收卡死的 running 批次工作（供排程器每 tick 呼叫）。回傳回收的 job 數。

    批量建立在 arq worker 裡執行：worker 在 job 跑到一半時被砍掉、OOM 或
    崩潰，job 就會永遠停在 running —— 班級狀態卡在「建立中」，重試入口
    也不會出現。狀態為 ``running``、且超過 ``max_running_hours`` 沒有任何
    task 有進度（含 worker 心跳）的 job，連同它尚未結束的 task 一起標成 failed。

    回收後 worker 若其實還活著（例如等 clone 超過 6 小時上限、心跳已停）：
    被標 failed 的 task 不會再建（``claim_pending_task``），正在等的那台最後
    建好時記成晚到的成功（``_record_task_success``），計數不會重複。
    """
    reaped = 0
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=max_running_hours)
    stale_class_ids: list[uuid.UUID] = []
    try:
        with Session(engine) as session:
            jobs = list(
                session.exec(
                    select(BatchProvisionJob).where(
                        BatchProvisionJob.status == BatchProvisionJobStatus.running
                    )
                ).all()
            )
            for job in jobs:
                tasks = bp_repo.get_job_tasks(session=session, job_id=job.id)
                last_progress = _last_progress_at(job, tasks)
                if last_progress is not None and last_progress > cutoff:
                    continue
                for task in tasks:
                    if task.status in (
                        BatchProvisionTaskStatus.pending,
                        BatchProvisionTaskStatus.running,
                    ):
                        task.status = BatchProvisionTaskStatus.failed
                        task.error = (
                            "Batch worker stopped responding; task was reaped"
                        )
                        task.finished_at = now
                        session.add(task)
                # 計數以 task 現況重算，避免與被回收的 task 對不上
                job.done = sum(
                    task.status == BatchProvisionTaskStatus.completed
                    for task in tasks
                )
                job.failed_count = sum(
                    task.status == BatchProvisionTaskStatus.failed for task in tasks
                )
                # 與 worker 收尾同一條規則：全部失敗才標 failed。已建好的機器仍屬於
                # 班級，排程器的週期開關機只認 completed，標成 failed 會讓這些學生
                # 整學期都不再自動開機；失敗台數仍由 failed_count 帶出重試入口。
                job.status = (
                    BatchProvisionJobStatus.completed
                    if job.done > 0
                    else BatchProvisionJobStatus.failed
                )
                job.finished_at = now
                session.add(job)
                session.commit()
                reaped += 1
                stale_class_ids.append(job.teaching_class_id)
                logger.warning(
                    "Reaped stale batch provision job %s: done=%d failed=%d total=%d",
                    job.id,
                    job.done,
                    job.failed_count,
                    job.total,
                )
    except Exception:
        logger.exception("reap_stale_batch_jobs failed")
        return reaped

    for class_id in stale_class_ids:
        if class_id is not None:
            _refresh_teaching_class_status(class_id)
    return reaped


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
    from app.services.teaching import class_capacity_service

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


def _template_clone_placement(
    session: Session,
    *,
    template: VMTemplate,
    target_node: str,
    disk_gb: int,
) -> dict[str, str | None]:
    """班級範本機器要建在哪台、磁碟放哪個儲存區（與容量預檢同一套規則）。

    - 目標節點不是範本節點：磁碟必須放共享儲存（PVE 跨節點 clone 的限制），
      挑不到就退回範本節點建。
    - 範本節點上同樣挑最適合的儲存區：以前一律沿用母機的儲存區，容量預檢
      試放的卻是節點上最適合的那個，兩邊對不上時會預檢通過、建機放不下。
      沒有受管儲存紀錄時回 None，照舊沿用母機的儲存區。
    """
    from app.services.vm import placement_service

    resource_type = "lxc" if str(template.resource_type).lower() == "lxc" else "vm"
    disk_gb = max(int(disk_gb or 0), int(template.default_disk or 0), 1)
    target_storage = None
    if target_node != template.node:
        target_storage = placement_service.select_best_storage_name(
            session=session,
            node_name=target_node,
            resource_type=resource_type,
            disk_gb=disk_gb,
            shared_only=True,
        )
    fallback_storage = placement_service.select_best_storage_name(
        session=session,
        node_name=template.node,
        resource_type=resource_type,
        disk_gb=disk_gb,
    )
    return {
        "target_node": target_node,
        "target_storage": target_storage,
        "fallback_storage": fallback_storage,
    }


def _sync_class_machine_mapping(
    *,
    session: Session,
    job_id: uuid.UUID,
    task_id: uuid.UUID,
    user_id: uuid.UUID,
    commit: bool = True,
) -> None:
    """Persist the class-to-student machine grant as soon as provisioning ends.

    The task must already carry the outcome (vmid / status / error); the
    mapping only points at it. ``commit=False`` leaves the transaction to the
    caller (the worker records task, mapping and job counts together).
    """
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
    upsert_student_machine_mapping(
        session,
        enrollment_id=enrollment.id,
        node_id=node.id,
        task_id=task_id,
    )
    if commit:
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

    reservation_key = (
        f"{params['ip_reservation_prefix']}:{user_id}"
        if params.get("ip_reservation_prefix")
        else None
    )

    if params.get("vm_template_id"):
        # 少了這個 key，clone worker 會當成「設得了」而一律發隨機密碼，
        # 密碼平台設不了的範本也被當成有套用
        source_template = password_policy.find_template(
            session, template_id=params["vm_template_id"]
        )
        payload = {
            "allow_password_reset": not password_policy.keeps_template_credentials(
                source_template
            ),
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
            "ip_reservation_key": reservation_key,
        }
        if target_node and source_template is not None:
            payload.update(
                _template_clone_placement(
                    session,
                    template=source_template,
                    target_node=target_node,
                    disk_gb=int(
                        params.get("disk_size") or params.get("rootfs_size") or 0
                    ),
                )
            )
        # 同步執行（本身已在 arq worker 的批次 job 裡）；task_id 無對應 TaskRecord，
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
            password=params.get("password") or generate_login_password(),
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
        req = VMCreateRequest(
            hostname=hostname,
            template_id=params["template_id"],
            username=params.get("username") or "student",
            password=params.get("password") or generate_login_password(),
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
            target_node=target_node,
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
