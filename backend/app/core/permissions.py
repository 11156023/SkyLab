import enum
import uuid
from typing import Any

from app.exceptions import PermissionDeniedError
from app.models import UserRole


class Permission(str, enum.Enum):
    ADMIN_ACCESS = "admin_access"
    AI_API_REVIEW = "ai_api_review"
    AI_API_VIEW_ALL = "ai_api_view_all"
    AUDIT_LOG_READ_ALL = "audit_log_read_all"
    CLASSROOM_MONITOR = "classroom_monitor"
    GROUP_OWNERSHIP_BYPASS = "group_ownership_bypass"
    NAT_RULES_SYNC = "nat_rules_sync"
    RESOURCE_OWNERSHIP_BYPASS = "resource_ownership_bypass"
    REVERSE_PROXY_RULES_SYNC = "reverse_proxy_rules_sync"
    SPEC_CHANGE_DIRECT_APPLY = "spec_change_direct_apply"
    SPEC_CHANGE_REVIEW = "spec_change_review"
    TEMPLATE_MANAGE = "template_manage"
    USER_MANAGE = "user_manage"
    VM_REQUEST_READ_ALL = "vm_request_read_all"
    VM_REQUEST_REVIEW = "vm_request_review"
    VM_REQUEST_USE_IMMEDIATE_MODE = "vm_request_use_immediate_mode"


_ALL_PERMISSIONS = frozenset(Permission)

_ROLE_PERMISSION_MATRIX: dict[UserRole, frozenset[Permission]] = {
    UserRole.student: frozenset(),
    UserRole.teacher: frozenset(
        {
            Permission.VM_REQUEST_USE_IMMEDIATE_MODE,
            Permission.TEMPLATE_MANAGE,
            Permission.CLASSROOM_MONITOR,
        }
    ),
    UserRole.admin: _ALL_PERMISSIONS,
}


def get_user_role(user: Any) -> UserRole:
    raw_role = getattr(user, "role", None)
    if isinstance(raw_role, UserRole):
        return raw_role
    if isinstance(raw_role, str):
        try:
            return UserRole(raw_role)
        except ValueError:
            pass
    if bool(getattr(user, "is_superuser", False)):
        return UserRole.admin
    return UserRole.student


def get_permissions(user: Any) -> frozenset[Permission]:
    permissions = set(_ROLE_PERMISSION_MATRIX.get(get_user_role(user), frozenset()))
    if bool(getattr(user, "is_superuser", False)):
        permissions.update(_ALL_PERMISSIONS)
    return frozenset(permissions)


def has_permission(user: Any, permission: Permission) -> bool:
    return permission in get_permissions(user)


def require_permission(
    user: Any,
    permission: Permission,
    *,
    detail: str = "The user doesn't have enough privileges",
) -> None:
    if not has_permission(user, permission):
        raise PermissionDeniedError(detail)


def is_admin(user: Any) -> bool:
    return has_permission(user, Permission.ADMIN_ACCESS)


def is_teacher(user: Any) -> bool:
    return get_user_role(user) == UserRole.teacher


def can_access_owner_resource(
    user: Any,
    owner_id: uuid.UUID | None,
    *,
    bypass_permission: Permission = Permission.RESOURCE_OWNERSHIP_BYPASS,
) -> bool:
    if has_permission(user, bypass_permission):
        return True
    return owner_id is not None and getattr(user, "id", None) == owner_id


def require_owner_or_permission(
    user: Any,
    owner_id: uuid.UUID | None,
    *,
    bypass_permission: Permission = Permission.RESOURCE_OWNERSHIP_BYPASS,
    detail: str = "Not enough privileges",
) -> None:
    if not can_access_owner_resource(
        user,
        owner_id,
        bypass_permission=bypass_permission,
    ):
        raise PermissionDeniedError(detail)
