"""教室 WebSocket：信令（presence）與 VNC 觀看資料面。"""

import logging

from fastapi import WebSocket

from app.api.deps.auth import get_ws_current_user
from app.api.websocket.utils import safe_close_websocket
from app.core.permissions import is_admin
from app.exceptions import AppError
from app.services.classroom import classroom_service
from app.services.classroom.presence import classroom_presence_hub
from app.services.classroom.vnc_session_manager import (
    SessionMode,
    vnc_session_manager,
)

logger = logging.getLogger(__name__)


async def classroom_presence_proxy(websocket: WebSocket, token: str) -> None:
    """信令連線：常駐直到斷線，接收 live/takeover 事件並回報 online 狀態。"""
    user, db = await get_ws_current_user(websocket, token=token)
    try:
        group_ids = classroom_service.get_group_ids_of_user(db, user.id)
    finally:
        db.close()

    await websocket.accept()
    logger.info(f"Classroom presence connected for user {user.email}")
    await classroom_presence_hub.register(
        user_id=user.id, group_ids=group_ids, websocket=websocket
    )
    logger.info(f"Classroom presence disconnected for user {user.email}")


async def classroom_watch_proxy(
    websocket: WebSocket, session_id: str, token: str
) -> None:
    """VNC 資料面：驗證權限後把訂閱者掛進 session fan-out。"""
    user, db = await get_ws_current_user(websocket, token=token)
    try:
        session = vnc_session_manager.get_session(session_id)
        if session is None:
            await safe_close_websocket(websocket, code=1008, reason="Session not found")
            return
        if session.mode is SessionMode.monitor:
            # monitor：發起者已通過 require_can_watch；其他觀看者同樣檢查
            classroom_service.require_can_watch(db, user, session.vmid)
        else:
            # broadcast：群組成員、發起者或 admin
            allowed = (
                session.started_by == user.id
                or is_admin(user)
                or (
                    session.group_id is not None
                    and session.group_id
                    in classroom_service.get_group_ids_of_user(db, user.id)
                )
            )
            if not allowed:
                await safe_close_websocket(
                    websocket, code=1008, reason="Permission denied"
                )
                return
    except AppError as exc:
        await safe_close_websocket(websocket, code=1008, reason=exc.message)
        return
    finally:
        db.close()

    await websocket.accept()
    try:
        await vnc_session_manager.attach_subscriber(
            session_id, user_id=user.id, websocket=websocket
        )
    except AppError as exc:
        await safe_close_websocket(websocket, code=1008, reason=exc.message)
    except Exception:
        logger.exception(f"Classroom watch failed for session {session_id}")
        await safe_close_websocket(websocket, code=1011, reason="Internal server error")
    finally:
        await safe_close_websocket(websocket, code=1000, reason="Session ended")
