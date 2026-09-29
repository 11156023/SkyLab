"""單上游 RFB 連線 + 後端 fan-out 的教室 VNC session 管理。

一個 vmid 至多一個 session。上游只建一條 PVE vncwebsocket 連線，
以 ServerMessageSplitter 切出完整訊息後廣播給所有訂閱者佇列；
訂閱者透過下游 RFB 握手（security=None）以標準 noVNC client 觀看。
只有持有控制權的訂閱者的輸入會被轉發到上游。
"""

import asyncio
import contextlib
import dataclasses
import itertools
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from app.core.config import settings
from app.core.i18n import t
from app.exceptions import AppError, ConflictError, NotFoundError
from app.infrastructure.proxmox import open_vncwebsocket
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
# 快取的整張畫面可以直接餵給新訂閱者；超過這個秒數才值得再向上游要一張，
# 因為非增量更新會廣播給全班，一次進場兩百人不能變成兩百次全畫面重畫。
_KEYFRAME_MAX_AGE_SECONDS = 5.0
# monitor session 最後一位訂閱者離開後，等這麼久沒人回來就收掉 session；
# 控制者的連線全斷了也在這時候把控制權還給學生。留一段寬限給重新整理／斷線重連。
_IDLE_GRACE_SECONDS = 30.0


class UpstreamConnection(Protocol):
    """上游 PVE websocket 需要的最小介面（websockets ClientConnection 相容）。"""

    async def recv(self) -> str | bytes:
        """接收上游送來的一個訊框。"""

    async def send(self, data: bytes, /) -> None:
        """送出一個二進位訊框到上游。"""

    async def close(self) -> None:
        """關閉上游連線。"""


class SubscriberSocket(DownstreamSocket, Protocol):
    """下游訂閱者 websocket 需要的最小介面（FastAPI WebSocket 相容）。"""

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """以指定 close code / reason 關閉下游連線。"""


class SessionMode(str, Enum):
    monitor = "monitor"
    broadcast = "broadcast"


