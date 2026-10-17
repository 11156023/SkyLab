"""app.api.websocket.course_progress 的老師端課程進度訂閱授權分支。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.api.websocket import course_progress as cp_ws
from app.exceptions import PermissionDeniedError

OWNER_ID = uuid.uuid4()


class FakeWs:
    def __init__(self) -> None:
        self.application_state = WebSocketState.CONNECTED
        self.client_state = WebSocketState.CONNECTED
        self.close_calls: list[tuple[int, str]] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_calls.append((code, reason))


class FakeDb:
    def __init__(self, path: Any) -> None:
        self.path = path
        self.closed = False
        self.got: list[Any] = []

    def get(self, model: Any, key: Any) -> Any:
        self.got.append(key)
        return self.path

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def registered(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_register(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(cp_ws.course_progress_hub, "register", fake_register)
    monkeypatch.setattr(cp_ws, "is_admin", lambda u: False)
    monkeypatch.setattr(cp_ws, "is_teacher", lambda u: True)
    monkeypatch.setattr(cp_ws, "require_teaching_access", lambda u, owner: None)
    return calls


def _patch_user(monkeypatch: pytest.MonkeyPatch, db: FakeDb) -> None:
    user = SimpleNamespace(id=uuid.uuid4(), email="teacher@example.com")

    async def fake_get_ws_current_user(websocket: Any, token: str) -> Any:
        return user, db

    monkeypatch.setattr(cp_ws, "get_ws_current_user", fake_get_ws_current_user)


async def _run(ws: FakeWs, path_id: str) -> None:
    await cp_ws.course_progress_proxy(cast(WebSocket, ws), path_id, token="t")


async def test_non_teacher_is_denied(
    monkeypatch: pytest.MonkeyPatch, registered: list[dict[str, Any]]
) -> None:
    db = FakeDb(None)
    _patch_user(monkeypatch, db)
    monkeypatch.setattr(cp_ws, "is_teacher", lambda u: False)
    ws = FakeWs()

    await _run(ws, str(uuid.uuid4()))

    assert ws.close_calls == [(1008, "Permission denied")]
    assert not ws.accepted
    assert db.closed
    assert registered == []


async def test_invalid_path_id(
    monkeypatch: pytest.MonkeyPatch, registered: list[dict[str, Any]]
) -> None:
    db = FakeDb(None)
    _patch_user(monkeypatch, db)
    ws = FakeWs()

    await _run(ws, "not-a-uuid")

    assert ws.close_calls == [(1008, "Invalid path id")]
    assert db.got == []


async def test_path_not_found(
    monkeypatch: pytest.MonkeyPatch, registered: list[dict[str, Any]]
) -> None:
    db = FakeDb(None)
    _patch_user(monkeypatch, db)
    ws = FakeWs()

    await _run(ws, str(uuid.uuid4()))

    assert ws.close_calls == [(1008, "Path not found")]


async def test_teacher_without_access_to_path_is_denied(
    monkeypatch: pytest.MonkeyPatch, registered: list[dict[str, Any]]
) -> None:
    db = FakeDb(SimpleNamespace(created_by=OWNER_ID))
    _patch_user(monkeypatch, db)

    def deny(user: Any, owner: Any) -> None:
        raise PermissionDeniedError()

    monkeypatch.setattr(cp_ws, "require_teaching_access", deny)
    ws = FakeWs()

    await _run(ws, str(uuid.uuid4()))

    assert ws.close_calls == [(1008, "Permission denied")]
    assert registered == []
