"""連到 PVE 的 vncwebsocket（VM 主控台、LXC 終端機、教室 fan-out 共用）。"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.typing import Subprotocol

from .client import get_connection_id_for_node, get_host_for_node
from .settings import get_proxmox_settings
from .tls import build_ws_ssl_context


async def open_vncwebsocket(
    node: str,
    kind: Literal["qemu", "lxc"],
    vmid: int,
    port: int | str,
    ticket: str,
    pve_auth_cookie: str,
    *,
    open_timeout: float | None = None,
) -> ClientConnection:
    """開一條到節點所屬連線 active host 的 vncwebsocket。

    一律走該節點所屬連線的 active host，多連線與 HA 切換後才會連到正確入口。
    ``open_timeout`` 為 None 時沿用 websockets 預設（10 秒），不可把 None 傳下去，
    那會變成無限等待。
    """
    cfg = get_proxmox_settings(get_connection_id_for_node(node))
    host = get_host_for_node(node)
    url = (
        f"wss://{host}:{cfg.port}"
        f"/api2/json/nodes/{node}/{kind}/{vmid}/vncwebsocket"
        f"?port={port}&vncticket={quote(ticket, safe='')}"
    )
    kwargs: dict[str, Any] = {} if open_timeout is None else {"open_timeout": open_timeout}
    # Cookie header must NOT be URL-encoded; Proxmox rejects percent-encoded cookies.
    # Proxmox vncwebsocket requires Sec-WebSocket-Protocol: binary (same as noVNC client).
    # proxy=None: disable system proxy — Proxmox is on a private network and
    # going through a proxy (websockets 16 default: proxy=True) breaks the connection.
    return await websockets.connect(
        url,
        ssl=build_ws_ssl_context(cfg),
        additional_headers={"Cookie": f"PVEAuthCookie={pve_auth_cookie}"},
        subprotocols=[Subprotocol("binary")],
        max_size=2**20,
        proxy=None,
        **kwargs,
    )
