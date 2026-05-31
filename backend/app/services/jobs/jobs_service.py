"""聚合多個 Job 來源並正規化為 JobItem。

設計策略：
- 直接查 DB（in-memory union + 排序），來源資料量在合理範圍內（最近 N 天）。
- 非 admin：依 user_id 過濾。
- 排序：依 updated_at desc，提供 limit/offset 分頁。
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.models import (
    DeletionRequest,
    DeletionRequestStatus,
    ScriptDeployLog,
    SpecChangeRequest,
    SpecChangeRequestStatus,
    User,
    VMMigrationJob,
    VMMigrationJobStatus,
    VMMigrationStatus,
    VMRequest,
    VMRequestStatus,
)
from app.schemas.jobs import (
    ACTIVE_JOB_STATUSES,
    JobDetail,
    JobItem,
    JobKind,
    JobsListResponse,
    JobStatus,
)

logger = logging.getLogger(__name__)


# 預設只回傳「最近 N 天」的歷史 job，避免 union 後資料量爆炸。
_HISTORY_WINDOW_DAYS = 30
# 每個來源預先抓取的上限（避免一次拉太多）。
_PER_SOURCE_FETCH_LIMIT = 200


# ─── 狀態 mapping ─────────────────────────────────────────────────────────────

_MIGRATION_STATUS_MAP: dict[VMMigrationJobStatus, JobStatus] = {
    VMMigrationJobStatus.pending: JobStatus.pending,
    VMMigrationJobStatus.running: JobStatus.running,
    VMMigrationJobStatus.completed: JobStatus.completed,
    VMMigrationJobStatus.failed: JobStatus.failed,
    VMMigrationJobStatus.blocked: JobStatus.blocked,
    VMMigrationJobStatus.cancelled: JobStatus.cancelled,
}

_VM_REQUEST_STATUS_MAP: dict[VMRequestStatus, JobStatus] = {
    VMRequestStatus.pending: JobStatus.pending,
    VMRequestStatus.approved: JobStatus.pending,        # 已核准、等待派發
    VMRequestStatus.rejected: JobStatus.failed,
    VMRequestStatus.cancelled: JobStatus.cancelled,
}

_SPEC_CHANGE_STATUS_MAP: dict[SpecChangeRequestStatus, JobStatus] = {
    SpecChangeRequestStatus.pending: JobStatus.pending,
    SpecChangeRequestStatus.approved: JobStatus.completed,
    SpecChangeRequestStatus.rejected: JobStatus.failed,
}

_SCRIPT_DEPLOY_STATUS_MAP: dict[str, JobStatus] = {
    "pending": JobStatus.pending,
    "running": JobStatus.running,
    "completed": JobStatus.completed,
    "failed": JobStatus.failed,
    "cancelled": JobStatus.cancelled,
}

_DELETION_STATUS_MAP: dict[DeletionRequestStatus, JobStatus] = {
    DeletionRequestStatus.pending: JobStatus.pending,
    DeletionRequestStatus.running: JobStatus.running,
    DeletionRequestStatus.completed: JobStatus.completed,
    DeletionRequestStatus.failed: JobStatus.failed,
    DeletionRequestStatus.cancelled: JobStatus.cancelled,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


_PROGRESS_RE = re.compile(r"(\d{1,3})\s*%")


def _parse_progress(text: str | None) -> int | None:
    if not text:
        return None
    m = _PROGRESS_RE.search(text)
    if not m:
        return None
    try:
        v = int(m.group(1))
        return max(0, min(100, v))
    except ValueError:
        return None


# ─── 各來源 → JobItem ──────────────────────────────────────────────────────────


def _migration_to_job(job: VMMigrationJob, *, user_email: str | None = None,
                      user_id: uuid.UUID | None = None) -> JobItem:
    title_parts = []
    if job.vmid is not None:
        title_parts.append(f"VM {job.vmid}")
    title_parts.append(f"遷移 → {job.target_node}")
    title = " ".join(title_parts)

    status = _MIGRATION_STATUS_MAP.get(job.status, JobStatus.pending)
    progress: int | None = None
    if status == JobStatus.completed:
        progress = 100
    elif status == JobStatus.running:
        progress = 50  # 無細節進度，給個視覺值
    elif status == JobStatus.pending:
        progress = 0

    return JobItem(
        id=f"migration:{job.id}",
        kind=JobKind.migration,
        title=title,
        status=status,
        progress=progress,
        message=job.last_error,
        user_id=user_id,
        user_email=user_email,
        created_at=_coerce_aware(job.requested_at) or _now(),
        updated_at=_coerce_aware(job.updated_at) or _now(),
        completed_at=_coerce_aware(job.finished_at),
        detail_url=f"/jobs?focus=migration:{job.id}",
        meta={
            "request_id": str(job.request_id),
            "source_node": job.source_node,
            "target_node": job.target_node,
            "vmid": job.vmid,
            "attempt_count": job.attempt_count,
            "rebalance_epoch": job.rebalance_epoch,
        },
    )


def _script_deploy_to_job(log: ScriptDeployLog, *,
                          user_email: str | None = None) -> JobItem:
    name = log.template_name or log.template_slug
    target = log.hostname or (str(log.vmid) if log.vmid else "")
    title = f"部署 {name}" + (f" → {target}" if target else "")

    status = _SCRIPT_DEPLOY_STATUS_MAP.get(log.status.lower(), JobStatus.pending)
    progress = _parse_progress(log.progress)
    if progress is None:
        if status == JobStatus.completed:
            progress = 100
        elif status == JobStatus.running:
            progress = 50

    return JobItem(
        id=f"script_deploy:{log.task_id}",
        kind=JobKind.script_deploy,
        title=title,
        status=status,
        progress=progress,
        message=log.message or log.error,
        user_id=log.user_id,
        user_email=user_email,
        created_at=_coerce_aware(log.created_at) or _now(),
        updated_at=_coerce_aware(log.updated_at) or _now(),
        completed_at=_coerce_aware(log.completed_at),
        detail_url=f"/jobs?focus=script_deploy:{log.task_id}",
        meta={
            "task_id": log.task_id,
            "vmid": log.vmid,
            "template_slug": log.template_slug,
            "template_name": log.template_name,
            "hostname": log.hostname,
        },
    )


def _vm_request_to_job(req: VMRequest) -> JobItem:
    user_email = req.user.email if req.user else None
    title = f"開機申請：{req.hostname}（{req.cores} cores / {req.memory} MB）"
    status = _VM_REQUEST_STATUS_MAP.get(req.status, JobStatus.pending)
    if req.status == VMRequestStatus.approved:
        if req.migration_status == VMMigrationStatus.failed or req.migration_error:
            status = JobStatus.failed
        elif req.vmid is not None:
            status = JobStatus.completed
        elif req.migration_status == VMMigrationStatus.running:
            status = JobStatus.running
    progress: int | None = None
    if status == JobStatus.completed:
        progress = 100
    elif status == JobStatus.running:
        progress = 60
    elif status == JobStatus.pending:
        progress = 0

    # 排程超時判斷：start_at 已過但仍在 pending / approved（尚未進入 provisioning）
    overdue = False
    overdue_minutes: int | None = None
    if (
        req.status in (VMRequestStatus.pending, VMRequestStatus.approved)
        and req.start_at is not None
    ):
        start_at_aware = _coerce_aware(req.start_at)
        if start_at_aware is not None:
            delta = (_now() - start_at_aware).total_seconds()
            if delta > 0:
                overdue = True
                overdue_minutes = int(delta // 60)

    base_message = req.review_comment or req.migration_error
    if overdue:
        overdue_label = (
            f"{overdue_minutes // 60} 小時"
            if overdue_minutes and overdue_minutes >= 60
            else f"{overdue_minutes or 0} 分鐘"
        )
        overdue_msg = f"排程開機時間已超時 {overdue_label}，仍未開始建立"
        message = f"{base_message}\n{overdue_msg}" if base_message else overdue_msg
    else:
        message = base_message

    return JobItem(
        id=f"vm_request:{req.id}",
        kind=JobKind.vm_request,
        title=title,
        status=status,
        progress=progress,
        message=message,
        user_id=req.user_id,
        user_email=user_email,
        created_at=_coerce_aware(req.created_at) or _now(),
        updated_at=_coerce_aware(req.reviewed_at) or _coerce_aware(req.created_at) or _now(),
        completed_at=_coerce_aware(req.reviewed_at) if status in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled} else None,
        detail_url=f"/approvals/{req.id}",
        meta={
            "vmid": req.vmid,
            "hostname": req.hostname,
            "raw_status": req.status.value,
            "resource_type": req.resource_type,
            "start_at": _isoformat(req.start_at),
            "overdue": overdue,
            "overdue_minutes": overdue_minutes,
        },
    )


def _spec_change_to_job(req: SpecChangeRequest) -> JobItem:
    user_email = req.user.email if req.user else None
    title = f"規格變更：VMID {req.vmid}（{req.change_type.value}）"
    status = _SPEC_CHANGE_STATUS_MAP.get(req.status, JobStatus.pending)
    progress = 100 if status == JobStatus.completed else (0 if status == JobStatus.pending else None)

    return JobItem(
        id=f"spec_change:{req.id}",
        kind=JobKind.spec_change,
        title=title,
        status=status,
        progress=progress,
        message=req.review_comment or req.reason,
        user_id=req.user_id,
        user_email=user_email,
        created_at=_coerce_aware(req.created_at) or _now(),
        updated_at=_coerce_aware(req.reviewed_at) or _coerce_aware(req.created_at) or _now(),
        completed_at=_coerce_aware(req.reviewed_at) if status in {JobStatus.completed, JobStatus.failed} else None,
        detail_url=f"/approvals/{req.id}",
        meta={
            "vmid": req.vmid,
            "raw_status": req.status.value,
            "change_type": req.change_type.value,
        },
    )


# ─── 來源查詢（已根據 user 過濾） ────────────────────────────────────────────


def _fetch_migration_jobs(
    session: Session, *, user: User, since: datetime
) -> list[JobItem]:
    is_admin = bool(user.is_superuser or getattr(user, "role", None) == "admin")
    stmt = (
        select(VMMigrationJob, VMRequest, User)
        .join(VMRequest, VMRequest.id == VMMigrationJob.request_id)
        .join(User, User.id == VMRequest.user_id)
        .where(VMMigrationJob.updated_at >= since)
    )
    if not is_admin:
        stmt = stmt.where(VMRequest.user_id == user.id)
    stmt = stmt.order_by(VMMigrationJob.updated_at.desc()).limit(_PER_SOURCE_FETCH_LIMIT)
    rows = session.exec(stmt).all()
    return [
        _migration_to_job(job, user_email=u.email, user_id=u.id)
        for (job, _req, u) in rows
    ]


def _fetch_script_deploy(
    session: Session, *, user: User, since: datetime
) -> list[JobItem]:
    is_admin = bool(user.is_superuser or getattr(user, "role", None) == "admin")
    stmt = select(ScriptDeployLog).where(ScriptDeployLog.updated_at >= since)
    if not is_admin:
        stmt = stmt.where(ScriptDeployLog.user_id == user.id)
    stmt = stmt.order_by(ScriptDeployLog.updated_at.desc()).limit(_PER_SOURCE_FETCH_LIMIT)
    rows = list(session.exec(stmt).all())

    # 取使用者 email
    user_ids = {r.user_id for r in rows if r.user_id is not None}
    email_map: dict[uuid.UUID, str] = {}
    if user_ids:
        users = session.exec(select(User).where(User.id.in_(user_ids))).all()
        email_map = {u.id: u.email for u in users}

    return [_script_deploy_to_job(r, user_email=email_map.get(r.user_id) if r.user_id else None) for r in rows]


def _fetch_vm_requests(
    session: Session, *, user: User, since: datetime
) -> list[JobItem]:
    """只回傳「進行中」與「最近結束」的 VM Request（避免 my-resources 整個倒灌進來）。"""
    is_admin = bool(user.is_superuser or getattr(user, "role", None) == "admin")
    stmt = (
        select(VMRequest)
        .options(selectinload(VMRequest.user))
        .where(VMRequest.created_at >= since)
    )
    if not is_admin:
        stmt = stmt.where(VMRequest.user_id == user.id)
    stmt = stmt.order_by(VMRequest.created_at.desc()).limit(_PER_SOURCE_FETCH_LIMIT)
    rows = session.exec(stmt).all()
    return [_vm_request_to_job(r) for r in rows]


def _fetch_spec_changes(
    session: Session, *, user: User, since: datetime
) -> list[JobItem]:
    is_admin = bool(user.is_superuser or getattr(user, "role", None) == "admin")
    stmt = (
        select(SpecChangeRequest)
        .options(selectinload(SpecChangeRequest.user))
        .where(SpecChangeRequest.created_at >= since)
    )
    if not is_admin:
        stmt = stmt.where(SpecChangeRequest.user_id == user.id)
    stmt = stmt.order_by(SpecChangeRequest.created_at.desc()).limit(_PER_SOURCE_FETCH_LIMIT)
    rows = session.exec(stmt).all()
    return [_spec_change_to_job(r) for r in rows]


def _deletion_to_job(req: DeletionRequest, *, user_email: str | None = None) -> JobItem:
    name = req.name or f"VMID {req.vmid}"
    title = f"刪除 {name}"
    status = _DELETION_STATUS_MAP.get(req.status, JobStatus.pending)
    progress: int | None
    if status == JobStatus.completed:
        progress = 100
    elif status == JobStatus.running:
        progress = 50
    elif status == JobStatus.pending:
        progress = 0
    else:
        progress = None
    updated = (
        _coerce_aware(req.completed_at)
        or _coerce_aware(req.started_at)
        or _coerce_aware(req.created_at)
        or _now()
    )
    return JobItem(
        id=f"deletion:{req.id}",
        kind=JobKind.deletion,
        title=title,
        status=status,
        progress=progress,
        message=req.error_message,
        user_id=req.user_id,
        user_email=user_email,
        created_at=_coerce_aware(req.created_at) or _now(),
        updated_at=updated,
        completed_at=_coerce_aware(req.completed_at)
        if status in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}
        else None,
        detail_url=f"/jobs?focus=deletion:{req.id}",
        meta={
            "vmid": req.vmid,
            "node": req.node,
            "resource_type": req.resource_type,
            "raw_status": req.status.value,
            "purge": req.purge,
            "force": req.force,
        },
    )


def _fetch_deletions(
    session: Session, *, user: User, since: datetime
) -> list[JobItem]:
    is_admin = bool(user.is_superuser or getattr(user, "role", None) == "admin")
    stmt = (
        select(DeletionRequest)
        .options(selectinload(DeletionRequest.user))
        .where(DeletionRequest.created_at >= since)
    )
    if not is_admin:
        stmt = stmt.where(DeletionRequest.user_id == user.id)
    stmt = stmt.order_by(DeletionRequest.created_at.desc()).limit(_PER_SOURCE_FETCH_LIMIT)
    rows = session.exec(stmt).all()
    return [
        _deletion_to_job(r, user_email=r.user.email if r.user else None)
        for r in rows
    ]


_FETCHERS = {
    JobKind.migration: _fetch_migration_jobs,
    JobKind.script_deploy: _fetch_script_deploy,
    JobKind.vm_request: _fetch_vm_requests,
    JobKind.spec_change: _fetch_spec_changes,
    JobKind.deletion: _fetch_deletions,
}


# ─── Public API ───────────────────────────────────────────────────────────────


def _aggregate_jobs(
    *,
    session: Session,
    user: User,
    kinds: Iterable[JobKind] | None,
    since: datetime,
) -> list[JobItem]:
    selected = list(kinds) if kinds else list(JobKind)
    items: list[JobItem] = []
    for kind in selected:
        fetcher = _FETCHERS.get(kind)
        if fetcher is None:
            continue
        try:
            items.extend(fetcher(session, user=user, since=since))
        except Exception as exc:  # noqa: BLE001 — 單一來源失敗不應拖垮整個查詢
            session.rollback()
            logger.exception("fetch jobs for kind=%s failed: %s", kind.value, exc)
    items.sort(key=lambda j: j.updated_at, reverse=True)
    return items


def list_jobs(
    *,
    session: Session,
    user: User,
    kinds: Iterable[JobKind] | None = None,
    statuses: Iterable[JobStatus] | None = None,
    active_only: bool = False,
    limit: int = 50,
    offset: int = 0,
    history_days: int = _HISTORY_WINDOW_DAYS,
) -> JobsListResponse:
    since = _now() - timedelta(days=history_days)
    all_items = _aggregate_jobs(session=session, user=user, kinds=kinds, since=since)

    active_count = sum(1 for j in all_items if j.status in ACTIVE_JOB_STATUSES)

    filtered = all_items
    if active_only:
        filtered = [j for j in filtered if j.status in ACTIVE_JOB_STATUSES]
    elif statuses:
        wanted = set(statuses)
        filtered = [j for j in filtered if j.status in wanted]

    total = len(filtered)
    page = filtered[offset : offset + limit]
    return JobsListResponse(items=page, total=total, active_count=active_count)


def list_recent_for_user(
    *,
    session: Session,
    user: User,
    limit: int = 5,
) -> JobsListResponse:
    """提供 banner popover 用：active 優先排在最上方，再補最近的歷史任務直到 limit。"""
    since = _now() - timedelta(days=_HISTORY_WINDOW_DAYS)
    all_items = _aggregate_jobs(session=session, user=user, kinds=None, since=since)
    active_count = sum(1 for j in all_items if j.status in ACTIVE_JOB_STATUSES)

    actives = [j for j in all_items if j.status in ACTIVE_JOB_STATUSES]
    others = [j for j in all_items if j.status not in ACTIVE_JOB_STATUSES]
    # active 全部納入（即使超過 limit，也要全部讓 user 看到正在跑的）；
    # 剩餘空間再補最近的歷史，但不少於 limit 筆總量
    page = actives + others[: max(0, limit - len(actives))]
    return JobsListResponse(items=page, total=len(all_items), active_count=active_count)


# ─── Detail (單筆) ────────────────────────────────────────────────────────────


class JobNotFoundError(Exception):
    pass


class JobAccessDeniedError(Exception):
    pass


def _isoformat(dt: datetime | None) -> str | None:
    aware = _coerce_aware(dt)
    return aware.isoformat() if aware else None


def _is_admin(user: User) -> bool:
    return bool(user.is_superuser or getattr(user, "role", None) == "admin")


def _ensure_owner_or_admin(user: User, owner_id: uuid.UUID | None) -> None:
    if _is_admin(user):
        return
    if owner_id is None or owner_id != user.id:
        raise JobAccessDeniedError("Not allowed to view this job")


def _detail_migration(session: Session, raw_id: str, user: User) -> JobDetail:
    try:
        job_uuid = uuid.UUID(raw_id)
    except ValueError as e:
        raise JobNotFoundError(f"invalid migration id {raw_id}") from e
    job = session.get(VMMigrationJob, job_uuid)
    if job is None:
        raise JobNotFoundError("migration job not found")
    req = session.get(VMRequest, job.request_id)
    owner_email: str | None = None
    owner_id: uuid.UUID | None = None
    hostname: str | None = None
    if req is not None:
        owner_id = req.user_id
        hostname = req.hostname
        if req.user is not None:
            owner_email = req.user.email
    _ensure_owner_or_admin(user, owner_id)
    item = _migration_to_job(job, user_email=owner_email, user_id=owner_id)
    extra = {
        "request_id": str(job.request_id),
        "vmid": job.vmid,
        "source_node": job.source_node,
        "target_node": job.target_node,
        "attempt_count": job.attempt_count,
        "rebalance_epoch": job.rebalance_epoch,
        "claimed_by": job.claimed_by,
        "requested_at": _isoformat(job.requested_at),
        "available_at": _isoformat(job.available_at),
        "claimed_at": _isoformat(job.claimed_at),
        "started_at": _isoformat(job.started_at),
        "finished_at": _isoformat(job.finished_at),
        "hostname": hostname,
    }
    return JobDetail(item=item, error=job.last_error, extra=extra)


def _detail_script_deploy(session: Session, raw_id: str, user: User) -> JobDetail:
    log = session.exec(
        select(ScriptDeployLog).where(ScriptDeployLog.task_id == raw_id)
    ).first()
    if log is None:
        raise JobNotFoundError("script deploy log not found")
    _ensure_owner_or_admin(user, log.user_id)
    owner_email: str | None = None
    if log.user_id is not None:
        owner = session.get(User, log.user_id)
        if owner is not None:
            owner_email = owner.email
    item = _script_deploy_to_job(log, user_email=owner_email)
    extra = {
        "task_id": log.task_id,
        "vmid": log.vmid,
        "template_slug": log.template_slug,
        "template_name": log.template_name,
        "script_path": log.script_path,
        "hostname": log.hostname,
        "raw_status": log.status,
        "progress_text": log.progress,
    }
    return JobDetail(item=item, output=log.output, error=log.error, extra=extra)


def _detail_vm_request(session: Session, raw_id: str, user: User) -> JobDetail:
    try:
        req_uuid = uuid.UUID(raw_id)
    except ValueError as e:
        raise JobNotFoundError(f"invalid vm_request id {raw_id}") from e
    req = session.exec(
        select(VMRequest).options(selectinload(VMRequest.user)).where(VMRequest.id == req_uuid)
    ).first()
    if req is None:
        raise JobNotFoundError("vm request not found")
    _ensure_owner_or_admin(user, req.user_id)
    item = _vm_request_to_job(req)
    extra = {
        "vmid": req.vmid,
        "hostname": req.hostname,
        "resource_type": req.resource_type,
        "raw_status": req.status.value,
        "cores": req.cores,
        "memory": req.memory,
        "storage": req.storage,
        "disk_size": req.disk_size,
        "rootfs_size": req.rootfs_size,
        "ostemplate": req.ostemplate,
        "template_id": req.template_id,
        "service_template_slug": req.service_template_slug,
        "assigned_node": req.assigned_node,
        "actual_node": req.actual_node,
        "desired_node": req.desired_node,
        "migration_status": req.migration_status.value,
        "expiry_date": req.expiry_date.isoformat() if req.expiry_date else None,
        "start_at": _isoformat(req.start_at),
        "end_at": _isoformat(req.end_at),
        "reason": req.reason,
        "review_comment": req.review_comment,
    }
    return JobDetail(item=item, error=req.migration_error, extra=extra)


def _detail_spec_change(session: Session, raw_id: str, user: User) -> JobDetail:
    try:
        sc_uuid = uuid.UUID(raw_id)
    except ValueError as e:
        raise JobNotFoundError(f"invalid spec_change id {raw_id}") from e
    req = session.exec(
        select(SpecChangeRequest)
        .options(selectinload(SpecChangeRequest.user))
        .where(SpecChangeRequest.id == sc_uuid)
    ).first()
    if req is None:
        raise JobNotFoundError("spec change not found")
    _ensure_owner_or_admin(user, req.user_id)
    item = _spec_change_to_job(req)
    extra = {
        "vmid": req.vmid,
        "change_type": req.change_type.value,
        "raw_status": req.status.value,
        "current_cpu": req.current_cpu,
        "current_memory": req.current_memory,
        "current_disk": req.current_disk,
        "requested_cpu": req.requested_cpu,
        "requested_memory": req.requested_memory,
        "requested_disk": req.requested_disk,
        "reason": req.reason,
        "review_comment": req.review_comment,
        "applied_at": _isoformat(req.applied_at),
    }
    return JobDetail(item=item, error=None, extra=extra)


def _detail_deletion(session: Session, raw_id: str, user: User) -> JobDetail:
    try:
        del_uuid = uuid.UUID(raw_id)
    except ValueError as e:
        raise JobNotFoundError(f"invalid deletion id {raw_id}") from e
    req = session.exec(
        select(DeletionRequest)
        .options(selectinload(DeletionRequest.user))
        .where(DeletionRequest.id == del_uuid)
    ).first()
    if req is None:
        raise JobNotFoundError("deletion request not found")
    _ensure_owner_or_admin(user, req.user_id)
    item = _deletion_to_job(req, user_email=req.user.email if req.user else None)
    extra = {
        "vmid": req.vmid,
        "node": req.node,
        "name": req.name,
        "resource_type": req.resource_type,
        "purge": req.purge,
        "force": req.force,
        "raw_status": req.status.value,
        "started_at": _isoformat(req.started_at),
        "completed_at": _isoformat(req.completed_at),
    }
    return JobDetail(item=item, error=req.error_message, extra=extra)


_DETAIL_FETCHERS = {
    JobKind.migration: _detail_migration,
    JobKind.script_deploy: _detail_script_deploy,
    JobKind.vm_request: _detail_vm_request,
    JobKind.spec_change: _detail_spec_change,
    JobKind.deletion: _detail_deletion,
}


def get_job_detail(*, session: Session, user: User, job_id: str) -> JobDetail:
    """job_id 格式：<kind>:<source_id>。"""
    if ":" not in job_id:
        raise JobNotFoundError(f"invalid job id {job_id}")
    kind_str, _, raw_id = job_id.partition(":")
    try:
        kind = JobKind(kind_str)
    except ValueError as e:
        raise JobNotFoundError(f"unknown kind {kind_str}") from e
    fetcher = _DETAIL_FETCHERS[kind]
    return fetcher(session, raw_id, user)
