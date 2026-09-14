import logging
from typing import Annotated

from fastapi import Depends

from app.api.deps.auth import CurrentUser
from app.api.deps.database import SessionDep
from app.core.authorizers import (
    can_bypass_resource_ownership,
    require_resource_access,
    require_teaching_access,
)
from app.core.i18n import t
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
        raise PermissionDeniedError(t("resource_access.no_permission"))

    if db_resource.teaching_class_id:
        from app.models import TeachingClass, TeachingClassStatus

        teaching_class = session.get(TeachingClass, db_resource.teaching_class_id)
        if teaching_class is None:
            raise PermissionDeniedError(
                t("resource_access.teaching_class_unassigned")
            )
        if db_resource.user_id == current_user.id:
            if teaching_class.status != TeachingClassStatus.active:
                raise PermissionDeniedError(
                    t("resource_access.teaching_class_inactive")
                )
            return
        require_teaching_access(current_user, teaching_class.owner_id)
        return

    if db_resource.allocation_scope == "teaching_class":
        raise PermissionDeniedError(t("resource_access.teaching_class_scope_lost"))

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


def check_resource_control_access(
    vmid: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    """使用層級的存取：擁有者／管理員之外，被分享的使用者也能開關機與開主控台。

    只用在電源控制、主控台、即時監控這些「用機器」的端點；憑證、快照、
    規格、對外服務等擁有者層級的操作仍走 ``check_resource_ownership``。
    """
    try:
        check_resource_ownership(vmid, current_user, session)
        return
    except PermissionDeniedError:
        from app.services.resource import sharing_service  # noqa: PLC0415

        if sharing_service.user_has_share(
            session=session, vmid=vmid, user_id=current_user.id
        ):
            return
        raise


def get_resource_info_controllable(
    vmid: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> dict:
    """Resource info for control-level access (owner, admin, or shared user)."""
    check_resource_control_access(vmid, current_user, session)
    return proxmox_service.find_resource(vmid)


ControlResourceInfoDep = Annotated[dict, Depends(get_resource_info_controllable)]


def get_vm_info_controllable(
    vmid: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> dict:
    """VM info for console access (owner, admin, or shared user)."""
    check_resource_control_access(vmid, current_user, session)
    return proxmox_service.find_resource(vmid)


ControlVmInfoDep = Annotated[dict, Depends(get_vm_info_controllable)]


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
    from app.services.resource.access import require_resource_management

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
