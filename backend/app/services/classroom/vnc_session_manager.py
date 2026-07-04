"""單上游 RFB 連線 + 後端 fan-out 的教室 VNC session 管理。

一個 vmid 至多一個 session。上游只建一條 PVE vncwebsocket 連線，
以 ServerMessageSplitter 切出完整訊息後廣播給所有訂閱者佇列；
訂閱者透過下游 RFB 握手（security=None）以標準 noVNC client 觀看。
只有持有控制權的訂閱者的輸入會被轉發到上游。
"""

import asyncio
import contextlib
import itertools
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from urllib.parse import quote

import websockets
from websockets.typing import Subprotocol

from app.core.config import settings
from app.exceptions import AppError, ConflictError, NotFoundError
from app.infrastructure.proxmox import (
    build_ws_ssl_context,
    get_active_host,
    get_proxmox_settings,
)
from app.infrastructure.vnc.handshake import (
    DownstreamSocket,
    ServerInitInfo,
    downstream_handshake,
    full_update_request,
    upstream_handshake,
)
from app.infrastructure.vnc.messages import (
    CLIENT_INPUT_TYPES,
    ClientMessageSplitter,
    ServerMessageSplitter,
)
from app.services.proxmox import proxmox_service

logger = logging.getLogger(__name__)

_SERVER_FRAMEBUFFER_UPDATE = 0


class UpstreamConnection(Protocol):
    """上游 PVE websocket 需要的最小介面（websockets ClientConnection 相容）。"""

    async def recv(self) -> str | bytes: ...

    async def send(self, data: bytes, /) -> None: ...

    async def close(self) -> None: ...


class SubscriberSocket(DownstreamSocket, Protocol):
    """下游訂閱者 websocket 需要的最小介面（FastAPI WebSocket 相容）。"""

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


class SessionMode(str, Enum):
    monitor = "monitor"
    broadcast = "broadcast"


@dataclass(frozen=True)
class ClassroomSession:
    id: str
    vmid: int
    mode: SessionMode
    group_id: uuid.UUID | None
    started_by: uuid.UUID
    controller_user_id: uuid.UUID | None
    subscriber_count: int


@dataclass
class _Subscriber:
    user_id: uuid.UUID
    websocket: SubscriberSocket
    queue: "asyncio.Queue[bytes]"


class _SessionState:
    def __init__(
        self,
        *,
        session_id: str,
        vmid: int,
        mode: SessionMode,
        group_id: uuid.UUID | None,
        started_by: uuid.UUID,
        upstream: UpstreamConnection,
        init: ServerInitInfo,
    ) -> None:
        self.id = session_id
        self.vmid = vmid
        self.mode = mode
        self.group_id = group_id
        self.started_by = started_by
        self.upstream = upstream
        self.init = init
        self.splitter = ServerMessageSplitter(init.width, init.height)
        self.controller_user_id: uuid.UUID | None = None
        self.subscribers: dict[int, _Subscriber] = {}
        self.pump_task: asyncio.Task[None] | None = None
        self.closed = False
        self._key_counter = itertools.count()

    def add_subscriber(self, subscriber: _Subscriber) -> int:
        key = next(self._key_counter)
        self.subscribers[key] = subscriber
        return key

    def snapshot(self) -> ClassroomSession:
        return ClassroomSession(
            id=self.id,
            vmid=self.vmid,
            mode=self.mode,
            group_id=self.group_id,
            started_by=self.started_by,
            controller_user_id=self.controller_user_id,
            subscriber_count=len(self.subscribers),
        )


SessionEndCallback = Callable[[ClassroomSession, str], Awaitable[None]]


