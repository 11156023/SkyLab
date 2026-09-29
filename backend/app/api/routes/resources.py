from typing import Any

from fastapi import APIRouter

from app.api.deps import (
    AdminUser,
    ControlResourceInfoDep,
    CurrentUser,
    ResourceInfoDep,
    SessionDep,
)
from app.schemas import ResourcePublic, SSHKeyResponse
from app.schemas.deletion_request import DeletionRequestCreated
from app.schemas.resource import (
    BatchActionRequest,
    BatchActionResponse,
    ExtendSessionResponse,
    SessionStatusResponse,
)
from app.services.resource import (
    credentials_service,
    deletion_service,
    resource_service,
)

router = APIRouter(prefix="/resources", tags=["resources"])


@router.get("/", response_model=list[ResourcePublic])
def list_resources(
    session: SessionDep, current_user: AdminUser, node: str | None = None
):
    return resource_service.list_all(
        session=session, node=node, viewer_id=current_user.id
    )


@router.get("/my", response_model=list[ResourcePublic])
def list_my_resources(session: SessionDep, current_user: CurrentUser):
    return resource_service.list_by_user(
        session=session, user_id=current_user.id
    )


@router.get("/my/session-status", response_model=list[SessionStatusResponse])
def list_my_session_statuses(session: SessionDep, current_user: CurrentUser):
    """本人所有執行中機器的關機／到期警告狀態（取代逐台輪詢 /{vmid}/session-status）。"""
    return resource_service.list_my_session_statuses(
        session=session, user_id=current_user.id
    )


@router.post("/batch", response_model=BatchActionResponse)
def batch_action(
    body: BatchActionRequest,
    session: SessionDep,
    current_user: CurrentUser,
):
    """Batch VM/LXC operations: start, stop, shutdown, reboot, reset, delete."""
    return resource_service.batch_action(
        session=session,
        vmids=body.vmids,
        action=body.action,
        user=current_user,
    )


@router.get("/{vmid}", response_model=ResourcePublic)
def get_resource(
    vmid: int,
    resource_info: ControlResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    public = resource_service.get_by_vmid(
        session=session, vmid=vmid, resource_info=resource_info
    )
    return resource_service.annotate_access_for_user(
        session=session, public=public, user=current_user
    )


@router.get("/{vmid}/config")
def get_resource_config(
    vmid: int, resource_info: ResourceInfoDep
) -> dict[str, Any]:
    """顯示用的機器設定（service 已過白名單，cloud-init 憑證不會外流）。"""
    return resource_service.get_config(vmid=vmid, resource_info=resource_info)


@router.post("/{vmid}/start")
def start_resource(
    vmid: int,
    resource_info: ControlResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    return resource_service.control(
        session=session,
        vmid=vmid,
        action="start",
        resource_info=resource_info,
        user_id=current_user.id,
    )


@router.post("/{vmid}/stop")
def stop_resource(
    vmid: int,
    resource_info: ControlResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    return resource_service.control(
        session=session,
        vmid=vmid,
        action="stop",
        resource_info=resource_info,
        user_id=current_user.id,
    )


@router.post("/{vmid}/reboot")
def reboot_resource(
    vmid: int,
    resource_info: ControlResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    return resource_service.control(
        session=session,
        vmid=vmid,
        action="reboot",
        resource_info=resource_info,
        user_id=current_user.id,
    )


@router.post("/{vmid}/shutdown")
def shutdown_resource(
    vmid: int,
    resource_info: ControlResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    return resource_service.control(
        session=session,
        vmid=vmid,
        action="shutdown",
        resource_info=resource_info,
        user_id=current_user.id,
    )


@router.post("/{vmid}/reset")
def reset_resource(
    vmid: int,
    resource_info: ControlResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    return resource_service.control(
        session=session,
        vmid=vmid,
        action="reset",
        resource_info=resource_info,
        user_id=current_user.id,
    )


@router.delete("/{vmid}", response_model=DeletionRequestCreated, status_code=202)
def delete_resource(
    vmid: int,
    session: SessionDep,
    current_user: CurrentUser,
    purge: bool = True,
    force: bool = False,
):
    """將刪除請求加入佇列，立即 202 回應，並由 arq worker 馬上開始執行。

    VM 在 Proxmox 已不存在但 DB 仍有記錄時直接清掉孤兒記錄並回 202；
    流程細節見 ``deletion_service.request_deletion``。
    """
    return deletion_service.request_deletion(
        session=session, user=current_user, vmid=vmid, purge=purge, force=force
    )


@router.get("/{vmid}/session-status", response_model=SessionStatusResponse)
def get_session_status(
    vmid: int,
    resource_info: ControlResourceInfoDep,
    session: SessionDep,
    _current_user: CurrentUser,
):
    """Live auto-stop status used by the student UI to show the warning dialog."""
    return resource_service.get_session_status(
        session=session, vmid=vmid, resource_info=resource_info
    )


@router.post("/{vmid}/extend-session", response_model=ExtendSessionResponse)
def extend_session(
    vmid: int,
    session: SessionDep,
    current_user: CurrentUser,
    _resource_info: ResourceInfoDep,
):
    """Add another practice quota window. Only valid mid-practice-session."""
    return resource_service.extend_session(
        session=session, vmid=vmid, user_id=current_user.id
    )


@router.get("/{vmid}/ssh-key", response_model=SSHKeyResponse)
def get_ssh_key(
    vmid: int,
    session: SessionDep,
    _current_user: CurrentUser,
    _resource_info: ResourceInfoDep,
):
    """取得資源的登入憑證（SSH 私鑰與初始密碼，僅限資源擁有者或管理員）"""
    return credentials_service.get_ssh_key(session=session, vmid=vmid)
