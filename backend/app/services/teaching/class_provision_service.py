"""班級機器佈建：節點 job 入列、失敗重試與殘留資源復原。

原本這些流程寫在 ``api/routes/teaching_classes.py`` 裡，和路由層的序列化
綁在一起，排程器或其他 service 無法重用。這裡只收「佈建」相關的邏輯；
班級狀態的重算仍在 ``class_status_service``。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlmodel import Session, select

from app.core.i18n import t
from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    BatchProvisionTask,
    BatchProvisionTaskStatus,
    ClassCapacityReservation,
    Resource,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
    TeachingClassStudent,
    TeachingClassStudentMachine,
)
from app.models.base import get_datetime_utc
from app.repositories import resource as resource_repo
from app.services.proxmox import proxmox_service
from app.services.resource import resource_service
from app.services.vm import batch_provision_service

logger = logging.getLogger(__name__)

DAY_CODE = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]

# running 超過這段時間沒完成的 task，重試時視為 worker 已死而非仍在進行
STALE_TASK_MINUTES = 30


def first_session_date(item: TeachingClass) -> date:
    """課程期間內第一個落在「每週上課日」的日期。

    ``start_date`` 只是課程期間的起點，它的星期未必等於 ``weekday``（例如學期
    從週一開始、但每週三上課），所以課次與排程都必須從這裡推算。
    """
    return item.start_date + timedelta(
        days=(item.weekday - item.start_date.weekday()) % 7
    )


def recurrence_rule(item: TeachingClass) -> tuple[str, int]:
    """回傳 (RRULE, 每次開機時長分鐘)：開機提前 boot_lead，關機延後 grace。"""
    first_session = first_session_date(item)
    start = datetime.combine(first_session, item.start_time) - timedelta(
        minutes=item.boot_lead_minutes
    )
    duration = (
        int(
            (
                datetime.combine(first_session, item.end_time)
                - datetime.combine(first_session, item.start_time)
            ).total_seconds()
            / 60
        )
        + item.boot_lead_minutes
        + int(getattr(item, "shutdown_grace_minutes", 0) or 0)
    )
    return (
        f"FREQ=WEEKLY;BYDAY={DAY_CODE[start.weekday()]};BYHOUR={start.hour};BYMINUTE={start.minute}",
        duration,
    )


def node_source_params(node: TeachingClassMachineNode) -> dict:
    if node.source_type == "template" and node.source_template_id:
        return {"vm_template_id": str(node.source_template_id)}
    if node.resource_type.lower() == "lxc":
        return {
            "ostemplate": node.custom_image_ref,
            "storage": "local-lvm",
            "unprivileged": node.custom_unprivileged,
        }
    return {
        "template_id": int(node.custom_image_ref or "0"),
        "storage": "local-lvm",
        "username": node.custom_username or "student",
    }


def submit_node_job(
    session: Session,
    *,
    item: TeachingClass,
    node: TeachingClassMachineNode,
    member_user_ids: list[uuid.UUID],
    retry: bool = False,
) -> uuid.UUID:
    """替一個機器節點建立批次佈建 job，回傳 job id。"""
    rule, duration = recurrence_rule(item)
    retry_suffix = f"-r{uuid.uuid4().hex[:8]}" if retry else ""
    return batch_provision_service.submit_batch_job_for_users(
        session=session,
        member_user_ids=member_user_ids,
        teaching_class_id=item.id,
        initiated_by_id=item.owner_id,
        resource_type="lxc" if node.resource_type.lower() == "lxc" else "qemu",
        hostname_prefix=(
            f"{item.code.lower().replace('_', '-')[:35]}-"
            f"{node.sort_order + 1}{retry_suffix}"
        ),
        params={
            **node_source_params(node),
            "cores": node.cpu,
            "memory": node.memory_mb,
            "disk_size": node.disk_gb,
            "rootfs_size": node.disk_gb,
            "environment_type": f"{item.name}-{node.role}",
            # 老師取的機器名：和快速練習走同一個欄位，學生在清單上才會看到
            # 「n8n」而不是 cls-973465c8-1-1 這種產生出來的主機名
            "os_info": node.name,
            "expiry_date": item.end_date.isoformat(),
            "ip_reservation_prefix": f"{item.id}:{node.node_key}",
        },
        recurrence_rule=rule,
        recurrence_duration_minutes=duration,
        schedule_timezone=item.timezone,
        capacity_reserved=True,
    )


def recover_existing_task_resource(
    *,
    session: Session,
    item: TeachingClass,
    node: TeachingClassMachineNode,
    task: BatchProvisionTask,
) -> bool:
    """Recover a VM created before a worker crash instead of cloning twice."""
    resource = session.exec(
        select(Resource).where(
            Resource.batch_job_id == task.job_id,
            Resource.user_id == task.user_id,
        )
    ).first()
    if resource is None:
        return False
    try:
        proxmox_service.find_resource(resource.vmid)
    except NotFoundError:
        resource_service.delete_orphan_db_record(
            session=session,
            vmid=resource.vmid,
            user_id=item.owner_id,
        )
        session.commit()
        return False
    except Exception:
        logger.exception(
            "Failed to verify existing class resource class_id=%s vmid=%s",
            item.id,
            resource.vmid,
        )
        raise BadRequestError(
            t("teachingClasses.cannotVerifyResourceRetryLater")
        ) from None

    resource_repo.assign_to_teaching_class(
        session=session,
        vmid=resource.vmid,
        teaching_class_id=item.id,
        commit=False,
    )
    enrollment = session.exec(
        select(TeachingClassStudent).where(
            TeachingClassStudent.class_id == item.id,
            TeachingClassStudent.user_id == task.user_id,
        )
    ).first()
    if enrollment is None:
        raise BadRequestError(t("teachingClasses.provisionedUserNoLongerInClass"))
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
    mapping.batch_task_id = task.id
    mapping.vmid = resource.vmid
    mapping.status = "completed"
    mapping.error = None
    task.status = BatchProvisionTaskStatus.completed
    task.vmid = resource.vmid
    task.resource_vmid = resource.vmid
    task.error = None
    task.finished_at = get_datetime_utc()
    session.add(mapping)
    session.add(task)
    session.flush()
    return True


def _retry_node(
    session: Session,
    *,
    item: TeachingClass,
    node: TeachingClassMachineNode,
    stale_before: datetime,
) -> tuple[bool, int]:
    """處理一個節點的失敗／殘留 task；回傳 (是否重新入列, 復原的 task 數)。"""
    job = session.get(BatchProvisionJob, node.batch_job_id) if node.batch_job_id else None
    if not job:
        return False, 0
    tasks = list(
        session.exec(
            select(BatchProvisionTask).where(BatchProvisionTask.job_id == job.id)
        ).all()
    )
    recovered = 0
    retry_user_ids: list[uuid.UUID] = []
    terminal_job = job.status in {
        BatchProvisionJobStatus.failed,
        BatchProvisionJobStatus.rejected,
        BatchProvisionJobStatus.cancelled,
    }
    for task in tasks:
        started_at = task.started_at
        if started_at and started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        stale = (
            task.status == BatchProvisionTaskStatus.running
            and started_at is not None
            and started_at <= stale_before
        )
        retryable = (
            task.status == BatchProvisionTaskStatus.failed
            or stale
            or (terminal_job and task.status != BatchProvisionTaskStatus.completed)
        )
        if not retryable:
            continue
        if recover_existing_task_resource(
            session=session, item=item, node=node, task=task
        ):
            recovered += 1
            continue
        if stale or (terminal_job and task.status != BatchProvisionTaskStatus.failed):
            task.status = BatchProvisionTaskStatus.failed
            task.error = (
                "Stale provisioning task superseded by class retry"
                if stale
                else "Incomplete task superseded by class retry"
            )
            task.finished_at = get_datetime_utc()
            session.add(task)
        retry_user_ids.append(task.user_id)
    job.done = sum(task.status == BatchProvisionTaskStatus.completed for task in tasks)
    job.failed_count = sum(
        task.status == BatchProvisionTaskStatus.failed for task in tasks
    )
    if job.done == job.total and job.failed_count == 0:
        job.status = BatchProvisionJobStatus.completed
        job.finished_at = get_datetime_utc()
    session.add(job)
    session.commit()
    if not retry_user_ids:
        return False, recovered
    if job.status == BatchProvisionJobStatus.running:
        job.status = BatchProvisionJobStatus.failed
        job.finished_at = get_datetime_utc()
        session.add(job)
        session.commit()
    node.batch_job_id = submit_node_job(
        session=session,
        item=item,
        node=node,
        member_user_ids=retry_user_ids,
        retry=True,
    )
    session.add(node)
    session.commit()
    return True, recovered


def retry_failed_class(session: Session, *, item: TeachingClass) -> None:
    """重跑班級裡失敗／殘留的佈建 task，並依結果推進班級狀態。

    - 有 task 重新入列 → 班級回到 ``pending_review`` 等審核
    - 只有復原、沒有重新入列且所有 job 都完成 → 套用拓樸並轉 ``active``
    - 什麼都沒做 → 依原狀態決定：``provisioning`` 視為沒有可重試的項目（400）
    """
    from app.services.course import course_service  # noqa: PLC0415 — 避免 import cycle
    from app.services.teaching import (  # noqa: PLC0415 — 避免 import cycle
        class_network_service,
    )

    if item.status not in {
        TeachingClassStatus.partial_failed,
        TeachingClassStatus.provisioning,
    }:
        raise BadRequestError(t("teachingClasses.onlyFailedCanRetry"))
    nodes = list(
        session.exec(
            select(TeachingClassMachineNode)
            .where(TeachingClassMachineNode.class_id == item.id)
            .order_by(TeachingClassMachineNode.sort_order)
        ).all()
    )
    submitted = 0
    recovered = 0
    stale_before = get_datetime_utc() - timedelta(minutes=STALE_TASK_MINUTES)
    for node in nodes:
        resubmitted, node_recovered = _retry_node(
            session, item=item, node=node, stale_before=stale_before
        )
        submitted += int(resubmitted)
        recovered += node_recovered

    current_jobs = [
        session.get(BatchProvisionJob, node.batch_job_id)
        for node in nodes
        if node.batch_job_id
    ]
    all_jobs_ready = (
        len(current_jobs) == len(nodes)
        and bool(nodes)
        and all(
            job is not None
            and job.status == BatchProvisionJobStatus.completed
            and job.done == job.total
            and job.failed_count == 0
            for job in current_jobs
        )
    )
    if submitted:
        item.status = TeachingClassStatus.pending_review
    elif item.status == TeachingClassStatus.provisioning and not recovered:
        raise BadRequestError(t("teachingClasses.noFailedOrStaleTasksToRetry"))
    elif not all_jobs_ready:
        item.status = TeachingClassStatus.provisioning
    else:
        topology_errors = class_network_service.apply_class_topology(
            session, class_id=item.id
        )
        if topology_errors:
            raise BadRequestError(
                t(
                    "teachingClasses.topologyRetryFailed",
                    details="；".join(topology_errors),
                )
            )
        item.status = TeachingClassStatus.active
        course_service.ensure_class_path(
            session,
            teaching_class=item,
            published=True,
        )
        reservation = session.exec(
            select(ClassCapacityReservation).where(
                ClassCapacityReservation.class_id == item.id
            )
        ).first()
        if reservation:
            reservation.status = "consumed"
            session.add(reservation)
    item.updated_at = get_datetime_utc()
    session.add(item)
    session.commit()


__all__ = [
    "DAY_CODE",
    "STALE_TASK_MINUTES",
    "first_session_date",
    "node_source_params",
    "recover_existing_task_resource",
    "recurrence_rule",
    "retry_failed_class",
    "submit_node_job",
]
