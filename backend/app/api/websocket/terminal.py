import asyncio
import logging

import websockets
from fastapi import WebSocket

from app.api.deps.auth import get_ws_current_user
from app.api.websocket.utils import (
    pump_client_to_upstream,
    pump_upstream_to_client,
    run_until_first_done,
    safe_close_websocket,
)
from app.exceptions import NotFoundError, ProxmoxError
from app.infrastructure.proxmox import (
    get_connection_id_for_node,
    get_proxmox_settings,
    open_vncwebsocket,
)
from app.services.proxmox import proxmox_service
from app.services.resource.access import require_resource_use

logger = logging.getLogger(__name__)


async def terminal_proxy(websocket: WebSocket, vmid: int, token: str):
    """WebSocket proxy for LXC container terminal access."""
    # Authenticate user and check ownership before accepting
    user, session = await get_ws_current_user(websocket, token=token)
    try:
        # 同步 DB 查詢丟到 worker thread，連線池耗盡時才不會凍住 event loop
        await asyncio.to_thread(
            require_resource_use, session=session, user=user, vmid=vmid
        )
    except Exception:
        await safe_close_websocket(websocket, code=1008, reason="Permission denied")
        return
    finally:
        # 權限檢查之後不再需要 DB；立刻關閉，避免整個終端機生命週期
        # 佔住一條 idle-in-transaction 連線（比照 classroom.py）。
        session.close()

    await websocket.accept()
    logger.info(f"Terminal proxy connection for LXC {vmid} by user {user.email}")

    pve_websocket = None

    try:
        # Find LXC container in cluster resources（先取得 node，認證與連線
        # 都要跟著該節點所屬的連線走）
        try:
            container_info = await asyncio.to_thread(proxmox_service.find_lxc, vmid)
        except NotFoundError:
            logger.error(f"LXC container {vmid} not found in cluster")
            await safe_close_websocket(websocket, code=1008, reason="LXC container not found")
            return

        node = container_info["node"]

        # Get session ticket (password-based, required for PVE WebSocket)
        try:
            pve_auth_cookie, _ = await proxmox_service.get_session_ticket(node)
        except ProxmoxError:
            logger.error("Proxmox session authentication failed")
            await safe_close_websocket(websocket, code=1008, reason="Authentication failed")
            return

        logger.info("Retrieved session ticket for WebSocket authentication")
        logger.info(
            f"LXC container {vmid} found on node {node}, status: {container_info.get('status', 'unknown')}"
        )

        # Get terminal proxy ticket
        console_data = await asyncio.to_thread(
            proxmox_service.get_terminal_ticket,
            node,
            vmid,
        )
        terminal_port = console_data["port"]
        terminal_ticket = console_data["ticket"]

        # termproxy 的認證訊息要用節點所屬連線的 PVE 帳號
        _cfg = get_proxmox_settings(get_connection_id_for_node(node))

        logger.debug(f"Connecting to Proxmox terminal WebSocket for LXC {vmid} on {node}")
        try:
            pve_websocket = await open_vncwebsocket(
                node, "lxc", vmid, terminal_port, terminal_ticket, pve_auth_cookie
            )
            logger.info("Successfully connected to Proxmox WebSocket for terminal")

            # Send initial authentication to termproxy
            # Format: username:ticket\n (newline is critical!)
            auth_message = f"{_cfg.user}:{terminal_ticket}\n"
            await pve_websocket.send(auth_message)
            logger.info("Sent authentication to termproxy")

        except websockets.exceptions.InvalidStatus as e:
            logger.error(
                f"Proxmox WebSocket rejected: HTTP {e.response.status_code} — {e.response.headers}"
            )
            await safe_close_websocket(websocket, code=1008, reason="Proxmox connection failed")
            return
        except Exception as e:
            logger.error(f"Proxmox WebSocket connection failed ({type(e).__name__}): {e}")
            await safe_close_websocket(websocket, code=1008, reason="Proxmox connection failed")
            return

        logger.info(f"WebSocket proxy established for LXC {vmid}")

        disconnect = asyncio.Event()
        await run_until_first_done(
            pump_upstream_to_client(pve_websocket, websocket, disconnect),
            pump_client_to_upstream(websocket, pve_websocket, disconnect),
        )

    except Exception as e:
        logger.error(f"Failed to establish WebSocket proxy: {e}", exc_info=True)
        await safe_close_websocket(websocket, code=1011, reason="Internal server error")
    finally:
        if pve_websocket:
            await pve_websocket.close()
        logger.info(f"Terminal proxy disconnected for LXC {vmid}")
