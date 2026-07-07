"""課程進度推播 hub：老師端訂閱單一學習路徑的即時進度事件。

比照 classroom presence hub 的 in-memory 模式：register 常駐讀取直到斷線，
發送失敗即淘汰死連線。事件 payload 形如：
    {"type": "progress", "user_id": ..., "room_id": ..., "task_id": ...,
     "question_id": ..., "room_progress_percent": ...}
"""

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class ProgressSocket(Protocol):
    """已 accept 的 FastAPI WebSocket 需要的最小介面。"""

    async def receive_text(self) -> str: ...

    async def send_json(self, data: dict[str, Any]) -> None: ...


@dataclass
class _Connection:
    path_id: uuid.UUID
    websocket: ProgressSocket
    # 以物件身分區分連線：同一位老師開多分頁各是一條
    key: object = field(default_factory=object)


class CourseProgressHub:
    def __init__(self) -> None:
        self._connections: dict[object, _Connection] = {}

    async def register(
        self, *, path_id: uuid.UUID, websocket: ProgressSocket
    ) -> None:
        """註冊訂閱並常駐讀取直到斷線（訊息內容忽略，僅偵測斷線）。"""
        conn = _Connection(path_id=path_id, websocket=websocket)
        self._connections[conn.key] = conn
        try:
            while True:
                await websocket.receive_text()
        except Exception:
            pass  # 斷線屬正常結束
        finally:
            self._connections.pop(conn.key, None)

    def subscriber_count(self, path_id: uuid.UUID) -> int:
        return sum(
            1 for c in self._connections.values() if c.path_id == path_id
        )

    async def broadcast(self, path_id: uuid.UUID, event: dict[str, Any]) -> None:
        for conn in [
            c for c in self._connections.values() if c.path_id == path_id
        ]:
            try:
                await conn.websocket.send_json(event)
            except Exception:
                # 死連線自動清；register 端的 finally 再清一次是 no-op
                self._connections.pop(conn.key, None)


course_progress_hub = CourseProgressHub()

__all__ = ["CourseProgressHub", "course_progress_hub"]
