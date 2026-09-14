"""範本生命週期服務：建立（VM→範本）、更新循環（Clone→Modify→Convert）、刪除。

耗時的 PVE 操作一律經由 arq 隊列（enqueue_task），本模組僅做
權限/狀態校驗、DB 讀寫與任務入列；PVE 細節在 tasks.py 的 handler。
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func as sa_func
from sqlmodel import Session, col, select

from app.core.db import engine
from app.core.i18n import t
from app.core.permissions import is_admin
from app.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.infrastructure.proxmox import get_proxmox_settings_for_node
from app.infrastructure.proxmox import operations as proxmox_ops
from app.infrastructure.queue import enqueue_task, report_progress
from app.models import (
    CourseEnvironment,
    CourseEnvironmentNode,
    CourseEnvironmentVersion,
    Resource,
    TaskRecord,
    TaskRecordStatus,
    TemplateAttachment,
    User,
    VMTemplate,
    VMTemplateStatus,
)
from app.repositories import task_record as task_record_repo
from app.repositories import vm_template as template_repo
from app.schemas.template import (
    TemplateCatalogItem,
    VMTemplateCreate,
    VMTemplatePublic,
    VMTemplateUpdate,
)
from app.services.template import template_files

logger = logging.getLogger(__name__)

TASK_CONVERT = "template.convert"
TASK_DELETE = "template.delete"
TASK_UPDATE_CLONE = "template.update_clone"
TASK_UPDATE_CONVERT = "template.update_convert"
TASK_UPDATE_CANCEL = "template.update_cancel"


# ---------------------------------------------------------------------------
# 查詢
# ---------------------------------------------------------------------------

def _to_public(
    template: VMTemplate,
    *,
    pve_vmids: set[int] | None = None,
    attachment_count: int | None = None,
) -> VMTemplatePublic:
    public = VMTemplatePublic.model_validate(template)
    if pve_vmids is not None:
        public.pve_exists = template.pve_vmid in pve_vmids
    if attachment_count is not None:
        public.attachment_count = attachment_count
    return public


def _attachment_counts(
    session: Session, template_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """各範本的附件數（一次 group by；失敗回空 dict 不擋列表）。"""
    if not template_ids:
        return {}
    try:
        rows = session.exec(
            select(
                TemplateAttachment.template_id,
                sa_func.count(col(TemplateAttachment.id)),
            )
            .where(col(TemplateAttachment.template_id).in_(template_ids))
            .group_by(col(TemplateAttachment.template_id))
        ).all()
        return {template_id: int(count) for template_id, count in rows}
    except Exception as exc:
        logger.warning("Failed to count template attachments: %s", exc)
        return {}


def _pve_template_vmids() -> set[int] | None:
    """PVE 端實際存在的範本 VMID（對帳用）；PVE 連不上時回 None 不阻擋列表。"""
    try:
        return {int(t["vmid"]) for t in proxmox_ops.get_vm_templates()}
    except Exception:
        return None


def list_templates(*, session: Session, user: User) -> list[VMTemplatePublic]:
    if is_admin(user):
        templates = template_repo.list_all_templates(session=session)
    else:
        # 學生只看 ready；teacher 自己擁有的任何狀態都看得到（擁有者條件涵蓋）
        templates = template_repo.list_visible_templates(
            session=session,
            user_id=user.id,
            only_ready=not _can_manage(user),
        )
    _reconcile_failed_template_tasks(session, templates)
    pve_vmids = _pve_template_vmids()
    counts = _attachment_counts(session, [t.id for t in templates])
    return [
        _to_public(
            t,
            pve_vmids=pve_vmids,
            attachment_count=counts.get(t.id, 0),
        )
        for t in templates
    ]


def list_student_catalog(*, session: Session) -> list[TemplateCatalogItem]:
    """The application catalogue any signed-in user may request a machine from.

    Only ready, globally visible templates appear, and each row is enriched
    with the PVE facts the request form needs (OS family and the source
    machine's own spec, which is the clone's floor).
    """
    from app.services.proxmox.provisioning_service import (  # noqa: PLC0415
        _template_disk_gb,
        is_windows_template,
    )

    templates = template_repo.list_student_catalog(session=session)
    if not templates:
        return []
    # VM 與 LXC 範本都要對帳，所以讀 pool 內的原始紀錄（VM 專用清單已排除 LXC）
    raw_by_vmid = {
        int(item["vmid"]): item for item in proxmox_ops.get_vm_templates()
    }
    catalog: list[TemplateCatalogItem] = []
    for template in templates:
        raw = raw_by_vmid.get(template.pve_vmid)
        if raw is None:
            # PVE 已經找不到的範本會在建立時失敗，不該出現在目錄裡
            continue
        max_memory = raw.get("maxmem")
        is_lxc = template.resource_type.lower() == "lxc"
        catalog.append(
            TemplateCatalogItem(
                id=template.id,
                pve_vmid=template.pve_vmid,
                name=template.name,
                description=template.description,
                resource_type=template.resource_type,
                node=template.node,
                version=template.version,
                # ostype 只有 VM 讀得到，而且每次查詢都會打 PVE，
                # 所以只對目錄裡的 VM 逐筆確認
                is_windows=(not is_lxc) and is_windows_template(template.pve_vmid),
                requires_gpu=bool(template.requires_gpu),
                cores=template.default_cores or (raw.get("maxcpu") or None),
                memory_mb=template.default_memory
                or (int(max_memory) // (1024 * 1024) if max_memory else None),
                disk_gb=template.default_disk or (_template_disk_gb(raw) or None),
            )
        )
    return catalog


def get_template_for_user(
    *, session: Session, user: User, template_id: uuid.UUID
) -> VMTemplatePublic:
    template = _get_or_404(session, template_id)
    _require_view(session, user, template)
    _reconcile_failed_template_tasks(session, [template])
    counts = _attachment_counts(session, [template.id])
    return _to_public(
        template,
        pve_vmids=_pve_template_vmids(),
        attachment_count=counts.get(template.id, 0),
    )


def _reconcile_failed_template_tasks(
    session: Session,
    templates: list[VMTemplate],
) -> None:
    changed = False
    for template in templates:
        if template.status not in {
            VMTemplateStatus.creating,
            VMTemplateStatus.updating,
        }:
            continue
        task = task_record_repo.get_latest_template_task(
            session=session,
            template_id=template.id,
        )
        if task is None or task.status != TaskRecordStatus.failed:
            continue
        template.status = VMTemplateStatus.failed
        template.error_message = task.error or "背景任務執行失敗"
        template_repo.touch(session=session, template=template, commit=False)
        changed = True
    if changed:
        session.commit()


def _get_or_404(session: Session, template_id: uuid.UUID) -> VMTemplate:
    template = template_repo.get_template(
        session=session, template_id=template_id
    )
    if template is None or template.status == VMTemplateStatus.deleted:
        raise NotFoundError(t("template.notFound"))
    return template


def _can_manage(user: User) -> bool:
    from app.core.authorizers import require_template_manage

    try:
        require_template_manage(user)
    except PermissionDeniedError:
        return False
    return True


def _require_view(session: Session, user: User, template: VMTemplate) -> None:
    _ = session  # 保留服務層既有呼叫介面；私人/公開判斷已不需查詢群組。
    if is_admin(user):
        return
    if not template_repo.is_template_visible_to_user(
        template=template, user_id=user.id
    ):
        raise NotFoundError(t("template.notFound"))


def _require_owner(user: User, template: VMTemplate) -> None:
    from app.core.authorizers import require_template_owner

    require_template_owner(user, template.owner_id)


# ---------------------------------------------------------------------------
# 建立（VM → 範本）
# ---------------------------------------------------------------------------

async def create_template(
    *, session: Session, user: User, data: VMTemplateCreate
) -> tuple[VMTemplatePublic, TaskRecord]:
    """校驗來源 VM 後建立範本紀錄並入列 convert 任務。"""
    from app.core.authorizers import require_template_manage

    require_template_manage(user)

    # 含軟刪除一起查：活躍紀錄擋重複，deleted 紀錄稍後復用
    # （pve_vmid 有 unique 約束，PVE 回收 VMID 後不能另建新列）
    existing = template_repo.get_template_by_pve_vmid(
        session=session, pve_vmid=data.source_vmid, include_deleted=True
    )
    if existing is not None and existing.status != VMTemplateStatus.deleted:
        raise ConflictError(
            t("template.vmidAlreadyRegistered", vmid=data.source_vmid)
        )

    try:
        pve_resource = proxmox_ops.find_resource(data.source_vmid)
    except NotFoundError:
        raise NotFoundError(
            t("template.sourceVmNotFound", vmid=data.source_vmid)
        )
    if pve_resource.get("template") == 1:
        raise BadRequestError(
            t("template.sourceAlreadyPveTemplate", vmid=data.source_vmid)
        )
    resource_type = "lxc" if pve_resource.get("type") == "lxc" else "qemu"
    node = str(pve_resource["node"])

    if data.requires_gpu and resource_type == "lxc":
        raise BadRequestError(t("template.lxcGpuUnsupported"))

    # 母機若是平台管理的資源，僅擁有者或 admin 能轉換（轉換後原 VM 消失）；
    # 未登記在平台的 pool 內 VM（孤兒、基礎設施 VM）也只有 admin 能轉換，
    # 與 check_resource_ownership 對未登記 VMID 的處理一致。
    owned = session.get(Resource, data.source_vmid)
    if owned is None and not is_admin(user):
        raise PermissionDeniedError(
            t("template.sourceVmNotRegistered", vmid=data.source_vmid)
        )
    if owned is not None and owned.user_id != user.id and not is_admin(user):
        raise PermissionDeniedError(
            t("template.sourceVmBelongsToOther", vmid=data.source_vmid)
        )

    if existing is not None:
        template = template_repo.revive_deleted_template(
            session=session,
            template=existing,
            name=data.name,
            description=data.description,
            owner_id=user.id,
            node=node,
            resource_type=resource_type,
            visibility=data.visibility,
            default_cores=data.default_cores,
            default_memory=data.default_memory,
            allow_password_change=data.allow_password_change,
            requires_gpu=data.requires_gpu,
            source_vmid=data.source_vmid,
        )
    else:
        template = template_repo.create_template(
            session=session,
            pve_vmid=data.source_vmid,
            name=data.name,
            description=data.description,
            owner_id=user.id,
            node=node,
            resource_type=resource_type,
            visibility=data.visibility,
            default_cores=data.default_cores,
            default_memory=data.default_memory,
            allow_password_change=data.allow_password_change,
            requires_gpu=data.requires_gpu,
            source_vmid=data.source_vmid,
        )
    try:
        record = await enqueue_task(
            session=session,
            task_type=TASK_CONVERT,
            user_id=user.id,
            template_id=template.id,
            payload={
                "template_id": str(template.id),
                "pve_vmid": template.pve_vmid,
                "resource_type": resource_type,
                "node": node,
            },
        )
    except Exception as exc:
        template.status = VMTemplateStatus.failed
        template.error_message = f"無法啟動轉換任務: {exc}"[:1000]
        template_repo.touch(session=session, template=template)
        raise
    return _to_public(template), record


async def retry_template_conversion(
    *,
    session: Session,
    user: User,
    template_id: uuid.UUID,
) -> tuple[VMTemplatePublic, TaskRecord]:
    template = _get_or_404(session, template_id)
    _require_owner(user, template)
    _reconcile_failed_template_tasks(session, [template])
    if template.status != VMTemplateStatus.failed:
        raise ConflictError(t("template.retryOnlyFailed"))

    try:
        pve_resource = proxmox_ops.find_resource(template.pve_vmid)
    except NotFoundError:
        raise NotFoundError(
            t("template.sourceVmGone", vmid=template.pve_vmid)
        )
    if pve_resource.get("template") == 1:
        template.status = VMTemplateStatus.ready
        template.error_message = None
        template_repo.touch(session=session, template=template)
        record = task_record_repo.create_task_record(
            session=session,
            task_type=TASK_CONVERT,
            user_id=user.id,
            template_id=template.id,
            payload={
                "template_id": str(template.id),
                "pve_vmid": template.pve_vmid,
                "resource_type": template.resource_type,
                "node": template.node,
            },
        )
        task_record_repo.mark_task_finished(
            session=session,
            task_id=record.id,
            status=TaskRecordStatus.succeeded,
            result={"vmid": template.pve_vmid, "already_converted": True},
            resource_vmid=template.pve_vmid,
        )
        return _to_public(template), session.get(TaskRecord, record.id) or record

    template.node = str(pve_resource["node"])
    template.resource_type = (
        "lxc" if pve_resource.get("type") == "lxc" else "qemu"
    )
    template.status = VMTemplateStatus.creating
    template.error_message = None
    template_repo.touch(session=session, template=template)
    try:
        record = await enqueue_task(
            session=session,
            task_type=TASK_CONVERT,
            user_id=user.id,
            template_id=template.id,
            payload={
                "template_id": str(template.id),
                "pve_vmid": template.pve_vmid,
                "resource_type": template.resource_type,
                "node": template.node,
            },
        )
    except Exception as exc:
        template.status = VMTemplateStatus.failed
        template.error_message = f"無法啟動轉換任務: {exc}"[:1000]
        template_repo.touch(session=session, template=template)
        raise
    return _to_public(template), record


# ---------------------------------------------------------------------------
# 更新 metadata / 可見範圍
# ---------------------------------------------------------------------------

def update_template(
    *,
    session: Session,
    user: User,
    template_id: uuid.UUID,
    data: VMTemplateUpdate,
) -> VMTemplatePublic:
    template = _get_or_404(session, template_id)
    _require_owner(user, template)

    updates: dict[str, Any] = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(template, field, value)
    if template.requires_gpu and template.resource_type == "lxc":
        raise BadRequestError(t("template.lxcGpuUnsupported"))
    template_repo.touch(session=session, template=template)
    return _to_public(template)


# ---------------------------------------------------------------------------
# 附件（使用手冊）
# ---------------------------------------------------------------------------

def _template_attachments(
    session: Session, template_id: uuid.UUID
) -> list[TemplateAttachment]:
    return list(
        session.exec(
            select(TemplateAttachment)
            .where(TemplateAttachment.template_id == template_id)
            .order_by(col(TemplateAttachment.created_at).asc())
        ).all()
    )


def list_attachments(
    *, session: Session, user: User, template_id: uuid.UUID
) -> list[TemplateAttachment]:
    template = _get_or_404(session, template_id)
    _require_view(session, user, template)
    return _template_attachments(session, template.id)


def get_manual_for_cloned_resource(
    *, session: Session, vmid: int
) -> tuple[VMTemplate | None, list[TemplateAttachment]]:
    """克隆機來源範本的手冊（依 Resource.template_id = 範本 pve_vmid 反查）。

    權限由呼叫端以資源擁有權驗證；刻意不檢查範本可見範圍——
    範本事後轉私人，已克隆機的擁有者仍應拿得到手冊。
    """
    resource = session.get(Resource, vmid)
    if resource is None or not resource.template_id:
        return None, []
    template = template_repo.get_template_by_pve_vmid(
        session=session, pve_vmid=resource.template_id
    )
    if template is None:
        return None, []
    return template, _template_attachments(session, template.id)


def get_manual_attachment_for_cloned_resource(
    *, session: Session, vmid: int, attachment_id: uuid.UUID
) -> tuple[Path, TemplateAttachment]:
    template, attachments = get_manual_for_cloned_resource(
        session=session, vmid=vmid
    )
    if template is None:
        raise NotFoundError(t("template.manualNotFound"))
    attachment = next(
        (a for a in attachments if a.id == attachment_id), None
    )
    if attachment is None:
        raise NotFoundError(t("template.attachmentNotFound"))
    path = template_files.attachment_path(template.id, attachment.id)
    if path is None:
        raise NotFoundError(t("template.attachmentFileMissing"))
    return path, attachment


def add_attachment(
    *,
    session: Session,
    user: User,
    template_id: uuid.UUID,
    filename: str,
    content_type: str | None,
    data: bytes,
) -> TemplateAttachment:
    template = _get_or_404(session, template_id)
    _require_owner(user, template)

    # 去掉路徑片段與控制字元（CR/LF 等），避免下載時的 Content-Disposition 被污染
    safe_name = "".join(
        ch for ch in Path(filename or "").name if ch.isprintable()
    ).strip()
    if not safe_name:
        raise BadRequestError(t("template.filenameRequired"))
    ext = Path(safe_name).suffix.lower()
    if ext not in template_files.ATTACHMENT_ALLOWED_EXTENSIONS:
        allowed = "、".join(
            sorted(template_files.ATTACHMENT_ALLOWED_EXTENSIONS)
        )
        raise BadRequestError(
            t(
                "template.attachmentTypeUnsupported",
                ext=ext or t("template.noExtension"),
                allowed=allowed,
            )
        )
    if len(data) > template_files.ATTACHMENT_MAX_BYTES:
        raise BadRequestError(t("template.attachmentTooLarge"))
    existing = list_attachments(
        session=session, user=user, template_id=template_id
    )
    if len(existing) >= template_files.ATTACHMENT_MAX_COUNT:
        raise BadRequestError(
            t(
                "template.attachmentLimitReached",
                limit=template_files.ATTACHMENT_MAX_COUNT,
            )
        )

    attachment = TemplateAttachment(
        template_id=template.id,
        filename=safe_name[:255],
        content_type=(content_type or None),
        size_bytes=len(data),
    )
    template_files.save_attachment(template.id, attachment.id, data)
    session.add(attachment)
    session.commit()
    session.refresh(attachment)
    return attachment


def get_attachment_for_download(
    *,
    session: Session,
    user: User,
    template_id: uuid.UUID,
    attachment_id: uuid.UUID,
) -> tuple[Path, TemplateAttachment]:
    template = _get_or_404(session, template_id)
    _require_view(session, user, template)
    attachment = session.get(TemplateAttachment, attachment_id)
    if attachment is None or attachment.template_id != template.id:
        raise NotFoundError(t("template.attachmentNotFound"))
    path = template_files.attachment_path(template.id, attachment.id)
    if path is None:
        raise NotFoundError(t("template.attachmentFileMissing"))
    return path, attachment


def remove_attachment(
    *,
    session: Session,
    user: User,
    template_id: uuid.UUID,
    attachment_id: uuid.UUID,
) -> None:
    template = _get_or_404(session, template_id)
    _require_owner(user, template)
    attachment = session.get(TemplateAttachment, attachment_id)
    if attachment is None or attachment.template_id != template.id:
        raise NotFoundError(t("template.attachmentNotFound"))
    session.delete(attachment)
    session.commit()
    template_files.delete_attachment(template.id, attachment_id)


# ---------------------------------------------------------------------------
# 刪除（先擋子機）
# ---------------------------------------------------------------------------

def _clone_children_vmids(session: Session, pve_vmid: int) -> list[int]:
    stmt = select(Resource.vmid).where(Resource.template_id == pve_vmid)
    return list(session.exec(stmt).all())


def _environments_referencing(session: Session, template_id: uuid.UUID) -> list[str]:
    """引用這個母範本的學習環境名稱（含草稿與已下架版本）。

    已發布的環境會在學生按下啟動時才用到來源範本，所以刪除前必須先盤點；
    草稿與已下架版本一樣要算，否則教師之後建立新版本會拿到空的來源。
    """
    rows = session.exec(
        select(CourseEnvironment.name)
        .join(
            CourseEnvironmentVersion,
            col(CourseEnvironmentVersion.environment_id) == col(CourseEnvironment.id),
        )
        .join(
            CourseEnvironmentNode,
            col(CourseEnvironmentNode.version_id) == col(CourseEnvironmentVersion.id),
        )
        .where(CourseEnvironmentNode.source_template_id == template_id)
        .distinct()
    ).all()
    return [str(name) for name in rows]


async def delete_template(
    *, session: Session, user: User, template_id: uuid.UUID
) -> TaskRecord:
    template = _get_or_404(session, template_id)
    _require_owner(user, template)
    if template.status == VMTemplateStatus.updating:
        raise ConflictError(t("template.updateCycleInProgress"))

    children = _clone_children_vmids(session, template.pve_vmid)
    if children:
        raise ConflictError(
            t(
                "template.hasClonedVms",
                vmids=", ".join(str(v) for v in sorted(children)),
            )
        )

    environments = _environments_referencing(session, template.id)
    if environments:
        shown = "、".join(environments[:3])
        more = (
            t("template.referencedByEnvironmentsMore", count=len(environments))
            if len(environments) > 3
            else ""
        )
        raise ConflictError(
            t("template.referencedByEnvironments", shown=shown, more=more)
        )

    return await enqueue_task(
        session=session,
        task_type=TASK_DELETE,
        user_id=user.id,
        template_id=template.id,
        payload={
            "template_id": str(template.id),
            "pve_vmid": template.pve_vmid,
            "resource_type": template.resource_type,
            "node": template.node,
        },
    )


# ---------------------------------------------------------------------------
# 更新循環：Clone → Modify → Convert
# ---------------------------------------------------------------------------

async def start_update_cycle(
    *, session: Session, user: User, template_id: uuid.UUID
) -> TaskRecord:
    """克隆出暫存母機供修改；成功後 template.source_vmid 指向暫存機。"""
    template = _get_or_404(session, template_id)
    _require_owner(user, template)
    if template.status != VMTemplateStatus.ready:
        raise ConflictError(
            t("template.mustBeReadyForUpdate", status=template.status.value)
        )

    template.status = VMTemplateStatus.updating
    template_repo.touch(session=session, template=template)

    return await enqueue_task(
        session=session,
        task_type=TASK_UPDATE_CLONE,
        user_id=user.id,
        template_id=template.id,
        payload={
            "template_id": str(template.id),
            "pve_vmid": template.pve_vmid,
            "resource_type": template.resource_type,
            "node": template.node,
            "name": template.name,
        },
    )


async def finish_update_cycle(
    *, session: Session, user: User, template_id: uuid.UUID
) -> TaskRecord:
    """把修改完的暫存機轉為新版範本並汰換舊版。"""
    template = _get_or_404(session, template_id)
    _require_owner(user, template)
    if template.status != VMTemplateStatus.updating:
        raise ConflictError(t("template.notInUpdateCycle"))
    temp_vmid = template.source_vmid
    if temp_vmid is None or temp_vmid == template.pve_vmid:
        raise ConflictError(t("template.updateCloneNotReady"))

    return await enqueue_task(
        session=session,
        task_type=TASK_UPDATE_CONVERT,
        user_id=user.id,
        template_id=template.id,
        payload={
            "template_id": str(template.id),
            "old_pve_vmid": template.pve_vmid,
            "temp_vmid": temp_vmid,
            "resource_type": template.resource_type,
            "node": template.node,
        },
    )


async def cancel_update_cycle(
    *, session: Session, user: User, template_id: uuid.UUID
) -> TaskRecord:
    template = _get_or_404(session, template_id)
    _require_owner(user, template)
    if template.status != VMTemplateStatus.updating:
        raise ConflictError(t("template.notInUpdateCycle"))

    return await enqueue_task(
        session=session,
        task_type=TASK_UPDATE_CANCEL,
        user_id=user.id,
        template_id=template.id,
        payload={
            "template_id": str(template.id),
            "temp_vmid": template.source_vmid,
            "pve_vmid": template.pve_vmid,
            "resource_type": template.resource_type,
            "node": template.node,
        },
    )


# ---------------------------------------------------------------------------
# 背景任務執行（worker 端；tasks.py handler 以 to_thread 呼叫，全部同步）
# ---------------------------------------------------------------------------

_SHUTDOWN_TIMEOUT_SECONDS = 180
_STOP_TIMEOUT_SECONDS = 60
_POLL_INTERVAL_SECONDS = 5


def _as_resource_type(raw: Any) -> proxmox_ops.ResourceType:
    return "lxc" if raw == "lxc" else "qemu"


def _wait_until_stopped(
    node: str, vmid: int, resource_type: proxmox_ops.ResourceType, timeout: float
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = proxmox_ops.get_status(node, vmid, resource_type)
        if status.get("status") == "stopped":
            return True
        time.sleep(_POLL_INTERVAL_SECONDS)
    return False


def _ensure_stopped(
    node: str, vmid: int, resource_type: proxmox_ops.ResourceType
) -> None:
    """優雅關機，逾時強制斷電；仍停不下來就放棄（convert/delete 都要求 stopped）。"""
    status = proxmox_ops.get_status(node, vmid, resource_type)
    if status.get("status") == "stopped":
        return
    proxmox_ops.control(node, vmid, resource_type, "shutdown")
    if _wait_until_stopped(node, vmid, resource_type, _SHUTDOWN_TIMEOUT_SECONDS):
        return
    proxmox_ops.control(node, vmid, resource_type, "stop")
    if not _wait_until_stopped(node, vmid, resource_type, _STOP_TIMEOUT_SECONDS):
        raise RuntimeError(f"VM {vmid} 無法停止")


# cloud-init clean 讓克隆機首次開機重跑 first-boot 模組；host key 只在
# guest 有 cloud-init 時才刪（否則克隆機 sshd 會因缺 host key 起不來）。
# machine-id 清空後 systemd 會在下次開機自動重生，避免全班克隆機共用同一組。
_CLOUD_INIT_RESET_SCRIPT = (
    "if command -v cloud-init >/dev/null 2>&1; then "
    "cloud-init clean --logs || true; "
    "rm -f /etc/ssh/ssh_host_*; "
    "fi; "
    "truncate -s 0 /etc/machine-id 2>/dev/null || true; "
    "rm -f /var/lib/dbus/machine-id 2>/dev/null || true"
)

# Windows 對應：刪整個 Cloudbase-Init registry key（含 per-instance plugin
# 執行紀錄與 unattend 狀態），克隆機首次開機重跑全部 plugin；OpenSSH host
# key 一併清掉（Win32-OpenSSH 的 sshd 服務啟動時會自動重生）。全部
# SilentlyContinue：沒裝 Cloudbase-Init / OpenSSH 也照樣成功。sysprep 因
# rearm 次數限制刻意不在此執行。字串刻意只用單引號，避免 qemu-ga 組
# Windows 命令列時的雙引號跳脫問題。
_CLOUDBASE_INIT_RESET_SCRIPT = (
    "Remove-Item -Recurse -Force "
    "'HKLM:\\SOFTWARE\\Cloudbase Solutions\\Cloudbase-Init' "
    "-ErrorAction SilentlyContinue; "
    "Remove-Item -Force 'C:\\ProgramData\\ssh\\ssh_host_*' "
    "-ErrorAction SilentlyContinue; "
    "exit 0"
)


_BOOT_AGENT_TIMEOUT_SECONDS = 120


def _wait_for_guest_agent(node: str, vmid: int, timeout: float) -> bool:
    """輪詢 agent ping 直到回應或逾時（開機後 agent 起來需時）。"""
    from app.infrastructure.proxmox import guest  # noqa: PLC0415

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if guest.ping_qemu_agent(node, vmid):
            return True
        time.sleep(_POLL_INTERVAL_SECONDS)
    return False


def _reset_cloud_init_state(
    node: str, vmid: int, resource_type: proxmox_ops.ResourceType
) -> bool:
    """轉範本關機前重設 guest 內 cloud-init / Cloudbase-Init 狀態（best-effort）。

    僅 qemu 才執行；母機是關機狀態會先開機等 agent 起來再重設，
    隨後 _ensure_stopped 照原流程關機。agent get-osinfo 回 mswindows
    時走 PowerShell 清 Cloudbase-Init registry 與 OpenSSH host key，
    其餘（含 get-osinfo 不支援時）走 /bin/sh 的 cloud-init 清理。
    LXC 無 cloud-init、agent 未裝都靜默略過，不阻擋轉換。回傳是否
    有執行成功。
    """
    if resource_type != "qemu":
        return False
    try:
        from app.infrastructure.proxmox import guest  # noqa: PLC0415

        status = proxmox_ops.get_status(node, vmid, resource_type)
        if status.get("status") != "running":
            # 關機的母機先開機重設；轉換流程接著會把它關回去
            proxmox_ops.control(node, vmid, resource_type, "start")
            if not _wait_for_guest_agent(
                node, vmid, _BOOT_AGENT_TIMEOUT_SECONDS
            ):
                logger.warning(
                    "VM %d booted for cloud-init reset but guest agent "
                    "did not come up within %ds; skipping reset",
                    vmid,
                    _BOOT_AGENT_TIMEOUT_SECONDS,
                )
                return False

        osinfo = guest.get_osinfo_qemu(node, vmid) or {}
        if str(osinfo.get("id") or "").lower() == "mswindows":
            command = [
                "powershell.exe",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                _CLOUDBASE_INIT_RESET_SCRIPT,
            ]
        else:
            command = ["/bin/sh", "-c", _CLOUD_INIT_RESET_SCRIPT]
        code, _out, err = guest.exec_qemu(node, vmid, command)
        if code != 0:
            logger.warning(
                "cloud-init reset failed for VM %d (exit %d): %s",
                vmid,
                code,
                (err or "").strip()[:300],
            )
            return False
        return True
    except Exception as exc:
        logger.warning("cloud-init reset skipped for VM %d: %s", vmid, exc)
        return False


def _remove_snapshots_for_convert(
    node: str, vmid: int, resource_type: proxmox_ops.ResourceType
) -> None:
    """轉範本前清掉所有快照（PVE 拒絕帶快照的 CT 轉範本）。

    qemu 雖允許帶快照轉換，但範本化後快照不可回滾、只佔空間，一併清掉。
    由新到舊刪，避免鏈中間節點的合併順序問題。
    """
    snapshots = proxmox_ops.list_snapshots(node, vmid, resource_type)
    real = [s for s in snapshots if s.get("name") and s["name"] != "current"]
    for snap in sorted(real, key=lambda s: s.get("snaptime") or 0, reverse=True):
        proxmox_ops.delete_snapshot(node, vmid, resource_type, snap["name"])


_QEMU_BOOT_DISK_KEYS = ("scsi0", "virtio0", "sata0", "ide0")


def _parse_disk_size_gb(raw: str) -> int | None:
    match = re.search(r"size=(\d+)([MGT]?)", raw)
    if match is None:
        return None
    value = int(match.group(1))
    unit = match.group(2) or "G"
    if unit == "M":
        return max(1, (value + 1023) // 1024)
    if unit == "T":
        return value * 1024
    return value


def _detect_template_disk_gb(
    node: str, vmid: int, resource_type: proxmox_ops.ResourceType
) -> int | None:
    """從 PVE config 讀範本開機磁碟大小（GB，best-effort）。

    default_disk 不開放使用者設定，一律以轉換完成當下的實際磁碟為準
    （克隆固定沿用，前端僅唯讀顯示）。
    """
    try:
        config = proxmox_ops.get_config(node, vmid, resource_type)
        if resource_type == "lxc":
            raw = config.get("rootfs")
        else:
            bootdisk = str(config.get("bootdisk") or "")
            raw = config.get(bootdisk) if bootdisk else None
            if raw is None:
                for key in _QEMU_BOOT_DISK_KEYS:
                    if config.get(key):
                        raw = config[key]
                        break
        if not raw:
            return None
        return _parse_disk_size_gb(str(raw))
    except Exception as exc:
        logger.warning(
            "Failed to detect disk size for VM %d: %s", vmid, exc
        )
        return None


def _strip_hostpci_for_convert(
    node: str, vmid: int, resource_type: proxmox_ops.ResourceType
) -> None:
    """轉範本前剝離 hostpci 直通裝置（best-effort，失敗不擋轉換）。

    範本不該綁實體 PCI 裝置：克隆機繼承 raw passthrough / SR-IOV VF 會
    導致同裝置只能一台開機、跨節點開機失敗，且 GPU 用量掃描（見
    gpu_service._build_usage_map）會把範本與克隆機都算進佔用，繞過
    申請流程。僅 qemu 有 hostpci；LXC 直接略過。
    """
    if resource_type != "qemu":
        return
    try:
        config = proxmox_ops.get_config(node, vmid, resource_type)
        keys = [f"hostpci{i}" for i in range(16) if config.get(f"hostpci{i}")]
        if not keys:
            return
        proxmox_ops.update_config(
            node, vmid, resource_type, delete=",".join(keys)
        )
        logger.info(
            "Stripped %s from VM %d before template convert",
            ",".join(keys),
            vmid,
        )
    except Exception as exc:
        logger.warning("hostpci strip skipped for VM %d: %s", vmid, exc)


def _set_template_error(
    template_id: uuid.UUID,
    error: str,
    *,
    status: VMTemplateStatus | None = None,
) -> None:
    """記錄錯誤訊息；status 為 None 時保留原狀態。"""
    with Session(engine) as session:
        template = session.get(VMTemplate, template_id)
        if template is None:
            return
        if status is not None:
            template.status = status
        template.error_message = error[:1000]
        template_repo.touch(session=session, template=template)


def run_convert_task(task_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
    """建立範本：重設 cloud-init → 關機 → convert → 標記 ready、移除母機 Resource。"""
    template_id = uuid.UUID(payload["template_id"])
    pve_vmid = int(payload["pve_vmid"])
    resource_type = _as_resource_type(payload["resource_type"])
    node = str(payload["node"])
    try:
        report_progress(task_id, 10)
        cloud_init_reset = _reset_cloud_init_state(node, pve_vmid, resource_type)
        report_progress(task_id, 25)
        _ensure_stopped(node, pve_vmid, resource_type)
        report_progress(task_id, 40)
        # 關機後才剝離，避免變更卡在 pending 被一起轉進範本
        _strip_hostpci_for_convert(node, pve_vmid, resource_type)
        _remove_snapshots_for_convert(node, pve_vmid, resource_type)
        report_progress(task_id, 60)
        proxmox_ops.convert_to_template(node, pve_vmid, resource_type)
        report_progress(task_id, 90)
    except Exception as exc:
        _set_template_error(
            template_id, str(exc), status=VMTemplateStatus.failed
        )
        raise
    detected_disk = _detect_template_disk_gb(node, pve_vmid, resource_type)
    with Session(engine) as session:
        template = session.get(VMTemplate, template_id)
        if template is not None:
            template.status = VMTemplateStatus.ready
            template.error_message = None
            if detected_disk is not None:
                template.default_disk = detected_disk
            template_repo.touch(session=session, template=template, commit=False)
        # 母機已轉為唯讀範本，從資源列表移除（克隆端會重新配置 IP/防火牆）
        resource = session.get(Resource, pve_vmid)
        if resource is not None:
            session.delete(resource)
        # 母機網路資源一併回收：IP 若留在已配置狀態會永久佔用，gateway
        # 上殘留的 NAT 埠轉發在 IP 重配給別台 VM 後會導流到新住戶
        try:
            from app.services.network import ip_management_service  # noqa: PLC0415

            ip_management_service.release_ip(session, pve_vmid)
        except Exception as exc:
            logger.warning("Failed to release IP for VM %s: %s", pve_vmid, exc)
        try:
            from app.services.network import nat_service  # noqa: PLC0415

            nat_service.remove_nat_rules_for_vmid(session, pve_vmid)
        except Exception as exc:
            logger.warning(
                "Failed to remove NAT rules for VM %s: %s", pve_vmid, exc
            )
        # 母機若來自申請單，一併標為已消耗；否則排程器會反覆嘗試
        # 啟動範本，資源頁也會把申請單復活成「建立失敗」placeholder
        from app.services.resource import resource_service  # noqa: PLC0415

        resource_service.mark_linked_request_consumed(
            session=session,
            vmid=pve_vmid,
            marker=resource_service.RESOURCE_CONVERTED_TO_TEMPLATE_MARKER,
        )
        session.commit()
    return {"vmid": pve_vmid, "cloud_init_reset": cloud_init_reset}


def run_delete_task(task_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
    """刪除範本：PVE 端存在才刪，成功後標記 deleted（軟刪除保留紀錄）。"""
    template_id = uuid.UUID(payload["template_id"])
    pve_vmid = int(payload["pve_vmid"])
    resource_type = _as_resource_type(payload["resource_type"])
    node = str(payload["node"])
    try:
        report_progress(task_id, 10)
        try:
            proxmox_ops.find_vm_template(pve_vmid)
            exists = True
        except NotFoundError:
            exists = False
        if exists:
            proxmox_ops.delete_resource(node, pve_vmid, resource_type)
        report_progress(task_id, 90)
    except Exception as exc:
        # 刪除失敗不改變狀態（範本仍可用），只記錄錯誤
        _set_template_error(template_id, str(exc))
        raise
    with Session(engine) as session:
        template = session.get(VMTemplate, template_id)
        if template is not None:
            template.status = VMTemplateStatus.deleted
            template.error_message = None
            template_repo.touch(session=session, template=template, commit=False)
        for attachment in session.exec(
            select(TemplateAttachment).where(
                TemplateAttachment.template_id == template_id
            )
        ).all():
            session.delete(attachment)
        session.commit()
    template_files.delete_all_for_template(template_id)
    return {"vmid": pve_vmid}


def run_update_clone_task(
    task_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """更新循環第一步：full clone 出暫存母機並登記為擁有者的 Resource。"""
    template_id = uuid.UUID(payload["template_id"])
    pve_vmid = int(payload["pve_vmid"])
    resource_type = _as_resource_type(payload["resource_type"])
    node = str(payload["node"])
    try:
        new_vmid = proxmox_ops.next_vmid()
        report_progress(task_id, 10)
        clone_name = f"tpl-{pve_vmid}-edit"
        pool = get_proxmox_settings_for_node(node).pool_name
        # 範本更新需要可獨立寫入的完整副本，一律 full clone
        if resource_type == "lxc":
            proxmox_ops.clone_lxc(
                node, pve_vmid, newid=new_vmid, hostname=clone_name,
                full=1, pool=pool,
            )
        else:
            proxmox_ops.clone_vm(
                node, pve_vmid, newid=new_vmid, name=clone_name,
                full=1, pool=pool,
            )
        report_progress(task_id, 80)
    except Exception as exc:
        # 克隆失敗 → 回復 ready，讓使用者可重新發起
        _set_template_error(
            template_id, str(exc), status=VMTemplateStatus.ready
        )
        raise
    with Session(engine) as session:
        template = session.get(VMTemplate, template_id)
        if template is not None:
            template.source_vmid = new_vmid
            template.error_message = None
            template_repo.touch(session=session, template=template, commit=False)
            # 暫存母機登記為擁有者的資源，讓擁有者能在資源頁開機/進 console 修改
            if template.owner_id is not None:
                session.add(
                    Resource(
                        vmid=new_vmid,
                        user_id=template.owner_id,
                        environment_type="範本更新母機",
                        created_at=datetime.now(timezone.utc),
                    )
                )
        session.commit()
    return {"vmid": new_vmid}


def run_update_convert_task(
    task_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """更新循環收尾：暫存機轉範本 → 汰換舊版 → DB 換 vmid、版本 +1。"""
    template_id = uuid.UUID(payload["template_id"])
    old_pve_vmid = int(payload["old_pve_vmid"])
    temp_vmid = int(payload["temp_vmid"])
    resource_type = _as_resource_type(payload["resource_type"])
    node = str(payload["node"])
    try:
        report_progress(task_id, 10)
        cloud_init_reset = _reset_cloud_init_state(node, temp_vmid, resource_type)
        report_progress(task_id, 25)
        _ensure_stopped(node, temp_vmid, resource_type)
        report_progress(task_id, 40)
        _remove_snapshots_for_convert(node, temp_vmid, resource_type)
        report_progress(task_id, 55)
        proxmox_ops.convert_to_template(node, temp_vmid, resource_type)
        report_progress(task_id, 70)
    except Exception as exc:
        # 保持 updating：使用者可修好後重試 finish，或 cancel 丟棄暫存機
        _set_template_error(template_id, str(exc))
        raise
    warning: str | None = None
    try:
        proxmox_ops.delete_resource(node, old_pve_vmid, resource_type)
    except Exception as exc:  # 舊版可能仍有 linked clone 子機，容忍失敗
        warning = f"舊版範本 {old_pve_vmid} 刪除失敗: {exc}"
    report_progress(task_id, 90)
    detected_disk = _detect_template_disk_gb(node, temp_vmid, resource_type)
    with Session(engine) as session:
        template = session.get(VMTemplate, template_id)
        if template is not None:
            template.pve_vmid = temp_vmid
            template.source_vmid = temp_vmid
            template.version += 1
            template.status = VMTemplateStatus.ready
            template.error_message = warning
            if detected_disk is not None:
                template.default_disk = detected_disk
            template_repo.touch(session=session, template=template, commit=False)
        # 暫存機已轉為範本，撤下資源列表紀錄
        temp_resource = session.get(Resource, temp_vmid)
        if temp_resource is not None:
            session.delete(temp_resource)
        session.commit()
    result: dict[str, Any] = {
        "vmid": temp_vmid,
        "cloud_init_reset": cloud_init_reset,
    }
    if warning:
        result["warning"] = warning
    return result


def run_update_cancel_task(
    task_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """取消更新循環：銷毀暫存母機（若已產生），範本回復 ready。"""
    template_id = uuid.UUID(payload["template_id"])
    pve_vmid = int(payload["pve_vmid"])
    resource_type = _as_resource_type(payload["resource_type"])
    node = str(payload["node"])
    raw_temp = payload.get("temp_vmid")
    temp_vmid = int(raw_temp) if raw_temp is not None else None
    removed = False
    try:
        report_progress(task_id, 10)
        if temp_vmid is not None and temp_vmid != pve_vmid:
            try:
                proxmox_ops.find_resource(temp_vmid)
                temp_exists = True
            except NotFoundError:
                temp_exists = False
            if temp_exists:
                _ensure_stopped(node, temp_vmid, resource_type)
                proxmox_ops.delete_resource(node, temp_vmid, resource_type)
                removed = True
        report_progress(task_id, 80)
    except Exception as exc:
        _set_template_error(template_id, str(exc))
        raise
    with Session(engine) as session:
        template = session.get(VMTemplate, template_id)
        if template is not None:
            template.status = VMTemplateStatus.ready
            template.source_vmid = None
            template.error_message = None
            template_repo.touch(session=session, template=template, commit=False)
        if temp_vmid is not None and temp_vmid != pve_vmid:
            temp_resource = session.get(Resource, temp_vmid)
            if temp_resource is not None:
                session.delete(temp_resource)
        session.commit()
    return {"vmid": pve_vmid, "temp_removed": removed}


__all__ = [
    "TASK_CONVERT",
    "TASK_DELETE",
    "TASK_UPDATE_CANCEL",
    "TASK_UPDATE_CLONE",
    "TASK_UPDATE_CONVERT",
    "cancel_update_cycle",
    "create_template",
    "delete_template",
    "finish_update_cycle",
    "get_template_for_user",
    "list_templates",
    "run_convert_task",
    "run_delete_task",
    "run_update_cancel_task",
    "run_update_clone_task",
    "run_update_convert_task",
    "start_update_cycle",
    "update_template",
]
