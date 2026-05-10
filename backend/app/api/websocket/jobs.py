"""WebSocket: /ws/jobs

每 N 秒推送一份「該使用者可見」的 jobs 快照給已連線的客戶端。
- 採用伺服器端輪詢 DB → 推送，避免改造各個 mutation 點。
- 透過 query string token 認證。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect

from app.api.deps.auth import get_ws_current_user
from app.services.jobs import jobs_service

logger = logging.getLogger(__name__)


_SNAPSHOT_INTERVAL_SECONDS = 3.0


async def jobs_ws_proxy(websocket: WebSocket, token: str) -> None:
    user, session = await get_ws_current_user(websocket, token=token)
    await websocket.accept()
    logger.debug("Jobs WS connected: user=%s", user.email)

    last_payload: str | None = None

    try:
        while True:
            try:
                snapshot = await asyncio.to_thread(
                    jobs_service.list_recent_for_user,
                    session=session,
                    user=user,
                    limit=20,
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
                # 利用 wait_for 同時偵測 client 主動 close
                await asyncio.wait_for(
                    websocket.receive_text(), timeout=_SNAPSHOT_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        logger.debug("Jobs WS disconnected: user=%s", user.email)
    except Exception:
        logger.exception("Jobs WS error: user=%s", user.email)
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
