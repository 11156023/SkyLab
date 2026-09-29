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
    require_admin_access,
    require_ai_api_access,
    require_ai_api_manage,
    require_classroom_monitor,
    require_immediate_vm_request_access,
    require_instructor_or_admin_access,
    require_resource_access,
    require_teaching_access,
    require_template_manage,
    require_template_owner,
    require_user_manage,
    require_vm_request_access,
    require_vm_request_cancel,
    require_vm_request_review,
)
from app.core.i18n import SUPPORTED_LANGUAGES, _catalog, translate
from app.core.permissions import (
    Permission,
    has_permission,
    require_owner_or_permission,
    require_permission,
)
from app.core.request_context import RequestContext, _request_context
from app.exceptions import PermissionDeniedError
from app.models import UserRole
from app.services.jobs import jobs_service


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


def test_resource_teaching_vm_and_ai_access_authorizers() -> None:
    owner_id = uuid.uuid4()
    owner = _user(role=UserRole.student, user_id=owner_id)
    admin = _user(role=UserRole.admin)
    stranger = _user(role=UserRole.student)

    require_resource_access(owner, owner_id)
    require_teaching_access(owner, owner_id)
    require_vm_request_access(owner, owner_id)
    require_ai_api_access(owner, owner_id)

    require_resource_access(admin, uuid.uuid4())
    require_teaching_access(admin, uuid.uuid4())
    require_vm_request_access(admin, uuid.uuid4())
    require_ai_api_access(admin, uuid.uuid4())

    with pytest.raises(PermissionDeniedError):
        require_resource_access(stranger, owner_id)
    with pytest.raises(PermissionDeniedError):
        require_teaching_access(stranger, owner_id)
    with pytest.raises(PermissionDeniedError):
        require_vm_request_access(stranger, owner_id)
    with pytest.raises(PermissionDeniedError):
        require_ai_api_access(stranger, owner_id)


def test_teaching_access_denial_uses_request_language() -> None:
    stranger = _user(role=UserRole.student)

    # 沒帶 detail 時用這次請求的語言（預設 zh-TW），不是寫死的英文
    with pytest.raises(PermissionDeniedError) as denied:
        require_teaching_access(stranger, uuid.uuid4())
    assert denied.value.message == "你沒有權限存取這個教學資源"

    token = _request_context.set(RequestContext(language="en"))
    try:
        with pytest.raises(PermissionDeniedError) as denied_en:
            require_teaching_access(stranger, uuid.uuid4())
    finally:
        _request_context.reset(token)
    assert denied_en.value.message == "Not authorized to access this teaching resource"

    # 呼叫端自己給的訊息照用
    with pytest.raises(PermissionDeniedError) as custom:
        require_teaching_access(stranger, uuid.uuid4(), detail="custom")
    assert custom.value.message == "custom"


@pytest.mark.parametrize(
    ("deny", "key"),
    [
        (
            lambda u: require_permission(u, Permission.ADMIN_ACCESS),
            "resource_access.insufficient_privileges",
        ),
        (
            lambda u: require_owner_or_permission(u, uuid.uuid4()),
            "resource_access.insufficient_privileges",
        ),
        (require_user_manage, "resource_access.insufficient_privileges"),
        (
            lambda u: require_resource_access(u, uuid.uuid4()),
            "resource_access.no_permission",
        ),
        (
            lambda u: require_ai_api_access(u, uuid.uuid4()),
            "resource_access.insufficient_privileges",
        ),
        (
            lambda u: require_ai_api_manage(u, uuid.uuid4()),
            "resource_access.insufficient_privileges",
        ),
        (
            lambda u: require_vm_request_access(u, uuid.uuid4()),
            "resource_access.insufficient_privileges",
        ),
        (
            lambda u: require_vm_request_cancel(u, uuid.uuid4()),
            "resource_access.insufficient_privileges",
        ),
        (require_vm_request_review, "resource_access.insufficient_privileges"),
        (
            require_immediate_vm_request_access,
            "resource_access.immediate_mode_forbidden",
        ),
        (require_template_manage, "resource_access.template_manage_forbidden"),
        (
            lambda u: require_template_owner(u, uuid.uuid4()),
            "resource_access.template_owner_only",
        ),
        (require_classroom_monitor, "resource_access.classroom_monitor_forbidden"),
        (require_admin_access, "resource_access.insufficient_privileges"),
        (
            require_instructor_or_admin_access,
            "resource_access.insufficient_privileges",
        ),
        (
            lambda u: jobs_service._ensure_owner_or_admin(u, uuid.uuid4()),
            "resource_access.job_view_forbidden",
        ),
    ],
)
def test_default_denials_follow_request_language(deny, key) -> None:
    student = _user(role=UserRole.student)

    for lang in SUPPORTED_LANGUAGES:
        token = _request_context.set(RequestContext(language=lang))
        try:
            with pytest.raises(
                (PermissionDeniedError, jobs_service.JobAccessDeniedError)
            ) as denied:
                deny(student)
        finally:
            _request_context.reset(token)
        assert str(denied.value) == translate(key, lang)

    # zh-TW／ja 真的有翻，不是留著英文原句
    assert translate(key, "zh-TW") != translate(key, "en")
    assert translate(key, "ja") != translate(key, "en")


def test_resource_access_messages_exist_in_every_language() -> None:
    def keys(lang: str) -> set[str]:
        return {k for k in _catalog(lang) if k.startswith("resource_access.")}

    for lang in SUPPORTED_LANGUAGES:
        assert keys(lang) == keys("zh-TW"), lang


def test_user_manage_authorizers() -> None:
    admin = _user(role=UserRole.admin)
    student = _user(role=UserRole.student)

    assert can_manage_users(admin) is True
    assert can_manage_users(student) is False
    require_user_manage(admin)
    with pytest.raises(PermissionDeniedError):
        require_user_manage(student)
