"""app.services.classroom.vnc_session_manager 的分支覆蓋測試（上游連線、收尾與回呼失敗）。"""

import asyncio
import logging
import uuid
from typing import Any

import pytest

from app.infrastructure.vnc.handshake import PIXEL_FORMAT_32BPP, ServerInitInfo
from app.services.classroom import vnc_session_manager as module
from app.services.classroom.vnc_session_manager import (
    ClassroomSession,
    SessionMode,
    VncSessionManager,
    _SessionState,
    _Subscriber,
)

INIT = ServerInitInfo(
    width=640, height=480, pixel_format=PIXEL_FORMAT_32BPP, name=b"vm"
)


class _ScriptedUpstream:
    """依序回傳預先排好的訊框，排完就當上游斷線。"""

    def __init__(self, frames: list[str | bytes] | None = None) -> None:
        self.frames = list(frames or [])
        self.sent: list[bytes] = []
        self.closed = False
        self.close_error: Exception | None = None

    async def recv(self) -> str | bytes:
        if not self.frames:
            raise ConnectionError("upstream closed")
        return self.frames.pop(0)

    async def send(self, data: bytes) -> None:
        self.sent.append(bytes(data))

    async def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class _QuietSocket:
    async def close(self, code: int = 1000, reason: str = "") -> None:
        return None


def _state(
    upstream: Any | None = None, *, mode: SessionMode = SessionMode.monitor
) -> _SessionState:
    return _SessionState(
        session_id=uuid.uuid4().hex,
        vmid=4242,
        mode=mode,
        class_id=uuid.uuid4(),
        started_by=uuid.uuid4(),
        upstream=upstream or _ScriptedUpstream(),
        init=INIT,
    )


async def test_start_session_releases_vmid_when_upstream_fails() -> None:
    manager = VncSessionManager()

    async def fail(_vmid: int) -> Any:
        raise ConnectionError("pve unreachable")

    manager._connect_upstream = fail  # type: ignore[method-assign]

    with pytest.raises(ConnectionError):
        await manager.start_session(
            vmid=1,
            mode=SessionMode.monitor,
            class_id=uuid.uuid4(),
            started_by=uuid.uuid4(),
        )
    assert manager.get_session_for_vmid(1) is None
    assert manager._vmid_index == {}


async def test_stop_unknown_session_is_noop() -> None:
    manager = VncSessionManager()
    await manager.stop_session("missing")
    assert manager.list_sessions() == []


async def test_idle_check_skips_closed_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_IDLE_GRACE_SECONDS", 0)
    manager = VncSessionManager()
    state = _state()
    state.closed = True
    finalized: list[str] = []

    async def finalize(_state: _SessionState, *, reason: str) -> None:
        finalized.append(reason)

    manager._finalize = finalize  # type: ignore[method-assign]
    await manager._idle_check(state)
    assert finalized == []


async def test_controller_released_callback_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(module, "_IDLE_GRACE_SECONDS", 0)
    manager = VncSessionManager()
    state = _state()
    state.controller_user_id = uuid.uuid4()
    state.add_subscriber(
        _Subscriber(
            user_id=uuid.uuid4(),
            websocket=_QuietSocket(),  # type: ignore[arg-type]
            queue=asyncio.Queue(),
        )
    )
    released: list[ClassroomSession] = []

    async def broken(_snapshot: ClassroomSession) -> None:
        raise RuntimeError("push failed")

    async def record(snapshot: ClassroomSession) -> None:
        released.append(snapshot)

    manager.on_controller_released(broken)
    manager.on_controller_released(record)

    with caplog.at_level(logging.ERROR, logger=module.__name__):
        await manager._idle_check(state)

    assert state.controller_user_id is None
    assert [snap.controller_user_id for snap in released] == [None]
    assert "Classroom controller released callback failed" in caplog.text


