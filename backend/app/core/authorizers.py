from __future__ import annotations

import uuid
from typing import Any

from app.core.permissions import (
    Permission,
    can_access_owner_resource,
    get_user_role,
    has_permission,
    is_admin,
    is_teacher,
    require_owner_or_permission,
    require_permission,
)
from app.exceptions import PermissionDeniedError
from app.models import UserRole


def can_manage_users(user: Any) -> bool:
    return has_permission(user, Permission.USER_MANAGE)


def require_user_manage(
    user: Any,
    *,
    detail: str = "The user doesn't have enough privileges",
) -> None:
    require_permission(user, Permission.USER_MANAGE, detail=detail)


def can_bypass_resource_ownership(user: Any) -> bool:
    return has_permission(user, Permission.RESOURCE_OWNERSHIP_BYPASS)


def require_resource_access(
    user: Any,
    owner_id: uuid.UUID | None,
    *,
    detail: str = "You don't have permission to access this resource",
) -> None:
    require_owner_or_permission(
        user,
        owner_id,
        bypass_permission=Permission.RESOURCE_OWNERSHIP_BYPASS,
        detail=detail,
    )


def can_bypass_group_ownership(user: Any) -> bool:
    return has_permission(user, Permission.GROUP_OWNERSHIP_BYPASS)


def require_group_access(
    user: Any,
    owner_id: uuid.UUID | None,
    *,
    detail: str = "Not authorized to access this group",
) -> None:
    require_owner_or_permission(
        user,
        owner_id,
        bypass_permission=Permission.GROUP_OWNERSHIP_BYPASS,
        detail=detail,
    )


def require_ai_api_access(
    user: Any,
    owner_id: uuid.UUID | None,
    *,
    detail: str = "Not enough privileges",
) -> None:
    require_owner_or_permission(
        user,
        owner_id,
        bypass_permission=Permission.AI_API_VIEW_ALL,
        detail=detail,
    )


def require_vm_request_access(
    user: Any,
    owner_id: uuid.UUID | None,
    *,
    detail: str = "Not enough privileges",
) -> None:
    require_owner_or_permission(
        user,
        owner_id,
        bypass_permission=Permission.VM_REQUEST_READ_ALL,
        detail=detail,
    )


def can_cancel_vm_request(
    user: Any,
    owner_id: uuid.UUID | None,
) -> bool:
    # Owner can cancel own request; admins/reviewers can cancel any request.
    return can_access_owner_resource(
        user,
        owner_id,
        bypass_permission=Permission.VM_REQUEST_REVIEW,
    )


def require_vm_request_cancel(
    user: Any,
    owner_id: uuid.UUID | None,
    *,
    detail: str = "Not enough privileges",
) -> None:
    if not can_cancel_vm_request(user, owner_id):
        raise PermissionDeniedError(detail)


def require_vm_request_review(
    user: Any,
    *,
    detail: str = "Not enough privileges",
) -> None:
    require_permission(user, Permission.VM_REQUEST_REVIEW, detail=detail)


def require_immediate_vm_request_access(
    user: Any,
    *,
    detail: str = "Only admins and teachers can use immediate mode",
) -> None:
    require_permission(
        user,
        Permission.VM_REQUEST_USE_IMMEDIATE_MODE,
        detail=detail,
    )


def can_auto_approve_vm_request(user: Any, *, mode: str) -> bool:
    if is_admin(user):
        return True
    if mode == "quick_template":
        return get_user_role(user) in {UserRole.student, UserRole.teacher}
    return mode == "immediate" and is_teacher(user)


def require_admin_access(
    user: Any,
    *,
    detail: str = "The user doesn't have enough privileges",
) -> None:
    require_permission(user, Permission.ADMIN_ACCESS, detail=detail)


def require_instructor_or_admin_access(
    user: Any,
    *,
    detail: str = "The user doesn't have enough privileges",
) -> None:
    require_permission(
        user,
        Permission.VM_REQUEST_USE_IMMEDIATE_MODE,
        detail=detail,
    )
