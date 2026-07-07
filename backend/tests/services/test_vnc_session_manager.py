"""VncSessionManager 測試：fan-out、控制權、佇列滿、上游關閉、session 生命週期。

不連真實 PVE：monkeypatch `VncSessionManager._connect_upstream` 回傳 fake duplex。
"""

import asyncio
import struct
import time
import uuid
from typing import Any

import pytest

from app.exceptions import AppError, ConflictError, NotFoundError
from app.infrastructure.vnc.handshake import (
    PIXEL_FORMAT_32BPP,
    RFB_VERSION,
    ServerInitInfo,
    full_update_request,
)
from app.services.classroom.vnc_session_manager import (
    ClassroomSession,
    SessionMode,
    VncSessionManager,
)

INIT = ServerInitInfo(width=640, height=480, pixel_format=PIXEL_FORMAT_32BPP, name=b"vm")

FULL_FBUR = full_update_request(640, 480, incremental=False)
INCREMENTAL_FBUR = full_update_request(640, 480, incremental=True)

HANDSHAKE_CLIENT_FRAMES = [RFB_VERSION, b"\x01", b"\x01"]

EXPECTED_HANDSHAKE_PREFIX = (
    RFB_VERSION
    + b"\x01\x01"
    + b"\x00\x00\x00\x00"
    + struct.pack(">HH", 640, 480)
    + PIXEL_FORMAT_32BPP
    + struct.pack(">I", 2)
    + b"vm"
)


def _fb_update_raw_1x1(fill: bytes) -> bytes:
    return (
        struct.pack(">BBH", 0, 0, 1)
        + struct.pack(">HHHHi", 0, 0, 1, 1, 0)
        + fill * 4
    )


FB_MSG_A = _fb_update_raw_1x1(b"\xaa")
FB_MSG_B = _fb_update_raw_1x1(b"\xbb")
BELL_MSG = b"\x02"

KEY_EVENT = struct.pack(">BBxxI", 4, 1, 0x41)
POINTER_EVENT = struct.pack(">BBHH", 5, 1, 10, 20)
CLIENT_FBUR = struct.pack(">BBHHHH", 3, 1, 0, 0, 640, 480)


