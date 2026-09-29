"""open_vncwebsocket：VM 主控台、LXC 終端機與教室 fan-out 共用的 PVE 連線。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.infrastructure.proxmox import vnc_websocket


@pytest.fixture
def connect_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    cfg = SimpleNamespace(port=8006)

    async def fake_connect(url: str, **kwargs: Any) -> str:
        calls.append({"url": url, **kwargs})
        return "ws"

    monkeypatch.setattr(vnc_websocket.websockets, "connect", fake_connect)
    monkeypatch.setattr(vnc_websocket, "get_connection_id_for_node", lambda node: 7)
    monkeypatch.setattr(
        vnc_websocket, "get_proxmox_settings", lambda connection_id: cfg
    )
    monkeypatch.setattr(vnc_websocket, "get_host_for_node", lambda node: "pve-b.lan")
    monkeypatch.setattr(vnc_websocket, "build_ws_ssl_context", lambda c: "ssl-ctx")
    return calls


async def test_builds_node_scoped_url_and_raw_cookie(
    connect_calls: list[dict[str, Any]],
) -> None:
    ws = await vnc_websocket.open_vncwebsocket(
        "node-b", "lxc", 123, 5901, "PVEVNC:a/b+c=", "PVE:root@pam:abc=="
    )

    assert ws == "ws"
    (call,) = connect_calls
    assert call["url"] == (
        "wss://pve-b.lan:8006/api2/json/nodes/node-b/lxc/123/vncwebsocket"
        "?port=5901&vncticket=PVEVNC%3Aa%2Fb%2Bc%3D"
    )
    # Cookie 不可 URL 編碼，PVE 會拒絕
    assert call["additional_headers"] == {"Cookie": "PVEAuthCookie=PVE:root@pam:abc=="}
    assert call["subprotocols"] == ["binary"]
    assert call["proxy"] is None
    assert call["ssl"] == "ssl-ctx"


async def test_open_timeout_is_only_passed_when_given(
    connect_calls: list[dict[str, Any]],
) -> None:
    await vnc_websocket.open_vncwebsocket("n", "qemu", 1, 5900, "t", "c")
    await vnc_websocket.open_vncwebsocket(
        "n", "qemu", 1, 5900, "t", "c", open_timeout=3.5
    )

    # 傳 None 會關掉 websockets 預設的 10 秒逾時，所以沒給時完全不帶這個參數
    assert "open_timeout" not in connect_calls[0]
    assert connect_calls[1]["open_timeout"] == 3.5
