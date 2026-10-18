"""app/api/routes/course_admin.py 路由層的單元測試（直接呼叫路由函式，service 全部替換）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.routes import course_admin as ca
from app.exceptions import BadRequestError, NotFoundError
from app.models import TeachingClass

OWNER = uuid.uuid4()
PATH_ID = uuid.uuid4()
ROOM_ID = uuid.uuid4()
TASK_ID = uuid.uuid4()
QUESTION_ID = uuid.uuid4()


class _FakeSession:
    def __init__(self, classes: dict[uuid.UUID, Any] | None = None) -> None:
        self.classes = classes or {}

    def get(self, model: type, key: Any) -> Any:
        assert model is TeachingClass
        return self.classes.get(key)


class _FakeCourseService:
    """記錄每個呼叫並回傳 (函式名, kwargs)。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_path_or_404(self, session: Any, path_id: uuid.UUID) -> Any:
        return SimpleNamespace(id=path_id, created_by=OWNER)

    def get_room_or_404(self, session: Any, room_id: uuid.UUID) -> Any:
        return SimpleNamespace(id=room_id, path_id=PATH_ID)

    def get_task_or_404(self, session: Any, task_id: uuid.UUID) -> Any:
        return SimpleNamespace(id=task_id, room_id=ROOM_ID)

    def get_question_or_404(self, session: Any, question_id: uuid.UUID) -> Any:
        return SimpleNamespace(id=question_id, task_id=TASK_ID)

    def __getattr__(self, name: str) -> Any:
        def _record(_session: Any, **kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            return (name, kwargs)

        return _record


@pytest.fixture()
def svc(monkeypatch: pytest.MonkeyPatch) -> _FakeCourseService:
    fake = _FakeCourseService()
    monkeypatch.setattr(ca, "course_service", fake)
    return fake


@pytest.fixture()
def access(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    checks: list[Any] = []
    monkeypatch.setattr(
        ca,
        "require_teaching_access",
        lambda user, owner_id: checks.append(owner_id),
    )
    return checks


def _user() -> Any:
    return SimpleNamespace(id=uuid.uuid4())


# ─── 路徑 ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bypass", [True, False])
def test_list_paths_owner_filter(
    monkeypatch: pytest.MonkeyPatch, svc: _FakeCourseService, bypass: bool
) -> None:
    monkeypatch.setattr(ca, "can_bypass_teaching_ownership", lambda user: bypass)
    user = _user()
    name, kwargs = ca.list_paths(session=_FakeSession(), current_user=user)
    assert name == "list_paths"
    assert kwargs["owner_id"] == (None if bypass else user.id)


def test_create_path_with_class_uses_class_owner(
    svc: _FakeCourseService, access: list
) -> None:
    class_id = uuid.uuid4()
    class_owner = uuid.uuid4()
    session = _FakeSession({class_id: SimpleNamespace(owner_id=class_owner)})
    data = SimpleNamespace(teaching_class_id=class_id)
    _, kwargs = ca.create_path(session=session, current_user=_user(), data=data)
    assert kwargs["user_id"] == class_owner
    assert access == [class_owner]


def test_create_path_missing_class(svc: _FakeCourseService, access: list) -> None:
    data = SimpleNamespace(teaching_class_id=uuid.uuid4())
    with pytest.raises(NotFoundError):
        ca.create_path(session=_FakeSession(), current_user=_user(), data=data)


def test_update_path_with_matching_class(svc: _FakeCourseService, access: list) -> None:
    class_id = uuid.uuid4()
    session = _FakeSession({class_id: SimpleNamespace(owner_id=OWNER)})
    data = SimpleNamespace(teaching_class_id=class_id)
    name, _ = ca.update_path(
        session=session, current_user=_user(), path_id=PATH_ID, data=data
    )
    assert name == "update_path"
    assert access == [OWNER, OWNER]


def test_update_path_owner_mismatch(svc: _FakeCourseService, access: list) -> None:
    class_id = uuid.uuid4()
    session = _FakeSession({class_id: SimpleNamespace(owner_id=uuid.uuid4())})
    data = SimpleNamespace(teaching_class_id=class_id)
    with pytest.raises(BadRequestError):
        ca.update_path(
            session=session, current_user=_user(), path_id=PATH_ID, data=data
        )
    assert svc.calls == []


def test_publish_delete_progress_and_rooms(
    monkeypatch: pytest.MonkeyPatch, svc: _FakeCourseService, access: list
) -> None:
    session = _FakeSession()
    user = _user()
    name, kwargs = ca.publish_path(
        session=session,
        current_user=user,
        path_id=PATH_ID,
        data=SimpleNamespace(published=True),
    )
    assert (name, kwargs) == (
        "set_path_published",
        {"path_id": PATH_ID, "published": True},
    )

    assert ca.delete_path(session=session, current_user=user, path_id=PATH_ID) is None
    assert svc.calls[-1] == ("delete_path", {"path_id": PATH_ID})

    monkeypatch.setattr(
        ca.progress_service,
        "path_progress_report",
        lambda s, path_id: ("report", path_id),
    )
    assert ca.path_progress(session=session, current_user=user, path_id=PATH_ID) == (
        "report",
        PATH_ID,
    )

    assert ca.list_rooms(session=session, current_user=user, path_id=PATH_ID)[0] == (
        "list_rooms"
    )
    data = SimpleNamespace(path_id=PATH_ID)
    assert ca.create_room(session=session, current_user=user, data=data) == (
        "create_room",
        {"data": data},
    )
    assert access == [OWNER] * 5


def test_room_routes(svc: _FakeCourseService, access: list) -> None:
    session = _FakeSession()
    user = _user()
    data = object()
    assert ca.update_room(
        session=session, current_user=user, room_id=ROOM_ID, data=data
    ) == ("update_room", {"room_id": ROOM_ID, "data": data})
    assert ca.delete_room(session=session, current_user=user, room_id=ROOM_ID) is None
    assert svc.calls[-1] == ("delete_room", {"room_id": ROOM_ID})
    assert ca.list_tasks(session=session, current_user=user, room_id=ROOM_ID) == (
        "list_tasks",
        {"room_id": ROOM_ID},
    )
    task_data = SimpleNamespace(room_id=ROOM_ID)
    assert ca.create_task(session=session, current_user=user, data=task_data) == (
        "create_task",
        {"data": task_data},
    )
    assert access == [OWNER] * 4


def test_task_routes(svc: _FakeCourseService, access: list) -> None:
    session = _FakeSession()
    user = _user()
    data = object()
    assert ca.update_task(
        session=session, current_user=user, task_id=TASK_ID, data=data
    ) == ("update_task", {"task_id": TASK_ID, "data": data})
    assert ca.delete_task(session=session, current_user=user, task_id=TASK_ID) is None
    assert svc.calls[-1] == ("delete_task", {"task_id": TASK_ID})
    assert ca.list_questions(session=session, current_user=user, task_id=TASK_ID) == (
        "list_questions",
        {"task_id": TASK_ID},
    )
    q_data = SimpleNamespace(task_id=TASK_ID)
    assert ca.create_question(session=session, current_user=user, data=q_data) == (
        "create_question",
        {"data": q_data},
    )
    assert access == [OWNER] * 4


def test_question_routes(svc: _FakeCourseService, access: list) -> None:
    session = _FakeSession()
    user = _user()
    data = object()
    assert ca.update_question(
        session=session, current_user=user, question_id=QUESTION_ID, data=data
    ) == ("update_question", {"question_id": QUESTION_ID, "data": data})
    assert (
        ca.delete_question(session=session, current_user=user, question_id=QUESTION_ID)
        is None
    )
    assert svc.calls[-1] == ("delete_question", {"question_id": QUESTION_ID})
    assert access == [OWNER] * 2
