"""教學情境的 VM 存取檢查：owner 本人、所屬群組的老師、或 admin。

與 ``api/deps/proxmox.check_resource_ownership``（owner/admin only）的差異：
多放行「VM 擁有者所屬群組的 owner（老師）」，供 E1 重置、E2 分發、
E4 快照管理、E6 批次調整共用。
"""

from __future__ import annotations

import logging

from sqlmodel import Session

from app.core.authorizers import can_bypass_resource_ownership
from app.exceptions import NotFoundError, PermissionDeniedError
from app.models import Resource, User
from app.repositories import group as group_repo

logger = logging.getLogger(__name__)


def require_vm_teaching_access(session: Session, user: User, vmid: int) -> Resource:
    resource = session.get(Resource, vmid)
    if resource is None:
        raise NotFoundError(f"Resource {vmid} not found")
    if resource.user_id == user.id:
        return resource
    if can_bypass_resource_ownership(user):
        return resource
    if group_repo.is_user_in_any_owned_group(
        session=session, instructor_id=user.id, member_user_id=resource.user_id
    ):
        return resource
    logger.warning(
        "User %s denied teaching access to resource %s", user.id, vmid
    )
    raise PermissionDeniedError("You don't have permission to manage this resource")
