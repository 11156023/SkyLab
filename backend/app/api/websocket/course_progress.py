"""課程進度 WebSocket：老師端訂閱單一學習路徑的即時答題事件。"""

import logging
import uuid

from fastapi import WebSocket

from app.api.deps.auth import get_ws_current_user
from app.api.websocket.utils import safe_close_websocket
from app.core.authorizers import require_teaching_access
from app.core.db import run_db_in_threadpool
from app.core.permissions import is_admin, is_teacher
from app.exceptions import AppError
from app.models import CoursePath
from app.services.course.progress_hub import course_progress_hub

logger = logging.getLogger(__name__)


async def course_progress_proxy(websocket: WebSocket, path_id: str, token: str) -> None:
    """驗證 token + 該路徑擁有老師/管理員權限後，把連線掛進該路徑的進度推播 hub。"""
    user, db = await get_ws_current_user(websocket, token=token)

    def authorize() -> tuple[uuid.UUID | None, str | None]:
        try:
            if not (is_teacher(user) or is_admin(user)):
                return None, "Permission denied"
            try:
                parsed = uuid.UUID(path_id)
            except ValueError:
                return None, "Invalid path id"
            path = db.get(CoursePath, parsed)
            if path is None:
                return None, "Path not found"
            try:
                require_teaching_access(user, path.created_by)
            except AppError:
                return None, "Permission denied"
            return parsed, None
        finally:
            db.close()

    parsed_path_id, reason = await run_db_in_threadpool(authorize)
    if reason:
        await safe_close_websocket(websocket, code=1008, reason=reason)
        return
    assert parsed_path_id is not None

    await websocket.accept()
    logger.info(
        "Course progress subscriber connected: user=%s path=%s",
        user.email,
        path_id,
    )
    await course_progress_hub.register(path_id=parsed_path_id, websocket=websocket)
    logger.info(
        "Course progress subscriber disconnected: user=%s path=%s",
        user.email,
        path_id,
    )
