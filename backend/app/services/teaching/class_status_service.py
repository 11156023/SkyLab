"""Recompute a teaching class status from its machine-node batch jobs.

The class status used to advance only when a teacher had the class workspace
open (``GET /provision-status`` polls every 3s). Nothing else wrote it back, so
a fully provisioned class could sit at ``provisioning`` in the class list
forever and the "需處理" filter never lit up. This module is the single place
that derives the status, and it is called both from that endpoint and from the
provisioning worker as each node job finishes.
"""

from __future__ import annotations

import logging
import uuid

from sqlmodel import Session, select

from app.models import (
    BatchProvisionJob,
    ClassCapacityReservation,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
)
from app.models.base import get_datetime_utc
from app.services.course import course_service
from app.services.teaching import class_network_service

logger = logging.getLogger(__name__)

IN_FLIGHT_JOB_VALUES = {"approved", "pending", "running", "completed"}
FAILED_JOB_VALUES = {"failed", "rejected", "cancelled"}


def _job_value(job: BatchProvisionJob) -> str:
    return job.status.value if hasattr(job.status, "value") else str(job.status)


def recompute(*, session: Session, class_id: uuid.UUID) -> TeachingClass | None:
    """Derive and persist the class status from its node jobs.

    Returns the class (unchanged when it is archived or has no jobs yet). The
    caller owns the commit.
    """
    item = session.get(TeachingClass, class_id)
    if item is None or item.status == TeachingClassStatus.archived:
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
    all_ready = len(jobs) == len(nodes) and all(
        value == "completed" and job.failed_count == 0 and job.done == job.total
        for value, job in zip(values, jobs, strict=True)
    )

    if all_ready:
        session.flush()
        topology_errors = class_network_service.apply_class_topology(
            session, class_id=class_id
        )
        if topology_errors:
            logger.warning(
                "Class %s topology failed after provisioning: %s",
                class_id,
                "; ".join(topology_errors),
            )
            item.status = TeachingClassStatus.partial_failed
        else:
            item.status = TeachingClassStatus.active
            course_service.ensure_class_path(
                session,
                teaching_class=item,
                published=True,
            )
            reservation = session.exec(
                select(ClassCapacityReservation).where(
                    ClassCapacityReservation.class_id == class_id
                )
            ).first()
            if reservation:
                reservation.status = "consumed"
                session.add(reservation)
    elif any_failed:
        item.status = TeachingClassStatus.partial_failed
    elif any(value in IN_FLIGHT_JOB_VALUES for value in values):
        item.status = TeachingClassStatus.provisioning
    else:
        item.status = TeachingClassStatus.pending_review

    item.updated_at = get_datetime_utc()
    session.add(item)
    return item


__all__ = ["recompute"]
