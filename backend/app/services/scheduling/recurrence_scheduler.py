"""Scheduler tick handlers for recurrence-based boot/stop.

These are registered alongside the existing migration handlers in
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
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.core.db import engine
from app.models import Resource, VMRequest
from app.repositories import resource as resource_repo
from app.services.proxmox import proxmox_service
from app.services.scheduling.recurrence import (
    compute_next_window,
    get_schedule_policy,
)

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def process_recurrence_windows() -> None:
    """Refresh ``next_window_start/end`` on every recurring VMRequest.

    A row's window is "stale" when ``next_window_end`` has passed; we then
    advance to the following occurrence. If no future occurrence exists
    (RRULE exhausted via UNTIL), the columns are cleared.
    """
    now = _utc_now()
    with Session(engine) as session:
        stmt = select(VMRequest).where(
            VMRequest.recurrence_rule.isnot(None),  # type: ignore[union-attr]
        )
        requests = list(session.exec(stmt).all())
        updated = 0
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

    if not targets:
        return

    logger.info("Scheduled boot: %d VM(s) to power on", len(targets))

    for batch_idx, batch in enumerate(_chunk(targets, policy.boot_batch_size)):
        for req in batch:
            try:
                _boot_one(req=req, window_end=req.next_window_end, grace=grace)
            except Exception:
                logger.exception(
                    "Scheduled boot failed for vmid=%s request=%s",
                    req.vmid, req.id,
                )
        # Sleep between batches (skip after final batch).
        if batch_idx < (len(targets) - 1) // policy.boot_batch_size:
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
        except Exception:  # noqa: BLE001 — Proxmox transient errors are common
            pass
        due.append(req)
    return due


def _boot_one(
    *,
    req: VMRequest,
    window_end: datetime | None,
    grace: timedelta,
) -> None:
    if req.vmid is None:
        return
    node = req.actual_node or req.assigned_node
    if not node:
        logger.warning("Cannot boot vmid=%s: no node assigned", req.vmid)
        return
    proxmox_service.control(node, req.vmid, _resource_type(req), "start")
    logger.info("Scheduled boot triggered: vmid=%s node=%s", req.vmid, node)

    if window_end is None:
        return
    auto_stop_at = window_end + grace
    with Session(engine) as session:
        resource_repo.set_auto_stop(
            session=session,
            vmid=req.vmid,
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
