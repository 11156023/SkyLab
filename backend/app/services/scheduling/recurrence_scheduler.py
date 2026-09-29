"""Scheduler tick handlers for recurrence-based boot/stop.

These are registered alongside the request lifecycle handlers in
:func:`app.services.scheduling.coordinator.run_scheduler`. Each handler
runs once per tick (default 60s) inside a worker thread.

Three handlers:

- :func:`process_recurrence_windows` — First archives/reclaims teaching
  classes that have ended (and retries failed reclaims), then recomputes
  ``next_window_start/end`` for vm_requests with a recurrence rule and, directly
  on the job row, for completed formal-class batch jobs (cleared when the
  class schedule is disabled or over).
- :func:`process_scheduled_boot` — For VMs whose next window starts within
  ``lead_time``, power them on in batches with a sleep between batches.
  Each booted VM gets ``auto_stop_at = window_end + grace_period``. Formal-class
  batch-job VMs inside their active window are booted too (no extra grace).
- :func:`process_auto_stops` — Shut down VMs whose ``auto_stop_at`` has elapsed
  (covers both ``window_grace`` and ``practice_quota`` reasons), escalating
  to a hard stop when the guest ignores the graceful shutdown.
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
from app.exceptions import NotFoundError, ProxmoxError
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
from app.services.scheduling import policy as scheduling_policy
from app.services.scheduling import support as scheduling_support
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
    return scheduling_policy.utc_now()


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
    """Refresh ``next_window_start/end`` on recurring VMRequests and batch jobs.

    Ended teaching classes are archived/reclaimed first (see
    :func:`_process_expired_class_lifecycle`).

    A row's window is "stale" when ``next_window_end`` has passed; we then
    advance to the following occurrence. If no future occurrence exists
    (RRULE exhausted via UNTIL), the columns are cleared. Completed
    formal-class batch jobs are refreshed on the job row itself; their window
    is cleared while the class schedule is disabled or the class has ended.
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
            try:
                window = compute_next_window(
                    rule=req.recurrence_rule or "",
                    duration_minutes=req.recurrence_duration_minutes or 0,
                    timezone=req.schedule_timezone,
                    after=now,
                )
            except Exception:
                # 單筆規則或時區壞掉只跳過它，其他申請照常更新
                logger.warning(
                    "Skipping recurrence window for vm_request %s",
                    req.id,
                    exc_info=True,
                )
                continue
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
            try:
                window = compute_active_or_next_window(
                    rule=job.recurrence_rule or "",
                    duration_minutes=job.recurrence_duration_minutes or 0,
                    timezone=job.schedule_timezone,
                    now=_class_schedule_reference(class_item, job, now),
                )
            except Exception:
                logger.warning(
                    "Skipping recurrence window for batch job %s",
                    job.id,
                    exc_info=True,
                )
                continue
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
        targets = _filter_due_for_boot(
            session=session,
            requests=candidates,
            grace_minutes=policy.window_grace_minutes,
        )
        # Snapshot plain values while the session is still open — commits made
        # inside _filter_due_for_boot expire these ORM objects, and they become
        # unreadable (DetachedInstanceError) once the session closes.
        boot_specs = [
            _BootSpec(
                request_id=req.id,
                vmid=req.vmid,
                node=req.actual_node or req.assigned_node,
                window_end=req.next_window_end,
                resource_type=scheduling_policy.resource_type_for_request(req),
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

    # 排程已清掉或改期的機器不再追蹤「已送出關機」的時間
    due_keys = {_stop_key(resource) for resource in due}
    for key in list(_shutdown_requested_at):
        if key not in due_keys:
            _shutdown_requested_at.pop(key, None)

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


def _safe_zone(name: str | None) -> ZoneInfo:
    """解析時區名稱；舊資料裡的無效時區退回預設值，不讓整輪排程中斷。"""
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (KeyError, ValueError, OSError):
        logger.warning(
            "Invalid timezone %r in schedule data; falling back to %s",
            name, DEFAULT_TIMEZONE,
        )
        return ZoneInfo(DEFAULT_TIMEZONE)


def _class_expired(teaching_class: TeachingClass, now: datetime) -> bool:
    tz = _safe_zone(teaching_class.timezone)
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
        expired_ids: list[uuid.UUID] = []
        retry_ids: list[uuid.UUID] = []
        for item in candidates:
            # 單一班級資料有問題只跳過它，不能讓後面所有租戶的視窗更新中斷
            try:
                if (
                    item.status != TeachingClassStatus.archived
                    and _class_expired(item, now)
                ):
                    expired_ids.append(item.id)
                if _class_reclaim_retry_due(item, now):
                    retry_ids.append(item.id)
            except Exception:
                logger.warning(
                    "Skipping lifecycle check for teaching class %s",
                    item.id,
                    exc_info=True,
                )

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
    tz = _safe_zone(job.schedule_timezone or teaching_class.timezone)
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
    tz = _safe_zone(job.schedule_timezone or teaching_class.timezone)
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
                    BatchProvisionTask.vmid.isnot(None),  # type: ignore[union-attr]
                )
            ).all()
        )
        for task in tasks:
            vmid = task.vmid
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
            try:
                info = _resource_info(vmid=vmid)
            except ProxmoxError as exc:
                # 單一連線故障只跳過這台，不能讓整輪排程開機中斷
                logger.warning(
                    "Scheduled boot: cannot look up vmid=%s: %s", vmid, exc
                )
                continue
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
    grace_minutes: int,
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
                scheduling_policy.resource_type_for_request(req),
            )
            if status.get("status") == "running":
                # Running but no auto_stop yet — set the grace stop and move on
                # without re-issuing start.
                _write_window_grace_stop(
                    session=session, vmid=req.vmid,
                    window_end=req.next_window_end,
                    grace_minutes=grace_minutes,
                )
                continue
        except Exception:
            pass  # 查不到目前狀態就視為需要啟動，交給後續 start 流程處理
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


