"""/ws/jobs 每輪重新確認使用者，且查詢失敗時仍看得到 client 斷線。

以前使用者被刪除後 expire_all → 重新載入會每輪拋 ObjectDeletedError，錯誤分支
只 sleep 不 receive，永遠看不到斷線，coroutine 與 DB session 一直洩漏；停用、
token 被撤銷的使用者也會一直收到快照。
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.websocket import jobs as jobs_ws

_USER_ID = uuid.uuid4()


class _Session:
    def __init__(self, user: Any) -> None:
        self._user = user
        self.calls: list[str] = []
        self.closed = False

    def expire_all(self) -> None:
        self.calls.append("expire_all")

    def get(self, model: Any, key: Any) -> Any:
        self.calls.append("get")
        return self._user

    def rollback(self) -> None:
        self.calls.append("rollback")

    def close(self) -> None:
        self.closed = True


def _user(**overrides: Any) -> SimpleNamespace:
    data: dict[str, Any] = {
        "id": _USER_ID,
        "email": "b3@example.com",
        "is_active": True,
        "token_version": 3,
        "totp_required": False,
        "totp_enabled": False,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.parametrize(
    "user",
    [
        pytest.param(None, id="deleted"),
        pytest.param(_user(is_active=False), id="deactivated"),
        pytest.param(_user(token_version=4), id="token-revoked"),
        pytest.param(_user(totp_required=True), id="must-enroll-2fa"),
    ],
)
def test_load_authorized_user_rejects_invalid_users(user: Any) -> None:
    session = _Session(user)

    assert jobs_ws._load_authorized_user(session, _USER_ID, 3) is None
    # 交易一定要結束，避免長連線佔住 idle-in-transaction
    assert session.calls == ["expire_all", "get", "rollback"]


def test_load_authorized_user_returns_valid_user() -> None:
    user = _user()
    session = _Session(user)

    assert jobs_ws._load_authorized_user(session, _USER_ID, 3) is user


class _FakeWebSocket:
    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self.accepted = False
        self.sent: list[str] = []
        self.closed_with: int | None = None
        self._messages = list(messages or [])

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def receive(self) -> dict[str, Any]:
        if self._messages:
            return self._messages.pop(0)
        await asyncio.sleep(3600)
        return {}

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


def _patch_auth(monkeypatch: pytest.MonkeyPatch, session: _Session) -> None:
    async def fake_get_ws_current_user(websocket: Any, token: str) -> Any:
        return _user(), session

    monkeypatch.setattr(jobs_ws, "get_ws_current_user", fake_get_ws_current_user)
    monkeypatch.setattr(jobs_ws, "_SNAPSHOT_INTERVAL_SECONDS", 0.01)


async def test_closes_with_1008_once_user_is_no_longer_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(None)  # 使用者已被刪除
    _patch_auth(monkeypatch, session)
    websocket = _FakeWebSocket()

    await asyncio.wait_for(jobs_ws.jobs_ws_proxy(websocket, token="t"), timeout=2)

    assert websocket.closed_with == 1008
    assert websocket.sent == []
    assert session.closed


async def test_failed_poll_still_notices_client_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_user())
    _patch_auth(monkeypatch, session)
    attempts = 0

    def failing_poll(*args: Any, **kwargs: Any) -> Any:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(jobs_ws, "_poll", failing_poll)
    websocket = _FakeWebSocket([{"type": "websocket.disconnect", "code": 1001}])

    await asyncio.wait_for(jobs_ws.jobs_ws_proxy(websocket, token="t"), timeout=2)

    # 第一次失敗後就看到斷線；若錯誤分支只 sleep，會重試到上限後走 close(1011)
    assert attempts == 1
    assert websocket.closed_with is None
    assert session.closed


async def test_gives_up_after_repeated_poll_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_user())
    _patch_auth(monkeypatch, session)
    monkeypatch.setattr(jobs_ws, "_MAX_CONSECUTIVE_FAILURES", 3)
    attempts = 0

    def failing_poll(*args: Any, **kwargs: Any) -> Any:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(jobs_ws, "_poll", failing_poll)
    websocket = _FakeWebSocket()

    await asyncio.wait_for(jobs_ws.jobs_ws_proxy(websocket, token="t"), timeout=2)

    assert attempts == 3
    assert websocket.closed_with == 1011
    assert session.closed
