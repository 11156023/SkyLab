import logging
import uuid

from fastapi import APIRouter
from sqlmodel import Session

from app.api.deps import AdminUser, CurrentUser, SessionDep, VmInfoDep
from app.api.websocket.vnc import register_vnc_session_cookie
from app.exceptions import BadRequestError, ProxmoxError
from app.infrastructure.worker import background_tasks
from app.schemas import (
    VMCreateRequest,
    VMCreateResponse,
    VMTemplateSchema,
    VNCInfoSchema,
)
from app.services.proxmox import provisioning_service, proxmox_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vm", tags=["vm"])


@router.get("/{vmid}/console", response_model=VNCInfoSchema)
async def get_vm_console(vmid: int, vm_info: VmInfoDep):
    """Get VNC console access for a VM (requires ownership or admin)."""
    try:
        if vm_info["type"] != "qemu":
            raise BadRequestError(f"Resource {vmid} is not a QEMU VM")

        node = vm_info["node"]
        pve_auth_cookie, csrf_token = await proxmox_service.get_session_ticket()
        console_data = await proxmox_service.get_vnc_ticket_with_session(
            node,
            vmid,
            pve_auth_cookie,
            csrf_token,
        )
        register_vnc_session_cookie(vmid, str(console_data["ticket"]), pve_auth_cookie)

        return {
            "vmid": vmid,
            "ws_url": f"/ws/vnc/{vmid}/",
            "ticket": console_data["ticket"],
            "port": str(console_data["port"]),
            "message": "Connect to this WebSocket URL to access the VM console",
        }
    except (BadRequestError, ProxmoxError):
        raise
    except Exception as e:
        logger.error(f"Failed to get console for VM {vmid}: {e}")
        raise ProxmoxError("Failed to get VM console")


def _run_create_vm(vm_data: VMCreateRequest, user_id: uuid.UUID) -> None:
    """背景執行 VM clone（route session 不可跨執行緒，開獨立 session）。"""
    from app.core.db import engine  # noqa: PLC0415 — 避免 import cycle

    try:
        with Session(engine) as task_session:
            provisioning_service.create_vm(
                session=task_session, vm_data=vm_data, user_id=user_id
            )
    except Exception:
        logger.exception(
            "Background VM create failed for hostname=%s", vm_data.hostname
        )


@router.post("/create", status_code=202, response_model=VMCreateResponse)
def create_vm(
    vm_data: VMCreateRequest, session: SessionDep, current_user: AdminUser
):
    """建立 VM（202：clone 於背景執行，前端以資源列表輪詢進度）。"""
    task_id = background_tasks.submit_sync(
        _run_create_vm,
        vm_data,
        current_user.id,
        name="admin-create-vm",
    )
    return VMCreateResponse(
        task_id=task_id or None,
        message="VM 建立中，請稍後於資源列表查看",
    )


@router.get("/templates", response_model=list[VMTemplateSchema])
def get_vm_templates(current_user: CurrentUser):
    return provisioning_service.get_vm_templates()
