"""app.api.websocket.jobs 的輪詢、提醒快取、等待間隔與錯誤關閉分支。"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import WebSocket

from app.api.websocket import jobs as jobs_ws

USER_ID = uuid.uuid4()


class FakeSession:
    def __init__(self, user: Any) -> None:
        self.user = user
        self.closed = False

    def expire_all(self) -> None:
        pass

    def get(self, model: Any, key: Any) -> Any:
        return self.user

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeWs:
    """receive 依序：腳本為 None 時等到被 wait_for 逾時取消，否則回傳該訊息。"""

    def __init__(
        self,
        script: list[dict[str, Any] | None] | None = None,
        *,
        send_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.script = list(script or [])
        self.send_error = send_error
        self.close_error = close_error
        self.sent: list[str] = []
        self.closed_with: list[int] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, text: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(text)

    async def receive(self) -> dict[str, Any]:
        item = self.script.pop(0) if self.script else None
        if item is None:
            await asyncio.sleep(3600)
            return {}
        return item

    async def close(self, code: int = 1000) -> None:
        self.closed_with.append(code)
        if self.close_error is not None:
            raise self.close_error


class Snap:
    def __init__(self, n: int, reminders: list[str] | None) -> None:
        self.n = n
        self.reminders = reminders

    def model_dump_json(self) -> str:
        return f"{self.n}:{self.reminders}"


def _user() -> SimpleNamespace:
    return SimpleNamespace(
        id=USER_ID,
        email="jobs@example.com",
        is_active=True,
        token_version=1,
        totp_required=False,
        totp_enabled=False,
    )


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    s = FakeSession(_user())

    async def fake_get_ws_current_user(websocket: Any, token: str) -> Any:
        return _user(), s

    monkeypatch.setattr(jobs_ws, "get_ws_current_user", fake_get_ws_current_user)
    monkeypatch.setattr(jobs_ws, "_SNAPSHOT_INTERVAL_SECONDS", 0.01)
    return s


def test_poll_returns_snapshot_for_valid_user(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, int, bool]] = []
    sentinel = object()

    def fake_fetch(
        session: Any, user: Any, limit: int, *, include_reminders: bool
    ) -> Any:
        calls.append((user.id, limit, include_reminders))
        return sentinel

    monkeypatch.setattr(jobs_ws, "_fetch_snapshot", fake_fetch)

    result = jobs_ws._poll(
        cast(Any, FakeSession(_user())), USER_ID, 1, include_reminders=True
    )

    assert result is sentinel
    assert calls == [(USER_ID, 20, True)]


async def test_wait_for_client_returns_immediately_when_interval_elapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs_ws, "_SNAPSHOT_INTERVAL_SECONDS", 0.0)
    ws = FakeWs()

    await asyncio.wait_for(jobs_ws._wait_for_client(cast(WebSocket, ws)), timeout=2)

    assert ws.script == []


async def test_reminders_are_cached_between_refresh_rounds(
    monkeypatch: pytest.MonkeyPatch, session: FakeSession
) -> None:
    monkeypatch.setattr(jobs_ws, "_REMINDER_REFRESH_ROUNDS", 2)
    rounds: list[bool] = []

    def fake_poll_once(
        user_id: uuid.UUID, token_version: int, *, include_reminders: bool
    ) -> Snap:
        rounds.append(include_reminders)
        n = len(rounds)
        return Snap(n, ["due-soon"] if include_reminders else None)

    monkeypatch.setattr(jobs_ws, "_poll_once", fake_poll_once)
    ws = FakeWs([None, {"type": "websocket.disconnect", "code": 1000}])

    await asyncio.wait_for(
        jobs_ws.jobs_ws_proxy(cast(WebSocket, ws), token="t"), timeout=2
    )

    assert rounds == [True, False]
    # 非刷新輪沿用上一輪的提醒
    assert ws.sent == ["1:['due-soon']", "2:['due-soon']"]
    assert session.closed


async def test_close_failure_after_error_is_ignored(
    monkeypatch: pytest.MonkeyPatch, session: FakeSession
) -> None:
    monkeypatch.setattr(jobs_ws, "_poll_once", lambda *a, **k: Snap(1, None))
    ws = FakeWs(
        send_error=RuntimeError("send failed"),
        close_error=RuntimeError("already closed"),
    )

    await asyncio.wait_for(
        jobs_ws.jobs_ws_proxy(cast(WebSocket, ws), token="t"), timeout=2
    )

    assert ws.closed_with == [1011]
