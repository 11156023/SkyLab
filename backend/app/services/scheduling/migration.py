"""Migration sub-domain extracted from coordinator.py.

Handles VMMigrationJob lifecycle: claim → precheck gates → execute → record
result. All public callers should import these from
``app.services.scheduling.coordinator`` (re-exported there) so that monkey-
patches against ``coordinator.*`` continue to work.

Key indirections
----------------
- ``_utc_now`` is resolved lazily through the coordinator module on every call
  so that test-time ``monkeypatch.setattr("...coordinator._utc_now", ...)``
  affects this module too.
- ``_refresh_actual_node`` lives in ``coordinator`` (cross-cuts both migration
  and lifecycle code); imported lazily here to avoid a circular import.
"""

from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from sqlmodel import Session

from app.exceptions import NotFoundError
from app.models import (
    VMMigrationJob,
    VMMigrationJobStatus,
    VMMigrationStatus,
    VMRequest,
)
from app.repositories import vm_migration_job as vm_migration_job_repo
from app.repositories import vm_request as vm_request_repo
from app.services.proxmox import proxmox_service
from app.services.scheduling import policy as scheduling_policy
from app.services.scheduling import support as scheduling_support
from app.services.user import audit_service

logger = logging.getLogger(__name__)

SCHEDULER_POLL_SECONDS = scheduling_policy.SCHEDULER_POLL_SECONDS
_MigrationPolicy = scheduling_policy.MigrationPolicy


# ---------------------------------------------------------------------------
# Local thin delegations (so that the migration block stays self-contained but
# tests patching ``coordinator._utc_now`` still take effect).
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    # Lazy import: avoids a circular import while letting test monkey-patches
    # against ``coordinator._utc_now`` propagate to this module's calls.
    from app.services.scheduling import coordinator

    return coordinator._utc_now()


def _normalize_datetime(value: datetime | None) -> datetime | None:
    return scheduling_policy.normalize_datetime(value)


def _resource_type_for_request(request: VMRequest) -> str:
    return scheduling_policy.resource_type_for_request(request)


def _migration_worker_id() -> str:
    return scheduling_policy.migration_worker_id()


def _next_retry_at(*, now: datetime, policy: _MigrationPolicy, attempt_count: int) -> datetime:
    return scheduling_policy.next_retry_at(
        now=now,
        policy=policy,
        attempt_count=attempt_count,
    )


# ---------------------------------------------------------------------------
# Storage / pinning helpers (delegations into ``scheduling_support``).
# ---------------------------------------------------------------------------


def _extract_storage_id(config_value: object) -> str | None:
    return scheduling_support.extract_storage_id(config_value)


def _storage_ids_available_on_node(*, node: str) -> set[str]:
    return scheduling_support.storage_ids_available_on_node(node=node)


def _detect_migration_pinned(
    *,
    node: str,
    vmid: int,
    resource_type: str,
) -> bool:
    return scheduling_support.detect_migration_pinned(
        node=node,
        vmid=vmid,
        resource_type=resource_type,
    )


def _migration_block_reason(
    *,
    source_node: str,
    target_node: str,
    vmid: int,
    resource_type: str,
) -> str | None:
    return scheduling_support.migration_block_reason(
        source_node=source_node,
        target_node=target_node,
        vmid=vmid,
        resource_type=resource_type,
    )


def _sync_request_migration_job(
    *,
    session: Session,
    request: VMRequest,
    source_node: str | None,
    now: datetime,
) -> None:
    scheduling_support.sync_request_migration_job(
        session=session,
        request=request,
        source_node=source_node,
        now=now,
    )


def _effective_request_migration_state(
    *,
    session: Session,
    request: VMRequest,
) -> tuple[VMMigrationStatus, str | None]:
    return scheduling_support.effective_request_migration_state(
        session=session,
        request=request,
    )


# ---------------------------------------------------------------------------
# Migration gate / record helpers
# ---------------------------------------------------------------------------


def _record_migration_gate_result(
    *,
    session: Session,
    request: VMRequest,
    current_node: str,
    desired_node: str,
    reason: str,
    now: datetime,
    job: VMMigrationJob | None = None,
    available_at: datetime | None = None,
) -> None:
    deferred = reason.startswith("Migration deferred")
    job_status = (
        VMMigrationJobStatus.pending if deferred else VMMigrationJobStatus.blocked
    )
    request_status = (
        VMMigrationStatus.pending if deferred else VMMigrationStatus.blocked
    )
    if job is not None:
        vm_migration_job_repo.update_job_status(
            session=session,
            job=job,
            status=job_status,
            last_error=reason,
            source_node=current_node,
            target_node=desired_node,
            vmid=request.vmid,
            finished_at=None if deferred else now,
            available_at=available_at,
            commit=False,
        )
    vm_request_repo.update_vm_request_provisioning(
        session=session,
        db_request=request,
        vmid=request.vmid,
        assigned_node=desired_node,
        desired_node=desired_node,
        actual_node=current_node,
        placement_strategy_used=request.placement_strategy_used,
        migration_status=request_status,
        migration_error=reason,
        rebalance_epoch=request.rebalance_epoch,
        last_rebalanced_at=request.last_rebalanced_at,
        last_migrated_at=request.last_migrated_at,
        commit=False,
    )