async def eventually(predicate: Any, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within timeout")


class FakeUpstream:
    """模擬已完成握手的 PVE 上游 duplex。"""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed = False
        self._incoming: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def recv(self) -> str | bytes:
        item = await self._incoming.get()
        if item is None:
            raise ConnectionError("upstream closed")
        return item

    async def send(self, data: bytes) -> None:
        self.sent.append(bytes(data))

    async def close(self) -> None:
        self.closed = True
        self._incoming.put_nowait(None)

    # 測試控制面
    def feed_server(self, data: bytes) -> None:
        self._incoming.put_nowait(data)

    def close_from_server(self) -> None:
        self._incoming.put_nowait(None)


class FakeSubscriberWs:
    """模擬已 accept 的 FastAPI WebSocket 訂閱者。"""

    def __init__(self, frames: list[bytes] | None = None) -> None:
        self.sent: list[bytes] = []
        self.closed_code: int | None = None
        self._send_gate = asyncio.Event()
        self._send_gate.set()
        self._incoming: asyncio.Queue[bytes | None] = asyncio.Queue()
        for frame in frames or []:
            self._incoming.put_nowait(frame)

    async def receive_bytes(self) -> bytes:
        item = await self._incoming.get()
        if item is None:
            raise RuntimeError("client disconnected")
        return item

    async def send_bytes(self, data: bytes) -> None:
        await self._send_gate.wait()
        if self.closed_code is not None:
            raise RuntimeError("websocket closed")
        self.sent.append(bytes(data))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_code = code
        self._send_gate.set()
        self._incoming.put_nowait(None)

    # 測試控制面
    def freeze_send(self) -> None:
        self._send_gate.clear()

    def client_send(self, data: bytes) -> None:
        self._incoming.put_nowait(data)

    def disconnect(self) -> None:
        self._incoming.put_nowait(None)

    def payload_after_handshake(self) -> bytes:
        joined = b"".join(self.sent)
        assert joined.startswith(EXPECTED_HANDSHAKE_PREFIX)
        return joined[len(EXPECTED_HANDSHAKE_PREFIX) :]


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream()


@pytest.fixture
def manager(
    upstream: FakeUpstream, monkeypatch: pytest.MonkeyPatch
) -> VncSessionManager:
    async def fake_connect(
        self: VncSessionManager, vmid: int
    ) -> tuple[FakeUpstream, ServerInitInfo]:
        return upstream, INIT

    monkeypatch.setattr(VncSessionManager, "_connect_upstream", fake_connect)
    return VncSessionManager()


USER_A = uuid.uuid4()
USER_B = uuid.uuid4()
TEACHER = uuid.uuid4()
GROUP = uuid.uuid4()


async def _start(
    manager: VncSessionManager,
    *,
    mode: SessionMode = SessionMode.monitor,
    vmid: int = 100,
    group_id: uuid.UUID | None = None,
) -> ClassroomSession:
    return await manager.start_session(
        vmid=vmid, mode=mode, group_id=group_id, started_by=TEACHER
    )


async def _attach(
    manager: VncSessionManager, session_id: str, user_id: uuid.UUID
) -> tuple[FakeSubscriberWs, asyncio.Task[None]]:
    ws = FakeSubscriberWs(frames=list(HANDSHAKE_CLIENT_FRAMES))
    task = asyncio.create_task(
        manager.attach_subscriber(session_id, user_id=user_id, websocket=ws)
    )
    await eventually(lambda: b"".join(ws.sent).startswith(EXPECTED_HANDSHAKE_PREFIX))
    return ws, task


class TestSessionLifecycle:
    async def test_duplicate_vmid_conflicts(self, manager: VncSessionManager) -> None:
        await _start(manager)
        with pytest.raises(ConflictError):
            await _start(manager)
        await manager.stop_session(manager.list_sessions()[0].id)

    async def test_initial_full_fbur_sent(
        self, manager: VncSessionManager, upstream: FakeUpstream
    ) -> None:
        session = await _start(manager)
        await eventually(lambda: FULL_FBUR in upstream.sent)
        await manager.stop_session(session.id)

    async def test_get_and_list_sessions(self, manager: VncSessionManager) -> None:
        session = await _start(manager, mode=SessionMode.broadcast, group_id=GROUP)
        assert manager.get_session(session.id) is not None
        assert manager.get_session("nope") is None
        assert [s.id for s in manager.list_sessions()] == [session.id]
        found = manager.find_broadcast_for_groups({GROUP})
        assert found is not None and found.id == session.id
        assert manager.find_broadcast_for_groups({uuid.uuid4()}) is None
        await manager.stop_session(session.id)
        assert manager.list_sessions() == []

    async def test_stop_session_closes_subscribers_and_fires_callback(
        self, manager: VncSessionManager
    ) -> None:
        ended: list[tuple[str, str]] = []

        async def on_end(session: ClassroomSession, reason: str) -> None:
            ended.append((session.id, reason))

        manager.on_session_end(on_end)
        session = await _start(manager)
        ws, task = await _attach(manager, session.id, USER_A)
        await manager.stop_session(session.id)
        await eventually(lambda: ws.closed_code is not None)
        await eventually(task.done)
        assert ended == [(session.id, "ended")]

    async def test_upstream_close_ends_session(
        self, manager: VncSessionManager, upstream: FakeUpstream
    ) -> None:
        ended: list[tuple[str, str]] = []

        async def on_end(session: ClassroomSession, reason: str) -> None:
            ended.append((session.id, reason))

        manager.on_session_end(on_end)
        session = await _start(manager)
        ws, task = await _attach(manager, session.id, USER_A)
        upstream.close_from_server()
        await eventually(lambda: manager.get_session(session.id) is None)
        await eventually(lambda: ws.closed_code is not None)
        await eventually(task.done)
        assert ended == [(session.id, "upstream_closed")]

    async def test_attach_to_unknown_session_raises(
        self, manager: VncSessionManager
    ) -> None:
        ws = FakeSubscriberWs()
        with pytest.raises(NotFoundError):
            await manager.attach_subscriber("nope", user_id=USER_A, websocket=ws)


class TestFanOut:
    async def test_fanout_order_consistent(
        self, manager: VncSessionManager, upstream: FakeUpstream
    ) -> None:
        session = await _start(manager)
        ws1, t1 = await _attach(manager, session.id, USER_A)
        ws2, t2 = await _attach(manager, session.id, USER_B)
        assert manager.get_session(session.id).subscriber_count == 2  # type: ignore[union-attr]

        # 跨分片餵入：兩個 FramebufferUpdate + Bell
        stream = FB_MSG_A + FB_MSG_B + BELL_MSG
        upstream.feed_server(stream[:10])
        upstream.feed_server(stream[10:])

        expected = FB_MSG_A + FB_MSG_B + BELL_MSG
        await eventually(lambda: ws1.payload_after_handshake() == expected)
        await eventually(lambda: ws2.payload_after_handshake() == expected)
        await manager.stop_session(session.id)
        await asyncio.gather(t1, t2)

    async def test_new_subscriber_triggers_keyframe_request(
        self, manager: VncSessionManager, upstream: FakeUpstream
    ) -> None:
        session = await _start(manager)
        await eventually(lambda: upstream.sent.count(FULL_FBUR) == 1)
        ws, task = await _attach(manager, session.id, USER_A)
        await eventually(lambda: upstream.sent.count(FULL_FBUR) == 2)
        await manager.stop_session(session.id)
        await task

    async def test_framebuffer_update_triggers_incremental_fbur(
        self, manager: VncSessionManager, upstream: FakeUpstream
    ) -> None:
        session = await _start(manager)
        upstream.feed_server(FB_MSG_A)
        await eventually(lambda: INCREMENTAL_FBUR in upstream.sent)
        # Bell 不觸發 FBUR
        count_before = upstream.sent.count(INCREMENTAL_FBUR)
        upstream.feed_server(BELL_MSG)
        await asyncio.sleep(0.05)
        assert upstream.sent.count(INCREMENTAL_FBUR) == count_before
        await manager.stop_session(session.id)

    async def test_queue_full_disconnects_subscriber(
        self,
        manager: VncSessionManager,
        upstream: FakeUpstream,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "CLASSROOM_SUBSCRIBER_QUEUE_SIZE", 2, raising=False)
        session = await _start(manager)
        ws, task = await _attach(manager, session.id, USER_A)
        ws.freeze_send()
        # consumer 卡住：1 則在 send 中，2 則佔滿 queue，第 4 則觸發 QueueFull
        for _ in range(6):
            upstream.feed_server(FB_MSG_A)
        await eventually(lambda: ws.closed_code == 1013)
        await eventually(
            lambda: manager.get_session(session.id).subscriber_count == 0  # type: ignore[union-attr]
        )
        await eventually(task.done)
        await manager.stop_session(session.id)

    async def test_max_subscribers_limit(
        self,
        manager: VncSessionManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "CLASSROOM_MAX_SUBSCRIBERS", 1, raising=False)
        session = await _start(manager)
        ws1, t1 = await _attach(manager, session.id, USER_A)
        ws2 = FakeSubscriberWs(frames=list(HANDSHAKE_CLIENT_FRAMES))
        with pytest.raises(AppError):
            await manager.attach_subscriber(
                session.id, user_id=USER_B, websocket=ws2
            )
        await manager.stop_session(session.id)
        await t1


class TestControl:
    async def test_input_dropped_without_control(
        self, manager: VncSessionManager, upstream: FakeUpstream
    ) -> None:
        session = await _start(manager)
        ws, task = await _attach(manager, session.id, USER_A)
        sent_before = list(upstream.sent)
        ws.client_send(KEY_EVENT)
        ws.client_send(POINTER_EVENT)
        ws.client_send(CLIENT_FBUR)  # 非輸入訊息也一律吞掉
        await asyncio.sleep(0.05)
        assert upstream.sent == sent_before
        await manager.stop_session(session.id)
        await task

    async def test_controller_input_forwarded(
        self, manager: VncSessionManager, upstream: FakeUpstream
    ) -> None:
        session = await _start(manager)
        ws, task = await _attach(manager, session.id, USER_A)
        await manager.set_controller(session.id, USER_A)
        ws.client_send(KEY_EVENT + POINTER_EVENT)
        await eventually(lambda: KEY_EVENT in upstream.sent)
        await eventually(lambda: POINTER_EVENT in upstream.sent)
        # FBUR 即使有控制權也吞掉
        sent_before = list(upstream.sent)
        ws.client_send(CLIENT_FBUR)
        await asyncio.sleep(0.05)
        assert upstream.sent == sent_before
        await manager.stop_session(session.id)
        await task

    async def test_release_control_stops_forwarding(
        self, manager: VncSessionManager, upstream: FakeUpstream
    ) -> None:
        session = await _start(manager)
        ws, task = await _attach(manager, session.id, USER_A)
        await manager.set_controller(session.id, USER_A)
        await manager.set_controller(session.id, None)
        sent_before = list(upstream.sent)
        ws.client_send(KEY_EVENT)
        await asyncio.sleep(0.05)
        assert upstream.sent == sent_before
        await manager.stop_session(session.id)
        await task

    async def test_is_input_blocked(self, manager: VncSessionManager) -> None:
        session = await _start(manager, vmid=100)
        assert manager.is_input_blocked(100) is False
        await manager.set_controller(session.id, TEACHER)
        assert manager.is_input_blocked(100) is True
        await manager.set_controller(session.id, None)
        assert manager.is_input_blocked(100) is False
        assert manager.is_input_blocked(999) is False
        await manager.stop_session(session.id)

    async def test_broadcast_mode_never_blocks_input(
        self, manager: VncSessionManager
    ) -> None:
        session = await _start(
            manager, mode=SessionMode.broadcast, vmid=200, group_id=GROUP
        )
        await manager.set_controller(session.id, TEACHER)
        assert manager.is_input_blocked(200) is False
        await manager.stop_session(session.id)

    async def test_set_controller_unknown_session_raises(
        self, manager: VncSessionManager
    ) -> None:
        with pytest.raises(NotFoundError):
            await manager.set_controller("nope", USER_A)
