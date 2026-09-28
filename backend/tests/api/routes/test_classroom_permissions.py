"""回歸測試：上課監看的觀看與接管要有 CLASSROOM_MONITOR 權限，不只是班級擁有者。

被降為學生的前任老師仍是舊班級的 owner；只看擁有者的話，他仍能對原班學生
的 VM 開監看、接管鍵盤滑鼠。停止 session 則刻意維持可用，讓殘留的 session
收得掉。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.api.routes import classroom as routes
from app.exceptions import PermissionDeniedError
from app.schemas.classroom import ClassroomControlRequest, ClassroomSessionCreate

DEMOTED_OWNER = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, role="student")
TEACHER = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, role="teacher")


def _live(user) -> SimpleNamespace:
    return SimpleNamespace(
        id="s1",
        vmid=101,
        mode=SimpleNamespace(value="monitor"),
        class_id=uuid.uuid4(),
        started_by=user.id,
        controller_user_id=None,
        subscriber_count=0,
    )


@pytest.fixture
def service_calls(monkeypatch):
    calls: list[str] = []

    async def start_watch(_session, user, _vmid, _class_id):
        calls.append("watch")
        return _live(user)

    async def start_broadcast(_session, user, _vmid, _class_id):
        calls.append("broadcast")
        return _live(user)

    async def set_control(_session, user, _session_id, _action):
        calls.append("control")
        return _live(user)

    async def stop_session(_session, _user, _session_id):
        calls.append("stop")

    monkeypatch.setattr(routes.classroom_service, "start_class_watch", start_watch)
    monkeypatch.setattr(
        routes.classroom_service, "start_class_broadcast", start_broadcast
    )
    monkeypatch.setattr(routes.classroom_service, "set_control", set_control)
    monkeypatch.setattr(routes.classroom_service, "stop_session", stop_session)
    return calls


def _body(mode: str) -> ClassroomSessionCreate:
    return ClassroomSessionCreate.model_validate(
        {"vmid": 101, "mode": mode, "class_id": str(uuid.uuid4())}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["monitor", "broadcast"])
async def test_demoted_owner_cannot_open_a_session(service_calls, mode: str) -> None:
    with pytest.raises(PermissionDeniedError) as caught:
        await routes.create_classroom_session(_body(mode), None, DEMOTED_OWNER)
    assert caught.value.message == "只有老師與管理員可以使用上課監看"
    assert service_calls == []


@pytest.mark.asyncio
async def test_demoted_owner_cannot_take_control(service_calls) -> None:
    with pytest.raises(PermissionDeniedError) as caught:
        await routes.set_classroom_control(
            "s1", ClassroomControlRequest(action="take"), None, DEMOTED_OWNER
        )
    assert caught.value.message != "classroom.monitor_forbidden"
    assert service_calls == []


@pytest.mark.asyncio
async def test_demoted_owner_can_still_stop_a_leftover_session(service_calls) -> None:
    await routes.stop_classroom_session("s1", None, DEMOTED_OWNER)
    assert service_calls == ["stop"]


@pytest.mark.asyncio
async def test_teacher_reaches_the_service(service_calls) -> None:
    await routes.create_classroom_session(_body("monitor"), None, TEACHER)
    await routes.set_classroom_control(
        "s1", ClassroomControlRequest(action="take"), None, TEACHER
    )
    assert service_calls == ["watch", "control"]
