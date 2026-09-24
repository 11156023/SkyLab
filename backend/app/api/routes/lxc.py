import logging

from fastapi import APIRouter

from app.api.deps import ControlLxcInfoDep, CurrentUser
from app.exceptions import ProxmoxError
from app.schemas import (
    TemplateSchema,
    TerminalInfoSchema,
)
from app.services.proxmox import provisioning_service, proxmox_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lxc", tags=["lxc"])


@router.get("/{vmid}/terminal", response_model=TerminalInfoSchema)
def get_lxc_terminal(vmid: int, container_info: ControlLxcInfoDep):
    """Get terminal access for an LXC container (owner, admin, or shared user)."""
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

