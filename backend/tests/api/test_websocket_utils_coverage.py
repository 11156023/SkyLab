"""app.api.websocket.utils 的雙向轉發 pump、run_until_first_done 與 safe_close_websocket。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import pytest
import websockets
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.api.websocket import utils


class FakeClientWs:
    def __init__(
        self,
        script: list[Any] | None = None,
        *,
        send_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.application_state = WebSocketState.CONNECTED
        self.client_state = WebSocketState.CONNECTED
        self.script = list(script or [])
        self.sent: list[Any] = []
        self.send_error = send_error
        self.close_error = close_error
        self.close_calls: list[tuple[int, str]] = []

    async def receive(self) -> dict[str, Any]:
        if not self.script:
            await asyncio.sleep(3600)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item()
        return item

    async def send_bytes(self, data: bytes) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(data)

    async def send_text(self, data: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_calls.append((code, reason))
        if self.close_error is not None:
            raise self.close_error


class FakePve:
    def __init__(
        self,
        messages: list[Any] | None = None,
        *,
        end_error: BaseException | None = None,
        send_error: Exception | None = None,
    ) -> None:
        self.messages = list(messages or [])
        self.end_error = end_error
        self.send_error = send_error
        self.sent: list[Any] = []

    def __aiter__(self) -> Any:
        return self._iter()

    async def _iter(self) -> Any:
        for message in self.messages:
            yield message
        if self.end_error is not None:
            raise self.end_error

    async def send(self, data: Any) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(data)


# ── pump_upstream_to_client ───────────────────────────────────────


async def test_upstream_forwards_bytes_and_text() -> None:
    ws = FakeClientWs()
    disconnect = asyncio.Event()
    await utils.pump_upstream_to_client(
        FakePve([b"\x01", "hello"]), cast(WebSocket, ws), disconnect
    )

    assert ws.sent == [b"\x01", "hello"]
    assert disconnect.is_set()


async def test_upstream_stops_when_already_disconnected() -> None:
    ws = FakeClientWs()
    disconnect = asyncio.Event()
    disconnect.set()
    await utils.pump_upstream_to_client(
        FakePve([b"x"]), cast(WebSocket, ws), disconnect
    )

    assert ws.sent == []


async def test_upstream_stops_when_client_send_fails() -> None:
    ws = FakeClientWs(send_error=RuntimeError("client gone"))
    disconnect = asyncio.Event()
    await utils.pump_upstream_to_client(
        FakePve([b"x", b"y"]), cast(WebSocket, ws), disconnect
    )

    assert disconnect.is_set()


async def test_upstream_connection_closed_is_normal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = FakeClientWs()
    disconnect = asyncio.Event()
    closed = websockets.exceptions.ConnectionClosed(None, None)
    with caplog.at_level(logging.ERROR, logger=utils.logger.name):
        await utils.pump_upstream_to_client(
            FakePve([b"a"], end_error=closed), cast(WebSocket, ws), disconnect
        )

    assert ws.sent == [b"a"]
    assert disconnect.is_set()
    assert not caplog.records


async def test_upstream_unexpected_error_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = FakeClientWs()
    disconnect = asyncio.Event()
    with caplog.at_level(logging.ERROR, logger=utils.logger.name):
        await utils.pump_upstream_to_client(
            FakePve(end_error=ValueError("bad frame")), cast(WebSocket, ws), disconnect
        )

    assert disconnect.is_set()
    assert any(
        "Error forwarding from Proxmox" in r.getMessage() for r in caplog.records
    )


# ── pump_client_to_upstream ───────────────────────────────────────


async def test_client_forwards_bytes_and_text_until_disconnect() -> None:
    ws = FakeClientWs(
        [
            {"type": "websocket.receive", "bytes": b"\x02"},
            {"type": "websocket.receive", "text": "cmd"},
            {"type": "websocket.receive"},
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )
    pve = FakePve()
    disconnect = asyncio.Event()
    await utils.pump_client_to_upstream(cast(WebSocket, ws), pve, disconnect)

    assert pve.sent == [b"\x02", "cmd"]
    assert disconnect.is_set()


async def test_client_stops_when_disconnect_set_during_receive() -> None:
    disconnect = asyncio.Event()

    def upstream_closed() -> dict[str, Any]:
        disconnect.set()
        return {"type": "websocket.receive", "text": "late"}

    ws = FakeClientWs([upstream_closed])
    pve = FakePve()
    await utils.pump_client_to_upstream(cast(WebSocket, ws), pve, disconnect)

    assert pve.sent == []


async def test_client_websocket_disconnect_is_normal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = FakeClientWs([WebSocketDisconnect(1001)])
    disconnect = asyncio.Event()
    with caplog.at_level(logging.ERROR, logger=utils.logger.name):
        await utils.pump_client_to_upstream(cast(WebSocket, ws), FakePve(), disconnect)

    assert disconnect.is_set()
    assert not caplog.records


async def test_client_forward_error_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = FakeClientWs([{"type": "websocket.receive", "text": "x"}])
    pve = FakePve(send_error=RuntimeError("pipe"))
    disconnect = asyncio.Event()
    with caplog.at_level(logging.ERROR, logger=utils.logger.name):
        await utils.pump_client_to_upstream(cast(WebSocket, ws), pve, disconnect)

    assert disconnect.is_set()
    assert any("Error forwarding to Proxmox" in r.getMessage() for r in caplog.records)


# ── run_until_first_done ──────────────────────────────────────────


# ── safe_close_websocket ──────────────────────────────────────────


async def test_safe_close_reraises_unrelated_attribute_error() -> None:
    ws = FakeClientWs(close_error=AttributeError("something else"))
    with pytest.raises(AttributeError, match="something else"):
        await utils.safe_close_websocket(cast(WebSocket, ws), code=1000)
