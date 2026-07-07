"""範本系統 2.0 API 路由。

權限規則（詳見 core/authorizers.py 與 template_service）：
- 列表/單筆查詢：全部角色（依可見範圍過濾）
- 建立/更新/刪除/更新循環：TEMPLATE_MANAGE（teacher/admin），且僅擁有者或 admin
- 任務查詢：本人或 admin
"""

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser, SessionDep
from app.core.permissions import is_admin
from app.exceptions import NotFoundError
from app.repositories import task_record as task_record_repo
from app.schemas.template import (
    TaskRecordPublic,
    TaskRecordsPublic,
    TemplateCloneRequest,
    TemplateCloneResponse,
    VMTemplateCreate,
    VMTemplatePublic,
    VMTemplatesPublic,
    VMTemplateTaskResponse,
    VMTemplateUpdate,
)
from app.services.template import clone_service, template_service

router = APIRouter(prefix="/templates", tags=["templates"])


# --- 任務狀態（必須宣告在 /{template_id} 之前，避免路徑衝突） ---


@router.get("/tasks", response_model=TaskRecordsPublic)
def list_my_tasks(
    session: SessionDep, current_user: CurrentUser, limit: int = 50
) -> TaskRecordsPublic:
    """列出自己的背景任務（新到舊）。"""
    records = task_record_repo.list_task_records_by_user(
        session=session, user_id=current_user.id, limit=min(max(limit, 1), 200)
    )
    data = [TaskRecordPublic.from_record(r) for r in records]
    return TaskRecordsPublic(data=data, count=len(data))


@router.get("/tasks/{task_id}", response_model=TaskRecordPublic)
def get_task(
    session: SessionDep, current_user: CurrentUser, task_id: uuid.UUID
) -> TaskRecordPublic:
    """查詢單一任務狀態（本人或 admin）。"""
    record = task_record_repo.get_task_record(session=session, task_id=task_id)
    if record is None or (
        record.user_id != current_user.id and not is_admin(current_user)
    ):
        raise NotFoundError("Task not found")
    return TaskRecordPublic.from_record(record)


# --- 範本 CRUD ---


@router.get("/", response_model=VMTemplatesPublic)
def list_templates(
    session: SessionDep, current_user: CurrentUser
) -> VMTemplatesPublic:
    """列出可見範本（admin 全部；teacher 自有+可見；student 僅 ready 且可見）。"""
    data = template_service.list_templates(session=session, user=current_user)
    return VMTemplatesPublic(data=data, count=len(data))


@router.post("/", response_model=VMTemplateTaskResponse)
async def create_template(
    session: SessionDep, current_user: CurrentUser, body: VMTemplateCreate
) -> VMTemplateTaskResponse:
    """把現有 VM/LXC 轉為範本（背景任務：關機 → convert-to-template）。"""
    template, record = await template_service.create_template(
        session=session, user=current_user, data=body
    )
    return VMTemplateTaskResponse(
        template=template, task=TaskRecordPublic.from_record(record)
    )


@router.get("/{template_id}", response_model=VMTemplatePublic)
def get_template(
    session: SessionDep, current_user: CurrentUser, template_id: uuid.UUID
) -> VMTemplatePublic:
    return template_service.get_template_for_user(
        session=session, user=current_user, template_id=template_id
    )


@router.patch("/{template_id}", response_model=VMTemplatePublic)
def update_template(
    session: SessionDep,
    current_user: CurrentUser,
    template_id: uuid.UUID,
    body: VMTemplateUpdate,
) -> VMTemplatePublic:
    """更新範本 metadata / 可見範圍（擁有者或 admin）。"""
    return template_service.update_template(
        session=session, user=current_user, template_id=template_id, data=body
    )


@router.delete("/{template_id}", response_model=TaskRecordPublic)
async def delete_template(
    session: SessionDep, current_user: CurrentUser, template_id: uuid.UUID
) -> TaskRecordPublic:
    """刪除範本；仍有 linked clone 子機時回 409。"""
    record = await template_service.delete_template(
        session=session, user=current_user, template_id=template_id
    )
    return TaskRecordPublic.from_record(record)


@router.post("/{template_id}/clone", response_model=TemplateCloneResponse)
async def clone_template(
    session: SessionDep,
    current_user: CurrentUser,
    template_id: uuid.UUID,
    body: TemplateCloneRequest,
) -> TemplateCloneResponse:
    """從範本克隆開通（student 單台套配額；teacher/admin 可批量）。"""
    records = await clone_service.request_clone(
        session=session, user=current_user, template_id=template_id, data=body
    )
    return TemplateCloneResponse(
        tasks=[TaskRecordPublic.from_record(r) for r in records]
    )


# --- 更新循環：Clone → Modify → Convert ---


@router.post("/{template_id}/update-cycle/start", response_model=TaskRecordPublic)
async def start_update_cycle(
    session: SessionDep, current_user: CurrentUser, template_id: uuid.UUID
) -> TaskRecordPublic:
    """克隆出暫存母機供修改（完成後出現在擁有者的資源列表）。"""
    record = await template_service.start_update_cycle(
        session=session, user=current_user, template_id=template_id
    )
    return TaskRecordPublic.from_record(record)


@router.post("/{template_id}/update-cycle/finish", response_model=TaskRecordPublic)
async def finish_update_cycle(
    session: SessionDep, current_user: CurrentUser, template_id: uuid.UUID
) -> TaskRecordPublic:
    """把修改完的暫存機轉為新版範本並汰換舊版。"""
    record = await template_service.finish_update_cycle(
        session=session, user=current_user, template_id=template_id
    )
    return TaskRecordPublic.from_record(record)


@router.post("/{template_id}/update-cycle/cancel", response_model=TaskRecordPublic)
async def cancel_update_cycle(
    session: SessionDep, current_user: CurrentUser, template_id: uuid.UUID
) -> TaskRecordPublic:
    """取消更新循環並銷毀暫存母機。"""
    record = await template_service.cancel_update_cycle(
        session=session, user=current_user, template_id=template_id
    )
    return TaskRecordPublic.from_record(record)
