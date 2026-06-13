import asyncio
import logging
from urllib.parse import quote

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from app.api.deps.auth import get_ws_current_user
from app.api.deps.proxmox import check_resource_ownership
from app.api.websocket.utils import safe_close_websocket
from app.exceptions import NotFoundError, ProxmoxError
from app.infrastructure.proxmox import (
    build_ws_ssl_context,
    get_active_host,
    get_proxmox_settings,
)
from app.services.proxmox import proxmox_service

logger = logging.getLogger(__name__)


async def terminal_proxy(websocket: WebSocket, vmid: int, token: str):
    """WebSocket proxy for LXC container terminal access."""
    # Authenticate user and check ownership before accepting
    user, session = await get_ws_current_user(websocket, token=token)
    try:
        check_resource_ownership(vmid, user, session)
    except Exception:
        session.close()
        await safe_close_websocket(websocket, code=1008, reason="Permission denied")
        return

    await websocket.accept()
    logger.info(f"Terminal proxy connection for LXC {vmid} by user {user.email}")

    pve_websocket = None

    try:
        # Get session ticket (password-based, required for PVE WebSocket)
        try:
            pve_auth_cookie, _ = await proxmox_service.get_session_ticket()
        except ProxmoxError:
            logger.error("Proxmox session authentication failed")
            await safe_close_websocket(websocket, code=1008, reason="Authentication failed")
            return

        logger.info("Retrieved session ticket for WebSocket authentication")

        # Find LXC container in cluster resources
        try:
            container_info = await asyncio.to_thread(proxmox_service.find_lxc, vmid)
        except NotFoundError:
            logger.error(f"LXC container {vmid} not found in cluster")
            await safe_close_websocket(websocket, code=1008, reason="LXC container not found")
            return

        node = container_info["node"]
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

        encoded_terminal_ticket = quote(terminal_ticket, safe="")

        # WebSocket URL for terminal — 使用 get_active_host() 確保 HA 切換後跟著用正確的節點
        _cfg = get_proxmox_settings()
        active_host = get_active_host()
        pve_ws_url = (
            f"wss://{active_host}:8006"
            f"/api2/json/nodes/{node}/lxc/{vmid}/vncwebsocket"
            f"?port={terminal_port}&vncticket={encoded_terminal_ticket}"
        )

        ssl_context = build_ws_ssl_context(_cfg)

        logger.debug(f"Connecting to Proxmox terminal WebSocket: {pve_ws_url}")
        try:
            # Cookie header must NOT be URL-encoded; Proxmox rejects percent-encoded cookies.
            # Proxmox vncwebsocket requires Sec-WebSocket-Protocol: binary (same as noVNC client).
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
                # PVE 端正常關閉連線
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
                # 客戶端斷線屬正常結束
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
                # 任務取消屬預期行為
                pass

    except Exception as e:
        logger.error(f"Failed to establish WebSocket proxy: {e}", exc_info=True)
        await safe_close_websocket(websocket, code=1011, reason="Internal server error")
    finally:
        if pve_websocket:
            await pve_websocket.close()
        session.close()
        logger.info(f"Terminal proxy disconnected for LXC {vmid}")
