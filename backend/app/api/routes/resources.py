import logging
import uuid
from typing import Any

from fastapi import APIRouter

from app.api.deps import (
    AdminUser,
    ControlResourceInfoDep,
    CurrentUser,
    ResourceInfoDep,
    SessionDep,
)
from app.core.authorizers import can_bypass_resource_ownership
from app.core.security import decrypt_value
from app.exceptions import NotFoundError, PermissionDeniedError, ProxmoxError
from app.models import DeletionRequestStatus
from app.repositories import resource as resource_repo
from app.schemas import ResourcePublic, SSHKeyResponse
from app.schemas.deletion_request import DeletionRequestCreated
from app.schemas.resource import (
    BatchActionRequest,
    BatchActionResponse,
    ExtendSessionResponse,
    SessionStatusResponse,
)
from app.services.proxmox import proxmox_service
from app.services.resource import deletion_service, resource_service
from app.services.resource.access import require_resource_management
from app.services.template import password_policy

logger = logging.getLogger(__name__)

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

    - 主路徑：API 寫入 DeletionRequest 後入列 ``resource.delete`` 任務，worker
      呼叫 ``deletion_service.process_one_request``，無需等 scheduler tick。
    - 兜底：scheduler 每隔 ``SCHEDULER_POLL_SECONDS`` 仍會掃描 pending request，
      涵蓋入列失敗 / worker 重啟的情況；pending→running 是條件式認領，不會重複執行。
    - 孤兒清理：若 VM 在 Proxmox 已不存在但 DB 仍有記錄，直接清理 DB 並回 202。
    """
    # Check DB ownership first (without requiring Proxmox to be available)
    db_resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    is_admin = can_bypass_resource_ownership(current_user)

    if db_resource is None:
        if not is_admin:
            raise NotFoundError(f"Resource {vmid} not found")
        # Admin deleting an orphan resource (exists in Proxmox but not in DB).
        # Fall through to locate it in Proxmox below.
        logger.info(
            "Admin %s deleting orphan resource %s (no DB record)",
            current_user.email, vmid,
        )
    elif not is_admin:
        try:
            require_resource_management(
                session=session, user=current_user, vmid=vmid
            )
        except PermissionDeniedError:
            logger.warning(
                "User %s attempted to delete resource %s without management rights",
                current_user.email, vmid,
            )
            raise

    # Try to locate the VM in Proxmox.
    # - If gone and DB record exists → clean up orphan DB record.
    # - If gone and no DB record → nothing to do.
    try:
        resource_info = proxmox_service.find_resource(vmid)
    except NotFoundError:
        if db_resource is not None:
            logger.warning(
                "Resource %s not found in Proxmox; cleaning up orphan DB record", vmid
            )
            resource_service.delete_orphan_db_record(
                session=session, vmid=vmid, user_id=current_user.id
            )
        else:
            logger.info("Resource %s not found in Proxmox and no DB record; nothing to clean up", vmid)
        return DeletionRequestCreated(
            id=uuid.uuid4(),
            vmid=vmid,
            status=DeletionRequestStatus.completed,
            message="Orphan DB record cleaned up (VM already removed from Proxmox)",
        )

    req = deletion_service.create_deletion_request(
        session=session,
        user_id=current_user.id,
        vmid=vmid,
        resource_info=resource_info,
        purge=purge,
        force=force,
    )
    # 只對剛建立的 pending 單入列；去重回傳的既有 pending/running 單已在處理中
    deletion_service.enqueue_processing(session=session, req=req)
    return DeletionRequestCreated(
        id=req.id,
        vmid=req.vmid,
        status=req.status,
        message="Deletion request queued",
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
    db_resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    if not db_resource:
        raise ProxmoxError("Resource not found in database")

    private_key: str | None = None
    if db_resource.ssh_private_key_encrypted:
        private_key = decrypt_value(db_resource.ssh_private_key_encrypted)
    login_password: str | None = None
    if db_resource.login_password_encrypted:
        login_password = decrypt_value(db_resource.login_password_encrypted)

    source_template = (
        password_policy.find_template(session, pve_vmid=db_resource.template_id)
        if login_password is None
        else None
    )
    return SSHKeyResponse(
        vmid=vmid,
        ssh_public_key=db_resource.ssh_public_key,
        ssh_private_key=private_key,
        login_password=login_password,
        login_password_pending=bool(
            login_password is None and db_resource.login_password_pending_encrypted
        ),
        uses_template_credentials=password_policy.keeps_template_credentials(
            source_template
        ),
    )
