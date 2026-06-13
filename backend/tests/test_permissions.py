import uuid
from types import SimpleNamespace

import pytest

from app.api.deps.auth import (
    get_current_active_superuser,
    get_current_instructor_or_admin,
)
from app.core.authorizers import (
    can_auto_approve_vm_request,
    can_manage_users,
    require_ai_api_access,
    require_group_access,
    require_immediate_vm_request_access,
    require_resource_access,
    require_user_manage,
    require_vm_request_access,
    require_vm_request_review,
)
from app.core.permissions import (
    Permission,
    has_permission,
    require_owner_or_permission,
)
from app.exceptions import PermissionDeniedError
from app.models import UserRole


def _user(
    *,
    role: UserRole,
    is_superuser: bool = False,
    user_id: uuid.UUID | None = None,
):
    return SimpleNamespace(
        id=user_id or uuid.uuid4(),
        role=role,
        is_superuser=is_superuser,
    )


def test_admin_role_has_admin_permissions_without_superuser_flag() -> None:
    user = _user(role=UserRole.admin, is_superuser=False)

    assert has_permission(user, Permission.ADMIN_ACCESS) is True
    assert has_permission(user, Permission.USER_MANAGE) is True
    assert has_permission(user, Permission.VM_REQUEST_REVIEW) is True


def test_teacher_only_has_immediate_mode_permission() -> None:
    user = _user(role=UserRole.teacher)

    assert has_permission(user, Permission.VM_REQUEST_USE_IMMEDIATE_MODE) is True
    assert has_permission(user, Permission.ADMIN_ACCESS) is False
    assert has_permission(user, Permission.VM_REQUEST_REVIEW) is False


def test_require_owner_or_permission_allows_owner_and_admin() -> None:
    owner_id = uuid.uuid4()
    owner = _user(role=UserRole.student, user_id=owner_id)
    admin = _user(role=UserRole.admin)

    require_owner_or_permission(owner, owner_id)
    require_owner_or_permission(admin, uuid.uuid4())


def test_require_owner_or_permission_rejects_non_owner_student() -> None:
    user = _user(role=UserRole.student)

    with pytest.raises(PermissionDeniedError):
        require_owner_or_permission(user, uuid.uuid4())


def test_admin_dependency_accepts_admin_role_without_superuser_flag() -> None:
    user = _user(role=UserRole.admin, is_superuser=False)

    assert get_current_active_superuser(user) is user


def test_instructor_dependency_accepts_teacher_and_rejects_student() -> None:
    teacher = _user(role=UserRole.teacher)
    student = _user(role=UserRole.student)

    assert get_current_instructor_or_admin(teacher) is teacher
    with pytest.raises(PermissionDeniedError):
        get_current_instructor_or_admin(student)


def test_vm_request_authorizers_match_existing_role_rules() -> None:
    admin = _user(role=UserRole.admin)
    teacher = _user(role=UserRole.teacher)
    student = _user(role=UserRole.student)

    assert can_auto_approve_vm_request(admin, mode="scheduled") is False
    assert can_auto_approve_vm_request(admin, mode="immediate") is True
    assert can_auto_approve_vm_request(admin, mode="quick_template") is True
    assert can_auto_approve_vm_request(teacher, mode="immediate") is True
    assert can_auto_approve_vm_request(teacher, mode="scheduled") is False
    assert can_auto_approve_vm_request(student, mode="immediate") is False
    assert can_auto_approve_vm_request(student, mode="quick_template") is True

    require_immediate_vm_request_access(teacher)
    require_vm_request_review(admin)
    with pytest.raises(PermissionDeniedError):
        require_immediate_vm_request_access(student)
    with pytest.raises(PermissionDeniedError):
        require_vm_request_review(teacher)


def test_resource_group_vm_and_ai_access_authorizers() -> None:
    owner_id = uuid.uuid4()
    owner = _user(role=UserRole.student, user_id=owner_id)
    admin = _user(role=UserRole.admin)
    stranger = _user(role=UserRole.student)

    require_resource_access(owner, owner_id)
    require_group_access(owner, owner_id)
    require_vm_request_access(owner, owner_id)
    require_ai_api_access(owner, owner_id)

    require_resource_access(admin, uuid.uuid4())
    require_group_access(admin, uuid.uuid4())
    require_vm_request_access(admin, uuid.uuid4())
    require_ai_api_access(admin, uuid.uuid4())

    with pytest.raises(PermissionDeniedError):
        require_resource_access(stranger, owner_id)
    with pytest.raises(PermissionDeniedError):
        require_group_access(stranger, owner_id)
    with pytest.raises(PermissionDeniedError):
        require_vm_request_access(stranger, owner_id)
    with pytest.raises(PermissionDeniedError):
        require_ai_api_access(stranger, owner_id)


def test_user_manage_authorizers() -> None:
    admin = _user(role=UserRole.admin)
    student = _user(role=UserRole.student)

    assert can_manage_users(admin) is True
    assert can_manage_users(student) is False
    require_user_manage(admin)
    with pytest.raises(PermissionDeniedError):
        require_user_manage(student)
