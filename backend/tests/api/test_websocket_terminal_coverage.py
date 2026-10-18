"""app.api.websocket.terminal 的 LXC 終端機 WebSocket 代理：權限、票證、連線失敗與轉發。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
import websockets
from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.api.websocket import terminal
from app.exceptions import NotFoundError, ProxmoxError

VMID = 202
NODE = "pve2"


class FakeClientWs:
    def __init__(self, script: list[Any] | None = None) -> None:
        self.application_state = WebSocketState.CONNECTED
        self.client_state = WebSocketState.CONNECTED
        self.accepted = False
        self.close_calls: list[tuple[int, str]] = []
        self.script = list(script or [])
        self.sent: list[Any] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_calls.append((code, reason))

    async def receive(self) -> dict[str, Any]:
        if not self.script:
            await asyncio.sleep(3600)
        return self.script.pop(0)

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


class FakePve:
    def __init__(self, send_error: Exception | None = None) -> None:
        self.sent: list[Any] = []
        self.closed = False
        self.send_error = send_error

    def __aiter__(self) -> FakePve:
        return self

    async def __anext__(self) -> Any:
        # 上游不主動送資料：等到被 run_until_first_done 取消
        await asyncio.sleep(3600)
        raise StopAsyncIteration

    async def send(self, data: Any) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    st: dict[str, Any] = {"session_closed": False, "pve": FakePve(), "open_args": None}
    user = SimpleNamespace(id=uuid.uuid4(), email="term@example.com")

    def close_session() -> None:
        st["session_closed"] = True

    session = SimpleNamespace(close=close_session)

    async def fake_get_ws_current_user(websocket: Any, token: str) -> Any:
        return user, session

    async def fake_get_session_ticket(node: str) -> tuple[str, str]:
        return "PVEAuthCookie=xyz", "csrf"

    async def fake_open(*args: Any) -> FakePve:
        st["open_args"] = args
        return st["pve"]

    monkeypatch.setattr(terminal, "get_ws_current_user", fake_get_ws_current_user)
    monkeypatch.setattr(
        terminal, "require_resource_console_access", lambda **kwargs: None
    )
    monkeypatch.setattr(
        terminal.proxmox_service,
        "find_lxc",
        lambda vmid: {"node": NODE, "status": "running"},
    )
    monkeypatch.setattr(
        terminal.proxmox_service, "get_session_ticket", fake_get_session_ticket
    )
    monkeypatch.setattr(
        terminal.proxmox_service,
        "get_terminal_ticket",
        lambda node, vmid: {"port": "5910", "ticket": "term-ticket"},
    )
    monkeypatch.setattr(terminal, "get_connection_id_for_node", lambda node: "conn-1")
    monkeypatch.setattr(
        terminal,
        "get_proxmox_settings",
        lambda connection_id: SimpleNamespace(user="root@pam"),
    )
    monkeypatch.setattr(terminal, "open_vncwebsocket", fake_open)
    return st


async def _run(ws: FakeClientWs) -> None:
    await asyncio.wait_for(
        terminal.terminal_proxy(cast(WebSocket, ws), VMID, token="tok"), timeout=5
    )


async def test_permission_check_unexpected_error_is_logged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    state: dict[str, Any],
) -> None:
    def broken(**kwargs: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(terminal, "require_resource_console_access", broken)
    ws = FakeClientWs()
    with caplog.at_level(logging.ERROR, logger=terminal.logger.name):
        await _run(ws)

    assert ws.close_calls == [(1008, "Permission denied")]
    assert state["session_closed"]
    assert any("permission check failed" in r.getMessage() for r in caplog.records)


async def test_container_not_found(
    monkeypatch: pytest.MonkeyPatch, state: dict[str, Any]
) -> None:
    def not_found(vmid: int) -> Any:
        raise NotFoundError()

    monkeypatch.setattr(terminal.proxmox_service, "find_lxc", not_found)
    ws = FakeClientWs()
    await _run(ws)

    assert ws.accepted
    assert ws.close_calls == [(1008, "LXC container not found")]


async def test_session_ticket_failure(
    monkeypatch: pytest.MonkeyPatch, state: dict[str, Any]
) -> None:
    async def failing(node: str) -> Any:
        raise ProxmoxError("auth failed")

    monkeypatch.setattr(terminal.proxmox_service, "get_session_ticket", failing)
    ws = FakeClientWs()
    await _run(ws)

    assert ws.close_calls == [(1008, "Authentication failed")]


async def test_open_rejected_with_invalid_status(
    monkeypatch: pytest.MonkeyPatch, state: dict[str, Any]
) -> None:
    async def rejected(*args: Any) -> Any:
        raise websockets.exceptions.InvalidStatus(
            cast(Any, SimpleNamespace(status_code=403, headers={"x": "y"}))
        )

    monkeypatch.setattr(terminal, "open_vncwebsocket", rejected)
    ws = FakeClientWs()
    await _run(ws)

    assert ws.close_calls == [(1008, "Proxmox connection failed")]


async def test_auth_message_send_failure(state: dict[str, Any]) -> None:
    state["pve"] = FakePve(send_error=OSError("broken pipe"))
    ws = FakeClientWs()
    await _run(ws)

    assert ws.close_calls == [(1008, "Proxmox connection failed")]
    # 連上後送認證失敗：finally 仍要關掉上游
    assert state["pve"].closed


async def test_unexpected_error_closes_with_1011(
    monkeypatch: pytest.MonkeyPatch, state: dict[str, Any]
) -> None:
    def broken(node: str, vmid: int) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(terminal.proxmox_service, "get_terminal_ticket", broken)
    ws = FakeClientWs()
    await _run(ws)

    assert ws.close_calls == [(1011, "Internal server error")]
    assert state["pve"].closed is False


async def test_successful_proxy_sends_auth_and_forwards(state: dict[str, Any]) -> None:
    ws = FakeClientWs(
        [
            {"type": "websocket.receive", "text": "ls\n"},
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )
    await _run(ws)

    assert ws.accepted
    assert state["open_args"] == (
        NODE,
        "lxc",
        VMID,
        "5910",
        "term-ticket",
        "PVEAuthCookie=xyz",
    )
    assert state["pve"].sent == ["root@pam:term-ticket\n", "ls\n"]
    assert state["pve"].closed
    assert ws.close_calls == []