class VncSessionManager:
    """管理所有教室 VNC session 的單例。"""

    def __init__(self) -> None:
        self._sessions: dict[str, _SessionState] = {}
        self._vmid_index: dict[int, str] = {}
        self._end_callbacks: list[SessionEndCallback] = []

    # ------------------------------------------------------------------
    # Session 生命週期
    # ------------------------------------------------------------------

    async def start_session(
        self,
        *,
        vmid: int,
        mode: SessionMode,
        group_id: uuid.UUID | None,
        started_by: uuid.UUID,
    ) -> ClassroomSession:
        if vmid in self._vmid_index:
            raise ConflictError(f"VM {vmid} already has an active classroom session")
        session_id = uuid.uuid4().hex
        # 先佔位，避免並發 start 對同一 vmid 建出兩條上游連線
        self._vmid_index[vmid] = session_id
        try:
            upstream, init = await self._connect_upstream(vmid)
        except Exception:
            self._vmid_index.pop(vmid, None)
            raise
        state = _SessionState(
            session_id=session_id,
            vmid=vmid,
            mode=mode,
            group_id=group_id,
            started_by=started_by,
            upstream=upstream,
            init=init,
        )
        self._sessions[session_id] = state
        state.pump_task = asyncio.create_task(self._pump(state))
        logger.info(
            "Classroom session %s started (vmid=%s mode=%s)", session_id, vmid, mode.value
        )
        return state.snapshot()

    async def stop_session(self, session_id: str, *, reason: str = "ended") -> None:
        state = self._sessions.get(session_id)
        if state is None:
            return
        await self._finalize(state, reason=reason)

    def get_session(self, session_id: str) -> ClassroomSession | None:
        state = self._sessions.get(session_id)
        return state.snapshot() if state else None

    def list_sessions(self) -> list[ClassroomSession]:
        return [state.snapshot() for state in self._sessions.values()]

    def find_broadcast_for_groups(
        self, group_ids: set[uuid.UUID]
    ) -> ClassroomSession | None:
        for state in self._sessions.values():
            if state.mode is SessionMode.broadcast and state.group_id in group_ids:
                return state.snapshot()
        return None

    def on_session_end(self, callback: SessionEndCallback) -> None:
        self._end_callbacks.append(callback)

    # ------------------------------------------------------------------
    # 控制權
    # ------------------------------------------------------------------

    async def set_controller(self, session_id: str, user_id: uuid.UUID | None) -> None:
        state = self._sessions.get(session_id)
        if state is None:
            raise NotFoundError("Classroom session not found")
        state.controller_user_id = user_id

    def is_input_blocked(self, vmid: int) -> bool:
        """monitor session 有控制權者時，學生自己主控台的輸入要被攔截。"""
        session_id = self._vmid_index.get(vmid)
        state = self._sessions.get(session_id) if session_id else None
        return (
            state is not None
            and state.mode is SessionMode.monitor
            and state.controller_user_id is not None
        )

    # ------------------------------------------------------------------
    # 訂閱者
    # ------------------------------------------------------------------

    async def attach_subscriber(
        self,
        session_id: str,
        *,
        user_id: uuid.UUID,
        websocket: SubscriberSocket,
    ) -> None:
        """對訂閱者做下游握手後常駐轉發，直到斷線或 session 結束。"""
        state = self._sessions.get(session_id)
        if state is None or state.closed:
            raise NotFoundError("Classroom session not found")
        if len(state.subscribers) >= settings.CLASSROOM_MAX_SUBSCRIBERS:
            raise AppError("Classroom subscriber limit reached", 429)

        await downstream_handshake(websocket, state.init)

        queue: asyncio.Queue[bytes] = asyncio.Queue(
            maxsize=settings.CLASSROOM_SUBSCRIBER_QUEUE_SIZE
        )
        subscriber = _Subscriber(user_id=user_id, websocket=websocket, queue=queue)
        key = state.add_subscriber(subscriber)
        try:
            # 為新訂閱者要一張全量 keyframe（廣播給所有人，成本一次）
            size = state.splitter.size
            await state.upstream.send(
                full_update_request(size.width, size.height, incremental=False)
            )
            consumer = asyncio.create_task(self._subscriber_consumer(subscriber))
            reader = asyncio.create_task(self._subscriber_reader(state, subscriber))
            try:
                await asyncio.wait(
                    {consumer, reader}, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                # 不論正常結束或本 handler 被取消，都要 cancel 並「消費」
                # 兩個子 task 的結果/例外（斷線屬正常結束），
                # 避免 "Task exception was never retrieved"。
                for task in (consumer, reader):
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
        finally:
            state.subscribers.pop(key, None)

    @staticmethod
    async def _subscriber_consumer(subscriber: _Subscriber) -> None:
        while True:
            data = await subscriber.queue.get()
            await subscriber.websocket.send_bytes(data)

    @staticmethod
    async def _subscriber_reader(state: _SessionState, subscriber: _Subscriber) -> None:
        splitter = ClientMessageSplitter()
        while True:
            data = await subscriber.websocket.receive_bytes()
            for msg_type, message in splitter.feed(data):
                if msg_type not in CLIENT_INPUT_TYPES:
                    # FBUR / SetPixelFormat / SetEncodings 一律吞掉：
                    # 上游的像素格式與更新節奏由 pump 統一控制
                    continue
                if (
                    state.controller_user_id is not None
                    and state.controller_user_id == subscriber.user_id
                ):
                    await state.upstream.send(message)

    # ------------------------------------------------------------------
    # 上游 pump
    # ------------------------------------------------------------------

    async def _pump(self, state: _SessionState) -> None:
        try:
            size = state.splitter.size
            await state.upstream.send(
                full_update_request(size.width, size.height, incremental=False)
            )
            while True:
                frame = await state.upstream.recv()
                if isinstance(frame, str):
                    continue
                for message in state.splitter.feed(frame):
                    self._broadcast(state, message)
                    if message[0] == _SERVER_FRAMEBUFFER_UPDATE:
                        # 收滿一張 → 立即要下一張增量更新
                        size = state.splitter.size
                        await state.upstream.send(
                            full_update_request(
                                size.width, size.height, incremental=True
                            )
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not state.closed:
                logger.info(
                    "Classroom session %s upstream closed: %s", state.id, exc
                )
        finally:
            if not state.closed:
                await self._finalize(state, reason="upstream_closed")

    def _broadcast(self, state: _SessionState, message: bytes) -> None:
        for key, subscriber in list(state.subscribers.items()):
            try:
                subscriber.queue.put_nowait(message)
            except asyncio.QueueFull:
                # 佇列滿代表訂閱者消化不了 → 直接斷開，不丟個別訊息
                state.subscribers.pop(key, None)
                asyncio.get_running_loop().create_task(
                    self._close_subscriber_ws(
                        subscriber.websocket, code=1013, reason="subscriber too slow"
                    )
                )

    @staticmethod
    async def _close_subscriber_ws(
        websocket: SubscriberSocket, *, code: int, reason: str
    ) -> None:
        with contextlib.suppress(Exception):
            await websocket.close(code=code, reason=reason)

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------

    async def _finalize(self, state: _SessionState, *, reason: str) -> None:
        if state.closed:
            return
        state.closed = True
        snapshot = state.snapshot()
        self._sessions.pop(state.id, None)
        self._vmid_index.pop(state.vmid, None)

        pump_task = state.pump_task
        if pump_task is not None and pump_task is not asyncio.current_task():
            pump_task.cancel()

        with contextlib.suppress(Exception):
            await state.upstream.close()

        for subscriber in list(state.subscribers.values()):
            await self._close_subscriber_ws(
                subscriber.websocket, code=1000, reason=f"session {reason}"
            )
        state.subscribers.clear()

        logger.info("Classroom session %s ended (%s)", state.id, reason)
        for callback in self._end_callbacks:
            try:
                await callback(snapshot, reason)
            except Exception:
                logger.exception("Classroom session end callback failed")

    # ------------------------------------------------------------------
    # 上游連線（真實 PVE；測試會 monkeypatch 這個方法）
    # ------------------------------------------------------------------

    async def _connect_upstream(
        self, vmid: int
    ) -> tuple[UpstreamConnection, ServerInitInfo]:
        vm_info = await asyncio.to_thread(proxmox_service.find_resource, vmid)
        node = vm_info["node"]

        pve_auth_cookie, csrf_token = await proxmox_service.get_session_ticket()
        console = await proxmox_service.get_vnc_ticket_with_session(
            node, vmid, pve_auth_cookie, csrf_token
        )
        vnc_ticket = str(console["ticket"])
        vnc_port = console["port"]

        cfg = get_proxmox_settings()
        url = (
            f"wss://{get_active_host()}:8006"
            f"/api2/json/nodes/{node}/qemu/{vmid}/vncwebsocket"
            f"?port={vnc_port}&vncticket={quote(vnc_ticket, safe='')}"
        )
        ws = await websockets.connect(
            url,
            ssl=build_ws_ssl_context(cfg),
            additional_headers={"Cookie": f"PVEAuthCookie={pve_auth_cookie}"},
            subprotocols=[Subprotocol("binary")],
            max_size=2**20,
            proxy=None,
        )
        try:
            init = await upstream_handshake(ws, vnc_ticket)
        except Exception:
            with contextlib.suppress(Exception):
                await ws.close()
            raise
        return ws, init


vnc_session_manager = VncSessionManager()
