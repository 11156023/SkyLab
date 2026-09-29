"""教室 session 收尾、晚進場者的畫面、學生提醒的語系。"""

from __future__ import annotations

import asyncio
import importlib
import re
import struct
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.core.i18n import _catalog, translate
from app.core.request_context import RequestContext, set_request_context
from app.exceptions import ConflictError
from app.infrastructure.vnc.handshake import (
    PIXEL_FORMAT_32BPP,
    RFB_VERSION,
    ServerInitInfo,
    full_update_request,
)
from app.models import Resource, User, UserRole
from app.services.classroom import classroom_service
from app.services.classroom.vnc_session_manager import (
    ClassroomSession,
    SessionMode,
    VncSessionManager,
)
from app.services.course.reminder_service import list_student_reminders

# 套件 __init__ 把同名的 manager 實例匯出，會遮住子模組本身，所以直接從 sys.modules 取
vsm_module = importlib.import_module("app.services.classroom.vnc_session_manager")

INIT = ServerInitInfo(width=640, height=480, pixel_format=PIXEL_FORMAT_32BPP, name=b"vm")
FULL_FBUR = full_update_request(640, 480, incremental=False)
INCREMENTAL_FBUR = full_update_request(640, 480, incremental=True)
HANDSHAKE_CLIENT_FRAMES = [RFB_VERSION, b"\x01", b"\x01"]


def _handshake_prefix(width: int, height: int) -> bytes:
    return (
        RFB_VERSION
        + b"\x01\x01"
        + b"\x00\x00\x00\x00"
        + struct.pack(">HH", width, height)
        + PIXEL_FORMAT_32BPP
        + struct.pack(">I", 2)
        + b"vm"
    )


def _fb_update_raw_1x1(fill: bytes) -> bytes:
    return struct.pack(">BBH", 0, 0, 1) + struct.pack(">HHHHi", 0, 0, 1, 1, 0) + fill * 4


FB_MSG_A = _fb_update_raw_1x1(b"\xaa")
FB_MSG_B = _fb_update_raw_1x1(b"\xbb")
FB_DESKTOP_SIZE = struct.pack(">BBH", 0, 0, 1) + struct.pack(">HHHHi", 0, 0, 1024, 768, -223)


