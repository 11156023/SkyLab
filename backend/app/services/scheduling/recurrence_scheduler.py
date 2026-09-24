"""Scheduler tick handlers for recurrence-based boot/stop.

These are registered alongside the request lifecycle handlers in
:func:`app.services.scheduling.coordinator.run_scheduler`. Each handler
runs once per tick (default 60s) inside a worker thread.

Three handlers:

- :func:`process_recurrence_windows` — Recompute ``next_window_start/end`` for
  vm_requests with a recurrence rule. Batch jobs reuse this through their
  member tasks.
- :func:`process_scheduled_boot` — For VMs whose next window starts within
  ``lead_time``, power them on in batches with a sleep between batches.
  Each booted VM gets ``auto_stop_at = window_end + grace_period``.
- :func:`process_auto_stops` — Shut down VMs whose ``auto_stop_at`` has elapsed
  (covers both ``window_grace`` and ``practice_quota`` reasons).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from app.core.db import engine
from app.models import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    BatchProvisionTask,
    BatchProvisionTaskStatus,
    Resource,
    TeachingClass,
    TeachingClassStatus,
    VMRequest,
)
from app.repositories import resource as resource_repo
from app.services.proxmox import proxmox_service
from app.services.scheduling.recurrence import (
    DEFAULT_TIMEZONE,
    compute_active_or_next_window,
    compute_next_window,
    get_schedule_policy,
)
from app.services.teaching import class_lifecycle_service

logger = logging.getLogger(__name__)

CLASS_RECLAIM_RETRY_INTERVAL = timedelta(minutes=15)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class _BootSpec:
    """Plain snapshot of a VMRequest taken while its session is still open.

    ``_filter_due_for_boot`` may commit (via set_auto_stop), which expires all
    ORM objects in the session; reading them after the session closes raises
    DetachedInstanceError.
    """

    request_id: uuid.UUID
    vmid: int
    node: str | None
    window_end: datetime | None
    resource_type: str
    grace_already_in_window: bool = False


def process_recurrence_windows() -> None:
    """Refresh ``next_window_start/end`` on every recurring VMRequest.

    A row's window is "stale" when ``next_window_end`` has passed; we then
    advance to the following occurrence. If no future occurrence exists
    (RRULE exhausted via UNTIL), the columns are cleared.
    """
    now = _utc_now()
    _process_expired_class_lifecycle(now=now)
    with Session(engine) as session:
        updated = 0
        stmt = select(VMRequest).where(
            VMRequest.recurrence_rule.isnot(None),  # type: ignore[union-attr]
        )
        requests = list(session.exec(stmt).all())
        for req in requests:
            if req.next_window_end and req.next_window_end > now:
                continue  # current window still valid
            window = compute_next_window(
                rule=req.recurrence_rule or "",
                duration_minutes=req.recurrence_duration_minutes or 0,
                timezone=req.schedule_timezone,
                after=now,
            )
            if window is None:
                req.next_window_start = None
                req.next_window_end = None
            else:
                req.next_window_start, req.next_window_end = window
            session.add(req)
            updated += 1

        jobs = list(
            session.exec(
                select(BatchProvisionJob).where(
                    BatchProvisionJob.recurrence_rule.isnot(None),  # type: ignore[union-attr]
                    BatchProvisionJob.status == BatchProvisionJobStatus.completed,
                )
            ).all()
        )
        for job in jobs:
            class_item = session.get(TeachingClass, job.teaching_class_id)
            if not _class_schedule_enabled(class_item, job, now):
                if job.next_window_start is not None or job.next_window_end is not None:
                    job.next_window_start = None
                    job.next_window_end = None
                    session.add(job)
                    updated += 1
                continue
            if job.next_window_end and job.next_window_end > now:
                continue
            window = compute_active_or_next_window(
                rule=job.recurrence_rule or "",
                duration_minutes=job.recurrence_duration_minutes or 0,
                timezone=job.schedule_timezone,
                now=_class_schedule_reference(class_item, job, now),
            )
            job.next_window_start, job.next_window_end = window or (None, None)
            session.add(job)
            updated += 1
        if updated:
            session.commit()
            logger.debug("Refreshed %d recurrence windows", updated)


def process_scheduled_boot() -> None:
    """Power on resources whose next window is about to start.

    Batches are sized by ``scheduled_boot_batch_size`` and separated by
    ``scheduled_boot_batch_interval_seconds`` to avoid hammering Proxmox.
    """
    now = _utc_now()
    with Session(engine) as session:
        policy = get_schedule_policy(session=session)
        lead = timedelta(minutes=policy.boot_lead_time_minutes)
        grace = timedelta(minutes=policy.window_grace_minutes)

        # Find requests whose window starts in [now, now+lead) and have not
        # already been booted for this window.
        stmt = select(VMRequest).where(
            VMRequest.next_window_start.isnot(None),  # type: ignore[union-attr]
            VMRequest.next_window_start <= now + lead,
            VMRequest.next_window_start > now - timedelta(minutes=1),
            VMRequest.vmid.isnot(None),  # type: ignore[union-attr]
        )
        candidates = list(session.exec(stmt).all())
        targets = _filter_due_for_boot(session=session, requests=candidates)
        # Snapshot plain values while the session is still open — commits made
        # inside _filter_due_for_boot expire these ORM objects, and they become
        # unreadable (DetachedInstanceError) once the session closes.
        boot_specs = [
            _BootSpec(
                request_id=req.id,
                vmid=req.vmid,
                node=req.actual_node or req.assigned_node,
                window_end=req.next_window_end,
                resource_type=_resource_type(req),
            )
            for req in targets
            if req.vmid is not None
        ]
        boot_specs.extend(
            _batch_boot_specs(session=session, now=now)
        )

    if not boot_specs:
        return

    logger.info("Scheduled boot: %d VM(s) to power on", len(boot_specs))

    for batch_idx, batch in enumerate(_chunk(boot_specs, policy.boot_batch_size)):
        for spec in batch:
            try:
                _boot_one(spec=spec, grace=grace)
            except Exception:
                logger.exception(
                    "Scheduled boot failed for vmid=%s request=%s",
                    spec.vmid, spec.request_id,
                )
        # Sleep between batches (skip after final batch).
        if batch_idx < (len(boot_specs) - 1) // policy.boot_batch_size:
            time.sleep(policy.boot_batch_interval_seconds)


def process_auto_stops() -> None:
    """Shut down VMs whose ``auto_stop_at`` has elapsed."""
    now = _utc_now()
    with Session(engine) as session:
        due = resource_repo.list_due_auto_stops(session=session, now=now)

    if not due:
        return

    logger.info("Auto-stop: %d VM(s) due", len(due))
    for resource in due:
        try:
            _stop_one(resource=resource)
        except Exception:
            logger.exception(
                "Auto-stop failed for vmid=%s reason=%s",
                resource.vmid, resource.auto_stop_reason,
            )


# ─── helpers ──────────────────────────────────────────────────────────────────


def _class_expired(teaching_class: TeachingClass, now: datetime) -> bool:
    tz = ZoneInfo(teaching_class.timezone or DEFAULT_TIMEZONE)
    cutoff = datetime.combine(
        teaching_class.end_date,
        teaching_class.end_time,
        tzinfo=tz,
    )
    return now >= cutoff.astimezone(UTC)


def _class_reclaim_retry_due(
    teaching_class: TeachingClass,
    now: datetime,
) -> bool:
    if (
        teaching_class.status != TeachingClassStatus.archived
        or teaching_class.resources_reclaimed_at is not None
    ):
        return False
    requested_at = teaching_class.reclaim_requested_at
    return (
        requested_at is None
        or requested_at <= now - CLASS_RECLAIM_RETRY_INTERVAL
    )


def _process_expired_class_lifecycle(*, now: datetime) -> None:
    """Immediately archive/reclaim ended classes and retry failed reclaims."""
    with Session(engine) as session:
        candidates = list(
            session.exec(
                select(TeachingClass).where(
                    (TeachingClass.status != TeachingClassStatus.archived)
                    | col(TeachingClass.resources_reclaimed_at).is_(None)
                )
            ).all()
        )
        expired_ids = [
            item.id
            for item in candidates
            if item.status != TeachingClassStatus.archived
            and _class_expired(item, now)
        ]
        retry_ids = [
            item.id
            for item in candidates
            if _class_reclaim_retry_due(item, now)
        ]

    for class_id in expired_ids:
        try:
            with Session(engine) as session:
                item = session.get(TeachingClass, class_id)
                if item is None or item.status == TeachingClassStatus.archived:
                    continue
                result = class_lifecycle_service.archive_and_reclaim(
                    session=session,
                    item=item,
                    requested_by=item.owner_id,
                    force=True,
                    reclaim_resources=True,
                )
                logger.info(
                    "Expired teaching class %s archived; queued=%d failed=%d",
                    class_id,
                    len(result["queued_vmids"]),
                    len(result["failed"]),
                )
        except Exception:
            logger.exception(
                "Automatic archive/reclaim failed for teaching class %s",
                class_id,
            )

    for class_id in retry_ids:
        try:
            with Session(engine) as session:
                item = session.get(TeachingClass, class_id)
                if item is None or not _class_reclaim_retry_due(item, now):
                    continue
                result = class_lifecycle_service.queue_reclaim(
                    session=session,
                    item=item,
                    requested_by=item.owner_id,
                    force=True,
                )
                logger.info(
                    "Retried class reclaim %s; queued=%d in_progress=%d failed=%d",
                    class_id,
                    len(result["queued_vmids"]),
                    len(result["in_progress_vmids"]),
                    len(result["failed"]),
                )
        except Exception:
            logger.exception(
                "Automatic reclaim retry failed for teaching class %s",
                class_id,
            )


def _class_schedule_reference(
    teaching_class: TeachingClass | None,
    job: BatchProvisionJob,
    now: datetime,
) -> datetime:
    if teaching_class is None:
        return now
    tz = ZoneInfo(job.schedule_timezone or teaching_class.timezone or DEFAULT_TIMEZONE)
    class_start = datetime.combine(
        teaching_class.start_date,
        teaching_class.start_time,
        tzinfo=tz,
    ).astimezone(UTC)
    return max(now, class_start)


def _class_schedule_enabled(
    teaching_class: TeachingClass | None,
    job: BatchProvisionJob,
    now: datetime,
) -> bool:
    if teaching_class is None or teaching_class.status == TeachingClassStatus.archived:
        return False
    tz = ZoneInfo(job.schedule_timezone or teaching_class.timezone or DEFAULT_TIMEZONE)
    local_date = now.astimezone(tz).date()
    return local_date <= teaching_class.end_date


def _batch_boot_specs(*, session: Session, now: datetime) -> list[_BootSpec]:
    """Build boot targets for formal-class resources in the active window."""
    jobs = list(
        session.exec(
            select(BatchProvisionJob).where(
                BatchProvisionJob.status == BatchProvisionJobStatus.completed,
                col(BatchProvisionJob.next_window_start).isnot(None),
                col(BatchProvisionJob.next_window_start) <= now,
                col(BatchProvisionJob.next_window_end) > now,
            )
        ).all()
    )
    specs: list[_BootSpec] = []
    for job in jobs:
        teaching_class = session.get(TeachingClass, job.teaching_class_id)
        if not _class_schedule_enabled(teaching_class, job, now):
            continue
        tasks = list(
            session.exec(
                select(BatchProvisionTask).where(
                    BatchProvisionTask.job_id == job.id,
                    BatchProvisionTask.status == BatchProvisionTaskStatus.completed,
                    BatchProvisionTask.resource_vmid.isnot(None),  # type: ignore[union-attr]
                )
            ).all()
        )
        for task in tasks:
            vmid = task.resource_vmid
            if vmid is None:
                continue
            resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
            if resource is None or resource.teaching_class_id != job.teaching_class_id:
                continue
            if (
                resource.auto_stop_at
                and job.next_window_end
                and resource.auto_stop_at >= job.next_window_end
            ):
                continue
            info = _resource_info(vmid=vmid)
            if not info:
                continue
            if info.get("status") == "running":
                _write_window_grace_stop(
                    session=session,
                    vmid=vmid,
                    window_end=job.next_window_end,
                    grace_minutes=0,
                )
                continue
            specs.append(
                _BootSpec(
                    request_id=job.id,
                    vmid=vmid,
                    node=info.get("node"),
                    window_end=job.next_window_end,
                    resource_type=("lxc" if info.get("type") == "lxc" else "qemu"),
                    grace_already_in_window=True,
                )
            )
    return specs


def _filter_due_for_boot(
    *,
    session: Session,
    requests: list[VMRequest],
) -> list[VMRequest]:
    """Drop requests whose VM is already running or already has a future
    auto_stop set for this window (idempotency across ticks)."""
    due: list[VMRequest] = []
    for req in requests:
        if req.vmid is None:
            continue
        resource = resource_repo.get_resource_by_vmid(session=session, vmid=req.vmid)
        if resource is None:
            continue
        # If we already scheduled this window's grace stop, scheduler already
        # booted the VM in a prior tick; skip.
        if (
            resource.auto_stop_at
            and req.next_window_end
            and resource.auto_stop_at >= req.next_window_end
        ):
            continue
        # Skip if running already (e.g. user manually started it ahead of time).
        try:
            status = proxmox_service.get_status(
                req.actual_node or req.assigned_node or "",
                req.vmid,
                _resource_type(req),
            )
            if status.get("status") == "running":
                # Running but no auto_stop yet — set the grace stop and move on
                # without re-issuing start.
                _write_window_grace_stop(
                    session=session, vmid=req.vmid,
                    window_end=req.next_window_end,
                    grace_minutes=get_schedule_policy(session=session).window_grace_minutes,
                )
                continue
        except Exception:
            pass
        due.append(req)
    return due


def _boot_one(
    *,
    spec: _BootSpec,
    grace: timedelta,
) -> None:
    if not spec.node:
        logger.warning("Cannot boot vmid=%s: no node assigned", spec.vmid)
        return
    proxmox_service.control(spec.node, spec.vmid, spec.resource_type, "start")
    logger.info("Scheduled boot triggered: vmid=%s node=%s", spec.vmid, spec.node)

    if spec.resource_type == "lxc":
        from app.services.resource import resource_service

        with Session(engine) as session:
            resource_service.ensure_lxc_platform_key(
                session=session,
                node=spec.node,
                vmid=spec.vmid,
            )
            resource_service.ensure_lxc_login_password(
                session=session,
                node=spec.node,
                vmid=spec.vmid,
            )

    if spec.window_end is None:
        return
    auto_stop_at = (
        spec.window_end
        if spec.grace_already_in_window
        else spec.window_end + grace
    )
    with Session(engine) as session:
        resource_repo.set_auto_stop(
            session=session,
            vmid=spec.vmid,
            auto_stop_at=auto_stop_at,
            auto_stop_reason="window_grace",
        )


def _write_window_grace_stop(
    *,
    session: Session,
    vmid: int,
    window_end: datetime | None,
    grace_minutes: int,
) -> None:
    if window_end is None:
        return
    resource_repo.set_auto_stop(
        session=session,
        vmid=vmid,
        auto_stop_at=window_end + timedelta(minutes=grace_minutes),
        auto_stop_reason="window_grace",
    )


def _stop_one(*, resource: Resource) -> None:
    """Try a graceful shutdown first; if the VM is still running after a few
    seconds, fall back to a hard stop."""
    info = _resource_info(vmid=resource.vmid)
    if info is None:
        # Already gone from Proxmox — clear the schedule so we don't loop.
        with Session(engine) as session:
            resource_repo.set_auto_stop(
                session=session, vmid=resource.vmid,
                auto_stop_at=None, auto_stop_reason=None,
            )
        return
    node = info["node"]
    rtype = info["type"]
    if info.get("status") != "running":
        # Already off — clear the schedule.
        with Session(engine) as session:
            resource_repo.set_auto_stop(
                session=session, vmid=resource.vmid,
                auto_stop_at=None, auto_stop_reason=None,
            )
        return
    try:
        proxmox_service.control(node, resource.vmid, rtype, "shutdown")
        logger.info("Auto-stop graceful shutdown: vmid=%s", resource.vmid)
    except Exception:
        logger.exception(
            "Graceful shutdown failed; forcing stop for vmid=%s", resource.vmid
        )
        try:
            proxmox_service.control(node, resource.vmid, rtype, "stop")
        except Exception:
            logger.exception("Hard stop also failed for vmid=%s", resource.vmid)
            return
    with Session(engine) as session:
        resource_repo.set_auto_stop(
            session=session, vmid=resource.vmid,
            auto_stop_at=None, auto_stop_reason=None,
        )


def _resource_info(*, vmid: int) -> dict | None:
    """Locate the resource on Proxmox to discover its node & type."""
    try:
        info = proxmox_service.find_resource(vmid)
    except Exception:
        return None
    return info if info else None


def _resource_type(req: VMRequest) -> str:
    return "lxc" if req.resource_type == "lxc" else "qemu"


def _chunk(items: list, size: int) -> list[list]:
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]


__all__ = [
    "process_auto_stops",
    "process_recurrence_windows",
    "process_scheduled_boot",
]
