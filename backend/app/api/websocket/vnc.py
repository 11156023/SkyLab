import asyncio
import logging
from time import monotonic

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from app.api.deps.auth import get_ws_current_user
from app.api.websocket.utils import (
    pump_upstream_to_client,
    run_until_first_done,
)
from app.api.websocket.utils import safe_close_websocket as _safe_close_websocket
from app.exceptions import NotFoundError, ProxmoxError
from app.infrastructure.proxmox import open_vncwebsocket
from app.infrastructure.vnc.messages import (
    ClientMessageSplitter,
    RfbStreamError,
    filter_client_bytes,
)
from app.services.classroom.vnc_session_manager import vnc_session_manager
from app.services.proxmox import proxmox_service
from app.services.resource.access import require_resource_use

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
        # 同步 DB 查詢丟到 worker thread，連線池耗盡時才不會凍住 event loop
        await asyncio.to_thread(
            require_resource_use, session=session, user=user, vmid=vmid
        )
    except Exception:
        await _safe_close_websocket(websocket, code=1008, reason="Permission denied")
        return
    finally:
        # 權限檢查之後不再需要 DB；立刻關閉，避免整個主控台生命週期
        # 佔住一條 idle-in-transaction 連線（比照 classroom.py）。
        session.close()

    await websocket.accept()
    logger.info(f"VNC proxy connection for VM {vmid} by user {user.email}")

    pve_websocket = None

    try:
        # Find VM in cluster resources（先取得 node，後續認證與連線都要跟著
        # 該節點所屬的連線走）
        try:
            vm_info = await asyncio.to_thread(proxmox_service.find_resource, vmid)
        except NotFoundError:
            logger.error(f"VM {vmid} not found in cluster")
            await _safe_close_websocket(websocket, code=1008, reason="VM not found")
            return

        node = vm_info["node"]

        # Re-use the ticket/port from the REST endpoint when available,
        # so the noVNC client authenticates with the same ticket. Either path
        # authenticates to PVE at most once.
        if vnc_ticket and vnc_port:
            pve_auth_cookie = _get_cached_vnc_session_cookie(vmid, vnc_ticket)
            if pve_auth_cookie is None:
                try:
                    pve_auth_cookie, _ = await proxmox_service.get_session_ticket(node)
                except ProxmoxError:
                    logger.error("Proxmox session authentication failed")
                    await _safe_close_websocket(websocket, code=1008, reason="Authentication failed")
                    return
        else:
            try:
                pve_auth_cookie, csrf_token = await proxmox_service.get_session_ticket(node)
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

        try:
            pve_websocket = await open_vncwebsocket(
                node, "qemu", vmid, vnc_port, vnc_ticket, pve_auth_cookie
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

        async def forward_to_proxmox():
            # 教室接管攔截：splitter 持續切框以維持訊息邊界同步；
            # 失去同步（未知訊息型別）時 fail-open 改為原樣轉發。
            input_splitter = ClientMessageSplitter()
            filter_passthrough = False
            try:
                while not disconnect.is_set():
                    data = await websocket.receive()
                    if data.get("type") == "websocket.disconnect":
                        break
                    if disconnect.is_set():
                        break
                    if "bytes" in data:
                        if filter_passthrough:
                            await pve_websocket.send(data["bytes"])
                            continue
                        blocked = vnc_session_manager.is_input_blocked(vmid)
                        try:
                            messages = filter_client_bytes(
                                input_splitter, data["bytes"], blocked=blocked
                            )
                        except RfbStreamError as exc:
                            logger.warning(
                                f"VM {vmid} console input filter lost sync "
                                f"({exc}); falling back to passthrough"
                            )
                            filter_passthrough = True
                            remainder = input_splitter.pending
                            if remainder:
                                await pve_websocket.send(remainder)
                            continue
                        for message in messages:
                            await pve_websocket.send(message)
                    elif "text" in data:
                        await pve_websocket.send(data["text"])
            except WebSocketDisconnect:
                # 客戶端斷線屬正常結束
                pass
            except Exception as e:
                logger.error(f"Error forwarding to Proxmox: {e}")
            finally:
                disconnect.set()

        await run_until_first_done(
            pump_upstream_to_client(pve_websocket, websocket, disconnect),
            forward_to_proxmox(),
        )

    except Exception as e:
        logger.error(f"Failed to establish WebSocket proxy: {e}", exc_info=True)
        await _safe_close_websocket(websocket, code=1011, reason="Internal server error")
    finally:
        if pve_websocket:
            await pve_websocket.close()
        logger.info(f"VNC proxy disconnected for VM {vmid}")
