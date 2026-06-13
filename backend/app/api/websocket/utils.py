from fastapi import WebSocket
from starlette.websockets import WebSocketState


async def safe_close_websocket(
    websocket: WebSocket,
    *,
    code: int,
    reason: str = "",
) -> None:
    """Close a WebSocket without raising if it is already closed/closing."""
    if websocket.application_state == WebSocketState.DISCONNECTED:
        return
    try:
        await websocket.close(code=code, reason=reason)
    except RuntimeError as exc:
        if "close message has been sent" not in str(exc):
            raise
    except AttributeError as exc:
        # uvicorn + websockets can raise this while closing a failed handshake.
        if "transfer_data_task" not in str(exc):
            raise
