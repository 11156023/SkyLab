"""Recompute a teaching class status from its machine-node batch jobs.

The class status used to advance only when a teacher had the class workspace
open (``GET /provision-status`` polls every 3s). Nothing else wrote it back, so
a fully provisioned class could sit at ``provisioning`` in the class list
forever and the "需處理" filter never lit up. This module is the single place
that derives the status, and it is called both from that endpoint and from the
provisioning worker as each node job finishes.

It also derives the per-machine runtime usage shown on the class page
(``class_resource_usage_items``) from the PVE cluster resource list.
"""

from __future__ import annotations

import logging
import math
import uuid
from collections.abc import Sequence

from sqlmodel import Session, select

from app.models import (
    BatchProvisionJob,
    ClassCapacityReservation,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
)
from app.models.base import get_datetime_utc
from app.schemas.teaching_class import ClassResourceUsageItem
from app.services.course import course_service
from app.services.teaching import class_network_service

logger = logging.getLogger(__name__)

IN_FLIGHT_JOB_VALUES = {"approved", "pending", "running", "completed"}
FAILED_JOB_VALUES = {"failed", "rejected", "cancelled"}

# 已經成功套用過拓樸的班級，記著當時那組工作的樣子。
# apply_class_topology 會對每台機器查一次防火牆規則，一個班就是上百次
# PVE 呼叫；班級已經是「可上課」而且工作沒有任何變動時不必重跑。
# 這份快取只是省呼叫，程序重啟後清空，最多就是多套一次（結果相同）。
_APPLIED_TOPOLOGY: dict[uuid.UUID, tuple[tuple[str, int, int, int], ...]] = {}


def _job_value(job: BatchProvisionJob) -> str:
    return job.status.value if hasattr(job.status, "value") else str(job.status)


def _job_signature(
    jobs: list[BatchProvisionJob],
) -> tuple[tuple[str, int, int, int], ...]:
    """工作的完成度快照：重試、換節點、補學生都會讓它改變。"""
    return tuple(
        sorted(
            (str(job.id), int(job.done), int(job.total), int(job.failed_count))
            for job in jobs
        )
    )


def jobs_all_ready(
    nodes: Sequence[TeachingClassMachineNode], jobs: Sequence[BatchProvisionJob]
) -> bool:
    """每個機器節點都有建機工作，而且全部完成、沒有任何失敗。

    ``jobs`` 只放查得到的工作；有節點沒有工作（或工作已不存在）時長度對不上，
    視為還沒就緒。
    """
    return (
        bool(nodes)
        and len(jobs) == len(nodes)
        and all(
            _job_value(job) == "completed"
            and job.failed_count == 0
            and job.done == job.total
            for job in jobs
        )
    )


def activate_class(
    session: Session, item: TeachingClass, jobs: list[BatchProvisionJob]
) -> list[str]:
    """建機全部完成後套用拓樸；成功才把班級轉成可上課。

    回傳拓樸錯誤（空清單＝成功），失敗時要怎麼處理由呼叫端決定：
    recompute 轉 partial_failed、手動重試直接回 400。成功時順便記下這組工作
    的快照，之後的 recompute 在工作沒變動時就不必再打一輪 PVE。呼叫端負責 commit。
    """
    session.flush()
    topology_errors = class_network_service.apply_class_topology(
        session, class_id=item.id
    )
    if topology_errors:
        _APPLIED_TOPOLOGY.pop(item.id, None)
        return topology_errors
    _APPLIED_TOPOLOGY[item.id] = _job_signature(jobs)
    mark_class_active(session, item)
    return []


def mark_class_active(session: Session, item: TeachingClass) -> None:
    """班級轉成可上課；課程殼只在「第一次上線」時自動發布。

    recompute 會在程序重啟、補學生、重試之後再次走到這裡。若每次都把課程殼
    設回 published，老師在課程管理頁刻意取消發布的內容就會被悄悄重新公開。
    容量預留在班級第一次上線時才轉成 consumed，之後除了撤回（當時還沒有任何
    機器）之外不會再回到 reserved，所以拿它當「是否第一次上線」的判斷；
    沒有預留紀錄的舊班級退回「之前不是 active」。呼叫端負責 commit。
    """
    was_active = item.status == TeachingClassStatus.active
    item.status = TeachingClassStatus.active
    reservation = session.exec(
        select(ClassCapacityReservation).where(
            ClassCapacityReservation.class_id == item.id
        )
    ).first()
    if reservation is not None:
        first_activation = reservation.status != "consumed"
        reservation.status = "consumed"
        session.add(reservation)
    else:
        first_activation = not was_active
    course_service.ensure_class_path(
        session,
        teaching_class=item,
        published=first_activation,
    )


