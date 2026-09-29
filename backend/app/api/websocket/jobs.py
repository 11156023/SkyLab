"""WebSocket: /ws/jobs

每 N 秒推送一份「該使用者可見」的 jobs 快照給已連線的客戶端。
- 採用伺服器端輪詢 DB → 推送，避免改造各個 mutation 點。
- 透過 query string token 認證。
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import WebSocket, WebSocketDisconnect
from sqlmodel import Session

from app.api.deps.auth import get_ws_current_user
from app.models import User
from app.schemas.jobs import JobsListResponse
from app.services.course import reminder_service
from app.services.jobs import jobs_service

logger = logging.getLogger(__name__)


_SNAPSHOT_INTERVAL_SECONDS = 3.0
# 提醒（資源期限／審核結果／課堂任務）變化以天、小時計，
# 不必跟 jobs 一樣每輪重算：每 N 輪查一次，其餘輪沿用快取。
_REMINDER_REFRESH_ROUNDS = 10
# 連續查詢失敗這麼多輪（約 1 分鐘）就放棄這條連線，交給前端重連
_MAX_CONSECUTIVE_FAILURES = 20


def _load_authorized_user(
    session: Session, user_id: uuid.UUID, token_version: int
) -> User | None:
    """每輪重新讀使用者：被刪除、停用、token 被撤銷（改密碼／重設 2FA）
    或被要求強制 2FA 卻尚未綁定時回 None，呼叫端應以 1008 關閉連線。

    不能直接沿用連線時的 ``user`` 物件：``expire_all`` 之後重新載入一個已被
    刪除的列會每輪拋 ObjectDeletedError。
    """
    session.expire_all()
    try:
        user = session.get(User, user_id)
        if (
            user is None
            or not user.is_active
            or user.token_version != token_version
            or (user.totp_required and not user.totp_enabled)
        ):
            return None
        return user
    finally:
        session.rollback()


def _fetch_snapshot(
    session: Session, user: User, limit: int, *, include_reminders: bool
) -> JobsListResponse:
    # 長連線重用同一個 session：每輪先 expire identity map，否則已載入的
    # job 物件屬性不會被新查詢覆寫，狀態會永遠停在第一次查到的值；
    # 查完 rollback 結束交易，避免整個 WS 生命週期佔住 idle-in-transaction 連線。
    session.expire_all()
    try:
        snapshot = jobs_service.list_recent_for_user(session=session, user=user, limit=limit)
        if include_reminders:
            snapshot.reminders = reminder_service.list_student_reminders(
                session, user_id=user.id
            )
        return snapshot
    finally:
        session.rollback()


def _poll(
    session: Session,
    user_id: uuid.UUID,
    token_version: int,
    *,
    include_reminders: bool,
) -> JobsListResponse | None:
    """一輪輪詢：先確認使用者仍有效（否則回 None），再取快照。"""
    user = _load_authorized_user(session, user_id, token_version)
    if user is None:
        return None
    return _fetch_snapshot(session, user, 20, include_reminders=include_reminders)


async def _wait_for_client(websocket: WebSocket) -> None:
    """等一個輪詢間隔，期間偵測 client 主動 close（收到就拋 WebSocketDisconnect）。

    用 receive() 而非 receive_text()：收到 binary frame 時 receive_text()
    會拋例外導致整條連線被收掉。
    """
    try:
        message = await asyncio.wait_for(
            websocket.receive(), timeout=_SNAPSHOT_INTERVAL_SECONDS
        )
    except asyncio.TimeoutError:
        # 逾時代表沒有新訊息，回到迴圈推送快照
        return
    if message.get("type") == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code") or 1000)


async def jobs_ws_proxy(websocket: WebSocket, token: str) -> None:
    user, session = await get_ws_current_user(websocket, token=token)
    await websocket.accept()
    # eagerly load before entering the loop：之後每輪只用 id 重新查使用者
    user_email = user.email
    user_id = user.id
    token_version = user.token_version
    logger.debug("Jobs WS connected: user=%s", user_email)

    last_payload: str | None = None
    cached_reminders: list | None = None
    round_index = 0
    consecutive_failures = 0

    try:
        while True:
            include_reminders = round_index % _REMINDER_REFRESH_ROUNDS == 0
            round_index += 1
            try:
                snapshot = await asyncio.to_thread(
                    _poll,
                    session,
                    user_id,
                    token_version,
                    include_reminders=include_reminders,
                )
            except Exception:
                consecutive_failures += 1
                logger.exception(
                    "Jobs WS snapshot fetch failed (%d/%d)",
                    consecutive_failures,
                    _MAX_CONSECUTIVE_FAILURES,
                )
                if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    await websocket.close(code=1011)
                    return
                # 失敗時也要偵測 client 斷線，否則迴圈永遠看不到 disconnect
                await _wait_for_client(websocket)
                continue
            consecutive_failures = 0

            if snapshot is None:
                logger.info(
                    "Jobs WS closing: user=%s is no longer authorized", user_email
                )
                await websocket.close(code=1008)
                return

            if include_reminders:
                cached_reminders = snapshot.reminders or []
            else:
                snapshot.reminders = cached_reminders

            payload = snapshot.model_dump_json()
            if payload != last_payload:
                await websocket.send_text(payload)
                last_payload = payload

            await _wait_for_client(websocket)
    except WebSocketDisconnect:
        logger.debug("Jobs WS disconnected: user=%s", user_email)
    except Exception:
        logger.exception("Jobs WS error: user=%s", user_email)
        try:
            await websocket.close(code=1011)
        except Exception:
            # 連線可能已關閉，關閉失敗可忽略
            pass
    finally:
        try:
            session.close()
        except Exception:
            # session 清理失敗可忽略
            pass


__all__ = ["jobs_ws_proxy"]
