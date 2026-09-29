"""教室 monitor 觀看的權限檢查。

發起者被降為學生後不應再能掛上 monitor session；
admin 與仍有監看權限的發起者照常可觀看；broadcast 不受影響（學生合法觀看）。
"""

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.api.websocket import classroom as classroom_ws
from app.models import UserRole


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


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    user: Any,
    mode: Any,
    started_by: uuid.UUID,
    class_ids: list[uuid.UUID] | None = None,
) -> list[uuid.UUID]:
    db = SimpleNamespace(close=lambda: None)
    class_id = uuid.uuid4()
    attached: list[uuid.UUID] = []

    async def fake_get_ws_current_user(websocket: Any, token: str) -> tuple[Any, Any]:
        return user, db

    async def fake_attach_subscriber(
        session_id: str, *, user_id: uuid.UUID, websocket: Any
    ) -> None:
        attached.append(user_id)

    session = SimpleNamespace(
        mode=mode, started_by=started_by, class_id=class_id, vmid=100
    )
    monkeypatch.setattr(classroom_ws, "get_ws_current_user", fake_get_ws_current_user)
    monkeypatch.setattr(
        classroom_ws.vnc_session_manager, "get_session", lambda session_id: session
    )
    monkeypatch.setattr(
        classroom_ws.vnc_session_manager, "attach_subscriber", fake_attach_subscriber
    )
    monkeypatch.setattr(
        classroom_ws.classroom_service,
        "get_class_ids_of_user",
        lambda db, user_id: [class_id] if class_ids is None else class_ids,
    )
    return attached


async def test_demoted_starter_cannot_watch_monitor_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id=uuid.uuid4(), email="s@example.com", role=UserRole.student)
    attached = _patch(
        monkeypatch,
        user=user,
        mode=classroom_ws.SessionMode.monitor,
        started_by=user.id,
    )
    ws = FakeWs()

    await classroom_ws.classroom_watch_proxy(cast(WebSocket, ws), "s1", token="tok")

    assert not ws.accepted
    assert attached == []
    assert ws.close_calls and ws.close_calls[0][0] == 1008


async def test_teacher_starter_can_watch_monitor_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id=uuid.uuid4(), email="t@example.com", role=UserRole.teacher)
    attached = _patch(
        monkeypatch,
        user=user,
        mode=classroom_ws.SessionMode.monitor,
        started_by=user.id,
    )
    ws = FakeWs()

    await classroom_ws.classroom_watch_proxy(cast(WebSocket, ws), "s1", token="tok")

    assert ws.accepted
    assert attached == [user.id]


async def test_admin_can_watch_monitor_session_started_by_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id=uuid.uuid4(), email="a@example.com", role=UserRole.admin)
    attached = _patch(
        monkeypatch,
        user=user,
        mode=classroom_ws.SessionMode.monitor,
        started_by=uuid.uuid4(),
    )
    ws = FakeWs()

    await classroom_ws.classroom_watch_proxy(cast(WebSocket, ws), "s1", token="tok")

    assert ws.accepted
    assert attached == [user.id]


async def test_student_class_member_can_watch_broadcast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id=uuid.uuid4(), email="s@example.com", role=UserRole.student)
    attached = _patch(
        monkeypatch,
        user=user,
        mode=classroom_ws.SessionMode.broadcast,
        started_by=uuid.uuid4(),
    )
    ws = FakeWs()

    await classroom_ws.classroom_watch_proxy(cast(WebSocket, ws), "s1", token="tok")

    assert ws.accepted
    assert attached == [user.id]