# 送出優雅關機後，超過這個時間機器還在跑就強制斷電
FORCE_STOP_AFTER_SECONDS = 300

# (vmid, auto_stop_at) → 第一次送出 shutdown 的 time.monotonic()。
# 只存在排程器行程記憶體：行程重啟頂多讓寬限期重算一次。
_shutdown_requested_at: dict[tuple[int, datetime | None], float] = {}

# cluster/resources 由 pvestatd 約每 10 秒更新一次，回報的 uptime 可能比實際
# 少幾秒；判斷「關機後又被開回來」時預留這段誤差，避免把送出關機前幾秒才
# 開機、正好不理 ACPI 的機器誤判成已重開
_UPTIME_STALENESS_SECONDS = 15


def _stop_key(resource: Resource) -> tuple[int, datetime | None]:
    return resource.vmid, resource.auto_stop_at


def _restarted_since_shutdown(info: dict, *, elapsed: float) -> bool:
    """送出關機後，機器是否已停下又被其他流程開回來。

    本次開機秒數（uptime）比送出關機至今還短，代表是關機之後才開的機。
    uptime 缺欄位或不是數字時無法判斷，回 False 照原本流程等待／升級。
    """
    try:
        uptime = int(info["uptime"])
    except (KeyError, TypeError, ValueError):
        return False
    return uptime + _UPTIME_STALENESS_SECONDS < elapsed


def _clear_auto_stop(resource: Resource) -> None:
    _shutdown_requested_at.pop(_stop_key(resource), None)
    with Session(engine) as session:
        resource_repo.set_auto_stop(
            session=session, vmid=resource.vmid,
            auto_stop_at=None, auto_stop_reason=None,
        )


def _stop_one(*, resource: Resource) -> None:
    """先送優雅關機；超過 FORCE_STOP_AFTER_SECONDS 仍在跑就強制斷電。

    proxmox_service.control 不等 PVE 任務結果，送出 shutdown 不代表機器
    會關（guest 可忽略 ACPI，例如停在開機選單或設了 HandlePowerKey=ignore）。
    所以排程要留到確認機器已停止才清掉，由之後的 tick 追蹤並升級成 stop。

    同一個 tick 裡 process_due_request_starts 比這裡先跑，申請時段內的機器
    被關掉後會先被開回來；看到它是關機之後才開的機（uptime 比送出關機至今
    還短）就當作這次自動關機已完成、清掉排程，不能再強制斷電。
    """
    info = _resource_info(vmid=resource.vmid)
    if info is None:
        # Already gone from Proxmox — clear the schedule so we don't loop.
        _clear_auto_stop(resource)
        return
    node = info["node"]
    rtype = info["type"]
    if info.get("status") != "running":
        # Already off — clear the schedule.
        _clear_auto_stop(resource)
        return

    key = _stop_key(resource)
    requested_at = _shutdown_requested_at.get(key)
    now = time.monotonic()
    if requested_at is not None:
        elapsed = now - requested_at
        if _restarted_since_shutdown(info, elapsed=elapsed):
            logger.info(
                "Auto-stop: vmid=%s restarted after shutdown; clearing schedule",
                resource.vmid,
            )
            _clear_auto_stop(resource)
            return
        if elapsed < FORCE_STOP_AFTER_SECONDS:
            return  # 已送出關機，等 guest 自己關
        logger.warning(
            "Auto-stop: vmid=%s ignored shutdown for %ss; forcing stop",
            resource.vmid, FORCE_STOP_AFTER_SECONDS,
        )
        proxmox_service.control(node, resource.vmid, rtype, "stop")
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
    _shutdown_requested_at[key] = now


def _resource_info(*, vmid: int) -> dict | None:
    """Locate the resource on Proxmox to discover its node & type.

    只有確定機器不在（NotFoundError）才回 None；PVE 連線暫時有問題時
    讓例外往外丟，保留排程等下一輪，不能把它當成「機器不在」清掉排程。
    """
    try:
        info = scheduling_support.find_resource_strict(vmid)
    except NotFoundError:
        return None
    return info if info else None


def _chunk(items: list, size: int) -> list[list]:
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]


__all__ = [
    "process_auto_stops",
    "process_recurrence_windows",
    "process_scheduled_boot",
]