@dataclass(frozen=True)
class ClassroomSession:
    id: str
    vmid: int
    mode: SessionMode
    class_id: uuid.UUID
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
        class_id: uuid.UUID,
        started_by: uuid.UUID,
        upstream: UpstreamConnection,
        init: ServerInitInfo,
    ) -> None:
        self.id = session_id
        self.vmid = vmid
        self.mode = mode
        self.class_id = class_id
        self.started_by = started_by
        self.upstream = upstream
        self.init = init
        self.splitter = ServerMessageSplitter(init.width, init.height)
        self.controller_user_id: uuid.UUID | None = None
        self.subscribers: dict[int, _Subscriber] = {}
        self.pump_task: asyncio.Task[None] | None = None
        self.closed = False
        # 上游只有一條連線：pump、訂閱者輸入、keyframe 請求都得排隊送，
        # 併發呼叫 send() 會把訊框交錯在一起，RFB 流就毀了。
        self.send_lock = asyncio.Lock()
        # 最近一張完整畫面（非增量 FramebufferUpdate），給新訂閱者直接用
        self.keyframe: bytes | None = None
        self.keyframe_at = 0.0
        self.keyframe_pending = False
        # keyframe 之後廣播過的增量更新：新訂閱者拿 keyframe 再依序補上這些，
        # 畫面才會跟其他人一致；只拿 keyframe 的話，這段期間變過的區域會一直停在舊畫面
        self.keyframe_deltas: list[bytes] = []
        # monitor session 的閒置檢查 task（訂閱者離開時重新排程）
        self.idle_task: asyncio.Task[None] | None = None
        # 背景關閉慢速訂閱者的 task，持有參考避免被 GC 中途回收
        self.close_tasks: set[asyncio.Task[None]] = set()
        self._key_counter = itertools.count()

    def add_subscriber(self, subscriber: _Subscriber) -> int:
        key = next(self._key_counter)
        self.subscribers[key] = subscriber
        return key

    def needs_fresh_keyframe(self) -> bool:
        """已經有人在要、或快取夠新，就不要再對上游要一張全畫面。"""
        if self.keyframe_pending:
            return False
        if self.keyframe is None:
            return True
        return (
            asyncio.get_running_loop().time() - self.keyframe_at
            > _KEYFRAME_MAX_AGE_SECONDS
        )

    def remember_keyframe(self, message: bytes) -> None:
        self.keyframe = message
        self.keyframe_at = asyncio.get_running_loop().time()
        self.keyframe_pending = False
        self.keyframe_deltas = []

    def drop_keyframe(self) -> None:
        """快取不能再用（尺寸變了、補差太多）→ 下一位進場者會重新要一張。"""
        self.keyframe = None
        self.keyframe_deltas = []

    def record_delta(self, message: bytes) -> None:
        """記下 keyframe 之後的增量更新；多到新訂閱者佇列放不下就整份作廢。

        keyframe 加上補差只佔佇列一半，另一半留給握手期間持續廣播進來的訊息，
        否則新訂閱者一進場就會因為佇列滿被踢掉。
        """
        if self.keyframe is None:
            return
        limit = max(settings.CLASSROOM_SUBSCRIBER_QUEUE_SIZE // 2 - 1, 0)
        if len(self.keyframe_deltas) >= limit:
            self.drop_keyframe()
            return
        self.keyframe_deltas.append(message)

    def snapshot(self) -> ClassroomSession:
        return ClassroomSession(
            id=self.id,
            vmid=self.vmid,
            mode=self.mode,
            class_id=self.class_id,
            started_by=self.started_by,
            controller_user_id=self.controller_user_id,
            subscriber_count=len(self.subscribers),
        )


async def _send_upstream(state: _SessionState, data: bytes) -> None:
    """序列化所有送往上游的訊框。"""
    async with state.send_lock:
        await state.upstream.send(data)


SessionEndCallback = Callable[[ClassroomSession, str], Awaitable[None]]
ControllerReleasedCallback = Callable[[ClassroomSession], Awaitable[None]]


class VncSessionManager:
    """管理所有教室 VNC session 的單例。"""

    def __init__(self) -> None:
        self._sessions: dict[str, _SessionState] = {}
        self._vmid_index: dict[int, str] = {}
        self._end_callbacks: list[SessionEndCallback] = []
        self._controller_released_callbacks: list[ControllerReleasedCallback] = []

    # ------------------------------------------------------------------
    # Session 生命週期
    # ------------------------------------------------------------------

    async def start_session(
        self,
        *,
        vmid: int,
        mode: SessionMode,
        class_id: uuid.UUID,
        started_by: uuid.UUID,
    ) -> ClassroomSession:
        if vmid in self._vmid_index:
            raise ConflictError(t("vnc_session.session_already_active", vmid=vmid))
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
            class_id=class_id,
            started_by=started_by,
            upstream=upstream,
            init=init,
        )
        self._sessions[session_id] = state
        state.pump_task = asyncio.create_task(self._pump(state))
        # 開了卻一直沒人接上來（頁面關掉、網路斷）也要能自己收掉
        self._schedule_idle_check(state)
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

    def get_session_for_vmid(self, vmid: int) -> ClassroomSession | None:
        session_id = self._vmid_index.get(vmid)
        state = self._sessions.get(session_id) if session_id else None
        return state.snapshot() if state else None

    def list_sessions(self) -> list[ClassroomSession]:
        return [state.snapshot() for state in self._sessions.values()]

    def find_broadcast_for_classes(
        self, class_ids: set[uuid.UUID]
    ) -> ClassroomSession | None:
        for state in self._sessions.values():
            if state.mode is SessionMode.broadcast and state.class_id in class_ids:
                return state.snapshot()
        return None

    def on_session_end(self, callback: SessionEndCallback) -> None:
        self._end_callbacks.append(callback)

    def on_controller_released(self, callback: ControllerReleasedCallback) -> None:
        """控制者的連線全斷、控制權被自動收回時通知（session 仍在）。"""
        self._controller_released_callbacks.append(callback)

    # ------------------------------------------------------------------
    # 控制權
    # ------------------------------------------------------------------

    async def set_controller(self, session_id: str, user_id: uuid.UUID | None) -> None:
        state = self._sessions.get(session_id)
        if state is None:
            raise NotFoundError(t("vnc_session.session_not_found"))
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
            raise NotFoundError(t("vnc_session.session_not_found"))
        if len(state.subscribers) >= settings.CLASSROOM_MAX_SUBSCRIBERS:
            raise AppError(t("vnc_session.subscriber_limit_reached"), 429)

        queue: asyncio.Queue[bytes] = asyncio.Queue(
            maxsize=settings.CLASSROOM_SUBSCRIBER_QUEUE_SIZE
        )
        if state.keyframe is not None:
            # 快取的整張畫面排在佇列最前面，握手一完成就先畫出來；
            # 接著補上之後廣播過的增量更新，畫面才會跟上現況
            queue.put_nowait(state.keyframe)
            for delta in state.keyframe_deltas:
                queue.put_nowait(delta)
        subscriber = _Subscriber(user_id=user_id, websocket=websocket, queue=queue)
        # 先佔名額再握手：握手不設限又不佔名額的話，卡在握手的 client
        # 可以無限堆積，名額檢查等於沒有做
        key = state.add_subscriber(subscriber)
        # 客體中途改過解析度（DesktopSize）時，要用現在的尺寸握手，
        # 否則 noVNC 以為畫面還是舊尺寸，超出的部分會被裁掉
        size = state.splitter.size
        init = dataclasses.replace(state.init, width=size.width, height=size.height)
        try:
            await asyncio.wait_for(
                downstream_handshake(websocket, init),
                timeout=settings.CLASSROOM_HANDSHAKE_TIMEOUT_SECONDS,
            )
            if state.needs_fresh_keyframe():
                # 沒有快取或快取太舊才向上游要；這張會廣播給全班，成本共用一次
                state.keyframe_pending = True
                size = state.splitter.size
                await _send_upstream(
                    state, full_update_request(size.width, size.height, incremental=False)
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
                # gather(return_exceptions=True) 會把 CancelledError / 例外
                # 當成結果回傳而不拋出，等同逐一 await 並 suppress
                await asyncio.gather(consumer, reader, return_exceptions=True)
        finally:
            state.subscribers.pop(key, None)
            self._schedule_idle_check(state)

    # ------------------------------------------------------------------
    # 閒置 / 控制者離線
    # ------------------------------------------------------------------

    def _schedule_idle_check(self, state: _SessionState) -> None:
        """monitor session 在寬限期後檢查：沒人看就收掉、控制者不在就還控制權。

        broadcast 不自動收：學生陸續進場前本來就可能一個訂閱者都沒有。
        """
        if state.closed or state.mode is not SessionMode.monitor:
            return
        if state.idle_task is not None:
            state.idle_task.cancel()
        state.idle_task = asyncio.get_running_loop().create_task(
            self._idle_check(state)
        )

    async def _idle_check(self, state: _SessionState) -> None:
        await asyncio.sleep(_IDLE_GRACE_SECONDS)
        # 已經開始收尾，之後的重新排程不要把這個 task 半途取消
        if state.idle_task is asyncio.current_task():
            state.idle_task = None
        if state.closed:
            return
        if not state.subscribers:
            await self._finalize(state, reason="idle")
            return
        controller = state.controller_user_id
        if controller is None or any(
            subscriber.user_id == controller
            for subscriber in state.subscribers.values()
        ):
            return
        # 老師關掉分頁或斷線卻沒按「釋放」：學生自己的主控台不能一直被鎖住
        state.controller_user_id = None
        snapshot = state.snapshot()
        logger.info(
            "Classroom session %s controller left; control released", state.id
        )
        for callback in self._controller_released_callbacks:
            try:
                await callback(snapshot)
            except Exception:
                logger.exception("Classroom controller released callback failed")

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
                allowed = (
                    state.controller_user_id is not None
                    and state.controller_user_id == subscriber.user_id
                )
                if allowed:
                    await _send_upstream(state, message)

    # ------------------------------------------------------------------
    # 上游 pump
    # ------------------------------------------------------------------

    async def _pump(self, state: _SessionState) -> None:
        try:
            size = state.splitter.size
            state.keyframe_pending = True
            await _send_upstream(
                state, full_update_request(size.width, size.height, incremental=False)
            )
            while True:
                frame = await state.upstream.recv()
                if isinstance(frame, str):
                    continue
                size_before = state.splitter.size
                messages = state.splitter.feed(frame)
                if state.splitter.size != size_before:
                    # 解析度變了，舊尺寸的快取畫面不能再給新訂閱者
                    state.drop_keyframe()
                for message in messages:
                    if message[0] == _SERVER_FRAMEBUFFER_UPDATE:
                        if state.keyframe_pending:
                            # 剛才要的是非增量更新 → 這張是完整畫面，留著給新訂閱者
                            state.remember_keyframe(message)
                        else:
                            state.record_delta(message)
                    self._broadcast(state, message)
                    if message[0] == _SERVER_FRAMEBUFFER_UPDATE:
                        # 收滿一張 → 立即要下一張增量更新
                        size = state.splitter.size
                        await _send_upstream(
                            state,
                            full_update_request(
                                size.width, size.height, incremental=True
                            ),
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
                task = asyncio.get_running_loop().create_task(
                    self._close_subscriber_ws(
                        subscriber.websocket, code=1013, reason="subscriber too slow"
                    )
                )
                # 只有 loop 持有參考的 task 可能被 GC 掉，要自己留著
                state.close_tasks.add(task)
                task.add_done_callback(state.close_tasks.discard)

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
        idle_task = state.idle_task
        if idle_task is not None and idle_task is not asyncio.current_task():
            idle_task.cancel()

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

        pve_auth_cookie, csrf_token = await proxmox_service.get_session_ticket(node)
        console = await proxmox_service.get_vnc_ticket_with_session(
            node, vmid, pve_auth_cookie, csrf_token
        )
        vnc_ticket = str(console["ticket"])
        vnc_port = console["port"]

        ws = await open_vncwebsocket(
            node,
            "qemu",
            vmid,
            vnc_port,
            vnc_ticket,
            pve_auth_cookie,
            # 節點沒回應時不要無限等：start_session 的呼叫端是 HTTP 請求
            open_timeout=settings.CLASSROOM_UPSTREAM_TIMEOUT_SECONDS,
        )
        try:
            init = await upstream_handshake(
                ws, vnc_ticket, timeout=settings.CLASSROOM_UPSTREAM_TIMEOUT_SECONDS
            )
        except Exception:
            with contextlib.suppress(Exception):
                await ws.close()
            raise
        return ws, init


vnc_session_manager = VncSessionManager()