# ---------------------------------------------------------------------------
# Core migration execution
# ---------------------------------------------------------------------------


def _migrate_request_to_desired_node(
    *,
    session: Session,
    request: VMRequest,
    current_node: str,
    now: datetime,
    policy: _MigrationPolicy,
    migrations_used: int,
    job: VMMigrationJob | None = None,
) -> tuple[str, bool]:
    desired_node = str(request.desired_node or request.assigned_node or "")
    if not desired_node or desired_node == current_node:
        return current_node, False
    if request.vmid is None:
        raise NotFoundError(f"Request {request.id} has no provisioned VMID")
    if policy.max_per_rebalance <= migrations_used:
        defer_reason = (
            "Migration deferred because this rebalance window reached the migration budget."
        )
        _record_migration_gate_result(
            session=session,
            request=request,
            current_node=current_node,
            desired_node=desired_node,
            reason=defer_reason,
            now=now,
            job=job,
            available_at=now + timedelta(seconds=SCHEDULER_POLL_SECONDS),
        )
        return current_node, False
    precheck_reason = scheduling_support.migration_precheck_reason_for_request(
        request=request,
        current_node=current_node,
        target_node=desired_node,
        policy=policy,
        now=now,
    )
    if precheck_reason:
        available_at = None
        last_migrated_at = _normalize_datetime(request.last_migrated_at)
        if precheck_reason.startswith("Migration deferred because automatic migration is disabled"):
            available_at = now + timedelta(seconds=SCHEDULER_POLL_SECONDS)
        elif (
            precheck_reason.startswith("Migration deferred because this request was migrated too recently.")
            and last_migrated_at is not None
        ):
            available_at = last_migrated_at + timedelta(minutes=policy.min_interval_minutes)
        _record_migration_gate_result(
            session=session,
            request=request,
            current_node=current_node,
            desired_node=desired_node,
            reason=precheck_reason,
            now=now,
            job=job,
            available_at=available_at,
        )
        return current_node, False
    resource_type = _resource_type_for_request(request)
    current_status = proxmox_service.get_status(
        current_node,
        request.vmid,
        resource_type,
    )
    online = str(current_status.get("status") or "").lower() == "running"
    started_at = _utc_now()
    if job is not None:
        vm_migration_job_repo.update_job_status(
            session=session,
            job=job,
            status=VMMigrationJobStatus.running,
            last_error=None,
            attempt_delta=1,
            available_at=None,
            started_at=started_at,
            source_node=current_node,
            target_node=desired_node,
            vmid=request.vmid,
            commit=False,
        )
    vm_request_repo.update_vm_request_provisioning(
        session=session,
        db_request=request,
        vmid=request.vmid,
        assigned_node=desired_node,
        desired_node=desired_node,
        actual_node=current_node,
        placement_strategy_used=request.placement_strategy_used,
        migration_status=VMMigrationStatus.running,
        migration_error=None,
        rebalance_epoch=request.rebalance_epoch,
        last_rebalanced_at=request.last_rebalanced_at,
        last_migrated_at=request.last_migrated_at,
        commit=False,
    )
    claim_refresh_interval_seconds = max(
        5,
        min(
            max(int(policy.claim_timeout_seconds or 0) // 3, 5),
            30,
        ),
    )
    last_claim_refresh = time.monotonic()

    def _heartbeat(_: dict) -> None:
        nonlocal last_claim_refresh
        if job is None:
            return
        if (time.monotonic() - last_claim_refresh) < claim_refresh_interval_seconds:
            return
        vm_migration_job_repo.extend_job_claim(
            session=session,
            job=job,
            now=_utc_now(),
            claim_timeout_seconds=policy.claim_timeout_seconds,
            commit=True,
        )
        last_claim_refresh = time.monotonic()

    if job is not None:
        session.commit()
    proxmox_service.migrate_resource(
        current_node,
        desired_node,
        request.vmid,
        resource_type,
        online=online,
        progress_callback=_heartbeat if job is not None else None,
    )
    migrated_resource = proxmox_service.find_resource(request.vmid)
    new_actual_node = str(migrated_resource["node"])
    finished_at = _utc_now()
    if job is not None:
        vm_migration_job_repo.update_job_status(
            session=session,
            job=job,
            status=(
                VMMigrationJobStatus.completed
                if new_actual_node == desired_node
                else VMMigrationJobStatus.blocked
            ),
            last_error=(
                None
                if new_actual_node == desired_node
                else f"Migration finished on unexpected node {new_actual_node}"
            ),
            source_node=current_node,
            target_node=desired_node,
            vmid=request.vmid,
            finished_at=finished_at,
            commit=False,
        )
    vm_request_repo.update_vm_request_provisioning(
        session=session,
        db_request=request,
        vmid=request.vmid,
        assigned_node=desired_node,
        desired_node=desired_node,
        actual_node=new_actual_node,
        placement_strategy_used=request.placement_strategy_used,
        migration_status=(
            VMMigrationStatus.completed
            if new_actual_node == desired_node
            else VMMigrationStatus.blocked
        ),
        migration_error=(
            None
            if new_actual_node == desired_node
            else f"Migration finished on unexpected node {new_actual_node}"
        ),
        rebalance_epoch=request.rebalance_epoch,
        last_rebalanced_at=request.last_rebalanced_at,
        last_migrated_at=(
            finished_at if new_actual_node == desired_node else request.last_migrated_at
        ),
        commit=False,
    )
    audit_service.log_action(
        session=session,
        user_id=None,
        vmid=request.vmid,
        action="resource_migrate",
        details=(
            f"Auto-rebalanced request {request.id} from {current_node} "
            f"to {new_actual_node} for active time slot balancing"
        ),
        commit=False,
    )
    logger.info(
        "Migrated request %s VMID %s from %s to %s",
        request.id,
        request.vmid,
        current_node,
        new_actual_node,
    )
    return new_actual_node, new_actual_node == desired_node


def _process_claimed_migration_job(
    *,
    job_id: uuid.UUID,
    session_bind,
    now: datetime,
    policy: _MigrationPolicy,
    active_request_ids: set[uuid.UUID],
    migrations_used: int,
) -> bool:
    # Lazy import to avoid circular import: coordinator imports from this
    # module, and ``_refresh_actual_node`` lives in coordinator because it is
    # also used from the lifecycle path.
    from app.services.scheduling.coordinator import _refresh_actual_node

    with Session(session_bind) as worker_session:
        job = vm_migration_job_repo.get_job_by_id(
            session=worker_session,
            job_id=job_id,
        )
        if job is None:
            return False

        request = vm_request_repo.get_vm_request_by_id(
            session=worker_session,
            request_id=job.request_id,
            for_update=True,
        )
        if request is None:
            deleted_jobs = vm_migration_job_repo.delete_jobs_for_request(
                session=worker_session,
                request_id=job.request_id,
                commit=False,
            )
            logger.warning(
                "Deleted %s orphaned migration job(s) for missing request %s",
                deleted_jobs,
                job.request_id,
            )
            worker_session.commit()
            return False

        if request.id not in active_request_ids:
            vm_migration_job_repo.update_job_status(
                session=worker_session,
                job=job,
                status=VMMigrationJobStatus.cancelled,
                last_error="Migration queue entry was cancelled because the request is no longer active.",
                finished_at=now,
                commit=False,
            )
            worker_session.commit()
            return False

        try:
            actual_node, _ = _refresh_actual_node(
                session=worker_session,
                request=request,
            )
        except NotFoundError:
            stale_vmid = request.vmid
            vm_migration_job_repo.update_job_status(
                session=worker_session,
                job=job,
                status=VMMigrationJobStatus.failed,
                last_error=(
                    f"Migration queue entry failed because VMID {stale_vmid} is stale."
                ),
                attempt_delta=1,
                finished_at=now,
                available_at=None,
                commit=False,
            )
            vm_request_repo.clear_vm_request_provisioning(
                session=worker_session,
                db_request=request,
                commit=False,
            )
            worker_session.commit()
            return False

        desired_node = str(request.desired_node or request.assigned_node or "")
        if not desired_node or desired_node == actual_node:
            vm_migration_job_repo.update_job_status(
                session=worker_session,
                job=job,
                status=VMMigrationJobStatus.completed,
                last_error=None,
                source_node=actual_node,
                target_node=desired_node or actual_node,
                vmid=request.vmid,
                finished_at=now,
                available_at=None,
                commit=False,
            )
            vm_request_repo.update_vm_request_provisioning(
                session=worker_session,
                db_request=request,
                vmid=request.vmid,
                assigned_node=desired_node or actual_node,
                desired_node=desired_node or actual_node,
                actual_node=actual_node,
                placement_strategy_used=request.placement_strategy_used,
                migration_status=VMMigrationStatus.completed,
                migration_error=None,
                rebalance_epoch=request.rebalance_epoch,
                last_rebalanced_at=request.last_rebalanced_at,
                last_migrated_at=request.last_migrated_at,
                commit=False,
            )
            worker_session.commit()
            return False

        try:
            _, migrated = _migrate_request_to_desired_node(
                session=worker_session,
                request=request,
                current_node=actual_node,
                now=now,
                policy=policy,
                migrations_used=migrations_used,
                job=job,
            )
            worker_session.commit()
            return migrated
        except Exception as exc:
            worker_session.rollback()
            retry_session = worker_session
            request_id = request.id
            job = vm_migration_job_repo.get_job_by_id(
                session=retry_session,
                job_id=job_id,
            )
            request = vm_request_repo.get_vm_request_by_id(
                session=retry_session,
                request_id=request_id,
                for_update=True,
            )
            if job is None or request is None:
                logger.exception(
                    "Failed to process migration job %s and could not reload state",
                    job_id,
                )
                return False
            exceeded_retry_limit = int(job.attempt_count or 0) >= policy.retry_limit > 0
            new_status = (
                VMMigrationJobStatus.failed
                if exceeded_retry_limit
                else VMMigrationJobStatus.pending
            )
            vm_migration_job_repo.update_job_status(
                session=retry_session,
                job=job,
                status=new_status,
                last_error=str(exc)[:500],
                source_node=actual_node,
                target_node=desired_node,
                vmid=request.vmid,
                finished_at=now if new_status == VMMigrationJobStatus.failed else None,
                available_at=(
                    None
                    if new_status == VMMigrationJobStatus.failed
                    else _next_retry_at(
                        now=now,
                        policy=policy,
                        attempt_count=int(job.attempt_count or 0),
                    )
                ),
                commit=False,
            )
            vm_request_repo.update_vm_request_provisioning(
                session=retry_session,
                db_request=request,
                vmid=request.vmid,
                assigned_node=desired_node,
                desired_node=desired_node,
                actual_node=actual_node,
                placement_strategy_used=request.placement_strategy_used,
                migration_status=(
                    VMMigrationStatus.failed
                    if new_status == VMMigrationJobStatus.failed
                    else VMMigrationStatus.pending
                ),
                migration_error=str(exc)[:500],
                rebalance_epoch=request.rebalance_epoch,
                last_rebalanced_at=request.last_rebalanced_at,
                last_migrated_at=request.last_migrated_at,
                commit=False,
            )
            logger.exception(
                "Failed to process migration job %s for request %s",
                job.id,
                request.id,
            )
            retry_session.commit()
            return False


def _process_pending_migration_jobs(
    *,
    session: Session,
    now: datetime,
    policy: _MigrationPolicy,
    active_requests: list[VMRequest],
) -> int:
    request_ids = [request.id for request in active_requests]
    claimed_jobs = vm_migration_job_repo.claim_jobs_for_requests(
        session=session,
        request_ids=request_ids,
        worker_id=_migration_worker_id(),
        now=now,
        limit=policy.worker_concurrency,
        claim_timeout_seconds=policy.claim_timeout_seconds,
    )
    if not claimed_jobs:
        return 0
    session.commit()
    active_request_ids = {request.id for request in active_requests}
    session_bind = session.get_bind()
    migration_budget = max(int(policy.max_per_rebalance or 0), 0)
    job_specs = [
        {
            "job_id": job.id,
            "session_bind": session_bind,
            "migrations_used": 0 if index < migration_budget else migration_budget,
        }
        for index, job in enumerate(claimed_jobs)
    ]

    # Resolve the worker via the coordinator module so test monkey-patches
    # against ``coordinator._process_claimed_migration_job`` keep working.
    from app.services.scheduling import coordinator as _coord

    process_one = _coord._process_claimed_migration_job

    if len(job_specs) == 1 or policy.worker_concurrency <= 1:
        migrated_count = sum(
            1
            for spec in job_specs
            if process_one(
                job_id=spec["job_id"],
                session_bind=spec["session_bind"],
                now=now,
                policy=policy,
                active_request_ids=active_request_ids,
                migrations_used=spec["migrations_used"],
            )
        )
        session.expire_all()
        return migrated_count

    migrated_count = 0
    with ThreadPoolExecutor(
        max_workers=min(policy.worker_concurrency, len(job_specs))
    ) as executor:
        futures = [
            executor.submit(
                process_one,
                job_id=spec["job_id"],
                session_bind=spec["session_bind"],
                now=now,
                policy=policy,
                active_request_ids=active_request_ids,
                migrations_used=spec["migrations_used"],
            )
            for spec in job_specs
        ]
        for future in as_completed(futures):
            try:
                if future.result():
                    migrated_count += 1
            except Exception:
                logger.exception("Unexpected error while processing claimed migration job")
    session.expire_all()
    return migrated_count
