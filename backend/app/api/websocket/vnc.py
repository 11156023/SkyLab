import asyncio
import logging
from time import monotonic
from urllib.parse import quote  # used for vncticket query param only

import websockets
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.api.deps.auth import get_ws_current_user
from app.api.deps.proxmox import check_resource_ownership
from app.exceptions import NotFoundError, ProxmoxError
from app.infrastructure.proxmox import (
    build_ws_ssl_context,
    get_active_host,
    get_proxmox_settings,
)
from app.services.proxmox import proxmox_service

logger = logging.getLogger(__name__)
_VNC_SESSION_CACHE_TTL_SECONDS = 90.0
_vnc_session_cookies: dict[tuple[int, str], tuple[str, float]] = {}


def register_vnc_session_cookie(vmid: int, vnc_ticket: str, pve_auth_cookie: str) -> None:
    _purge_expired_vnc_session_cookies()
    _vnc_session_cookies[(int(vmid), str(vnc_ticket))] = (
        pve_auth_cookie,
        monotonic() + _VNC_SESSION_CACHE_TTL_SECONDS,
    )


def _get_cached_vnc_session_cookie(vmid: int, vnc_ticket: str) -> str | None:
    _purge_expired_vnc_session_cookies()
    item = _vnc_session_cookies.get((int(vmid), str(vnc_ticket)))
    return item[0] if item else None


def _purge_expired_vnc_session_cookies() -> None:
    now = monotonic()
    for key, (_cookie, expires_at) in list(_vnc_session_cookies.items()):
        if expires_at <= now:
            _vnc_session_cookies.pop(key, None)


async def _safe_close_websocket(
    websocket: WebSocket,
    *,
    code: int,
    reason: str = "",
) -> None:
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


async def vnc_proxy(
    websocket: WebSocket,
    vmid: int,
    token: str,
    vnc_ticket: str = "",
    vnc_port: str = "",
):
    """WebSocket proxy for VM VNC console access.

    When *vnc_ticket* and *vnc_port* are supplied (from the REST
    ``/console`` endpoint) the proxy re-uses them so that the noVNC
    client can authenticate with the **same** ticket it already has.
    Otherwise the proxy creates a fresh ticket (fallback).
    """
    # Authenticate user and check ownership before accepting
    user, session = await get_ws_current_user(websocket, token=token)
    try:
        check_resource_ownership(vmid, user, session)
    except Exception:
        session.close()
        await _safe_close_websocket(websocket, code=1008, reason="Permission denied")
        return

    await websocket.accept()
    logger.info(f"VNC proxy connection for VM {vmid} by user {user.email}")

    pve_websocket = None

    try:
        pve_auth_cookie = _get_cached_vnc_session_cookie(vmid, vnc_ticket) if vnc_ticket else None
        if pve_auth_cookie is None:
            try:
                pve_auth_cookie, _ = await proxmox_service.get_session_ticket()
            except ProxmoxError:
                logger.error("Proxmox session authentication failed")
                await _safe_close_websocket(websocket, code=1008, reason="Authentication failed")
                return

        # Find VM in cluster resources
        try:
            vm_info = await asyncio.to_thread(proxmox_service.find_resource, vmid)
        except NotFoundError:
            logger.error(f"VM {vmid} not found in cluster")
            await _safe_close_websocket(websocket, code=1008, reason="VM not found")
            return

        node = vm_info["node"]

        # Re-use the ticket/port from the REST endpoint when available,
        # so the noVNC client authenticates with the same ticket.
        if not (vnc_ticket and vnc_port):
            csrf_token = ""
            try:
                pve_auth_cookie, csrf_token = await proxmox_service.get_session_ticket()
            except ProxmoxError:
                logger.error("Proxmox session authentication failed")
                await _safe_close_websocket(websocket, code=1008, reason="Authentication failed")
                return
            console_data = await proxmox_service.get_vnc_ticket_with_session(
                node,
                vmid,
                pve_auth_cookie,
                csrf_token,
            )
            vnc_port = console_data["port"]
            vnc_ticket = console_data["ticket"]

        encoded_vnc_ticket = quote(vnc_ticket, safe="")

        # WebSocket URL for VNC — 使用 get_active_host() 確保 HA 切換後跟著用正確的節點
        _cfg = get_proxmox_settings()
        active_host = get_active_host()
        pve_ws_url = (
            f"wss://{active_host}:8006"
            f"/api2/json/nodes/{node}/qemu/{vmid}/vncwebsocket"
            f"?port={vnc_port}&vncticket={encoded_vnc_ticket}"
        )

        ssl_context = build_ws_ssl_context(_cfg)

        try:
            # Cookie header must NOT be URL-encoded; Proxmox rejects percent-encoded cookies.
            # Proxmox vncwebsocket requires Sec-WebSocket-Protocol: binary.
            # proxy=None: disable system proxy — Proxmox is on a private network and
            # going through a proxy (websockets 16 default: proxy=True) breaks the connection.
            pve_websocket = await websockets.connect(
                pve_ws_url,
                ssl=ssl_context,
                additional_headers={"Cookie": f"PVEAuthCookie={pve_auth_cookie}"},
                subprotocols=["binary"],
                max_size=2**20,
                proxy=None,
            )
        except websockets.exceptions.InvalidStatus as e:
            logger.error(
                f"Proxmox WebSocket rejected: HTTP {e.response.status_code}"
            )
            await _safe_close_websocket(websocket, code=1008, reason="Proxmox connection failed")
            return
        except Exception as e:
            logger.error(f"Proxmox WebSocket connection failed ({type(e).__name__}): {e}")
            await _safe_close_websocket(websocket, code=1008, reason="Proxmox connection failed")
            return

        logger.info(f"WebSocket proxy established for VM {vmid}")

        disconnect = asyncio.Event()

        async def forward_from_proxmox():
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
                pass
            except Exception as e:
                logger.error(f"Error forwarding from Proxmox: {e}")
            finally:
                disconnect.set()

        async def forward_to_proxmox():
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
                pass
            except Exception as e:
                logger.error(f"Error forwarding to Proxmox: {e}")
            finally:
                disconnect.set()

        # Run both directions; cancel the other when one finishes
        tasks = [
            asyncio.create_task(forward_from_proxmox()),
            asyncio.create_task(forward_to_proxmox()),
        ]
        _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logger.error(f"Failed to establish WebSocket proxy: {e}", exc_info=True)
        await _safe_close_websocket(websocket, code=1011, reason="Internal server error")
    finally:
        if pve_websocket:
            await pve_websocket.close()
        session.close()
        logger.info(f"VNC proxy disconnected for VM {vmid}")
