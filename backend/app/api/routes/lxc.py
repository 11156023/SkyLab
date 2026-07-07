import logging
import uuid

from fastapi import APIRouter
from sqlmodel import Session

from app.api.deps import AdminUser, CurrentUser, LxcInfoDep, SessionDep
from app.exceptions import ProxmoxError
from app.infrastructure.worker import background_tasks
from app.schemas import (
    LXCCreateRequest,
    LXCCreateResponse,
    TemplateSchema,
    TerminalInfoSchema,
)
from app.services.proxmox import provisioning_service, proxmox_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lxc", tags=["lxc"])


@router.get("/{vmid}/terminal", response_model=TerminalInfoSchema)
def get_lxc_terminal(vmid: int, container_info: LxcInfoDep):
    """Get terminal access for an LXC container (requires ownership or admin)."""
    try:
        node = container_info["node"]
        console_data = proxmox_service.get_terminal_ticket(node, vmid)

        return {
            "vmid": vmid,
            "ws_url": f"/ws/terminal/{vmid}/",
            "ticket": console_data["ticket"],
            "message": "Connect to this WebSocket URL to access the LXC terminal",
        }
    except ProxmoxError:
        raise
    except Exception as e:
        logger.error(f"Failed to get terminal for LXC {vmid}: {e}")
        raise ProxmoxError("Failed to get LXC terminal")


@router.get("/templates", response_model=list[TemplateSchema])
def get_templates(current_user: CurrentUser):
    return provisioning_service.get_lxc_templates()


def _run_create_lxc(lxc_data: LXCCreateRequest, user_id: uuid.UUID) -> None:
    """背景執行 LXC 建立（route session 不可跨執行緒，開獨立 session）。"""
    from app.core.db import engine  # noqa: PLC0415 — 避免 import cycle

    try:
        with Session(engine) as task_session:
            provisioning_service.create_lxc(
                session=task_session, lxc_data=lxc_data, user_id=user_id
            )
    except Exception:
        logger.exception(
            "Background LXC create failed for hostname=%s", lxc_data.hostname
        )


@router.post("/create", status_code=202, response_model=LXCCreateResponse)
def create_lxc(
    lxc_data: LXCCreateRequest, session: SessionDep, current_user: AdminUser
):
    """建立 LXC（202：建立於背景執行，前端以資源列表輪詢進度）。"""
    task_id = background_tasks.submit_sync(
        _run_create_lxc,
        lxc_data,
        current_user.id,
        name="admin-create-lxc",
    )
    return LXCCreateResponse(
        task_id=task_id or None,
        message="LXC 建立中，請稍後於資源列表查看",
    )
