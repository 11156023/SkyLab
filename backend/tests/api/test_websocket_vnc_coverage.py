"""app.api.websocket.vnc 的 VNC WebSocket 代理：權限、票證、連線失敗與雙向轉發。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest
import websockets
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.api.websocket import vnc
from app.exceptions import NotFoundError, ProxmoxError
from app.infrastructure.vnc.messages import RfbStreamError

VMID = 101
NODE = "pve1"


class FakeClientWs:
    """瀏覽器端：receive 依腳本回傳；腳本項目可為 dict、例外或回呼。"""

    def __init__(self, script: list[Any] | None = None) -> None:
        self.application_state = WebSocketState.CONNECTED
        self.client_state = WebSocketState.CONNECTED
        self.accepted = False
        self.close_calls: list[tuple[int, str]] = []
        self.script = list(script or [])
        self.disconnect: asyncio.Event | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_calls.append((code, reason))

    async def receive(self) -> dict[str, Any]:
        if not self.script:
            await asyncio.sleep(3600)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item(self)
        return item


class FakePve:
    def __init__(self, send_error: Exception | None = None) -> None:
        self.sent: list[Any] = []
        self.closed = False
        self.send_error = send_error

    async def send(self, data: Any) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


class FakeSplitter:
    def __init__(self, pending: bytes = b"") -> None:
        self.pending = pending


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """預設替換外部依賴為成功路徑；個別測試再覆寫。"""
    state: dict[str, Any] = {
        "session_closed": False,
        "session_ticket_calls": 0,
        "pve": FakePve(),
        "open_args": None,
    }
    monkeypatch.setattr(vnc, "_vnc_session_cookies", {})

    user = SimpleNamespace(id=uuid.uuid4(), email="vnc@example.com")

    def close_session() -> None:
        state["session_closed"] = True

    session = SimpleNamespace(close=close_session)

    async def fake_get_ws_current_user(websocket: Any, token: str) -> Any:
        return user, session

    async def fake_get_session_ticket(node: str) -> tuple[str, str]:
        state["session_ticket_calls"] += 1
        return "PVEAuthCookie=abc", "csrf"

    async def fake_get_vnc_ticket_with_session(
        node: str, vmid: int, cookie: str, csrf: str
    ) -> dict[str, Any]:
        return {"port": "5900", "ticket": "fresh-ticket"}

    async def fake_open(*args: Any) -> FakePve:
        state["open_args"] = args
        return state["pve"]

    async def fake_pump(pve: Any, websocket: Any, disconnect: asyncio.Event) -> None:
        websocket.disconnect = disconnect
        await asyncio.sleep(3600)

    monkeypatch.setattr(vnc, "get_ws_current_user", fake_get_ws_current_user)
    monkeypatch.setattr(vnc, "require_resource_console_access", lambda **kwargs: None)
    monkeypatch.setattr(
        vnc.proxmox_service, "find_resource", lambda vmid: {"node": NODE}
    )
    monkeypatch.setattr(
        vnc.proxmox_service, "get_session_ticket", fake_get_session_ticket
    )
    monkeypatch.setattr(
        vnc.proxmox_service,
        "get_vnc_ticket_with_session",
        fake_get_vnc_ticket_with_session,
    )
    monkeypatch.setattr(vnc, "open_vncwebsocket", fake_open)
    monkeypatch.setattr(vnc, "pump_upstream_to_client", fake_pump)
    return state


async def _run(ws: FakeClientWs, **kwargs: Any) -> None:
    await asyncio.wait_for(
        vnc.vnc_proxy(cast(WebSocket, ws), VMID, token="tok", **kwargs), timeout=5
    )


def _disconnect_msg() -> dict[str, Any]:
    return {"type": "websocket.disconnect", "code": 1000}


# ── session cookie 快取 ────────────────────────────────────────────


def test_register_and_get_cached_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(vnc, "monotonic", lambda: now[0])

    vnc.register_vnc_session_cookie(VMID, "ticket-a", "cookie-a")
    assert vnc._get_cached_vnc_session_cookie(VMID, "ticket-a") == "cookie-a"
    assert vnc._get_cached_vnc_session_cookie(VMID, "other") is None

    # 超過 TTL 後被清掉
    now[0] += vnc._VNC_SESSION_CACHE_TTL_SECONDS + 1
    assert vnc._get_cached_vnc_session_cookie(VMID, "ticket-a") is None
    assert vnc._vnc_session_cookies == {}


# ── 權限檢查 ───────────────────────────────────────────────────────


async def test_permission_check_unexpected_error_is_logged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    _isolate: dict[str, Any],
) -> None:
    def broken(**kwargs: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(vnc, "require_resource_console_access", broken)
    ws = FakeClientWs()
    with caplog.at_level(logging.ERROR, logger=vnc.logger.name):
        await _run(ws)

    assert ws.close_calls == [(1008, "Permission denied")]
    assert _isolate["session_closed"]
    assert any("permission check failed" in r.getMessage() for r in caplog.records)


# ── 找不到 VM／認證失敗 ────────────────────────────────────────────


async def test_vm_not_found_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_found(vmid: int) -> Any:
        raise NotFoundError()

    monkeypatch.setattr(vnc.proxmox_service, "find_resource", not_found)
    ws = FakeClientWs()
    await _run(ws)

    assert ws.accepted
    assert ws.close_calls == [(1008, "VM not found")]


async def _failing_session_ticket(node: str) -> Any:
    raise ProxmoxError("auth failed")


async def test_reused_ticket_without_cached_cookie_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        vnc.proxmox_service, "get_session_ticket", _failing_session_ticket
    )
    ws = FakeClientWs()
    await _run(ws, vnc_ticket="t1", vnc_port="5901")

    assert ws.close_calls == [(1008, "Authentication failed")]


async def test_fresh_ticket_auth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vnc.proxmox_service, "get_session_ticket", _failing_session_ticket
    )
    ws = FakeClientWs()
    await _run(ws)

    assert ws.close_calls == [(1008, "Authentication failed")]


# ── 連 PVE WebSocket 失敗 ──────────────────────────────────────────


async def test_open_rejected_with_invalid_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def rejected(*args: Any) -> Any:
        raise websockets.exceptions.InvalidStatus(
            cast(Any, SimpleNamespace(status_code=401, headers={}))
        )

    monkeypatch.setattr(vnc, "open_vncwebsocket", rejected)
    ws = FakeClientWs()
    await _run(ws)

    assert ws.close_calls == [(1008, "Proxmox connection failed")]


async def test_open_generic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(*args: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(vnc, "open_vncwebsocket", broken)
    ws = FakeClientWs()
    await _run(ws)

    assert ws.close_calls == [(1008, "Proxmox connection failed")]


async def test_unexpected_error_closes_with_1011(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_vnc_ticket(*args: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        vnc.proxmox_service, "get_vnc_ticket_with_session", broken_vnc_ticket
    )
    ws = FakeClientWs()
    await _run(ws)

    assert ws.close_calls == [(1011, "Internal server error")]


# ── 成功連線與轉發 ─────────────────────────────────────────────────


async def test_forwarding_filters_bytes_and_passes_text(
    monkeypatch: pytest.MonkeyPatch, _isolate: dict[str, Any]
) -> None:
    blocked_calls: list[bool] = []

    def fake_filter(splitter: Any, data: bytes, *, blocked: bool) -> list[bytes]:
        blocked_calls.append(blocked)
        return [data + b"-1", data + b"-2"]

    monkeypatch.setattr(vnc, "filter_client_bytes", fake_filter)
    monkeypatch.setattr(vnc, "ClientMessageSplitter", FakeSplitter)
    monkeypatch.setattr(vnc.vnc_session_manager, "is_input_blocked", lambda vmid: True)
    ws = FakeClientWs(
        [
            {"type": "websocket.receive", "bytes": b"k"},
            {"type": "websocket.receive", "text": "hello"},
            {"type": "websocket.receive"},
            _disconnect_msg(),
        ]
    )
    await _run(ws)

    assert ws.accepted
    assert _isolate["open_args"][3:5] == ("5900", "fresh-ticket")
    assert _isolate["pve"].sent == [b"k-1", b"k-2", "hello"]
    assert blocked_calls == [True]
    assert _isolate["pve"].closed


async def test_filter_lost_sync_falls_back_to_passthrough(
    monkeypatch: pytest.MonkeyPatch, _isolate: dict[str, Any]
) -> None:
    def lose_sync(splitter: Any, data: bytes, *, blocked: bool) -> list[bytes]:
        raise RfbStreamError("unknown message type")

    monkeypatch.setattr(vnc, "filter_client_bytes", lose_sync)
    monkeypatch.setattr(vnc, "ClientMessageSplitter", lambda: FakeSplitter(b"rest"))
    ws = FakeClientWs(
        [
            {"type": "websocket.receive", "bytes": b"bad"},
            {"type": "websocket.receive", "bytes": b"raw"},
            _disconnect_msg(),
        ]
    )
    await _run(ws)

    # 失去同步：先送出 splitter 殘留，其後原樣轉發
    assert _isolate["pve"].sent == [b"rest", b"raw"]


async def test_disconnect_set_while_waiting_for_client(
    _isolate: dict[str, Any],
) -> None:
    def upstream_closed(ws: FakeClientWs) -> dict[str, Any]:
        # 等待 client 訊息期間上游已斷線
        assert ws.disconnect is not None
        ws.disconnect.set()
        return {"type": "websocket.receive", "text": "late"}

    ws = FakeClientWs([cast(Callable[..., Any], upstream_closed)])
    await _run(ws)

    assert _isolate["pve"].sent == []
    assert _isolate["pve"].closed


async def test_client_disconnect_exception_is_normal_end(
    _isolate: dict[str, Any],
) -> None:
    ws = FakeClientWs([WebSocketDisconnect(1006)])
    await _run(ws)

    assert ws.close_calls == []
    assert _isolate["pve"].closed


async def test_forwarding_error_is_logged(
    caplog: pytest.LogCaptureFixture, _isolate: dict[str, Any]
) -> None:
    _isolate["pve"] = FakePve(send_error=RuntimeError("pipe broken"))
    ws = FakeClientWs([{"type": "websocket.receive", "text": "x"}])
    with caplog.at_level(logging.ERROR, logger=vnc.logger.name):
        await _run(ws)

    assert any("Error forwarding to Proxmox" in r.getMessage() for r in caplog.records)
    assert _isolate["pve"].closed
