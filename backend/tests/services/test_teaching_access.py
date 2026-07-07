"""教學存取檢查測試：owner / 群組老師 / admin / 陌生人邊界。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import NotFoundError, PermissionDeniedError
from app.services.teaching import access as teaching_access

OWNER_ID = uuid.uuid4()


class _FakeSession:
    def __init__(self, resource) -> None:
        self._resource = resource

    def get(self, model: type, key: object):
        return self._resource


def _resource() -> SimpleNamespace:
    return SimpleNamespace(vmid=101, user_id=OWNER_ID)


@pytest.fixture(autouse=True)
def no_bypass(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        teaching_access, "can_bypass_resource_ownership", lambda user: False
    )
    monkeypatch.setattr(
        teaching_access.group_repo,
        "is_user_in_any_owned_group",
        lambda **kwargs: False,
    )


def test_owner_allowed() -> None:
    user = SimpleNamespace(id=OWNER_ID)
    result = teaching_access.require_vm_teaching_access(
        _FakeSession(_resource()), user, 101
    )
    assert result.vmid == 101


def test_admin_bypass_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        teaching_access, "can_bypass_resource_ownership", lambda user: True
    )
    user = SimpleNamespace(id=uuid.uuid4())
    assert teaching_access.require_vm_teaching_access(
        _FakeSession(_resource()), user, 101
    )


def test_group_teacher_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        teaching_access.group_repo,
        "is_user_in_any_owned_group",
        lambda **kwargs: True,
    )
    user = SimpleNamespace(id=uuid.uuid4())
    assert teaching_access.require_vm_teaching_access(
        _FakeSession(_resource()), user, 101
    )


def test_stranger_denied() -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    with pytest.raises(PermissionDeniedError):
        teaching_access.require_vm_teaching_access(
            _FakeSession(_resource()), user, 101
        )


def test_missing_resource_404() -> None:
    user = SimpleNamespace(id=OWNER_ID)
    with pytest.raises(NotFoundError):
        teaching_access.require_vm_teaching_access(_FakeSession(None), user, 999)
