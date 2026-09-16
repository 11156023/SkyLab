"""防火牆／網路清單的可見範圍要跟著師生關係走。

背景：require_resource_management 早就讓班級老師能管學生的課堂機，但拓撲、NAT、
反向代理清單都只查 Resource.user_id == me，老師在拓撲上根本選不到學生的機器。
這裡鎖定 list_reachable_resources / can_manage_resource 的規則，兩者必須與
require_resource_management 的判定一致。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import PermissionDeniedError
from app.models import UserRole
from app.repositories import resource as resource_repo
from app.services.resource import access

TEACHER = uuid.uuid4()
STUDENT_A = uuid.uuid4()
STUDENT_B = uuid.uuid4()
OUTSIDER = uuid.uuid4()
CLASS_1 = uuid.uuid4()
CLASS_OTHER = uuid.uuid4()


def _user(user_id: uuid.UUID, role: UserRole = UserRole.student) -> Any:
    return SimpleNamespace(id=user_id, role=role, is_superuser=False)


def _resource(
    vmid: int,
    user_id: uuid.UUID,
    *,
    teaching_class_id: uuid.UUID | None = None,
    allocation_scope: str | None = None,
) -> Any:
    scope = allocation_scope or ("teaching_class" if teaching_class_id else "personal")
    return SimpleNamespace(
        vmid=vmid,
        user_id=user_id,
        teaching_class_id=teaching_class_id,
        allocation_scope=scope,
    )


# 老師自己的示範機、兩位學生在 CLASS_1 的課堂機、學生 A 的個人機、
# 別班的課堂機、外人的個人機
TEACHER_DEMO = _resource(100, TEACHER)
A_CLASS_VM = _resource(201, STUDENT_A, teaching_class_id=CLASS_1)
B_CLASS_VM = _resource(202, STUDENT_B, teaching_class_id=CLASS_1)
A_PERSONAL = _resource(210, STUDENT_A)
OTHER_CLASS_VM = _resource(301, OUTSIDER, teaching_class_id=CLASS_OTHER)
OUTSIDER_VM = _resource(400, OUTSIDER)
ALL = [TEACHER_DEMO, A_CLASS_VM, B_CLASS_VM, A_PERSONAL, OTHER_CLASS_VM, OUTSIDER_VM]

OWNED_CLASSES = {TEACHER: {CLASS_1}, OUTSIDER: {CLASS_OTHER}}


@pytest.fixture(autouse=True)
def _fake_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        resource_repo, "get_all_resources", lambda *, session: list(ALL)
    )
    monkeypatch.setattr(
        resource_repo,
        "get_resources_by_user",
        lambda *, session, user_id: [r for r in ALL if r.user_id == user_id],
    )
    monkeypatch.setattr(
        resource_repo,
        "get_resources_by_teaching_classes",
        lambda *, session, teaching_class_ids: [
            r for r in ALL if r.teaching_class_id in set(teaching_class_ids)
        ],
    )
    monkeypatch.setattr(
        resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: next((r for r in ALL if r.vmid == vmid), None),
    )
    monkeypatch.setattr(
        access,
        "list_owned_teaching_class_ids",
        lambda *, session, user: set(OWNED_CLASSES.get(user.id, set())),
    )


def _vmids(user: Any) -> set[int]:
    return access.list_reachable_vmids(session=None, user=user)  # type: ignore[arg-type]


# ─── 可見範圍 ────────────────────────────────────────────────────────────────


def test_teacher_sees_own_vms_and_every_vm_in_owned_classes() -> None:
    teacher = _user(TEACHER, UserRole.teacher)
    assert _vmids(teacher) == {100, 201, 202}


def test_teacher_does_not_see_students_personal_vms_or_other_classes() -> None:
    teacher = _user(TEACHER, UserRole.teacher)
    assert 210 not in _vmids(teacher)  # 學生的個人機與班級無關
    assert 301 not in _vmids(teacher)  # 別人班級的機器


def test_student_sees_only_own_vms_including_class_vm() -> None:
    student = _user(STUDENT_A)
    assert _vmids(student) == {201, 210}


def test_teacher_without_classes_falls_back_to_own_vms() -> None:
    lonely = _user(uuid.uuid4(), UserRole.teacher)
    assert _vmids(lonely) == set()


def test_admin_sees_everything() -> None:
    admin = _user(uuid.uuid4(), UserRole.admin)
    assert _vmids(admin) == {r.vmid for r in ALL}


def test_reachable_list_has_no_duplicates_when_teacher_is_also_student(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """老師自己在班級裡也分到一台（user_id=老師）：own 與 taught 重疊只留一筆。"""
    both = _resource(150, TEACHER, teaching_class_id=CLASS_1)
    monkeypatch.setattr(
        resource_repo, "get_resources_by_user", lambda *, session, user_id: [both]
    )
    monkeypatch.setattr(
        resource_repo,
        "get_resources_by_teaching_classes",
        lambda *, session, teaching_class_ids: [both, A_CLASS_VM],
    )
    teacher = _user(TEACHER, UserRole.teacher)
    listed = access.list_reachable_resources(session=None, user=teacher)  # type: ignore[arg-type]
    assert [r.vmid for r in listed] == [150, 201]


# ─── can_manage 與 require_resource_management 必須同一套規則 ────────────────


@pytest.mark.parametrize(
    ("user_id", "role", "resource", "expected"),
    [
        (TEACHER, UserRole.teacher, TEACHER_DEMO, True),
        (TEACHER, UserRole.teacher, A_CLASS_VM, True),  # 班級老師管學生課堂機
        (TEACHER, UserRole.teacher, A_PERSONAL, False),  # 學生個人機與老師無關
        (TEACHER, UserRole.teacher, OTHER_CLASS_VM, False),  # 別班
        (STUDENT_A, UserRole.student, A_PERSONAL, True),
        (STUDENT_A, UserRole.student, A_CLASS_VM, False),  # 學生對課堂機只有使用權
        (STUDENT_A, UserRole.student, B_CLASS_VM, False),
        (OUTSIDER, UserRole.student, TEACHER_DEMO, False),
    ],
)
def test_can_manage_matches_require_resource_management(
    user_id: uuid.UUID, role: UserRole, resource: Any, expected: bool
) -> None:
    user = _user(user_id, role)
    owned = OWNED_CLASSES.get(user_id, set())

    assert (
        access.can_manage_resource(resource=resource, user=user, owned_class_ids=owned)
        is expected
    )

    session = _SessionWithClass()
    if expected:
        access.require_resource_management(session=session, user=user, vmid=resource.vmid)
    else:
        with pytest.raises(PermissionDeniedError):
            access.require_resource_management(
                session=session, user=user, vmid=resource.vmid
            )


def test_orphaned_class_vm_is_manageable_by_nobody_but_admin() -> None:
    orphan = _resource(
        500, STUDENT_A, teaching_class_id=None, allocation_scope="teaching_class"
    )
    student = _user(STUDENT_A)
    teacher = _user(TEACHER, UserRole.teacher)
    admin = _user(uuid.uuid4(), UserRole.admin)

    assert not access.can_manage_resource(
        resource=orphan, user=student, owned_class_ids=set()
    )
    assert not access.can_manage_resource(
        resource=orphan, user=teacher, owned_class_ids={CLASS_1}
    )
    assert access.can_manage_resource(resource=orphan, user=admin, owned_class_ids=set())


class _SessionWithClass:
    """require_resource_management 只用 session.get(TeachingClass, id) 查班級擁有者。"""

    _CLASSES = {
        CLASS_1: SimpleNamespace(id=CLASS_1, owner_id=TEACHER),
        CLASS_OTHER: SimpleNamespace(id=CLASS_OTHER, owner_id=OUTSIDER),
    }

    def get(self, _model: Any, key: uuid.UUID) -> Any:
        return self._CLASSES.get(key)
