"""Operation-level access rules for personal and teaching-class resources.

師生關係只有一種來源：TeachingClass.owner_id 是老師，Resource.teaching_class_id
把機器掛到班級。老師對班級底下每台學生機器都有擁有者層級的管理權；學生對
課堂機只有使用權（開關機、主控台），防火牆、快照、規格這類設定不開放。
"""

import logging
import uuid
from typing import Any

from sqlmodel import Session, select

from app.core.authorizers import (
    can_bypass_resource_ownership,
    require_resource_access,
    require_teaching_access,
)
from app.core.i18n import t
from app.exceptions import PermissionDeniedError
from app.models import Resource, TeachingClass, TeachingClassStatus
from app.repositories import resource as resource_repo

logger = logging.getLogger(__name__)


def require_resource_ownership(*, session: Session, user: Any, vmid: int) -> None:
    """擁有者層級的存取（``api.deps.proxmox.check_resource_ownership`` 的本體）。

    管理員一律通過；課堂機的學生擁有者在班級仍 active 時可用，班級老師可用；
    個人機只看 ``user_id``。不符合時拋 PermissionDeniedError。
    """
    if can_bypass_resource_ownership(user):
        return

    # Check if the resource exists in the database
    db_resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)

    if not db_resource:
        # Resource not in database - deny access for non-superusers
        logger.warning(
            f"User {user.email} attempted to access unregistered resource {vmid}"
        )
        raise PermissionDeniedError(t("resource_access.no_permission"))

    if db_resource.teaching_class_id:
        teaching_class = session.get(TeachingClass, db_resource.teaching_class_id)
        if teaching_class is None:
            raise PermissionDeniedError(
                t("resource_access.teaching_class_unassigned")
            )
        if db_resource.user_id == user.id:
            if teaching_class.status != TeachingClassStatus.active:
                raise PermissionDeniedError(
                    t("resource_access.teaching_class_inactive")
                )
            return
        require_teaching_access(user, teaching_class.owner_id)
        return

    if db_resource.allocation_scope == "teaching_class":
        raise PermissionDeniedError(t("resource_access.teaching_class_scope_lost"))

    try:
        require_resource_access(user, db_resource.user_id)
    except PermissionDeniedError:
        logger.warning(
            f"User {user.email} attempted to access resource {vmid} "
            f"owned by user {db_resource.user_id}"
        )
        raise


def require_resource_use(*, session: Session, user: Any, vmid: int) -> None:
    """使用層級的存取：擁有者／管理員之外，被分享的使用者也能開關機與開主控台。

    ``api.deps.proxmox.check_resource_control_access`` 與批次電源操作共用這一份。
    憑證、快照、規格、對外服務等擁有者層級的操作仍走 ``require_resource_ownership``。
    """
    try:
        require_resource_ownership(session=session, user=user, vmid=vmid)
        return
    except PermissionDeniedError:
        from app.services.resource import sharing_service

        if sharing_service.user_has_share(session=session, vmid=vmid, user_id=user.id):
            return
        raise


def require_resource_management(
    *, session: Session, user: Any, vmid: int
) -> None:
    """Require ownership-level control, not merely class-member use access."""
    if can_bypass_resource_ownership(user):
        return
    resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    if resource is None:
        raise PermissionDeniedError(
            "You don't have permission to manage this resource"
        )
    if resource.teaching_class_id:
        teaching_class = session.get(TeachingClass, resource.teaching_class_id)
        if teaching_class is None:
            raise PermissionDeniedError(
                "This teaching-class resource is no longer assigned"
            )
        require_teaching_access(
            user,
            teaching_class.owner_id,
            detail="Only the class teacher or an administrator can manage this resource",
        )
        return
    if resource.allocation_scope == "teaching_class":
        raise PermissionDeniedError(
            "This teaching-class resource has lost its class assignment; "
            "an administrator must reclaim it"
        )
    require_resource_access(user, resource.user_id)


def list_teaching_class_ids_owned_by(
    *, session: Session, user_id: uuid.UUID
) -> set[uuid.UUID]:
    """``user_id`` 擔任老師（owner）的班級 id 集合。"""
    stmt = select(TeachingClass.id).where(TeachingClass.owner_id == user_id)
    return set(session.exec(stmt).all())


def list_owned_teaching_class_ids(*, session: Session, user: Any) -> set[uuid.UUID]:
    """使用者擔任老師（owner）的班級 id 集合。"""
    return list_teaching_class_ids_owned_by(session=session, user_id=user.id)


def can_manage_resource(
    *, resource: Resource, user: Any, owned_class_ids: set[uuid.UUID]
) -> bool:
    """require_resource_management 的無例外版本，供清單批次標示 can_manage。

    規則必須與 require_resource_management 完全一致：
    - admin 一律可管
    - 課堂機只看班級老師，機器的 user_id（學生）不算
    - 失去班級歸屬的課堂機沒人能管，等管理員回收
    - 個人機只看 user_id
    """
    if can_bypass_resource_ownership(user):
        return True
    if resource.teaching_class_id:
        return resource.teaching_class_id in owned_class_ids
    if resource.allocation_scope == "teaching_class":
        return False
    return bool(resource.user_id == user.id)


def list_reachable_resources(*, session: Session, user: Any) -> list[Resource]:
    """防火牆／NAT／反向代理清單的可見範圍。

    自己的機器（含自己在課堂裡分到的機器）∪ 我擔任老師的班級底下所有學生機器；
    admin 看全部。老師對後者本來就有管理權（require_resource_management），
    這裡只是讓清單跟操作權一致，不然老師在拓撲上根本選不到學生的機器。
    """
    if can_bypass_resource_ownership(user):
        return resource_repo.get_all_resources(session=session)
    own = resource_repo.get_resources_by_user(session=session, user_id=user.id)
    class_ids = list_owned_teaching_class_ids(session=session, user=user)
    if not class_ids:
        return own
    taught = resource_repo.get_resources_by_teaching_classes(
        session=session, teaching_class_ids=class_ids
    )
    seen = {r.vmid for r in own}
    return own + [r for r in taught if r.vmid not in seen]


def list_reachable_vmids(*, session: Session, user: Any) -> set[int]:
    return {r.vmid for r in list_reachable_resources(session=session, user=user)}


__all__ = [
    "can_manage_resource",
    "list_owned_teaching_class_ids",
    "list_reachable_resources",
    "list_reachable_vmids",
    "list_teaching_class_ids_owned_by",
    "require_resource_management",
    "require_resource_ownership",
    "require_resource_use",
]
