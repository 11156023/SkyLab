"""Coverage tests for app.services.teaching.access."""

import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import NotFoundError, PermissionDeniedError
from app.models import Resource, TeachingClass, TeachingClassStatus
from app.services.teaching import access


class _FakeSession:
    def __init__(self, resource=None, teaching_class=None):
        self._items = {Resource: resource, TeachingClass: teaching_class}

    def get(self, model, _key):
        return self._items.get(model)


@pytest.fixture
def calls(monkeypatch):
    recorded: list[tuple[str, object]] = []
    monkeypatch.setattr(access, "can_bypass_resource_ownership", lambda _u: False)
    monkeypatch.setattr(
        access,
        "require_resource_access",
        lambda _u, owner: recorded.append(("resource", owner)),
    )
    monkeypatch.setattr(
        access,
        "require_teaching_access",
        lambda _u, owner: recorded.append(("teaching", owner)),
    )
    return recorded


def _user():
    return SimpleNamespace(id=uuid.uuid4())


def test_missing_resource_raises_not_found(calls):
    with pytest.raises(NotFoundError):
        access.require_vm_teaching_access(_FakeSession(), _user(), 101)


def test_bypass_user_gets_resource(monkeypatch):
    resource = SimpleNamespace(teaching_class_id=None)
    monkeypatch.setattr(access, "can_bypass_resource_ownership", lambda _u: True)
    assert (
        access.require_vm_teaching_access(_FakeSession(resource), _user(), 1)
        is resource
    )


def test_lost_class_assignment_is_denied(calls):
    resource = SimpleNamespace(
        teaching_class_id=None, allocation_scope="teaching_class", user_id=None
    )
    with pytest.raises(PermissionDeniedError):
        access.require_vm_teaching_access(_FakeSession(resource), _user(), 1)


def test_personal_resource_checks_resource_owner(calls):
    owner = uuid.uuid4()
    resource = SimpleNamespace(
        teaching_class_id=None, allocation_scope="personal", user_id=owner
    )
    assert (
        access.require_vm_teaching_access(_FakeSession(resource), _user(), 1)
        is resource
    )
    assert calls == [("resource", owner)]


def test_missing_teaching_class_is_denied(calls):
    resource = SimpleNamespace(teaching_class_id=uuid.uuid4(), user_id=None)
    with pytest.raises(PermissionDeniedError):
        access.require_vm_teaching_access(_FakeSession(resource), _user(), 1)


def test_student_owner_blocked_when_class_inactive(calls):
    user = _user()
    resource = SimpleNamespace(teaching_class_id=uuid.uuid4(), user_id=user.id)
    klass = SimpleNamespace(status=TeachingClassStatus.archived, owner_id=None)
    with pytest.raises(PermissionDeniedError):
        access.require_vm_teaching_access(_FakeSession(resource, klass), user, 1)


def test_student_owner_allowed_when_class_active(calls):
    user = _user()
    resource = SimpleNamespace(teaching_class_id=uuid.uuid4(), user_id=user.id)
    klass = SimpleNamespace(status=TeachingClassStatus.active, owner_id=None)
    assert (
        access.require_vm_teaching_access(_FakeSession(resource, klass), user, 1)
        is resource
    )
    assert calls == []


def test_other_user_requires_teaching_access(calls):
    teacher = uuid.uuid4()
    resource = SimpleNamespace(teaching_class_id=uuid.uuid4(), user_id=uuid.uuid4())
    klass = SimpleNamespace(status=TeachingClassStatus.active, owner_id=teacher)
    assert (
        access.require_vm_teaching_access(_FakeSession(resource, klass), _user(), 1)
        is resource
    )
    assert calls == [("teaching", teacher)]
