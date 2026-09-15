"""Operation-level access rules for personal and teaching-class resources.

師生關係只有一種來源：TeachingClass.owner_id 是老師，Resource.teaching_class_id
把機器掛到班級。老師對班級底下每台學生機器都有擁有者層級的管理權；學生對
課堂機只有使用權（開關機、主控台），防火牆、快照、規格這類設定不開放。
"""

import uuid
from typing import Any

from sqlmodel import Session, select

from app.core.authorizers import (
    can_bypass_resource_ownership,
    require_resource_access,
    require_teaching_access,
)
from app.exceptions import PermissionDeniedError
from app.models import Resource, TeachingClass
from app.repositories import resource as resource_repo


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


def list_owned_teaching_class_ids(*, session: Session, user: Any) -> set[uuid.UUID]:
    """使用者擔任老師（owner）的班級 id 集合。"""
    stmt = select(TeachingClass.id).where(TeachingClass.owner_id == user.id)
    return set(session.exec(stmt).all())


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
    "require_resource_management",
]
