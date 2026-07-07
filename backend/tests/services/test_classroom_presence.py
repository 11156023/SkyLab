"""教室信令 hub 與 vnc_proxy 輸入攔截（filter_client_bytes）測試。"""

import asyncio
import struct
import time
import uuid
from typing import Any

import pytest

from app.infrastructure.vnc.messages import (
    ClientMessageSplitter,
    RfbStreamError,
    filter_client_bytes,
)
from app.services.classroom.presence import ClassroomPresenceHub

U1 = uuid.uuid4()
U2 = uuid.uuid4()
G1 = uuid.uuid4()
G2 = uuid.uuid4()

KEY_EVENT = struct.pack(">BBxxI", 4, 1, 0x41)
POINTER_EVENT = struct.pack(">BBHH", 5, 1, 10, 20)
CUT_TEXT = struct.pack(">BxxxI", 6, 2) + b"hi"
FBUR = struct.pack(">BBHHHH", 3, 1, 0, 0, 800, 600)
SET_ENCODINGS = struct.pack(">BxH", 2, 1) + struct.pack(">i", 0)
# QEMU Client Message (255) / Extended Key Event (submessage 0)：共 12 bytes
QEMU_EXT_KEY = struct.pack(">BBHII", 255, 0, 1, 0x41, 30)


async def eventually(predicate: Any, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within timeout")


# ---------------------------------------------------------------------------
# filter_client_bytes（vnc_proxy 接管攔截核心）
# ---------------------------------------------------------------------------


class TestFilterClientBytes:
    def test_blocked_drops_input_keeps_fbur(self) -> None:
        splitter = ClientMessageSplitter()
        out = filter_client_bytes(
            splitter, KEY_EVENT + FBUR + POINTER_EVENT + CUT_TEXT, blocked=True
        )
        assert out == [FBUR]

    def test_blocked_drops_qemu_ext_key(self) -> None:
        splitter = ClientMessageSplitter()
        out = filter_client_bytes(splitter, QEMU_EXT_KEY + FBUR, blocked=True)
        assert out == [FBUR]

    def test_unblocked_passes_everything(self) -> None:
        splitter = ClientMessageSplitter()
        stream = KEY_EVENT + FBUR + QEMU_EXT_KEY + SET_ENCODINGS
        out = filter_client_bytes(splitter, stream, blocked=False)
        assert out == [KEY_EVENT, FBUR, QEMU_EXT_KEY, SET_ENCODINGS]

    def test_boundary_sync_maintained_across_toggle(self) -> None:
        """未攔截時 splitter 也持續 feed，之後開啟攔截仍在正確邊界。"""
        splitter = ClientMessageSplitter()
        # 先餵半個 KeyEvent（未攔截）
        assert filter_client_bytes(splitter, KEY_EVENT[:5], blocked=False) == []
        # 補完 + 一個 FBUR（此時已被攔截）
        out = filter_client_bytes(splitter, KEY_EVENT[5:] + FBUR, blocked=True)
        assert out == [FBUR]

    def test_unknown_type_raises_and_pending_keeps_remainder(self) -> None:
        splitter = ClientMessageSplitter()
        bad = b"\x63" + b"junk"
        with pytest.raises(RfbStreamError):
            filter_client_bytes(splitter, bad, blocked=False)
        # fail-open：pending 保留未消化的 bytes 供原樣轉發
        assert splitter.pending == bad

    def test_qemu_unknown_submessage_raises(self) -> None:
        splitter = ClientMessageSplitter()
        with pytest.raises(RfbStreamError):
            splitter.feed(b"\xff\x01" + b"\x00" * 10)


# ---------------------------------------------------------------------------
# ClassroomPresenceHub
# ---------------------------------------------------------------------------


class FakePresenceWs:
    def __init__(self, *, broken_send: bool = False) -> None:
        self.events: list[dict[str, Any]] = []
        self.broken_send = broken_send
        self._incoming: asyncio.Queue[str | None] = asyncio.Queue()

    async def receive_text(self) -> str:
        item = await self._incoming.get()
        if item is None:
            raise RuntimeError("client disconnected")
        return item

    async def send_json(self, data: dict[str, Any]) -> None:
        if self.broken_send:
            raise RuntimeError("dead connection")
        self.events.append(data)

    def disconnect(self) -> None:
        self._incoming.put_nowait(None)


async def _register(
    hub: ClassroomPresenceHub,
    user_id: uuid.UUID,
    group_ids: set[uuid.UUID],
    *,
    broken_send: bool = False,
) -> tuple[FakePresenceWs, "asyncio.Task[None]"]:
    ws = FakePresenceWs(broken_send=broken_send)
    task = asyncio.create_task(
        hub.register(user_id=user_id, group_ids=group_ids, websocket=ws)
    )
    await eventually(lambda: user_id in hub.online_user_ids(next(iter(group_ids))))
    return ws, task


class TestClassroomPresenceHub:
    async def test_online_and_disconnect_cleanup(self) -> None:
        hub = ClassroomPresenceHub()
        ws1, t1 = await _register(hub, U1, {G1})
        ws2, t2 = await _register(hub, U2, {G1, G2})
        assert hub.online_user_ids(G1) == {U1, U2}
        assert hub.online_user_ids(G2) == {U2}

        ws1.disconnect()
        await eventually(lambda: hub.online_user_ids(G1) == {U2})
        ws2.disconnect()
        await eventually(lambda: hub.online_user_ids(G1) == set())
        await eventually(t1.done)
        await eventually(t2.done)

    async def test_broadcast_only_to_group(self) -> None:
        hub = ClassroomPresenceHub()
        ws1, t1 = await _register(hub, U1, {G1})
        ws2, t2 = await _register(hub, U2, {G2})
        event = {"type": "live_started", "session_id": "s1"}
        await hub.broadcast_to_group(G1, event)
        assert ws1.events == [event]
        assert ws2.events == []
        ws1.disconnect()
        ws2.disconnect()
        await eventually(t1.done)
        await eventually(t2.done)

    async def test_send_to_user(self) -> None:
        hub = ClassroomPresenceHub()
        ws1, t1 = await _register(hub, U1, {G1})
        ws1b, t1b = await _register(hub, U1, {G1})  # 同一使用者兩個分頁
        ws2, t2 = await _register(hub, U2, {G1})
        event = {"type": "takeover_started"}
        await hub.send_to_user(U1, event)
        assert ws1.events == [event]
        assert ws1b.events == [event]
        assert ws2.events == []
        for ws in (ws1, ws1b, ws2):
            ws.disconnect()
        for task in (t1, t1b, t2):
            await eventually(task.done)

    async def test_dead_connection_cleaned_on_broadcast(self) -> None:
        hub = ClassroomPresenceHub()
        ws1, t1 = await _register(hub, U1, {G1}, broken_send=True)
        ws2, t2 = await _register(hub, U2, {G1})
        await hub.broadcast_to_group(G1, {"type": "live_started"})
        # 死連線送不出去 → 自動移出線上名單；健康連線照收
        assert hub.online_user_ids(G1) == {U2}
        assert ws2.events == [{"type": "live_started"}]
        ws1.disconnect()
        ws2.disconnect()
        await eventually(t1.done)
        await eventually(t2.done)
