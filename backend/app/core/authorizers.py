from __future__ import annotations

import uuid
from typing import Any

from app.core.i18n import t
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

# 各 require_* 的預設訊息一律在呼叫當下才 t()：預設參數在 import 時就定值，
# 拿不到這次請求的語言，所以 detail 預設 None，呼叫端自訂的訊息優先。


def can_manage_users(user: Any) -> bool:
    return has_permission(user, Permission.USER_MANAGE)


def require_user_manage(
    user: Any,
    *,
    detail: str | None = None,
) -> None:
    require_permission(
        user,
        Permission.USER_MANAGE,
        detail=detail or t("resource_access.insufficient_privileges"),
    )


def can_bypass_resource_ownership(user: Any) -> bool:
    return has_permission(user, Permission.RESOURCE_OWNERSHIP_BYPASS)


def require_resource_access(
    user: Any,
    owner_id: uuid.UUID | None,
    *,
    detail: str | None = None,
) -> None:
    require_owner_or_permission(
        user,
        owner_id,
        bypass_permission=Permission.RESOURCE_OWNERSHIP_BYPASS,
        detail=detail or t("resource_access.no_permission"),
    )


def can_bypass_teaching_ownership(user: Any) -> bool:
    return has_permission(user, Permission.TEACHING_OWNERSHIP_BYPASS)


def require_teaching_access(
    user: Any,
    owner_id: uuid.UUID | None,
    *,
    detail: str | None = None,
) -> None:
    require_owner_or_permission(
        user,
        owner_id,
        bypass_permission=Permission.TEACHING_OWNERSHIP_BYPASS,
        detail=detail or t("resource_access.teaching_no_permission"),
    )


def require_ai_api_access(
    user: Any,
    owner_id: uuid.UUID | None,
    *,
    detail: str | None = None,
) -> None:
    require_owner_or_permission(
        user,
        owner_id,
        bypass_permission=Permission.AI_API_VIEW_ALL,
        detail=detail or t("resource_access.insufficient_privileges"),
    )


def require_ai_api_manage(
    user: Any,
    owner_id: uuid.UUID | None,
    *,
    detail: str | None = None,
) -> None:
    """金鑰的寫入操作（輪替／改名／刪除）。

    故意不吃 ``AI_API_VIEW_ALL``：那是「看得到別人的金鑰清單」的唯讀權限，
    拿來當寫入繞過等於讓唯讀角色能撤銷別人的金鑰。代操一律要 ``AI_API_MANAGE_ALL``
    （目前只有管理員有），且呼叫端必須留下稽核紀錄。
    """
    require_owner_or_permission(
        user,
        owner_id,
        bypass_permission=Permission.AI_API_MANAGE_ALL,
        detail=detail or t("resource_access.insufficient_privileges"),
    )


def require_vm_request_access(
    user: Any,
    owner_id: uuid.UUID | None,
    *,
    detail: str | None = None,
) -> None:
    require_owner_or_permission(
        user,
        owner_id,
        bypass_permission=Permission.VM_REQUEST_READ_ALL,
        detail=detail or t("resource_access.insufficient_privileges"),
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
    detail: str | None = None,
) -> None:
    if not can_cancel_vm_request(user, owner_id):
        raise PermissionDeniedError(
            detail or t("resource_access.insufficient_privileges")
        )


def require_vm_request_review(
    user: Any,
    *,
    detail: str | None = None,
) -> None:
    require_permission(
        user,
        Permission.VM_REQUEST_REVIEW,
        detail=detail or t("resource_access.insufficient_privileges"),
    )


def require_immediate_vm_request_access(
    user: Any,
    *,
    detail: str | None = None,
) -> None:
    require_permission(
        user,
        Permission.VM_REQUEST_USE_IMMEDIATE_MODE,
        detail=detail or t("resource_access.immediate_mode_forbidden"),
    )


def can_auto_approve_vm_request(user: Any, *, mode: str) -> bool:
    if mode == "quick_template":
        return get_user_role(user) in {UserRole.student, UserRole.teacher, UserRole.admin}
    if mode == "immediate":
        return is_teacher(user) or is_admin(user)
    return False


def require_template_manage(
    user: Any,
    *,
    detail: str | None = None,
) -> None:
    require_permission(
        user,
        Permission.TEMPLATE_MANAGE,
        detail=detail or t("resource_access.template_manage_forbidden"),
    )


def require_template_owner(
    user: Any,
    owner_id: uuid.UUID | None,
    *,
    detail: str | None = None,
) -> None:
    require_owner_or_permission(
        user,
        owner_id,
        bypass_permission=Permission.ADMIN_ACCESS,
        detail=detail or t("resource_access.template_owner_only"),
    )


def require_classroom_monitor(
    user: Any,
    *,
    detail: str | None = None,
) -> None:
    require_permission(
        user,
        Permission.CLASSROOM_MONITOR,
        detail=detail or t("resource_access.classroom_monitor_forbidden"),
    )


def require_admin_access(
    user: Any,
    *,
    detail: str | None = None,
) -> None:
    require_permission(
        user,
        Permission.ADMIN_ACCESS,
        detail=detail or t("resource_access.insufficient_privileges"),
    )


def require_instructor_or_admin_access(
    user: Any,
    *,
    detail: str | None = None,
) -> None:
    require_immediate_vm_request_access(
        user,
        detail=detail or t("resource_access.insufficient_privileges"),
    )
