"""WebSocket: /ws/jobs

每 N 秒推送一份「該使用者可見」的 jobs 快照給已連線的客戶端。
- 採用伺服器端輪詢 DB → 推送，避免改造各個 mutation 點。
- 透過 query string token 認證。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect
from sqlmodel import Session

from app.api.deps.auth import get_ws_current_user
from app.models import User
from app.schemas.jobs import JobsListResponse
from app.services.jobs import jobs_service

logger = logging.getLogger(__name__)


_SNAPSHOT_INTERVAL_SECONDS = 3.0


def _fetch_snapshot(session: Session, user: User, limit: int) -> JobsListResponse:
    # 長連線重用同一個 session：每輪先 expire identity map，否則已載入的
    # job 物件屬性不會被新查詢覆寫，狀態會永遠停在第一次查到的值；
    # 查完 rollback 結束交易，避免整個 WS 生命週期佔住 idle-in-transaction 連線。
    session.expire_all()
    try:
        return jobs_service.list_recent_for_user(session=session, user=user, limit=limit)
    finally:
        session.rollback()


async def jobs_ws_proxy(websocket: WebSocket, token: str) -> None:
    user, session = await get_ws_current_user(websocket, token=token)
    await websocket.accept()
    user_email = user.email  # eagerly load before entering the loop
    logger.debug("Jobs WS connected: user=%s", user_email)

    last_payload: str | None = None

    try:
        while True:
            try:
                snapshot = await asyncio.to_thread(
                    _fetch_snapshot,
                    session,
                    user,
                    20,
                )
            except Exception:  # noqa: BLE001 — 單次失敗不應斷線
                logger.exception("Jobs WS snapshot fetch failed")
                await asyncio.sleep(_SNAPSHOT_INTERVAL_SECONDS)
                continue

            payload = snapshot.model_dump_json()
            if payload != last_payload:
                await websocket.send_text(payload)
                last_payload = payload

            try:
                # 利用 wait_for 同時偵測 client 主動 close。
                # 用 receive() 而非 receive_text()：收到 binary frame 時
                # receive_text() 會拋例外導致整條連線被收掉。
                message = await asyncio.wait_for(
                    websocket.receive(), timeout=_SNAPSHOT_INTERVAL_SECONDS
                )
                if message.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect(message.get("code") or 1000)
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        logger.debug("Jobs WS disconnected: user=%s", user_email)
    except Exception:
        logger.exception("Jobs WS error: user=%s", user_email)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        try:
            session.close()
        except Exception:
            pass


__all__ = ["jobs_ws_proxy"]
