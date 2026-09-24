import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

import websockets
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)


async def pump_upstream_to_client(
    pve_websocket: Any, websocket: WebSocket, disconnect: asyncio.Event
) -> None:
    """把 Proxmox 端訊息原樣轉給瀏覽器；任一端結束就設 disconnect。"""
    try:
        async for message in pve_websocket:
            if disconnect.is_set():
                break
            try:
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)
            except Exception:
                break
    except websockets.exceptions.ConnectionClosed:
        # PVE 端正常關閉連線
        pass
    except Exception as e:
        logger.error(f"Error forwarding from Proxmox: {e}")
    finally:
        disconnect.set()


async def pump_client_to_upstream(
    websocket: WebSocket, pve_websocket: Any, disconnect: asyncio.Event
) -> None:
    """把瀏覽器端訊息原樣轉給 Proxmox（VNC 另有輸入攔截版本）。"""
    try:
        while not disconnect.is_set():
            data = await websocket.receive()
            if data.get("type") == "websocket.disconnect":
                break
            if disconnect.is_set():
                break
            if "bytes" in data:
                await pve_websocket.send(data["bytes"])
            elif "text" in data:
                await pve_websocket.send(data["text"])
    except WebSocketDisconnect:
        # 客戶端斷線屬正常結束
        pass
    except Exception as e:
        logger.error(f"Error forwarding to Proxmox: {e}")
    finally:
        disconnect.set()


async def run_until_first_done(*coroutines: Coroutine[Any, Any, None]) -> None:
    """同時跑雙向轉發；任一方向結束就取消其餘（取消屬預期行為）。"""
    tasks = [asyncio.create_task(coro) for coro in coroutines]
    _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def safe_close_websocket(
    websocket: WebSocket,
    *,
    code: int,
    reason: str = "",
) -> None:
    """Close a WebSocket without raising if it is already closed/closing."""
    if (
        websocket.application_state == WebSocketState.DISCONNECTED
        or websocket.client_state == WebSocketState.DISCONNECTED
    ):
        return
    try:
        await websocket.close(code=code, reason=reason)
    except RuntimeError as exc:
        # starlette: 'Cannot call "send" once a close message has been sent.'
        # uvicorn: "Unexpected ASGI message 'websocket.close', after sending
        #          'websocket.close' or response already completed."
        message = str(exc)
        if (
            "close message has been sent" not in message
            and "Unexpected ASGI message 'websocket.close'" not in message
        ):
            raise
    except AttributeError as exc:
        # uvicorn + websockets can raise this while closing a failed handshake.
        if "transfer_data_task" not in str(exc):
            raise
