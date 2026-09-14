"""聚合多個 Job 來源並正規化為 JobItem。

設計策略：
- 直接查 DB（in-memory union + 排序），來源資料量在合理範圍內（最近 N 天）。
- 非 admin：依 user_id 過濾。
- 排序：依 updated_at desc，提供 limit/offset 分頁。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.models import (
    DeletionRequest,
    DeletionRequestStatus,
    SpecChangeRequest,
    SpecChangeRequestStatus,
    TaskRecord,
    TaskRecordStatus,
    User,
    VMProvisioningStatus,
    VMRequest,
    VMRequestStatus,
    VMTemplate,
)
from app.schemas.jobs import (
    ACTIVE_JOB_STATUSES,
    JobDetail,
    JobItem,
    JobKind,
    JobsListResponse,
    JobStatus,
)
from app.services.resource.resource_service import (
    RESOURCE_CONVERTED_TO_TEMPLATE_MARKER,
    RESOURCE_DELETED_BY_USER_MARKER,
    RESOURCE_DELETED_ORPHAN_MARKER,
)

logger = logging.getLogger(__name__)


# 預設只回傳「最近 N 天」的歷史 job，避免 union 後資料量爆炸。
_HISTORY_WINDOW_DAYS = 30
# 每個來源預先抓取的上限（避免一次拉太多）。
_PER_SOURCE_FETCH_LIMIT = 200


# ─── 狀態 mapping ─────────────────────────────────────────────────────────────

_VM_REQUEST_STATUS_MAP: dict[VMRequestStatus, JobStatus] = {
    VMRequestStatus.pending: JobStatus.pending,
    VMRequestStatus.approved: JobStatus.pending,        # 已核准、等待派發
    VMRequestStatus.rejected: JobStatus.failed,
    VMRequestStatus.cancelled: JobStatus.cancelled,
    VMRequestStatus.expired: JobStatus.cancelled,       # 時段過完沒人審，失效
}

def _spec_change_job_status(req: SpecChangeRequest) -> tuple[JobStatus, str | None]:
    """規格變更的正規化狀態與補充訊息。

    核准不再等於完成：規格要等申請人按「套用」、背景任務關機改完開機後
    才寫 applied_at。
    """
    if req.status == SpecChangeRequestStatus.pending:
        return JobStatus.pending, None
    if req.status == SpecChangeRequestStatus.rejected:
        return JobStatus.failed, None
    if req.status == SpecChangeRequestStatus.cancelled:
        return JobStatus.cancelled, None
    if req.applied_at is not None:
        return JobStatus.completed, req.apply_error  # 成功但可能帶警告（自動開機失敗）
    if req.apply_error:
        return JobStatus.failed, req.apply_error
    if req.apply_started_at is not None:
        return JobStatus.running, "套用中：關機 → 改規格 → 開機"
    return JobStatus.blocked, "已核准，等待申請人按「套用」"

_TEMPLATE_TASK_STATUS_MAP: dict[TaskRecordStatus, JobStatus] = {
    TaskRecordStatus.queued: JobStatus.pending,
    TaskRecordStatus.running: JobStatus.running,
    TaskRecordStatus.succeeded: JobStatus.completed,
    TaskRecordStatus.failed: JobStatus.failed,
}

# 與前端 TEMPLATE_TASK_LABEL 對齊
_TEMPLATE_TASK_TYPE_LABEL: dict[str, str] = {
    "template.convert": "轉換範本",
    "template.delete": "刪除範本",
    "template.update_clone": "更新循環：建立暫存母機",
    "template.update_convert": "更新循環：轉換新版",
    "template.update_cancel": "更新循環：取消",
    "template.clone": "克隆開通",
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


# ─── 各來源 → JobItem ──────────────────────────────────────────────────────────


# 申請單被「消耗」（資源刪除 / 轉範本）時 mark_linked_request_consumed 會把
# marker 寫進 provisioning_error / review_comment 並標 failed（讓排程器停手）。
# 對使用者而言這不是失敗——機器曾成功開通，只是後續生命週期把申請單結束掉，
# 因此顯示層轉譯成「已結案」而非紅色錯誤。
_CONSUMED_REQUEST_MESSAGES = {
    RESOURCE_CONVERTED_TO_TEMPLATE_MARKER: "母機已轉為範本，申請單已結案",
    RESOURCE_DELETED_BY_USER_MARKER: "資源已由使用者刪除，申請單已結案",
    RESOURCE_DELETED_ORPHAN_MARKER: "資源紀錄已清理（PVE 端已不存在），申請單已結案",
}


def _consumed_request_message(req: VMRequest) -> str | None:
    return _CONSUMED_REQUEST_MESSAGES.get(
        req.provisioning_error or ""
    ) or _CONSUMED_REQUEST_MESSAGES.get(req.review_comment or "")


def _vm_request_to_job(req: VMRequest) -> JobItem:
    user_email = req.user.email if req.user else None
    title = f"開機申請：{req.hostname}（{req.cores} cores / {req.memory} MB）"
    status = _VM_REQUEST_STATUS_MAP.get(req.status, JobStatus.pending)
    consumed_message = _consumed_request_message(req)
    if req.status == VMRequestStatus.approved:
        if consumed_message:
            status = JobStatus.completed
        elif req.provisioning_status == VMProvisioningStatus.failed or req.provisioning_error:
            status = JobStatus.failed
        elif req.vmid is not None:
            status = JobStatus.completed
        elif req.provisioning_status == VMProvisioningStatus.running:
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
        consumed_message is None
        and req.status in (VMRequestStatus.pending, VMRequestStatus.approved)
        and req.start_at is not None
    ):
        start_at_aware = _coerce_aware(req.start_at)
        if start_at_aware is not None:
            delta = (_now() - start_at_aware).total_seconds()
            if delta > 0:
                overdue = True
                overdue_minutes = int(delta // 60)

    base_message = (
        consumed_message or req.review_comment or req.provisioning_error
    )
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
            "consumed": consumed_message is not None,
        },
    )


def _spec_change_to_job(req: SpecChangeRequest) -> JobItem:
    user_email = req.user.email if req.user else None
    title = f"規格變更：VMID {req.vmid}（{req.change_type.value}）"
    status, status_message = _spec_change_job_status(req)
    progress = 100 if status == JobStatus.completed else (0 if status == JobStatus.pending else None)
    finished_at = (
        req.applied_at
        if status == JobStatus.completed
        else req.reviewed_at if status in {JobStatus.failed, JobStatus.cancelled} else None
    )
    last_touched = req.applied_at or req.apply_started_at or req.reviewed_at or req.created_at

    return JobItem(
        id=f"spec_change:{req.id}",
        kind=JobKind.spec_change,
        title=title,
        status=status,
        progress=progress,
        message=status_message or req.review_comment or req.reason,
        user_id=req.user_id,
        user_email=user_email,
        created_at=_coerce_aware(req.created_at) or _now(),
        updated_at=_coerce_aware(last_touched) or _now(),
        completed_at=_coerce_aware(finished_at) if finished_at else None,
        detail_url=f"/approvals/{req.id}",
        meta={
            "vmid": req.vmid,
            "raw_status": req.status.value,
            "change_type": req.change_type.value,
        },
    )


# ─── 來源查詢（已根據 user 過濾） ────────────────────────────────────────────


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


def _parse_json(text: str | None) -> dict:
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _template_task_to_job(
    record: TaskRecord,
    *,
    user_email: str | None = None,
    template_name: str | None = None,
) -> JobItem:
    payload = _parse_json(record.payload)
    label = _TEMPLATE_TASK_TYPE_LABEL.get(record.task_type, record.task_type)
    # 目標：克隆任務顯示新主機名，其餘顯示範本名（範本已刪除時退回 VMID）
    target = (
        payload.get("hostname")
        if record.task_type == "template.clone"
        else template_name
    ) or (f"VMID {payload['pve_vmid']}" if payload.get("pve_vmid") else None)
    title = f"{label}：{target}" if target else label

    status = _TEMPLATE_TASK_STATUS_MAP.get(record.status, JobStatus.pending)
    progress = 100 if status == JobStatus.completed else record.progress

    updated = (
        _coerce_aware(record.finished_at)
        or _coerce_aware(record.started_at)
        or _coerce_aware(record.created_at)
        or _now()
    )
    return JobItem(
        id=f"template:{record.id}",
        kind=JobKind.template,
        title=title,
        status=status,
        progress=progress,
        message=record.error,
        user_id=record.user_id,
        user_email=user_email,
        created_at=_coerce_aware(record.created_at) or _now(),
        updated_at=updated,
        completed_at=_coerce_aware(record.finished_at),
        detail_url=f"/jobs?focus=template:{record.id}",
        meta={
            "task_type": record.task_type,
            "template_id": str(record.template_id) if record.template_id else None,
            "template_name": template_name,
            "resource_vmid": record.resource_vmid,
            "hostname": payload.get("hostname"),
        },
    )


def _template_name_map(
    session: Session, records: Iterable[TaskRecord]
) -> dict[uuid.UUID, str]:
    ids = {r.template_id for r in records if r.template_id is not None}
    if not ids:
        return {}
    rows = session.exec(select(VMTemplate).where(VMTemplate.id.in_(ids))).all()
    return {t.id: t.name for t in rows}


def _fetch_template_tasks(
    session: Session, *, user: User, since: datetime
) -> list[JobItem]:
    is_admin = bool(user.is_superuser or getattr(user, "role", None) == "admin")
    stmt = select(TaskRecord).where(TaskRecord.created_at >= since)
    if not is_admin:
        stmt = stmt.where(TaskRecord.user_id == user.id)
    stmt = stmt.order_by(TaskRecord.created_at.desc()).limit(_PER_SOURCE_FETCH_LIMIT)
    rows = list(session.exec(stmt).all())

    user_ids = {r.user_id for r in rows}
    email_map: dict[uuid.UUID, str] = {}
    if user_ids:
        users = session.exec(select(User).where(User.id.in_(user_ids))).all()
        email_map = {u.id: u.email for u in users}
    name_map = _template_name_map(session, rows)

    return [
        _template_task_to_job(
            r,
            user_email=email_map.get(r.user_id),
            template_name=name_map.get(r.template_id) if r.template_id else None,
        )
        for r in rows
    ]


_FETCHERS = {
    JobKind.vm_request: _fetch_vm_requests,
    JobKind.spec_change: _fetch_spec_changes,
    JobKind.deletion: _fetch_deletions,
    JobKind.template: _fetch_template_tasks,
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
        "assigned_node": req.assigned_node,
        "actual_node": req.actual_node,
        "desired_node": req.desired_node,
        "provisioning_status": req.provisioning_status.value,
        "expiry_date": req.expiry_date.isoformat() if req.expiry_date else None,
        "start_at": _isoformat(req.start_at),
        "end_at": _isoformat(req.end_at),
        "reason": req.reason,
        "review_comment": req.review_comment,
    }
    return JobDetail(
        item=item,
        error=(
            None
            if _consumed_request_message(req)
            else req.provisioning_error
        ),
        extra=extra,
    )


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


def _detail_template_task(session: Session, raw_id: str, user: User) -> JobDetail:
    try:
        task_uuid = uuid.UUID(raw_id)
    except ValueError as e:
        raise JobNotFoundError(f"invalid template task id {raw_id}") from e
    record = session.get(TaskRecord, task_uuid)
    if record is None:
        raise JobNotFoundError("template task not found")
    _ensure_owner_or_admin(user, record.user_id)

    owner = session.get(User, record.user_id)
    template_name: str | None = None
    if record.template_id is not None:
        template = session.get(VMTemplate, record.template_id)
        if template is not None:
            template_name = template.name

    item = _template_task_to_job(
        record,
        user_email=owner.email if owner else None,
        template_name=template_name,
    )
    extra = {
        "task_type": record.task_type,
        "template_id": str(record.template_id) if record.template_id else None,
        "template_name": template_name,
        "raw_status": record.status.value,
        "resource_vmid": record.resource_vmid,
        "payload": _parse_json(record.payload),
        "result": _parse_json(record.result),
        "started_at": _isoformat(record.started_at),
        "finished_at": _isoformat(record.finished_at),
    }
    return JobDetail(item=item, error=record.error, extra=extra)


_DETAIL_FETCHERS = {
    JobKind.vm_request: _detail_vm_request,
    JobKind.spec_change: _detail_spec_change,
    JobKind.deletion: _detail_deletion,
    JobKind.template: _detail_template_task,
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
