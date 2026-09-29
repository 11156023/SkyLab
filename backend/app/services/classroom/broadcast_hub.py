"""常駐 WebSocket 推播 hub 的共用骨架（教室信令 hub、課程進度 hub 共用）。

只管連線登記、斷線偵測與並行推播；誰該收到事件由子類別自己挑連線。
連線以物件身分區分，同一位使用者開多個分頁各是一條。
"""

import asyncio
from typing import Any, Generic, Protocol, TypeVar

from app.utils.websocket import close_quietly


class JsonSocket(Protocol):
    """已 accept 的 FastAPI WebSocket 需要的最小介面。"""

    async def receive_text(self) -> str:
        """讀取下一則文字訊息（斷線時拋出）。"""

    async def send_json(self, data: dict[str, Any]) -> None:
        """推送一則 JSON 事件。"""


class HubConnection(Protocol):
    @property
    def key(self) -> object: ...

    @property
    def websocket(self) -> JsonSocket: ...


ConnT = TypeVar("ConnT", bound=HubConnection)


class JsonBroadcastHub(Generic[ConnT]):
    def __init__(self) -> None:
        self._connections: dict[object, ConnT] = {}

    def _send_timeout(self) -> float:
        """單一連線的推播逾時秒數。

        子類別各自回傳自己模組的 ``SEND_TIMEOUT_SECONDS``，並在呼叫當下才讀，
        測試 monkeypatch 那個模組常數才會生效。
        """
        raise NotImplementedError

    async def _hold(self, conn: ConnT) -> None:
        """登記連線並常駐讀取直到斷線（訊息內容忽略，僅偵測斷線）。"""
        self._connections[conn.key] = conn
        try:
            while True:
                await conn.websocket.receive_text()
        except Exception:
            pass  # 斷線（WebSocketDisconnect 或其他中斷）屬正常結束
        finally:
            self._connections.pop(conn.key, None)

    async def _send_to(self, connections: list[ConnT], event: dict[str, Any]) -> None:
        """同時推給所有連線：逐一 await 會讓一條慢連線拖住整批推播。"""
        if not connections:
            return
        await asyncio.gather(
            *(self._send_one(conn, event) for conn in connections),
            return_exceptions=True,
        )

    async def _send_one(self, conn: ConnT, event: dict[str, Any]) -> None:
        try:
            await asyncio.wait_for(
                conn.websocket.send_json(event), timeout=self._send_timeout()
            )
        except Exception:
            # 逾時或送出失敗一律當死連線清掉；_hold 端的 finally 再清一次是 no-op
            self._connections.pop(conn.key, None)
            await close_quietly(conn.websocket)


__all__ = ["HubConnection", "JsonBroadcastHub", "JsonSocket"]
