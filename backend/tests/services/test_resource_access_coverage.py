"""Coverage tests for app.services.resource.access."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import PermissionDeniedError
from app.services.resource import access

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


# ── require_resource_ownership ─────────────────────────────────────────────


def test_ownership_unregistered_resource_denied(state: dict[str, Any]) -> None:
    state["resource"] = None
    with pytest.raises(PermissionDeniedError):
        access.require_resource_ownership(session=_FakeSession(), user=USER, vmid=150)  # type: ignore[arg-type]


def test_ownership_class_missing_denied(state: dict[str, Any]) -> None:
    state["resource"] = _resource(teaching_class_id=uuid.uuid4())
    with pytest.raises(PermissionDeniedError):
        access.require_resource_ownership(session=_FakeSession(), user=USER, vmid=150)  # type: ignore[arg-type]


# ── require_resource_use ───────────────────────────────────────────────────


# ── require_resource_console_access ────────────────────────────────────────


def _console(state: dict[str, Any]) -> None:
    access.require_resource_console_access(
        session=_FakeSession(),  # type: ignore[arg-type]
        user=USER,
        vmid=150,
    )


def test_console_resource_vanished(
    state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(access, "require_resource_use", lambda **_kw: None)
    state["resource"] = None
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


# ── listing helpers ────────────────────────────────────────────────────────
