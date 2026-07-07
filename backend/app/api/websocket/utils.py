from fastapi import WebSocket
from starlette.websockets import WebSocketState


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
