"""教室信令 hub：常駐 WebSocket 連線的線上名單與事件推播。

事件 payload 形如：
    {"type": "live_started" | "live_stopped" | "takeover_started"
             | "takeover_stopped" | "watch_force_closed",
     "session_id": ..., "vmid": ..., "class_id": ...}
"""

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.services.classroom.broadcast_hub import JsonBroadcastHub, JsonSocket

logger = logging.getLogger(__name__)

# 單一連線的推播逾時：一條卡住的 TCP 連線不能讓整班的事件跟著卡住
SEND_TIMEOUT_SECONDS = 5


@dataclass
class _Connection:
    user_id: uuid.UUID
    class_ids: set[uuid.UUID]
    websocket: JsonSocket
    # dataclass eq=False 效果：以身分比較，同一 user 多分頁各是一條連線
    key: object = field(default_factory=object)


class ClassroomPresenceHub(JsonBroadcastHub[_Connection]):
    def _send_timeout(self) -> float:
        return SEND_TIMEOUT_SECONDS

    async def register(
        self,
        *,
        user_id: uuid.UUID,
        class_ids: set[uuid.UUID],
        websocket: JsonSocket,
    ) -> None:
        """註冊連線並常駐讀取直到斷線（訊息內容忽略，僅偵測斷線）。"""
        await self._hold(
            _Connection(user_id=user_id, class_ids=set(class_ids), websocket=websocket)
        )

    def online_user_ids_for_class(self, class_id: uuid.UUID) -> set[uuid.UUID]:
        return {
            conn.user_id
            for conn in self._connections.values()
            if class_id in conn.class_ids
        }

    async def broadcast_to_class(self, class_id: uuid.UUID, event: dict[str, Any]) -> None:
        await self._send_to(
            [c for c in self._connections.values() if class_id in c.class_ids], event
        )

    async def send_to_user(self, user_id: uuid.UUID, event: dict[str, Any]) -> None:
        await self._send_to(
            [c for c in self._connections.values() if c.user_id == user_id], event
        )


classroom_presence_hub = ClassroomPresenceHub()
