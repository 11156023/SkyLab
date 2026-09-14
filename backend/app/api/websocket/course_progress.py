"""課程進度 WebSocket：老師端訂閱單一學習路徑的即時答題事件。"""

import logging
import uuid

from fastapi import WebSocket

from app.api.deps.auth import get_ws_current_user
from app.api.websocket.utils import safe_close_websocket
from app.core.authorizers import require_teaching_access
from app.core.permissions import is_admin, is_teacher
from app.exceptions import AppError
from app.models import CoursePath
from app.services.course.progress_hub import course_progress_hub

logger = logging.getLogger(__name__)


async def course_progress_proxy(
    websocket: WebSocket, path_id: str, token: str
) -> None:
    """驗證 token + 該路徑擁有老師/管理員權限後，把連線掛進該路徑的進度推播 hub。"""
    user, db = await get_ws_current_user(websocket, token=token)
    try:
        if not (is_teacher(user) or is_admin(user)):
            await safe_close_websocket(
                websocket, code=1008, reason="Permission denied"
            )
            return
        try:
            parsed_path_id = uuid.UUID(path_id)
        except ValueError:
            await safe_close_websocket(
                websocket, code=1008, reason="Invalid path id"
            )
            return
        path = db.get(CoursePath, parsed_path_id)
        if path is None:
            await safe_close_websocket(
                websocket, code=1008, reason="Path not found"
            )
            return
        # 與 REST 端 course_admin 一致：只有路徑擁有者（或管理員）能訂閱
        # 學生答題事件，避免任何老師都能監看其他老師課程的進度。
        try:
            require_teaching_access(user, path.created_by)
        except AppError:
            await safe_close_websocket(
                websocket, code=1008, reason="Permission denied"
            )
            return
    finally:
        db.close()

    await websocket.accept()
    logger.info(
        "Course progress subscriber connected: user=%s path=%s",
        user.email,
        path_id,
    )
    await course_progress_hub.register(
        path_id=parsed_path_id, websocket=websocket
    )
    logger.info(
        "Course progress subscriber disconnected: user=%s path=%s",
        user.email,
        path_id,
    )
