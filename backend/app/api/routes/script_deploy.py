"""服務模板腳本部署 API

提供服務模板的無人值守部署功能：
- POST /deploy: 啟動部署任務（背景執行）
- GET /status/{task_id}: 查詢部署進度
- GET /logs: 列出歷史部署日誌
- GET /logs/{task_id}: 查詢單筆部署日誌詳細內容（含完整 output）
"""

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import func, select

from app.api.deps import AdminUser, SessionDep
from app.models.script_deploy_log import ScriptDeployLog
from app.repositories import resource as resource_repo
from app.schemas.script_deploy import (
    ScriptDeployLogDetail,
    ScriptDeployLogList,
    ScriptDeployLogListItem,
    ScriptDeployRequest,
    ScriptDeployResponse,
    ScriptDeployStatus,
)
from app.services.network import script_deploy_service
from app.services.user import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/script-deploy", tags=["script-deploy"])


@router.post("/deploy", response_model=ScriptDeployResponse)
def deploy_service_template(
    request: ScriptDeployRequest,
    current_user: AdminUser,
) -> ScriptDeployResponse:
    """啟動服務模板的無人值守部署。

    從 GitHub community-scripts/ProxmoxVE 下載腳本，
    以無人值守方式在 Proxmox 節點上部署服務容器。
    """
    task_id = script_deploy_service.start_deployment(
        request_data=request.model_dump(),
        user_id=str(current_user.id),
    )

    return ScriptDeployResponse(
        task_id=task_id,
        message=f"部署任務已啟動：{request.template_slug} → {request.hostname}",
    )


@router.get("/status/{task_id}", response_model=ScriptDeployStatus)
def get_deploy_status(
    task_id: str,
    current_user: AdminUser,
) -> ScriptDeployStatus:
    """查詢部署任務的當前狀態。"""
    task = script_deploy_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="找不到該部署任務")

    return ScriptDeployStatus(
        task_id=task.task_id,
        status=task.status,
        progress=task.progress,
        vmid=task.vmid,
        message=task.message,
        error=task.error,
        output=task.output or None,
    )


@router.post("/cancel/{task_id}")
def cancel_deployment(
    task_id: str,
    current_user: AdminUser,
) -> dict:
    """取消正在執行的部署任務。

    觸發背景部署執行緒在最近的檢查點中斷 → 走 rollback 流程，
    自動銷毀已建立的容器並釋放預分配的 IP。
    """
    task = script_deploy_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="找不到該部署任務")
    if task.status != "running":
        raise HTTPException(
            status_code=400,
            detail=f"任務狀態為 {task.status}，僅 running 任務可以取消",
        )

    ok = script_deploy_service.cancel_task(task_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="取消請求送出失敗（任務可能剛剛結束或尚未進入可取消階段）",
        )

    logger.info(
        "User %s requested cancel for deployment task %s",
        current_user.email,
        task_id,
    )
    return {
        "task_id": task_id,
        "message": "取消請求已送出，部署將在最近的檢查點停止並回滾",
    }


@router.post("/register/{task_id}")
def register_deployed_resource(
    task_id: str,
    session: SessionDep,
    current_user: AdminUser,
) -> dict:
    """部署成功後，將容器註冊到資料庫。

    前端在確認部署成功（status=completed）後呼叫此端點，
    將新建的容器資訊寫入資料庫以便追蹤管理。
    """
    task = script_deploy_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="找不到該部署任務")
    if task.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="無權操作此部署任務")
    if task.status != "completed":
        raise HTTPException(status_code=400, detail="任務尚未完成或已失敗")
    if task.vmid is None:
        raise HTTPException(status_code=400, detail="無法取得 VMID")

    request_id: uuid.UUID | None = None
    if task.request_id:
        try:
            request_id = uuid.UUID(task.request_id)
        except ValueError:
            logger.warning("Invalid script deploy request_id ignored: %s", task.request_id)

    try:
        resource_repo.create_resource(
            session=session,
            vmid=task.vmid,
            user_id=current_user.id,
            environment_type=task.template_name or "服務模板",
            os_info=None,
            expiry_date=None,
            request_id=request_id,
            commit=False,
        )

        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            vmid=task.vmid,
            action="script_deploy",
            details=f"透過腳本部署服務模板，VMID: {task.vmid}",
            commit=False,
        )
        session.commit()

        return {
            "message": f"容器 VMID {task.vmid} 已成功註冊",
            "vmid": task.vmid,
        }
    except Exception as e:
        session.rollback()
        logger.error("註冊部署資源失敗: %s", e)
        raise HTTPException(status_code=500, detail=f"註冊資源失敗: {e}")


@router.get("/logs", response_model=ScriptDeployLogList)
def list_deploy_logs(
    session: SessionDep,
    current_user: AdminUser,  # noqa: ARG001 — required for AdminUser auth
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, description="running | completed | failed"),
    template_slug: str | None = None,
    vmid: int | None = None,
) -> ScriptDeployLogList:
    """列出歷史部署日誌（Admin 可見全部）。"""
    stmt = select(ScriptDeployLog)
    count_stmt = select(func.count()).select_from(ScriptDeployLog)

    if status:
        stmt = stmt.where(ScriptDeployLog.status == status)
        count_stmt = count_stmt.where(ScriptDeployLog.status == status)
    if template_slug:
        stmt = stmt.where(ScriptDeployLog.template_slug == template_slug)
        count_stmt = count_stmt.where(ScriptDeployLog.template_slug == template_slug)
    if vmid is not None:
        stmt = stmt.where(ScriptDeployLog.vmid == vmid)
        count_stmt = count_stmt.where(ScriptDeployLog.vmid == vmid)

    total = session.exec(count_stmt).one()
    stmt = stmt.order_by(ScriptDeployLog.created_at.desc()).offset(offset).limit(limit)
    rows = session.exec(stmt).all()

    items = [ScriptDeployLogListItem.model_validate(r, from_attributes=True) for r in rows]
    return ScriptDeployLogList(items=items, total=int(total), limit=limit, offset=offset)


@router.get("/logs/{task_id}", response_model=ScriptDeployLogDetail)
def get_deploy_log(
    task_id: str,
    session: SessionDep,
    current_user: AdminUser,  # noqa: ARG001 — required for AdminUser auth
) -> ScriptDeployLogDetail:
    """查詢單筆部署日誌詳細內容（含完整 output 與 error）。"""
    row = session.exec(
        select(ScriptDeployLog).where(ScriptDeployLog.task_id == task_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="找不到該部署日誌")
    return ScriptDeployLogDetail.model_validate(row, from_attributes=True)