async def test_pump_ignores_text_frames_then_finalizes() -> None:
    manager = VncSessionManager()
    upstream = _ScriptedUpstream(["text frame"])
    state = _state(upstream, mode=SessionMode.broadcast)
    manager._sessions[state.id] = state
    manager._vmid_index[state.vmid] = state.id
    ended: list[str] = []

    async def on_end(_snapshot: ClassroomSession, reason: str) -> None:
        ended.append(reason)

    manager.on_session_end(on_end)
    await manager._pump(state)

    assert state.closed is True
    assert ended == ["upstream_closed"]
    # 只送過開場的整張畫面請求，文字訊框沒有觸發任何東西
    assert len(upstream.sent) == 1


async def test_finalize_twice_and_end_callback_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = VncSessionManager()
    upstream = _ScriptedUpstream()
    upstream.close_error = RuntimeError("already gone")
    state = _state(upstream)
    calls: list[str] = []

    async def broken(_snapshot: ClassroomSession, _reason: str) -> None:
        raise RuntimeError("callback failed")

    async def record(_snapshot: ClassroomSession, reason: str) -> None:
        calls.append(reason)

    manager.on_session_end(broken)
    manager.on_session_end(record)

    with caplog.at_level(logging.ERROR, logger=module.__name__):
        await manager._finalize(state, reason="ended")
        await manager._finalize(state, reason="again")

    assert calls == ["ended"]
    assert "Classroom session end callback failed" in caplog.text


class _FakeWs:
    def __init__(self, *, close_error: Exception | None = None) -> None:
        self.closed = False
        self.close_error = close_error

    async def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


@pytest.fixture
def fake_pve(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    def find_resource(vmid: int) -> dict[str, Any]:
        calls["find"] = vmid
        return {"node": "pve1"}

    async def get_session_ticket(node: str) -> tuple[str, str]:
        calls["ticket_node"] = node
        return "cookie", "csrf"

    async def get_vnc_ticket_with_session(
        node: str, vmid: int, cookie: str, csrf: str
    ) -> dict[str, Any]:
        calls["vnc"] = (node, vmid, cookie, csrf)
        return {"ticket": 12345, "port": 5900}

    monkeypatch.setattr(module.proxmox_service, "find_resource", find_resource)
    monkeypatch.setattr(
        module.proxmox_service, "get_session_ticket", get_session_ticket
    )
    monkeypatch.setattr(
        module.proxmox_service,
        "get_vnc_ticket_with_session",
        get_vnc_ticket_with_session,
    )
    return calls


async def test_connect_upstream_opens_websocket_and_handshakes(
    fake_pve: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _FakeWs()
    opened: list[tuple[Any, ...]] = []

    async def open_ws(*args: Any, **kwargs: Any) -> _FakeWs:
        opened.append((*args, kwargs))
        return ws

    async def handshake(conn: Any, ticket: str, *, timeout: float) -> ServerInitInfo:
        assert conn is ws
        assert ticket == "12345"
        return INIT

    monkeypatch.setattr(module, "open_vncwebsocket", open_ws)
    monkeypatch.setattr(module, "upstream_handshake", handshake)

    upstream, init = await VncSessionManager()._connect_upstream(77)

    assert upstream is ws and init is INIT
    assert fake_pve["find"] == 77
    assert fake_pve["vnc"] == ("pve1", 77, "cookie", "csrf")
    assert opened[0][:6] == ("pve1", "qemu", 77, 5900, "12345", "cookie")
    assert ws.closed is False


async def test_connect_upstream_closes_websocket_when_handshake_fails(
    fake_pve: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _FakeWs(close_error=RuntimeError("close failed"))

    async def open_ws(*_args: Any, **_kwargs: Any) -> _FakeWs:
        return ws

    async def handshake(*_args: Any, **_kwargs: Any) -> ServerInitInfo:
        raise TimeoutError("handshake timed out")

    monkeypatch.setattr(module, "open_vncwebsocket", open_ws)
    monkeypatch.setattr(module, "upstream_handshake", handshake)

    with pytest.raises(TimeoutError):
        await VncSessionManager()._connect_upstream(78)
    assert ws.closed is True
