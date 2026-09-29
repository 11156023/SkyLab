from typing import Annotated

from fastapi import Depends

from app.api.deps.auth import CurrentUser
from app.api.deps.database import SessionDep
from app.services.proxmox import proxmox_service
from app.services.resource.access import (
    require_resource_management,
    require_resource_ownership,
    require_resource_use,
)


def check_resource_ownership(
    vmid: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    """擁有者層級的存取；規則本體在 ``services.resource.access``。

    Raises PermissionDeniedError if the user doesn't have permission.
    """
    require_resource_ownership(session=session, user=current_user, vmid=vmid)


def get_resource_info(
    vmid: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> dict:
    """Get resource info with permission check (requires ownership or admin)."""
    check_resource_ownership(vmid, current_user, session)
    return proxmox_service.find_resource(vmid)


ResourceInfoDep = Annotated[dict, Depends(get_resource_info)]


def check_resource_control_access(
    vmid: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    """使用層級的存取：擁有者／管理員之外，被分享的使用者也能開關機與開主控台。

    只用在電源控制、主控台、即時監控這些「用機器」的端點；憑證、快照、
    規格、對外服務等擁有者層級的操作仍走 ``check_resource_ownership``。
    規則本體在 ``services.resource.access.require_resource_use``。
    """
    require_resource_use(session=session, user=current_user, vmid=vmid)


def get_resource_info_controllable(
    vmid: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> dict:
    """Resource info for control-level access (owner, admin, or shared user)."""
    check_resource_control_access(vmid, current_user, session)
    return proxmox_service.find_resource(vmid)


ControlResourceInfoDep = Annotated[dict, Depends(get_resource_info_controllable)]

# VM 主控台用的名稱；與 ControlResourceInfoDep 是同一個 dependency。
ControlVmInfoDep = ControlResourceInfoDep


def get_lxc_info_controllable(
    vmid: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> dict:
    """LXC info for terminal access (owner, admin, or shared user)."""
    check_resource_control_access(vmid, current_user, session)
    return proxmox_service.find_lxc(vmid)


ControlLxcInfoDep = Annotated[dict, Depends(get_lxc_info_controllable)]


def check_firewall_access(
    vmid: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    require_resource_management(session=session, user=current_user, vmid=vmid)


def get_resource_info_teaching(
    vmid: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> dict:
    """owner / 群組老師 / admin 皆可通過的資源資訊 dep（模組 E）。"""
    from app.services.teaching import access as teaching_access

    teaching_access.require_vm_teaching_access(session, current_user, vmid)
    return proxmox_service.find_resource(vmid)


TeachingResourceInfoDep = Annotated[dict, Depends(get_resource_info_teaching)]
