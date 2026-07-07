"""範本生命週期服務：建立（VM→範本）、更新循環（Clone→Modify→Convert）、刪除。

耗時的 PVE 操作一律經由 arq 隊列（enqueue_task），本模組僅做
權限/狀態校驗、DB 讀寫與任務入列；PVE 細節在 tasks.py 的 handler。
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from app.core.db import engine
from app.core.permissions import is_admin
from app.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.infrastructure.proxmox import get_proxmox_settings
from app.infrastructure.proxmox import operations as proxmox_ops
from app.infrastructure.queue import enqueue_task, report_progress
from app.models import (
    Resource,
    TaskRecord,
    User,
    VMTemplate,
    VMTemplateStatus,
)
from app.repositories import vm_template as template_repo
from app.schemas.template import (
    VMTemplateCreate,
    VMTemplatePublic,
    VMTemplateUpdate,
)

TASK_CONVERT = "template.convert"
TASK_DELETE = "template.delete"
TASK_UPDATE_CLONE = "template.update_clone"
TASK_UPDATE_CONVERT = "template.update_convert"
TASK_UPDATE_CANCEL = "template.update_cancel"


# ---------------------------------------------------------------------------
# 查詢
# ---------------------------------------------------------------------------

def _to_public(
    session: Session,
    template: VMTemplate,
    *,
    pve_vmids: set[int] | None = None,
) -> VMTemplatePublic:
    public = VMTemplatePublic.model_validate(template)
    public.group_ids = template_repo.get_group_ids(
        session=session, template_id=template.id
    )
    if pve_vmids is not None:
        public.pve_exists = template.pve_vmid in pve_vmids
    return public


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
    pve_vmids = _pve_template_vmids()
    return [
        _to_public(session, t, pve_vmids=pve_vmids) for t in templates
    ]


def get_template_for_user(
    *, session: Session, user: User, template_id: uuid.UUID
) -> VMTemplatePublic:
    template = _get_or_404(session, template_id)
    _require_view(session, user, template)
    return _to_public(session, template, pve_vmids=_pve_template_vmids())


def _get_or_404(session: Session, template_id: uuid.UUID) -> VMTemplate:
    template = template_repo.get_template(
        session=session, template_id=template_id
    )
    if template is None or template.status == VMTemplateStatus.deleted:
        raise NotFoundError("Template not found")
    return template


def _can_manage(user: User) -> bool:
    from app.core.authorizers import require_template_manage

    try:
        require_template_manage(user)
    except PermissionDeniedError:
        return False
    return True


def _require_view(session: Session, user: User, template: VMTemplate) -> None:
    if is_admin(user):
        return
    if not template_repo.is_template_visible_to_user(
        session=session, template=template, user_id=user.id
    ):
        raise NotFoundError("Template not found")


def _require_owner(user: User, template: VMTemplate) -> None:
    from app.core.authorizers import require_template_owner

    require_template_owner(user, template.owner_id)


# ---------------------------------------------------------------------------
# 建立（VM → 範本）
# ---------------------------------------------------------------------------

def _validate_group_ids(
    session: Session, user: User, group_ids: list[uuid.UUID]
) -> None:
    if not group_ids:
        return
    groups = template_repo.get_groups_by_ids(
        session=session, group_ids=group_ids
    )
    found = {g.id for g in groups}
    missing = [str(gid) for gid in group_ids if gid not in found]
    if missing:
        raise BadRequestError(f"Group(s) not found: {', '.join(missing)}")
    if not is_admin(user):
        not_owned = [g.name for g in groups if g.owner_id != user.id]
        if not_owned:
            raise PermissionDeniedError(
                f"You can only bind templates to your own groups: {', '.join(not_owned)}"
            )


async def create_template(
    *, session: Session, user: User, data: VMTemplateCreate
) -> tuple[VMTemplatePublic, TaskRecord]:
    """校驗來源 VM 後建立範本紀錄並入列 convert 任務。"""
    from app.core.authorizers import require_template_manage

    require_template_manage(user)
    _validate_group_ids(session, user, data.group_ids)

    if (
        template_repo.get_template_by_pve_vmid(
            session=session, pve_vmid=data.source_vmid
        )
        is not None
    ):
        raise ConflictError(
            f"VMID {data.source_vmid} is already registered as a template"
        )

    try:
        pve_resource = proxmox_ops.find_resource(data.source_vmid)
    except NotFoundError:
        raise NotFoundError(
            f"VM {data.source_vmid} not found in the managed pool"
        )
    if pve_resource.get("template") == 1:
        raise BadRequestError(
            f"VM {data.source_vmid} is already a PVE template"
        )
    resource_type = "lxc" if pve_resource.get("type") == "lxc" else "qemu"
    node = str(pve_resource["node"])

    # 母機若是平台管理的資源，僅擁有者或 admin 能轉換（轉換後原 VM 消失）
    owned = session.get(Resource, data.source_vmid)
    if owned is not None and owned.user_id != user.id and not is_admin(user):
        raise PermissionDeniedError(
            f"VM {data.source_vmid} belongs to another user"
        )

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
        default_disk=data.default_disk,
        source_vmid=data.source_vmid,
    )
    template_repo.set_group_links(
        session=session, template_id=template.id, group_ids=data.group_ids
    )

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
    return _to_public(session, template), record


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

    if data.group_ids is not None:
        _validate_group_ids(session, user, data.group_ids)
        template_repo.set_group_links(
            session=session,
            template_id=template.id,
            group_ids=data.group_ids,
            commit=False,
        )

    updates: dict[str, Any] = data.model_dump(
        exclude_unset=True, exclude={"group_ids"}
    )
    for field, value in updates.items():
        setattr(template, field, value)
    template_repo.touch(session=session, template=template)
    return _to_public(session, template)


# ---------------------------------------------------------------------------
# 刪除（先擋子機）
# ---------------------------------------------------------------------------

def _clone_children_vmids(session: Session, pve_vmid: int) -> list[int]:
    stmt = select(Resource.vmid).where(Resource.template_id == pve_vmid)
    return list(session.exec(stmt).all())


async def delete_template(
    *, session: Session, user: User, template_id: uuid.UUID
) -> TaskRecord:
    template = _get_or_404(session, template_id)
    _require_owner(user, template)
    if template.status == VMTemplateStatus.updating:
        raise ConflictError(
            "Template is in an update cycle; finish or cancel it first"
        )

    children = _clone_children_vmids(session, template.pve_vmid)
    if children:
        raise ConflictError(
            "Template still has cloned VMs: "
            + ", ".join(str(v) for v in sorted(children))
            + ". Delete them first."
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
            f"Template must be ready to start an update cycle (now: {template.status.value})"
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
        raise ConflictError("Template is not in an update cycle")
    temp_vmid = template.source_vmid
    if temp_vmid is None or temp_vmid == template.pve_vmid:
        raise ConflictError(
            "Update-cycle clone is not ready yet; wait for the clone task to finish"
        )

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
        raise ConflictError("Template is not in an update cycle")

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
    """建立範本：關機 → convert-to-template → 標記 ready、移除母機 Resource 紀錄。"""
    template_id = uuid.UUID(payload["template_id"])
    pve_vmid = int(payload["pve_vmid"])
    resource_type = _as_resource_type(payload["resource_type"])
    node = str(payload["node"])
    try:
        report_progress(task_id, 10)
        _ensure_stopped(node, pve_vmid, resource_type)
        report_progress(task_id, 50)
        proxmox_ops.convert_to_template(node, pve_vmid, resource_type)
        report_progress(task_id, 90)
    except Exception as exc:
        _set_template_error(
            template_id, str(exc), status=VMTemplateStatus.failed
        )
        raise
    with Session(engine) as session:
        template = session.get(VMTemplate, template_id)
        if template is not None:
            template.status = VMTemplateStatus.ready
            template.error_message = None
            template_repo.touch(session=session, template=template, commit=False)
        # 母機已轉為唯讀範本，從資源列表移除（克隆端會重新配置 IP/防火牆）
        resource = session.get(Resource, pve_vmid)
        if resource is not None:
            session.delete(resource)
        session.commit()
    return {"vmid": pve_vmid}


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
            template_repo.touch(session=session, template=template)
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
        pool = get_proxmox_settings().pool_name
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
        _ensure_stopped(node, temp_vmid, resource_type)
        report_progress(task_id, 40)
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
    with Session(engine) as session:
        template = session.get(VMTemplate, template_id)
        if template is not None:
            template.pve_vmid = temp_vmid
            template.source_vmid = temp_vmid
            template.version += 1
            template.status = VMTemplateStatus.ready
            template.error_message = warning
            template_repo.touch(session=session, template=template, commit=False)
        # 暫存機已轉為範本，撤下資源列表紀錄
        temp_resource = session.get(Resource, temp_vmid)
        if temp_resource is not None:
            session.delete(temp_resource)
        session.commit()
    result: dict[str, Any] = {"vmid": temp_vmid}
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