async def eventually(predicate: Any, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within timeout")


class FakeUpstream:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self._incoming: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def recv(self) -> str | bytes:
        item = await self._incoming.get()
        if item is None:
            raise ConnectionError("upstream closed")
        return item

    async def send(self, data: bytes) -> None:
        self.sent.append(bytes(data))

    async def close(self) -> None:
        self._incoming.put_nowait(None)

    def feed_server(self, data: bytes) -> None:
        self._incoming.put_nowait(data)


class FakeSubscriberWs:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed_code: int | None = None
        self._incoming: asyncio.Queue[bytes | None] = asyncio.Queue()
        for frame in HANDSHAKE_CLIENT_FRAMES:
            self._incoming.put_nowait(frame)

    async def receive_bytes(self) -> bytes:
        item = await self._incoming.get()
        if item is None:
            raise RuntimeError("client disconnected")
        return item

    async def send_bytes(self, data: bytes) -> None:
        if self.closed_code is not None:
            raise RuntimeError("websocket closed")
        self.sent.append(bytes(data))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_code = code
        self._incoming.put_nowait(None)

    def disconnect(self) -> None:
        self._incoming.put_nowait(None)


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream()


@pytest.fixture
def manager(upstream: FakeUpstream, monkeypatch: pytest.MonkeyPatch) -> VncSessionManager:
    async def fake_connect(self: VncSessionManager, vmid: int):
        return upstream, INIT

    monkeypatch.setattr(VncSessionManager, "_connect_upstream", fake_connect)
    return VncSessionManager()


TEACHER = uuid.uuid4()
OTHER = uuid.uuid4()
CLASS = uuid.uuid4()


async def _start(manager: VncSessionManager, mode: SessionMode = SessionMode.monitor):
    return await manager.start_session(
        vmid=100, mode=mode, class_id=CLASS, started_by=TEACHER
    )


async def _attach(manager: VncSessionManager, session_id: str, user_id: uuid.UUID, size=(640, 480)):
    ws = FakeSubscriberWs()
    task = asyncio.create_task(
        manager.attach_subscriber(session_id, user_id=user_id, websocket=ws)
    )
    prefix = _handshake_prefix(*size)
    await eventually(lambda: b"".join(ws.sent).startswith(prefix))
    return ws, task


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


async def test_controller_leaving_releases_control(
    manager: VncSessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vsm_module, "_IDLE_GRACE_SECONDS", 0.2)
    released: list[ClassroomSession] = []

    async def on_released(snapshot: ClassroomSession) -> None:
        released.append(snapshot)

    manager.on_controller_released(on_released)
    session = await _start(manager)
    teacher_ws, teacher_task = await _attach(manager, session.id, TEACHER)
    other_ws, other_task = await _attach(manager, session.id, OTHER)
    await manager.set_controller(session.id, TEACHER)
    assert manager.is_input_blocked(100) is True

    teacher_ws.disconnect()
    await eventually(lambda: not manager.is_input_blocked(100))

    assert [item.id for item in released] == [session.id]
    assert manager.get_session(session.id) is not None  # 還有人在看，session 保留
    await manager.stop_session(session.id)
    await asyncio.gather(teacher_task, other_task)


async def test_monitor_session_ends_when_everyone_left(
    manager: VncSessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vsm_module, "_IDLE_GRACE_SECONDS", 0.2)
    ended: list[str] = []

    async def on_end(_snapshot: ClassroomSession, reason: str) -> None:
        ended.append(reason)

    manager.on_session_end(on_end)
    session = await _start(manager)
    ws, task = await _attach(manager, session.id, TEACHER)
    await manager.set_controller(session.id, TEACHER)

    ws.disconnect()
    await eventually(lambda: manager.get_session(session.id) is None)

    assert ended == ["idle"]
    assert manager.is_input_blocked(100) is False
    await task
    # 收掉之後同一台可以再開
    again = await _start(manager)
    await manager.stop_session(again.id)


async def test_reattaching_within_grace_keeps_the_session(
    manager: VncSessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vsm_module, "_IDLE_GRACE_SECONDS", 0.3)
    session = await _start(manager)
    ws, task = await _attach(manager, session.id, TEACHER)
    ws.disconnect()
    await task
    ws2, task2 = await _attach(manager, session.id, TEACHER)
    await asyncio.sleep(0.5)
    assert manager.get_session(session.id) is not None
    await manager.stop_session(session.id)
    await task2


async def test_broadcast_without_viewers_is_not_auto_stopped(
    manager: VncSessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vsm_module, "_IDLE_GRACE_SECONDS", 0.01)
    session = await _start(manager, SessionMode.broadcast)
    await asyncio.sleep(0.1)
    assert manager.get_session(session.id) is not None
    await manager.stop_session(session.id)


def _teacher(user_id: uuid.UUID) -> User:
    return User(
        id=user_id,
        email=f"{user_id.hex[:8]}@example.edu",
        role=UserRole.teacher,
        hashed_password="x",
    )


async def test_start_class_watch_reuses_own_monitor_session(
    manager: VncSessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(classroom_service, "vnc_session_manager", manager)
    monkeypatch.setattr(
        classroom_service, "require_can_watch_class", lambda *_args, **_kwargs: None
    )
    first = await classroom_service.start_class_watch(
        None, _teacher(TEACHER), 100, CLASS  # type: ignore[arg-type]
    )
    second = await classroom_service.start_class_watch(
        None, _teacher(TEACHER), 100, CLASS  # type: ignore[arg-type]
    )
    assert second.id == first.id

    with pytest.raises(ConflictError):
        await classroom_service.start_class_watch(
            None, _teacher(OTHER), 100, CLASS  # type: ignore[arg-type]
        )
    await manager.stop_session(first.id)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


async def test_late_joiner_handshakes_with_the_current_size(
    manager: VncSessionManager, upstream: FakeUpstream
) -> None:
    session = await _start(manager)
    upstream.feed_server(FB_DESKTOP_SIZE)
    await eventually(
        lambda: full_update_request(1024, 768, incremental=True) in upstream.sent
    )

    ws, task = await _attach(manager, session.id, TEACHER, size=(1024, 768))

    assert b"".join(ws.sent).startswith(_handshake_prefix(1024, 768))
    await manager.stop_session(session.id)
    await task


async def test_late_joiner_gets_keyframe_plus_later_deltas(
    manager: VncSessionManager, upstream: FakeUpstream
) -> None:
    session = await _start(manager)
    upstream.feed_server(FB_MSG_A)  # 開場要的全畫面
    await eventually(lambda: upstream.sent.count(INCREMENTAL_FBUR) == 1)
    upstream.feed_server(FB_MSG_B)  # 之後的增量更新
    await eventually(lambda: upstream.sent.count(INCREMENTAL_FBUR) == 2)

    ws, task = await _attach(manager, session.id, TEACHER)
    prefix = _handshake_prefix(640, 480)
    await eventually(lambda: b"".join(ws.sent)[len(prefix):] == FB_MSG_A + FB_MSG_B)

    assert upstream.sent.count(FULL_FBUR) == 1  # 沒有多要一張全畫面
    await manager.stop_session(session.id)
    await task


async def test_too_many_deltas_drop_the_cache(
    manager: VncSessionManager,
    upstream: FakeUpstream,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "CLASSROOM_SUBSCRIBER_QUEUE_SIZE", 4, raising=False)
    session = await _start(manager)
    upstream.feed_server(FB_MSG_A)
    await eventually(lambda: upstream.sent.count(INCREMENTAL_FBUR) == 1)
    for index in range(3):
        upstream.feed_server(FB_MSG_B)
        await eventually(
            lambda n=index: upstream.sent.count(INCREMENTAL_FBUR) == n + 2
        )

    ws, task = await _attach(manager, session.id, TEACHER)
    # 快取作廢 → 新訂閱者觸發一張新的全畫面，而不是拿到過時的畫面
    await eventually(lambda: upstream.sent.count(FULL_FBUR) == 2)
    await manager.stop_session(session.id)
    await task


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

_CJK = re.compile(r"[぀-ヿ一-鿿]")


@pytest.fixture
def english() -> Iterator[None]:
    set_request_context(RequestContext(language="en"))
    yield
    set_request_context(RequestContext())


def test_student_reminders_follow_request_language(english: None) -> None:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        student = User(
            email="student@example.edu", role=UserRole.student, hashed_password="x"
        )
        session.add(student)
        session.commit()
        today = date(2026, 8, 25)
        session.add(
            Resource(
                vmid=301,
                user_id=student.id,
                environment_type="LXC",
                expiry_date=today,
                created_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
        )
        session.commit()

        rows = list_student_reminders(
            session,
            user_id=student.id,
            now=datetime(2026, 8, 25, 1, 0, tzinfo=UTC),
        )

    [row] = rows
    assert row.title == "Resource #301 is expiring soon"
    assert row.description == (
        "Expires today. If you still need it, request an extension soon."
    )
    assert row.time_label == "Today"
    for text in (row.title, row.description, row.time_label):
        assert not _CJK.search(text)


@pytest.mark.parametrize(
    ("key", "params"),
    [
        ("course_reminder.today", {}),
        ("course_reminder.tomorrow", {}),
        ("course_reminder.resource_fallback_name", {"vmid": 1}),
        ("course_reminder.resource_expires_today", {}),
        ("course_reminder.resource_expires_in_days", {"days": 2}),
        ("course_reminder.resource_expiring_title", {"name": "x"}),
        ("course_reminder.request_approved_title", {}),
        ("course_reminder.request_rejected_title", {}),
        ("course_reminder.request_approved_description", {"hostname": "h"}),
        ("course_reminder.request_rejected_description", {"hostname": "h"}),
        ("course_reminder.class_task_title", {"class_name": "c", "title": "t"}),
        ("course_reminder.class_task_description", {"week": 3}),
        ("course.teacher_fallback", {}),
        ("class_capacity.ip_insufficient", {"required": 5, "available": 2}),
    ],
)
@pytest.mark.parametrize("lang", ["zh-TW", "en", "ja"])
def test_new_message_keys_exist_in_every_language(
    key: str, params: dict[str, object], lang: str
) -> None:
    # translate() 缺 key 時會退回 zh-TW，驗不出 en／ja 漏譯，所以直接查各語言 catalog
    template = _catalog(lang).get(key)
    assert template, f"{key} missing in {lang}"
    text = template.format(**params)
    assert "{" not in text
    for value in params.values():
        assert str(value) in text


def test_zh_tw_reminder_text_is_unchanged() -> None:
    assert translate("course_reminder.today", "zh-TW") == "今天"
    assert (
        translate("course_reminder.resource_fallback_name", "zh-TW", vmid=7)
        == "資源 #7"
    )
    assert (
        translate("class_capacity.ip_insufficient", "zh-TW", required=5, available=2)
        == "IP 不足：需要 5 個，目前只剩 2 個"
    )
