import logging
import uuid

from fastapi import APIRouter

from app.api.deps import (
    AdminUser,
    CurrentUser,
    ResourceInfoDep,
    SessionDep,
)
from app.core.authorizers import can_bypass_resource_ownership, require_resource_access
from app.core.security import decrypt_value
from app.exceptions import NotFoundError, PermissionDeniedError, ProxmoxError
from app.infrastructure.worker import submit_sync
from app.models import DeletionRequestStatus
from app.repositories import resource as resource_repo
from app.schemas import NodeSchema, ResourcePublic, SSHKeyResponse
from app.schemas.deletion_request import DeletionRequestCreated
from app.schemas.resource import (
    BatchActionRequest,
    BatchActionResponse,
    ExtendSessionResponse,
    SessionStatusResponse,
)
from app.services.proxmox import proxmox_service
from app.services.resource import deletion_service, resource_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resources", tags=["resources"])


@router.get("/nodes", response_model=list[NodeSchema])
def list_nodes(current_user: AdminUser):
    try:
        return proxmox_service.list_nodes()
    except Exception as e:
        logger.error(f"Failed to get nodes: {e}")
        raise ProxmoxError("Failed to get nodes")


@router.get("/", response_model=list[ResourcePublic])
def list_resources(
    session: SessionDep, current_user: AdminUser, node: str | None = None
):
    return resource_service.list_all(session=session, node=node)


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
        user_id=current_user.id,
        is_admin=current_user.is_superuser,
    )


@router.get("/{vmid}", response_model=ResourcePublic)
def get_resource(vmid: int, resource_info: ResourceInfoDep, session: SessionDep):
    return resource_service.get_by_vmid(
        session=session, vmid=vmid, resource_info=resource_info
    )


@router.get("/{vmid}/config")
def get_resource_config(vmid: int, resource_info: ResourceInfoDep):
    return resource_service.get_config(vmid=vmid, resource_info=resource_info)


@router.post("/{vmid}/start")
def start_resource(
    vmid: int,
    resource_info: ResourceInfoDep,
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
    resource_info: ResourceInfoDep,
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
    resource_info: ResourceInfoDep,
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
    resource_info: ResourceInfoDep,
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
    resource_info: ResourceInfoDep,
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
    """將刪除請求加入佇列，立即 202 回應，並在背景馬上開始執行。

    - 主路徑：API 寫入 DeletionRequest 後，立即 fire-and-forget 一個背景 task
      呼叫 ``deletion_service.process_one_request``，無需等 scheduler tick。
    - 兜底：scheduler 每隔 ``SCHEDULER_POLL_SECONDS`` 仍會掃描 pending request，
      涵蓋 server restart / 背景任務失敗的情況。
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
            require_resource_access(current_user, db_resource.user_id)
        except PermissionDeniedError:
            logger.warning(
                "User %s attempted to delete resource %s owned by %s",
                current_user.email, vmid, db_resource.user_id,
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
    # Only kick the background task for freshly queued requests; if an existing
    # pending/running request was returned (deduplication), it's already being
    # handled.
    if req.status == DeletionRequestStatus.pending:
        submit_sync(
            deletion_service.process_one_request,
            req.id,
            name=f"delete_resource:{vmid}",
            task_id=str(req.id),
            # Retries are handled inside process_one_request itself; no
            # need for the runner to retry on top of that.
            max_retries=0,
        )
    return DeletionRequestCreated(
        id=req.id,
        vmid=req.vmid,
        status=req.status,
        message="Deletion request queued",
    )


@router.get("/{vmid}/session-status", response_model=SessionStatusResponse)
def get_session_status(
    vmid: int,
    resource_info: ResourceInfoDep,
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
    """取得資源的 SSH 金鑰（包含私鑰，僅限資源擁有者或管理員）"""
    db_resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    if not db_resource:
        raise ProxmoxError("Resource not found in database")

    private_key: str | None = None
    if db_resource.ssh_private_key_encrypted:
        private_key = decrypt_value(db_resource.ssh_private_key_encrypted)

    return SSHKeyResponse(
        vmid=vmid,
        ssh_public_key=db_resource.ssh_public_key,
        ssh_private_key=private_key,
    )
