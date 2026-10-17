"""app.api.websocket.classroom 的 presence 信令與 VNC 觀看授權／錯誤分支。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.api.websocket import classroom as classroom_ws
from app.exceptions import AppError

USER_ID = uuid.uuid4()


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
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _patch_user(monkeypatch: pytest.MonkeyPatch, *, admin: bool = False) -> FakeDb:
    user = SimpleNamespace(id=USER_ID, email="cls@example.com")
    db = FakeDb()

    async def fake_get_ws_current_user(websocket: Any, token: str) -> Any:
        return user, db

    monkeypatch.setattr(classroom_ws, "get_ws_current_user", fake_get_ws_current_user)
    monkeypatch.setattr(classroom_ws, "is_admin", lambda u: admin)
    return db


def _patch_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: Any = None,
    started_by: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
    attach_error: BaseException | None = None,
    missing: bool = False,
) -> list[uuid.UUID]:
    attached: list[uuid.UUID] = []
    session = (
        None
        if missing
        else SimpleNamespace(
            mode=mode or classroom_ws.SessionMode.broadcast,
            started_by=started_by or uuid.uuid4(),
            class_id=class_id or uuid.uuid4(),
            vmid=300,
        )
    )

    async def fake_attach(
        session_id: str, *, user_id: uuid.UUID, websocket: Any
    ) -> None:
        if attach_error is not None:
            raise attach_error
        attached.append(user_id)

    monkeypatch.setattr(
        classroom_ws.vnc_session_manager, "get_session", lambda session_id: session
    )
    monkeypatch.setattr(
        classroom_ws.vnc_session_manager, "attach_subscriber", fake_attach
    )
    return attached


async def _watch(ws: FakeWs) -> None:
    await classroom_ws.classroom_watch_proxy(cast(WebSocket, ws), "sess-x", token="t")


# ── presence ──────────────────────────────────────────────────────


# ── watch 授權 ────────────────────────────────────────────────────


async def test_watch_session_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _patch_user(monkeypatch)
    _patch_session(monkeypatch, missing=True)
    ws = FakeWs()

    await _watch(ws)

    assert ws.close_calls == [(1008, "Session not found")]
    assert not ws.accepted
    assert db.closed


async def test_monitor_non_starter_checks_class_watch_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_user(monkeypatch)
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        classroom_ws, "require_classroom_monitor", lambda u, detail: None
    )
    monkeypatch.setattr(
        classroom_ws.classroom_service,
        "require_can_watch_class",
        lambda db, user, class_id, vmid: calls.append((class_id, vmid)),
    )
    class_id = uuid.uuid4()
    attached = _patch_session(
        monkeypatch, mode=classroom_ws.SessionMode.monitor, class_id=class_id
    )
    ws = FakeWs()

    await _watch(ws)

    assert calls == [(class_id, 300)]
    assert attached == [USER_ID]
    assert ws.close_calls == [(1000, "Session ended")]


async def test_broadcast_outsider_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_user(monkeypatch)
    monkeypatch.setattr(
        classroom_ws.classroom_service,
        "get_class_ids_of_user",
        lambda db, user_id: set(),
    )
    attached = _patch_session(monkeypatch)
    ws = FakeWs()

    await _watch(ws)

    assert attached == []
    assert ws.close_calls == [(1008, "Permission denied")]


# ── attach_subscriber 錯誤分支 ─────────────────────────────────────


@pytest.mark.parametrize(
    ("error", "expected_first_close"),
    [
        pytest.param(
            AppError("session full", 409), (1008, "session full"), id="app-error"
        ),
        pytest.param(TimeoutError(), (1008, "Handshake timeout"), id="timeout"),
        pytest.param(
            RuntimeError("boom"), (1011, "Internal server error"), id="unexpected"
        ),
    ],
)
async def test_attach_errors_close_with_reason(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    expected_first_close: tuple[int, str],
) -> None:
    _patch_user(monkeypatch)
    _patch_session(monkeypatch, started_by=USER_ID, attach_error=error)
    ws = FakeWs()

    await _watch(ws)

    assert ws.accepted
    assert ws.close_calls == [expected_first_close, (1000, "Session ended")]
