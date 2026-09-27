"""課程進度推播 hub：老師端訂閱單一學習路徑的即時進度事件。

連線登記、斷線偵測與並行推播跟教室信令 hub 共用 ``JsonBroadcastHub``；
這裡只決定事件要推給訂閱哪條路徑的連線。事件 payload 形如：
    {"type": "progress", "user_id": ..., "room_id": ..., "task_id": ...,
     "question_id": ..., "room_progress_percent": ...}
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.services.classroom.broadcast_hub import JsonBroadcastHub, JsonSocket

# 與教室信令 hub 同一個約定：單一連線送不出去就淘汰，不拖住整批推播
SEND_TIMEOUT_SECONDS = 5


@dataclass
class _Connection:
    path_id: uuid.UUID
    websocket: JsonSocket
    # 以物件身分區分連線：同一位老師開多分頁各是一條
    key: object = field(default_factory=object)


class CourseProgressHub(JsonBroadcastHub[_Connection]):
    def _send_timeout(self) -> float:
        return SEND_TIMEOUT_SECONDS

    async def register(self, *, path_id: uuid.UUID, websocket: JsonSocket) -> None:
        """註冊訂閱並常駐讀取直到斷線（訊息內容忽略，僅偵測斷線）。"""
        await self._hold(_Connection(path_id=path_id, websocket=websocket))

    def subscriber_count(self, path_id: uuid.UUID) -> int:
        return sum(1 for c in self._connections.values() if c.path_id == path_id)

    async def broadcast(self, path_id: uuid.UUID, event: dict[str, Any]) -> None:
        """同時推給該路徑的所有訂閱者，避免一條慢連線拖住整批推播。"""
        await self._send_to(
            [c for c in self._connections.values() if c.path_id == path_id], event
        )


course_progress_hub = CourseProgressHub()

__all__ = ["CourseProgressHub", "course_progress_hub"]
