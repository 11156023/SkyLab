import logging
import uuid

from fastapi import APIRouter
from sqlmodel import Session

from app.api.deps import AdminUser, ControlVmInfoDep, CurrentUser, SessionDep
from app.api.websocket.vnc import register_vnc_session_cookie
from app.core.i18n import t
from app.core.permissions import Permission, has_permission
from app.exceptions import BadRequestError, ProxmoxError
from app.infrastructure.worker import background_tasks
from app.repositories import vm_template as vm_template_repo
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
async def get_vm_console(vmid: int, vm_info: ControlVmInfoDep):
    """Get VNC console access for a VM (owner, admin, or shared user)."""
    try:
        if vm_info["type"] != "qemu":
            raise BadRequestError(f"Resource {vmid} is not a QEMU VM")

        node = vm_info["node"]
        pve_auth_cookie, csrf_token = await proxmox_service.get_session_ticket(node)
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
        message=t("vm.creating"),
    )


@router.get("/templates", response_model=list[VMTemplateSchema])
def get_vm_templates(session: SessionDep, current_user: CurrentUser):
    """PVE 上的 VM 基礎映像。

    平台已註冊的單機母範本也是 PVE template，但它們的可見範圍由範本系統治理，
    不能從這裡外洩：非教師只拿得到未註冊的基礎映像，母範本另由
    ``/templates/catalog`` 依「開放學生申請」旗標提供。
    """
    templates = provisioning_service.get_vm_templates()
    if has_permission(current_user, Permission.TEMPLATE_MANAGE):
        return templates
    registered = vm_template_repo.registered_pve_vmids(session=session)
    return [item for item in templates if item.vmid not in registered]
