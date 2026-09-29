"""教室接管狀態重同步與服務層的上課監看權限檢查。

- GET /classroom/live 回傳 taken_over_vmids：學生重連後以後端為準重建「老師接管中」覆蓋。
- 觀看與接管在服務層也要求 CLASSROOM_MONITOR（被降為學生的前任班級擁有者不行），
  停止 session 則刻意維持可用，讓殘留的 session 收得掉。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.api.routes import classroom as routes
from app.exceptions import PermissionDeniedError
from app.models import (
    Resource,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
    TeachingClassStudent,
    TeachingClassStudentMachine,
    User,
    UserRole,
)
from app.schemas.classroom import ClassroomLivePublic
from app.services.classroom import classroom_service
from app.services.classroom.vnc_session_manager import ClassroomSession, SessionMode


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,  # type: ignore[arg-type]
            TeachingClass.__table__,  # type: ignore[arg-type]
            TeachingClassStudent.__table__,  # type: ignore[arg-type]
            TeachingClassMachineNode.__table__,  # type: ignore[arg-type]
            TeachingClassStudentMachine.__table__,  # type: ignore[arg-type]
            Resource.__table__,  # type: ignore[arg-type]
        ],
    )
    with Session(engine) as session:
        yield session


def _user(db: Session, role: UserRole) -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="x",
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _class(db: Session, owner: User) -> TeachingClass:
    teaching_class = TeachingClass(
        owner_id=owner.id,
        name="Linux",
        code=f"CS-{uuid.uuid4().hex[:8]}",
        term="115-1",
        start_date=date(2026, 9, 1),
        end_date=date(2027, 1, 31),
        weekday=1,
        start_time=time(13, 10),
        end_time=time(16, 0),
        status=TeachingClassStatus.active,
    )
    db.add(teaching_class)
    db.commit()
    db.refresh(teaching_class)
    return teaching_class


def _resource(db: Session, owner: User, vmid: int) -> None:
    db.add(
        Resource(
            vmid=vmid,
            user_id=owner.id,
            environment_type="vm",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()


def _session(
    vmid: int,
    *,
    mode: SessionMode = SessionMode.monitor,
    controller: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
    started_by: uuid.UUID | None = None,
) -> ClassroomSession:
    return ClassroomSession(
        id=uuid.uuid4().hex,
        vmid=vmid,
        mode=mode,
        class_id=class_id or uuid.uuid4(),
        started_by=started_by or uuid.uuid4(),
        controller_user_id=controller,
        subscriber_count=1,
    )


class _StubManager:
    def __init__(self, sessions: list[ClassroomSession]) -> None:
        self.sessions = sessions
        self.controllers: list[uuid.UUID | None] = []
        self.stopped: list[str] = []

    def list_sessions(self) -> list[ClassroomSession]:
        return list(self.sessions)

    def get_session(self, session_id: str) -> ClassroomSession | None:
        return next((s for s in self.sessions if s.id == session_id), None)

    def find_broadcast_for_classes(
        self, _class_ids: set[uuid.UUID]
    ) -> ClassroomSession | None:
        return None

    async def set_controller(
        self, _session_id: str, user_id: uuid.UUID | None
    ) -> None:
        self.controllers.append(user_id)

    async def stop_session(self, session_id: str) -> None:
        self.stopped.append(session_id)


# ---------------------------------------------------------------------------
# taken_over_vmids
# ---------------------------------------------------------------------------


def test_only_own_machines_under_active_takeover_are_listed(db: Session) -> None:
    teacher = _user(db, UserRole.teacher)
    student = _user(db, UserRole.student)
    classmate = _user(db, UserRole.student)
    _resource(db, student, 101)
    _resource(db, student, 102)
    _resource(db, student, 103)
    _resource(db, classmate, 201)
    manager = _StubManager(
        [
            _session(101, controller=teacher.id),  # 接管中
            _session(102),  # 只在觀看，沒有接管
            _session(103, mode=SessionMode.broadcast, controller=teacher.id),
            _session(201, controller=teacher.id),  # 別人的機器
        ]
    )

    assert classroom_service.list_taken_over_vmids_for_user(
        db, student, manager=manager
    ) == [101]
    assert classroom_service.list_taken_over_vmids_for_user(
        db, classmate, manager=manager
    ) == [201]


def test_no_active_takeover_returns_empty_list(db: Session) -> None:
    student = _user(db, UserRole.student)
    _resource(db, student, 101)

    assert (
        classroom_service.list_taken_over_vmids_for_user(
            db, student, manager=_StubManager([_session(101)])
        )
        == []
    )


@pytest.mark.asyncio
async def test_live_endpoint_reports_taken_over_vmids(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    teacher = _user(db, UserRole.teacher)
    student = _user(db, UserRole.student)
    bystander = _user(db, UserRole.student)
    _resource(db, student, 101)
    _resource(db, bystander, 202)
    monkeypatch.setattr(
        classroom_service,
        "vnc_session_manager",
        _StubManager([_session(101, controller=teacher.id)]),
    )

    taken = await routes.get_live_broadcast(db, student)
    untouched = await routes.get_live_broadcast(db, bystander)

    assert taken.session is None
    assert taken.taken_over_vmids == [101]
    assert untouched.taken_over_vmids == []


def test_live_schema_defaults_to_empty_takeover_list() -> None:
    assert ClassroomLivePublic().model_dump() == {
        "session": None,
        "taken_over_vmids": [],
    }


# ---------------------------------------------------------------------------
# 服務層的上課監看權限
# ---------------------------------------------------------------------------


def test_demoted_owner_cannot_watch_in_service_layer(db: Session) -> None:
    former_teacher = _user(db, UserRole.student)
    teaching_class = _class(db, former_teacher)

    with pytest.raises(PermissionDeniedError) as caught:
        classroom_service.require_can_watch_class(
            db, former_teacher, teaching_class.id, 101
        )
    assert caught.value.message == "只有老師與管理員可以使用上課監看"


@pytest.mark.asyncio
async def test_demoted_starter_cannot_take_control_but_can_stop(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    former_teacher = _user(db, UserRole.student)
    teaching_class = _class(db, former_teacher)
    live = _session(101, class_id=teaching_class.id, started_by=former_teacher.id)
    manager = _StubManager([live])
    monkeypatch.setattr(classroom_service, "vnc_session_manager", manager)

    with pytest.raises(PermissionDeniedError) as caught:
        await classroom_service.set_control(db, former_teacher, live.id, "take")
    assert caught.value.message == "只有老師與管理員可以使用上課監看"
    assert manager.controllers == []

    await classroom_service.stop_session(db, former_teacher, live.id)
    assert manager.stopped == [live.id]


@pytest.mark.asyncio
async def test_teacher_starter_can_still_take_control(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    teacher = _user(db, UserRole.teacher)
    teaching_class = _class(db, teacher)
    live = _session(101, class_id=teaching_class.id, started_by=teacher.id)
    manager = _StubManager([live])
    monkeypatch.setattr(classroom_service, "vnc_session_manager", manager)

    await classroom_service.set_control(db, teacher, live.id, "take")
    assert manager.controllers == [teacher.id]