def recompute(*, session: Session, class_id: uuid.UUID) -> TeachingClass | None:
    """Derive and persist the class status from its node jobs.

    Returns the class (unchanged when it is archived or has no jobs yet). The
    caller owns the commit.
    """
    item = session.get(TeachingClass, class_id)
    if item is None or item.status == TeachingClassStatus.archived:
        _APPLIED_TOPOLOGY.pop(class_id, None)
        return item

    nodes = list(
        session.exec(
            select(TeachingClassMachineNode).where(
                TeachingClassMachineNode.class_id == class_id
            )
        ).all()
    )
    jobs = [
        job
        for node in nodes
        if node.batch_job_id
        and (job := session.get(BatchProvisionJob, node.batch_job_id)) is not None
    ]
    values = [_job_value(job) for job in jobs]
    if not values:
        return item

    any_failed = any(job.failed_count > 0 for job in jobs) or any(
        value in FAILED_JOB_VALUES for value in values
    )
    if jobs_all_ready(nodes, jobs):
        if (
            item.status == TeachingClassStatus.active
            and _APPLIED_TOPOLOGY.get(class_id) == _job_signature(jobs)
        ):
            # 已經可上課、工作也沒變 → 規則早就套好了，不必再打一輪 PVE
            return item
        topology_errors = activate_class(session, item, jobs)
        if topology_errors:
            logger.warning(
                "Class %s topology failed after provisioning: %s",
                class_id,
                "; ".join(topology_errors),
            )
            item.status = TeachingClassStatus.partial_failed
    elif any_failed:
        item.status = TeachingClassStatus.partial_failed
    elif any(value in IN_FLIGHT_JOB_VALUES for value in values):
        item.status = TeachingClassStatus.provisioning
    else:
        item.status = TeachingClassStatus.pending_review

    item.updated_at = get_datetime_utc()
    session.add(item)
    return item


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _usage_percent(used: object, total: object) -> float | None:
    used_number = _finite_float(used)
    total_number = _finite_float(total)
    if used_number is None or total_number is None or total_number <= 0:
        return None
    return round(max(0.0, min(100.0, used_number / total_number * 100)), 2)


def class_resource_usage_items(
    vmids: list[int], cluster_resources: list[dict]
) -> list[ClassResourceUsageItem]:
    """把班級機器的 vmid 對到 PVE 叢集資源，算出 CPU／記憶體使用率。

    查不到的 vmid 仍回一列（status=unknown），重複的 vmid 只算一次；
    PVE 回傳的數值可能是字串、None 或 NaN，無法轉成有限數字的一律視為未知。
    """
    resources_by_vmid = {
        int(resource["vmid"]): resource
        for resource in cluster_resources
        if resource.get("vmid") is not None
    }
    items: list[ClassResourceUsageItem] = []
    for vmid in sorted(set(vmids)):
        resource = resources_by_vmid.get(vmid)
        if resource is None:
            items.append(ClassResourceUsageItem(vmid=vmid, status="unknown"))
            continue

        cpu_ratio = _finite_float(resource.get("cpu"))
        cpu_usage_pct = (
            round(max(0.0, min(100.0, cpu_ratio * 100)), 2)
            if cpu_ratio is not None
            else None
        )
        mem_used = _finite_float(resource.get("mem"))
        mem_total = _finite_float(resource.get("maxmem"))
        items.append(
            ClassResourceUsageItem(
                vmid=vmid,
                status=str(resource.get("status") or "unknown").lower(),
                cpu_usage_pct=cpu_usage_pct,
                ram_usage_pct=_usage_percent(mem_used, mem_total),
                mem_used_bytes=int(mem_used) if mem_used is not None else None,
                mem_total_bytes=int(mem_total) if mem_total is not None else None,
            )
        )
    return items


__all__ = [
    "activate_class",
    "class_resource_usage_items",
    "jobs_all_ready",
    "mark_class_active",
    "recompute",
]
