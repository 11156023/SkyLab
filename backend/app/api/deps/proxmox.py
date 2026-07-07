import logging
from typing import Annotated

from fastapi import Depends

from app.api.deps.auth import CurrentUser
from app.api.deps.database import SessionDep
from app.core.authorizers import (
    can_bypass_resource_ownership,
    require_resource_access,
)
from app.exceptions import PermissionDeniedError
from app.repositories import resource as resource_repo
from app.services.proxmox import proxmox_service

logger = logging.getLogger(__name__)


def check_resource_ownership(
    vmid: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    """
    Check if the current user owns the resource or is a superuser.
    Raises PermissionDeniedError if the user doesn't have permission.
    """
    if can_bypass_resource_ownership(current_user):
        return

    # Check if the resource exists in the database
    db_resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)

    if not db_resource:
        # Resource not in database - deny access for non-superusers
        logger.warning(
            f"User {current_user.email} attempted to access unregistered resource {vmid}"
        )
        raise PermissionDeniedError(
            "You don't have permission to access this resource"
        )

    try:
        require_resource_access(current_user, db_resource.user_id)
    except PermissionDeniedError:
        logger.warning(
            f"User {current_user.email} attempted to access resource {vmid} "
            f"owned by user {db_resource.user_id}"
        )
        raise


def get_vm_info(
    vmid: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> dict:
    """Get VM info with permission check (requires ownership or admin)."""
    check_resource_ownership(vmid, current_user, session)
    return proxmox_service.find_resource(vmid)


VmInfoDep = Annotated[dict, Depends(get_vm_info)]


def get_lxc_info(
    vmid: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> dict:
    """Get LXC info with permission check (requires ownership or admin)."""
    check_resource_ownership(vmid, current_user, session)
    return proxmox_service.find_lxc(vmid)


LxcInfoDep = Annotated[dict, Depends(get_lxc_info)]


def get_resource_info(
    vmid: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> dict:
    """Get resource info with permission check (requires ownership or admin)."""
    check_resource_ownership(vmid, current_user, session)
    return proxmox_service.find_resource(vmid)


ResourceInfoDep = Annotated[dict, Depends(get_resource_info)]


def check_firewall_access(
    vmid: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    check_resource_ownership(vmid, current_user, session)


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
