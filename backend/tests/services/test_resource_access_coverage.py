"""Coverage tests for app.services.resource.access."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import PermissionDeniedError
from app.models import TeachingClassStatus
from app.services.resource import access, sharing_service
from app.services.resource import resource_service as resource_service_module

USER = SimpleNamespace(id=uuid.uuid4(), email="s@example.com")
TEACHER_ID = uuid.uuid4()


def _resource(**kw: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "vmid": 150,
        "user_id": USER.id,
        "teaching_class_id": None,
        "allocation_scope": "personal",
        "expiry_date": None,
    }
    values.update(kw)
    return SimpleNamespace(**values)


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(
        self, classes: dict[Any, Any] | None = None, rows: list[Any] | None = None
    ) -> None:
        self.classes = classes or {}
        self.rows = rows or []

    def get(self, _model: Any, key: Any) -> Any:
        return self.classes.get(key)

    def exec(self, _stmt: Any) -> _Rows:
        return _Rows(self.rows)


@pytest.fixture()
def state(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    st: dict[str, Any] = {"resource": _resource(), "bypass": False, "teaching": []}
    monkeypatch.setattr(
        access.resource_repo, "get_resource_by_vmid", lambda **_kw: st["resource"]
    )
    monkeypatch.setattr(
        access, "can_bypass_resource_ownership", lambda _u: st["bypass"]
    )

    def _resource_access(user: Any, owner_id: Any) -> None:
        if owner_id != user.id:
            raise PermissionDeniedError("not owner")

    def _teaching_access(
        user: Any, owner_id: Any, *, detail: str | None = None
    ) -> None:
        st["teaching"].append(owner_id)
        if owner_id != user.id:
            raise PermissionDeniedError(detail or "not teacher")

    monkeypatch.setattr(access, "require_resource_access", _resource_access)
    monkeypatch.setattr(access, "require_teaching_access", _teaching_access)
    return st


def _class(status: TeachingClassStatus = TeachingClassStatus.active) -> SimpleNamespace:
    return SimpleNamespace(owner_id=TEACHER_ID, status=status)


# ── require_resource_ownership ─────────────────────────────────────────────


def test_ownership_admin_bypass(state: dict[str, Any]) -> None:
    state["bypass"] = True
    state["resource"] = None
    access.require_resource_ownership(session=_FakeSession(), user=USER, vmid=150)  # type: ignore[arg-type]


def test_ownership_unregistered_resource_denied(state: dict[str, Any]) -> None:
    state["resource"] = None
    with pytest.raises(PermissionDeniedError):
        access.require_resource_ownership(session=_FakeSession(), user=USER, vmid=150)  # type: ignore[arg-type]


def test_ownership_class_missing_denied(state: dict[str, Any]) -> None:
    state["resource"] = _resource(teaching_class_id=uuid.uuid4())
    with pytest.raises(PermissionDeniedError):
        access.require_resource_ownership(session=_FakeSession(), user=USER, vmid=150)  # type: ignore[arg-type]


def test_ownership_student_in_active_and_inactive_class(state: dict[str, Any]) -> None:
    cid = uuid.uuid4()
    state["resource"] = _resource(teaching_class_id=cid)
    access.require_resource_ownership(
        session=_FakeSession({cid: _class()}),  # type: ignore[arg-type]
        user=USER,
        vmid=150,
    )
    with pytest.raises(PermissionDeniedError):
        access.require_resource_ownership(
            session=_FakeSession({cid: _class(TeachingClassStatus.archived)}),  # type: ignore[arg-type]
            user=USER,
            vmid=150,
        )


def test_ownership_teacher_of_class(state: dict[str, Any]) -> None:
    cid = uuid.uuid4()
    teacher = SimpleNamespace(id=TEACHER_ID, email="t@example.com")
    state["resource"] = _resource(teaching_class_id=cid)
    access.require_resource_ownership(
        session=_FakeSession({cid: _class()}),  # type: ignore[arg-type]
        user=teacher,
        vmid=150,
    )
    assert state["teaching"] == [TEACHER_ID]


def test_ownership_lost_class_scope_denied(state: dict[str, Any]) -> None:
    state["resource"] = _resource(allocation_scope="teaching_class")
    with pytest.raises(PermissionDeniedError):
        access.require_resource_ownership(session=_FakeSession(), user=USER, vmid=150)  # type: ignore[arg-type]


def test_ownership_personal_owner_and_stranger(state: dict[str, Any]) -> None:
    access.require_resource_ownership(session=_FakeSession(), user=USER, vmid=150)  # type: ignore[arg-type]
    state["resource"] = _resource(user_id=uuid.uuid4())
    with pytest.raises(PermissionDeniedError):
        access.require_resource_ownership(session=_FakeSession(), user=USER, vmid=150)  # type: ignore[arg-type]


# ── require_resource_use ───────────────────────────────────────────────────


def test_use_falls_back_to_share(
    state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    state["resource"] = _resource(user_id=uuid.uuid4())
    shared = {"value": True}
    monkeypatch.setattr(
        sharing_service, "user_has_share", lambda **_kw: shared["value"]
    )
    access.require_resource_use(session=_FakeSession(), user=USER, vmid=150)  # type: ignore[arg-type]
    shared["value"] = False
    with pytest.raises(PermissionDeniedError):
        access.require_resource_use(session=_FakeSession(), user=USER, vmid=150)  # type: ignore[arg-type]


# ── require_resource_console_access ────────────────────────────────────────


def _console(state: dict[str, Any]) -> None:
    access.require_resource_console_access(
        session=_FakeSession(),  # type: ignore[arg-type]
        user=USER,
        vmid=150,
    )


def test_console_admin_bypass(state: dict[str, Any]) -> None:
    state["bypass"] = True
    _console(state)


def test_console_resource_vanished(
    state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(access, "require_resource_use", lambda **_kw: None)
    state["resource"] = None
    with pytest.raises(PermissionDeniedError):
        _console(state)


def test_console_teaching_class_skips_window(
    state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(access, "require_resource_use", lambda **_kw: None)
    state["resource"] = _resource(teaching_class_id=uuid.uuid4())
    _console(state)


def test_console_expired_denied(state: dict[str, Any]) -> None:
    state["resource"] = _resource(expiry_date=date.today() - timedelta(days=2))
    with pytest.raises(PermissionDeniedError):
        _console(state)


@pytest.mark.parametrize("reason", [None, "before_window"])
def test_console_start_window(
    state: dict[str, Any], monkeypatch: pytest.MonkeyPatch, reason: str | None
) -> None:
    state["resource"] = _resource(expiry_date=date.today() + timedelta(days=30))
    monkeypatch.setattr(
        resource_service_module,
        "start_window_state",
        lambda **_kw: (reason, None, None),
    )
    if reason is None:
        _console(state)
    else:
        with pytest.raises(PermissionDeniedError):
            _console(state)


# ── require_resource_management ────────────────────────────────────────────


def _manage(state: dict[str, Any], session: Any = None, user: Any = USER) -> None:
    access.require_resource_management(
        session=session or _FakeSession(), user=user, vmid=150
    )


def test_management_admin_bypass(state: dict[str, Any]) -> None:
    state["bypass"] = True
    state["resource"] = None
    _manage(state)


def test_management_missing_resource(state: dict[str, Any]) -> None:
    state["resource"] = None
    with pytest.raises(PermissionDeniedError):
        _manage(state)


def test_management_class_machine(state: dict[str, Any]) -> None:
    cid = uuid.uuid4()
    state["resource"] = _resource(teaching_class_id=cid)
    with pytest.raises(PermissionDeniedError):
        _manage(state)  # class row missing
    with pytest.raises(PermissionDeniedError):
        _manage(state, _FakeSession({cid: _class()}))  # student is not teacher
    _manage(
        state,
        _FakeSession({cid: _class()}),
        SimpleNamespace(id=TEACHER_ID, email="t@example.com"),
    )


def test_management_lost_class_scope_and_personal(state: dict[str, Any]) -> None:
    state["resource"] = _resource(allocation_scope="teaching_class")
    with pytest.raises(PermissionDeniedError):
        _manage(state)
    state["resource"] = _resource()
    _manage(state)


# ── listing helpers ────────────────────────────────────────────────────────


def test_list_owned_teaching_class_ids() -> None:
    cid = uuid.uuid4()
    out = access.list_owned_teaching_class_ids(
        session=_FakeSession(rows=[cid, cid]),  # type: ignore[arg-type]
        user=USER,
    )
    assert out == {cid}


def test_can_manage_resource(state: dict[str, Any]) -> None:
    cid = uuid.uuid4()
    res = _resource()
    assert access.can_manage_resource(resource=res, user=USER, owned_class_ids=set())  # type: ignore[arg-type]
    assert not access.can_manage_resource(
        resource=_resource(teaching_class_id=cid),  # type: ignore[arg-type]
        user=USER,
        owned_class_ids=set(),
    )
    assert access.can_manage_resource(
        resource=_resource(teaching_class_id=cid),  # type: ignore[arg-type]
        user=USER,
        owned_class_ids={cid},
    )
    assert not access.can_manage_resource(
        resource=_resource(allocation_scope="teaching_class"),  # type: ignore[arg-type]
        user=USER,
        owned_class_ids=set(),
    )
    state["bypass"] = True
    assert access.can_manage_resource(
        resource=_resource(user_id=uuid.uuid4()),  # type: ignore[arg-type]
        user=USER,
        owned_class_ids=set(),
    )


def test_list_reachable_resources(
    state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    own = [_resource(vmid=1), _resource(vmid=2)]
    taught = [_resource(vmid=2), _resource(vmid=3)]
    class_ids: set[uuid.UUID] = set()
    monkeypatch.setattr(
        access.resource_repo, "get_all_resources", lambda **_kw: ["all"]
    )
    monkeypatch.setattr(
        access.resource_repo, "get_resources_by_user", lambda **_kw: list(own)
    )
    monkeypatch.setattr(
        access.resource_repo,
        "get_resources_by_teaching_classes",
        lambda **_kw: list(taught),
    )
    monkeypatch.setattr(
        access, "list_owned_teaching_class_ids", lambda **_kw: class_ids
    )

    assert access.list_reachable_vmids(session=_FakeSession(), user=USER) == {1, 2}  # type: ignore[arg-type]
    class_ids.add(uuid.uuid4())
    assert access.list_reachable_vmids(session=_FakeSession(), user=USER) == {1, 2, 3}  # type: ignore[arg-type]
    state["bypass"] = True
    assert access.list_reachable_resources(session=_FakeSession(), user=USER) == ["all"]  # type: ignore[arg-type]
