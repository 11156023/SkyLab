"""教室信令 hub：常駐 WebSocket 連線的線上名單與事件推播。

事件 payload 形如：
    {"type": "live_started" | "live_stopped" | "takeover_started"
             | "takeover_stopped" | "watch_force_closed",
     "session_id": ..., "vmid": ..., "group_id": ...}
"""

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class PresenceSocket(Protocol):
    """已 accept 的 FastAPI WebSocket 需要的最小介面。"""

    async def receive_text(self) -> str: ...

    async def send_json(self, data: dict[str, Any]) -> None: ...


@dataclass
class _Connection:
    user_id: uuid.UUID
    group_ids: set[uuid.UUID]
    websocket: PresenceSocket
    # dataclass eq=False 效果：以身分比較，同一 user 多分頁各是一條連線
    key: object = field(default_factory=object)


class ClassroomPresenceHub:
    def __init__(self) -> None:
        self._connections: dict[object, _Connection] = {}

    async def register(
        self,
        *,
        user_id: uuid.UUID,
        group_ids: set[uuid.UUID],
        websocket: PresenceSocket,
    ) -> None:
        """註冊連線並常駐讀取直到斷線（訊息內容忽略，僅偵測斷線）。"""
        conn = _Connection(user_id=user_id, group_ids=set(group_ids), websocket=websocket)
        self._connections[conn.key] = conn
        try:
            while True:
                await websocket.receive_text()
        except Exception:
            pass  # 斷線（WebSocketDisconnect 或其他中斷）屬正常結束
        finally:
            self._connections.pop(conn.key, None)

    def online_user_ids(self, group_id: uuid.UUID) -> set[uuid.UUID]:
        return {
            conn.user_id
            for conn in self._connections.values()
            if group_id in conn.group_ids
        }

    async def broadcast_to_group(self, group_id: uuid.UUID, event: dict[str, Any]) -> None:
        await self._send_to(
            [c for c in self._connections.values() if group_id in c.group_ids], event
        )

    async def send_to_user(self, user_id: uuid.UUID, event: dict[str, Any]) -> None:
        await self._send_to(
            [c for c in self._connections.values() if c.user_id == user_id], event
        )

    async def _send_to(self, connections: list[_Connection], event: dict[str, Any]) -> None:
        for conn in connections:
            try:
                await conn.websocket.send_json(event)
            except Exception:
                # 死連線自動清；register 端的 finally 再清一次是 no-op
                self._connections.pop(conn.key, None)


classroom_presence_hub = ClassroomPresenceHub()
