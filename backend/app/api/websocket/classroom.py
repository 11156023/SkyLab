"""教室 WebSocket：信令（presence）與 VNC 觀看資料面。"""

import logging
from typing import cast
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect

from app.api.deps.auth import get_ws_current_user
from app.api.websocket.utils import safe_close_websocket
from app.core.authorizers import require_classroom_monitor
from app.core.db import run_db_in_threadpool
from app.core.i18n import t
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

    def prepare() -> set[UUID]:
        try:
            return cast(set[UUID], classroom_service.get_class_ids_of_user(db, user.id))
        finally:
            db.close()

    class_ids = await run_db_in_threadpool(prepare)

    await websocket.accept()
    logger.info(f"Classroom presence connected for user {user.email}")
    await classroom_presence_hub.register(
        user_id=user.id,
        class_ids=class_ids,
        websocket=websocket,
    )
    logger.info(f"Classroom presence disconnected for user {user.email}")


async def classroom_watch_proxy(
    websocket: WebSocket, session_id: str, token: str
) -> None:
    """VNC 資料面：驗證權限後把訂閱者掛進 session fan-out。"""
    user, db = await get_ws_current_user(websocket, token=token)
    session = vnc_session_manager.get_session(session_id)

    def authorize() -> str | None:
        try:
            if session is None:
                return "Session not found"
            if session.mode is SessionMode.monitor:
                if not is_admin(user):
                    require_classroom_monitor(
                        user, detail=t("classroom.monitor_forbidden")
                    )
                if session.started_by != user.id and not is_admin(user):
                    classroom_service.require_can_watch_class(
                        db, user, session.class_id, session.vmid
                    )
            elif not (
                session.started_by == user.id
                or is_admin(user)
                or session.class_id
                in classroom_service.get_class_ids_of_user(db, user.id)
            ):
                return "Permission denied"
            return None
        except AppError as exc:
            return str(exc.message)
        finally:
            db.close()

    reason = await run_db_in_threadpool(authorize)
    if reason:
        await safe_close_websocket(websocket, code=1008, reason=reason)
        return

    await websocket.accept()
    try:
        await vnc_session_manager.attach_subscriber(
            session_id, user_id=user.id, websocket=websocket
        )
    except AppError as exc:
        await safe_close_websocket(websocket, code=1008, reason=exc.message)
    except WebSocketDisconnect:
        # 客戶端斷線（含握手途中）屬正常結束
        pass
    except TimeoutError:
        # RFB 握手逾時：不是伺服器故障，名額已在 attach_subscriber 釋放
        logger.info(f"Classroom watch handshake timed out for session {session_id}")
        await safe_close_websocket(websocket, code=1008, reason="Handshake timeout")
    except Exception:
        logger.exception(f"Classroom watch failed for session {session_id}")
        await safe_close_websocket(websocket, code=1011, reason="Internal server error")
    finally:
        await safe_close_websocket(websocket, code=1000, reason="Session ended")
