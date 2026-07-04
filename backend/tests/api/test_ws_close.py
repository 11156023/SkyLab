"""safe_close_websocket 與教室觀看斷線處理測試。

重現場景：客戶端在 RFB 握手或觀看途中斷線後，
關閉已死連線不應炸出 RuntimeError，也不應記成 ERROR。
"""

import logging
import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.api.websocket import classroom as classroom_ws
from app.api.websocket.utils import safe_close_websocket

# uvicorn websockets_impl 對已完成連線再送 close 的錯誤訊息
UVICORN_DOUBLE_CLOSE = (
    "Unexpected ASGI message 'websocket.close', after sending "
    "'websocket.close' or response already completed."
)
# starlette 對已送出 close 的連線再 send 的錯誤訊息
STARLETTE_DOUBLE_CLOSE = 'Cannot call "send" once a close message has been sent.'


class FakeCloseWs:
    def __init__(
        self,
        *,
        application_state: WebSocketState = WebSocketState.CONNECTED,
        client_state: WebSocketState = WebSocketState.CONNECTED,
        close_error: Exception | None = None,
    ) -> None:
        self.application_state = application_state
        self.client_state = client_state
        self.close_error = close_error
        self.close_calls: list[tuple[int, str]] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_calls.append((code, reason))
        if self.close_error is not None:
            raise self.close_error


class TestSafeCloseWebsocket:
    async def test_swallows_uvicorn_double_close_error(self) -> None:
        ws = FakeCloseWs(close_error=RuntimeError(UVICORN_DOUBLE_CLOSE))
        await safe_close_websocket(cast(WebSocket, ws), code=1000, reason="bye")

    async def test_swallows_starlette_double_close_error(self) -> None:
        ws = FakeCloseWs(close_error=RuntimeError(STARLETTE_DOUBLE_CLOSE))
        await safe_close_websocket(cast(WebSocket, ws), code=1000, reason="bye")

    async def test_skips_close_when_client_already_disconnected(self) -> None:
        ws = FakeCloseWs(client_state=WebSocketState.DISCONNECTED)
        await safe_close_websocket(cast(WebSocket, ws), code=1000, reason="bye")
        assert ws.close_calls == []

    async def test_skips_close_when_application_already_disconnected(self) -> None:
        ws = FakeCloseWs(application_state=WebSocketState.DISCONNECTED)
        await safe_close_websocket(cast(WebSocket, ws), code=1000, reason="bye")
        assert ws.close_calls == []

    async def test_reraises_unrelated_runtime_error(self) -> None:
        ws = FakeCloseWs(close_error=RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            await safe_close_websocket(cast(WebSocket, ws), code=1000)


class TestClassroomWatchDisconnect:
    async def test_client_disconnect_is_normal_end(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """握手途中客戶端斷線：不記 ERROR、不往外拋例外。"""
        user = SimpleNamespace(id=uuid.uuid4(), email="watcher@example.com")
        db = SimpleNamespace(close=lambda: None)

        async def fake_get_ws_current_user(
            websocket: Any, token: str
        ) -> tuple[Any, Any]:
            return user, db

        session = SimpleNamespace(
            mode=classroom_ws.SessionMode.broadcast,
            started_by=user.id,
            group_id=None,
            vmid=100,
        )

        async def fake_attach_subscriber(
            session_id: str, *, user_id: uuid.UUID, websocket: Any
        ) -> None:
            raise WebSocketDisconnect(1006)

        monkeypatch.setattr(
            classroom_ws, "get_ws_current_user", fake_get_ws_current_user
        )
        monkeypatch.setattr(
            classroom_ws.vnc_session_manager,
            "get_session",
            lambda session_id: session,
        )
        monkeypatch.setattr(
            classroom_ws.vnc_session_manager,
            "attach_subscriber",
            fake_attach_subscriber,
        )

        # 客戶端已斷線：後續任何 close 都會踩到 uvicorn 的 double-close
        ws = FakeCloseWs(close_error=RuntimeError(UVICORN_DOUBLE_CLOSE))
        with caplog.at_level(logging.ERROR):
            await classroom_ws.classroom_watch_proxy(
                cast(WebSocket, ws), "sess-1", token="tok"
            )

        assert ws.accepted
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors == []
