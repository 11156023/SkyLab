"""班級機器佈建：節點 job 入列、失敗重試與殘留資源復原。

原本這些流程寫在 ``api/routes/teaching_classes.py`` 裡，和路由層的序列化
綁在一起，排程器或其他 service 無法重用。這裡只收「佈建」相關的邏輯；
班級狀態的重算仍在 ``class_status_service``。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlmodel import Session, col, delete, select

from app.core.i18n import t
from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    BatchProvisionTask,
    BatchProvisionTaskStatus,
    CourseEnvironment,
    CourseEnvironmentNode,
    CourseEnvironmentVersion,
    CourseEnvironmentVersionStatus,
    Resource,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
    TeachingClassStudent,
    TeachingClassStudentMachine,
    TeachingClassWeek,
    VMTemplate,
    VMTemplateStatus,
)
from app.models.base import get_datetime_utc
from app.repositories import batch_provision as bp_repo
from app.repositories import resource as resource_repo
from app.services.proxmox import provisioning_service, proxmox_service
from app.services.resource import resource_service
from app.services.scheduling.recurrence import build_weekly_rule
from app.services.teaching import class_capacity_service
from app.services.teaching.student_machine_mapping import upsert_student_machine_mapping
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
        build_weekly_rule([DAY_CODE[start.weekday()]], start.hour, start.minute),
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
    upsert_student_machine_mapping(
        session,
        enrollment_id=enrollment.id,
        node_id=node.id,
        task_id=task.id,
        vmid=resource.vmid,
        status="completed",
        error=None,
    )
    task.status = BatchProvisionTaskStatus.completed
    task.vmid = resource.vmid
    task.error = None
    task.finished_at = get_datetime_utc()
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
    from app.services.teaching import class_status_service

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
        job
        for node in nodes
        if node.batch_job_id
        and (job := session.get(BatchProvisionJob, node.batch_job_id)) is not None
    ]
    if submitted:
        item.status = TeachingClassStatus.pending_review
    elif item.status == TeachingClassStatus.provisioning and not recovered:
        raise BadRequestError(t("teachingClasses.noFailedOrStaleTasksToRetry"))
    elif not class_status_service.jobs_all_ready(nodes, current_jobs):
        item.status = TeachingClassStatus.provisioning
    else:
        topology_errors = class_status_service.activate_class(
            session, item, current_jobs
        )
        if topology_errors:
            raise BadRequestError(
                t(
                    "teachingClasses.topologyRetryFailed",
                    details="；".join(topology_errors),
                )
            )
    item.updated_at = get_datetime_utc()
    session.add(item)
    session.commit()


def _class_nodes(
    session: Session, class_id: uuid.UUID
) -> list[TeachingClassMachineNode]:
    """班級的機器節點，依編輯器排序。"""
    return list(
        session.exec(
            select(TeachingClassMachineNode)
            .where(TeachingClassMachineNode.class_id == class_id)
            .order_by(col(TeachingClassMachineNode.sort_order))
        ).all()
    )


def _class_students(
    session: Session, class_id: uuid.UUID
) -> list[TeachingClassStudent]:
    return list(
        session.exec(
            select(TeachingClassStudent)
            .where(TeachingClassStudent.class_id == class_id)
            .order_by(col(TeachingClassStudent.joined_at))
        ).all()
    )


def revert_class_to_planning(
    session: Session,
    item: TeachingClass,
    nodes: list[TeachingClassMachineNode],
) -> None:
    """把班級退回可編輯的 planning：解除節點與 job 的關聯、放掉容量並解鎖。

    審核退回與「重設失敗班級」共用這段收尾；不 commit，由呼叫端決定。
    """
    for node in nodes:
        node.batch_job_id = None
        session.add(node)
    class_capacity_service.release(session, class_id=item.id)
    item.status = TeachingClassStatus.planning
    item.locked_at = None
    item.updated_at = get_datetime_utc()
    session.add(item)


def select_course(
    session: Session,
    *,
    item: TeachingClass,
    course_version_id: uuid.UUID,
    current_user: object,
) -> None:
    """班級改用某個已發佈的課程版本：依版本的機器節點整批重建班級節點。"""
    from app.core.authorizers import require_teaching_access

    if item.status != TeachingClassStatus.planning or item.locked_at is not None:
        raise BadRequestError(t("teachingClasses.courseLockedCannotChange"))
    version = session.get(CourseEnvironmentVersion, course_version_id)
    if version is None or version.status != CourseEnvironmentVersionStatus.published:
        raise BadRequestError(t("teachingClasses.onlyPublishedCourseVersion"))
    environment = session.get(CourseEnvironment, version.environment_id)
    if environment is None:
        raise NotFoundError(t("teachingClasses.courseEnvironmentNotFound"))
    if environment.usage_scope not in {"course", "both"}:
        raise BadRequestError(t("teachingClasses.environmentNotForFormalCourse"))
    require_teaching_access(current_user, environment.owner_id)
    source_nodes = list(
        session.exec(
            select(CourseEnvironmentNode)
            .where(CourseEnvironmentNode.version_id == version.id)
            .order_by(col(CourseEnvironmentNode.sort_order))
        ).all()
    )
    if not source_nodes:
        raise BadRequestError(t("teachingClasses.courseVersionNoMachines"))
    session.exec(
        delete(TeachingClassMachineNode).where(
            col(TeachingClassMachineNode.class_id) == item.id
        )
    )
    for node in source_nodes:
        session.add(
            TeachingClassMachineNode(
                class_id=item.id,
                node_key=node.node_key,
                source_type=node.source_type,
                source_template_id=node.source_template_id,
                custom_image_ref=node.custom_image_ref,
                custom_storage=None,
                custom_username=node.custom_username,
                custom_unprivileged=node.custom_unprivileged,
                name=node.name,
                role=node.role,
                resource_type=node.resource_type,
                cpu=node.cpu,
                memory_mb=node.memory_mb,
                # 克隆機不可能小於來源範本；把下限寫進班級節點，之後的容量
                # 預檢、IP/資源保留與開機才會用同一個數字。
                disk_gb=provisioning_service.clone_source_disk_gb(session, node),
                network=node.network,
                sort_order=node.sort_order,
            )
        )
    # 換課程版本後機器節點整批重建：新版本沒有的節點，週次指向它就會讓
    # 學生端看不到任何機器，改回「全部機器」
    new_keys = {node.node_key for node in source_nodes}
    for week in session.exec(
        select(TeachingClassWeek).where(
            TeachingClassWeek.class_id == item.id,
            col(TeachingClassWeek.target_node_key).is_not(None),
        )
    ).all():
        if week.target_node_key not in new_keys:
            week.target_node_key = None
            session.add(week)
    item.course_version_id = version.id
    item.updated_at = get_datetime_utc()
    session.add(item)
    session.commit()


def _require_node_templates_ready(
    session: Session, nodes: list[TeachingClassMachineNode]
) -> None:
    for node in nodes:
        if node.source_type != "template" or not node.source_template_id:
            continue
        template = session.get(VMTemplate, node.source_template_id)
        if template is None or template.status != VMTemplateStatus.ready:
            raise BadRequestError(t("course_env.template_not_ready", name=node.name))


def _recover_failed_provision_submit(session: Session, item: TeachingClass) -> None:
    """送出批次 job 中途失敗時，讓班級回到還能處理的狀態。

    鎖與容量保留在送 job 前就 commit 了；若就此放著，班級會卡在
    「planning + locked_at」：/provision 因鎖拒絕、reset/retry 要求
    partial_failed、審核又找不到 pending job，只剩封存一途。
    - 一個 job 都沒建成：放掉容量與 IP、解鎖，回到可以重送的 planning。
    - 已經建了部分 job：轉成 pending_review，交給既有的審核／退回流程
      （退回會放掉容量並解鎖）收尾。
    """
    session.rollback()
    if any(node.batch_job_id for node in _class_nodes(session, item.id)):
        item.status = TeachingClassStatus.pending_review
    else:
        class_capacity_service.release(session, class_id=item.id)
        item.locked_at = None
    item.updated_at = get_datetime_utc()
    session.add(item)
    session.commit()


def provision_class(
    session: Session,
    *,
    item: TeachingClass,
    nodes: list[TeachingClassMachineNode],
    students: list[TeachingClassStudent],
) -> None:
    """保留容量、鎖定班級並替每個機器節點送出批次 job，班級轉待審核。"""
    if not nodes or not students:
        raise BadRequestError(t("teachingClasses.studentsAndMachinesRequired"))
    if item.status != TeachingClassStatus.planning or item.locked_at is not None:
        raise BadRequestError(t("teachingClasses.classLockedOrSubmitted"))
    if item.course_version_id is None:
        raise BadRequestError(t("teachingClasses.selectPublishedCourseFirst"))
    # 送 job 時才會發現範本不是 ready（例如範本正在更新），那時容量與鎖
    # 都已經 commit 了；先在沒有任何副作用前擋下來。
    _require_node_templates_ready(session, nodes)
    class_capacity_service.reserve(
        session,
        class_id=item.id,
        course_version_id=item.course_version_id,
        nodes=nodes,
        students=students,
    )
    item.locked_at = get_datetime_utc()
    session.add(item)
    session.commit()
    member_user_ids = [row.user_id for row in students]
    try:
        for node in nodes:
            if node.batch_job_id:
                continue
            node.batch_job_id = submit_node_job(
                session=session,
                item=item,
                node=node,
                member_user_ids=member_user_ids,
            )
            session.add(node)
            session.commit()
    except Exception:
        _recover_failed_provision_submit(session, item)
        raise
    item.status = TeachingClassStatus.pending_review
    item.updated_at = get_datetime_utc()
    session.add(item)
    session.commit()


def reset_failed_class(session: Session, *, item: TeachingClass) -> None:
    """建機全數失敗（沒有任何一台成功）時，把班級退回 planning 重新編輯。"""
    if item.status != TeachingClassStatus.partial_failed:
        raise BadRequestError(t("teachingClasses.onlyFailedCanResetToEdit"))
    enrollment_ids = [row.id for row in _class_students(session, item.id)]
    machine_rows: list[TeachingClassStudentMachine] = []
    if enrollment_ids:
        machine_rows = list(
            session.exec(
                select(TeachingClassStudentMachine).where(
                    col(TeachingClassStudentMachine.class_student_id).in_(
                        enrollment_ids
                    )
                )
            ).all()
        )
    if any(row.vmid is not None for row in machine_rows):
        raise BadRequestError(t("teachingClasses.partialMachinesUseRetry"))
    for row in machine_rows:
        session.delete(row)
    revert_class_to_planning(session, item, _class_nodes(session, item.id))
    session.commit()


def reconcile_class(session: Session, *, item: TeachingClass) -> None:
    """把建機工作的結果寫回班級：學生機器對應、班級狀態、必要時套用拓樸。"""
    from app.services.teaching import class_status_service

    if item.status == TeachingClassStatus.archived:
        return
    enrollment_by_user = {row.user_id: row for row in _class_students(session, item.id)}
    for node in _class_nodes(session, item.id):
        job = (
            session.get(BatchProvisionJob, node.batch_job_id)
            if node.batch_job_id
            else None
        )
        if not job:
            continue
        tasks = session.exec(
            select(BatchProvisionTask).where(BatchProvisionTask.job_id == job.id)
        ).all()
        for task in tasks:
            enrollment = enrollment_by_user.get(task.user_id)
            if not enrollment:
                continue
            upsert_student_machine_mapping(
                session,
                enrollment_id=enrollment.id,
                node_id=node.id,
                task_id=task.id,
                vmid=task.vmid,
                status=(
                    task.status.value
                    if hasattr(task.status, "value")
                    else str(task.status)
                ),
                error=task.error,
            )
    class_status_service.recompute(session=session, class_id=item.id)
    session.commit()
    session.refresh(item)


def review_teaching_class(
    session: Session,
    *,
    item: TeachingClass,
    reviewer_id: uuid.UUID,
    decision: BatchProvisionJobStatus,
    review_comment: str | None = None,
) -> list[BatchProvisionJob]:
    """管理員一次審核班級所有待審 job，並依結果推進班級狀態。

    - 核准 → 班級轉 ``provisioning``
    - 退回且已有機器建出來 → ``partial_failed``（交給重試／回收）
    - 退回且一台都沒建 → 退回 ``planning`` 並放掉容量
    """
    nodes = _class_nodes(session, item.id)
    jobs = [
        job
        for job_id in (node.batch_job_id for node in nodes if node.batch_job_id)
        if (job := bp_repo.get_job(session=session, job_id=job_id)) is not None
    ]
    pending_ids = [
        job.id for job in jobs if job.status == BatchProvisionJobStatus.pending_review
    ]
    # 保留原本審核頁看到的訊息；review_batch_jobs 自己的同類檢查給其他呼叫端用
    if not pending_ids:
        raise BadRequestError(t("batchProvision.noPendingJobs"))
    reviewed = batch_provision_service.review_batch_jobs(
        session=session,
        job_ids=pending_ids,
        reviewer_id=reviewer_id,
        decision=decision,
        review_comment=review_comment,
    )
    if decision == BatchProvisionJobStatus.approved:
        item.status = TeachingClassStatus.provisioning
    else:
        all_tasks = [
            task
            for job in jobs
            for task in bp_repo.get_job_tasks(session=session, job_id=job.id)
        ]
        if any(task.vmid is not None for task in all_tasks):
            item.status = TeachingClassStatus.partial_failed
        else:
            revert_class_to_planning(session, item, nodes)
    item.updated_at = get_datetime_utc()
    session.add(item)
    session.commit()
    return reviewed


__all__ = [
    "DAY_CODE",
    "STALE_TASK_MINUTES",
    "first_session_date",
    "node_source_params",
    "provision_class",
    "reconcile_class",
    "recover_existing_task_resource",
    "recurrence_rule",
    "reset_failed_class",
    "retry_failed_class",
    "revert_class_to_planning",
    "review_teaching_class",
    "select_course",
    "submit_node_job",
]
