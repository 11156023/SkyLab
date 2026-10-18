"""app.services.classroom.classroom_service 的分支覆蓋測試（權限、清單、編排與結束事件）。"""

import logging
import uuid
from datetime import UTC, date, datetime, time
from typing import Any

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.exceptions import BadRequestError, NotFoundError, PermissionDeniedError
from app.models import (
    BatchProvisionJob,
    BatchProvisionTask,
    Resource,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
    TeachingClassStudent,
    TeachingClassStudentMachine,
    User,
    UserRole,
)
from app.services.classroom import classroom_service
from app.services.classroom.vnc_session_manager import ClassroomSession, SessionMode
from tests.utils.class_machines import add_student_machine

_TABLES = [
    User.__table__,  # type: ignore[attr-defined]
    TeachingClass.__table__,  # type: ignore[attr-defined]
    TeachingClassStudent.__table__,  # type: ignore[attr-defined]
    TeachingClassMachineNode.__table__,  # type: ignore[attr-defined]
    TeachingClassStudentMachine.__table__,  # type: ignore[attr-defined]
    BatchProvisionJob.__table__,  # type: ignore[attr-defined]
    BatchProvisionTask.__table__,  # type: ignore[attr-defined]
    Resource.__table__,  # type: ignore[attr-defined]
]


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(eng, tables=_TABLES)
    return eng


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session


def _user(db: Session, role: UserRole) -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="x",
        role=role,
        is_superuser=role is UserRole.admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _class(
    db: Session,
    owner: User,
    *students: User,
    status: TeachingClassStatus = TeachingClassStatus.active,
) -> TeachingClass:
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
        status=status,
    )
    db.add(teaching_class)
    db.commit()
    db.refresh(teaching_class)
    for student in students:
        db.add(TeachingClassStudent(class_id=teaching_class.id, user_id=student.id))
    db.commit()
    return teaching_class


def _enrollment(
    db: Session, teaching_class: TeachingClass, user_id: uuid.UUID
) -> TeachingClassStudent:
    row = db.exec(
        select(TeachingClassStudent).where(
            TeachingClassStudent.class_id == teaching_class.id,
            TeachingClassStudent.user_id == user_id,
        )
    ).first()
    assert row is not None
    return row


def _node(
    db: Session,
    teaching_class: TeachingClass,
    *,
    resource_type: str = "qemu",
    node_key: str = "client",
) -> TeachingClassMachineNode:
    node = TeachingClassMachineNode(
        class_id=teaching_class.id,
        node_key=node_key,
        source_template_id=uuid.uuid4(),
        name="Client",
        role="student",
        resource_type=resource_type,
        cpu=2,
        memory_mb=2048,
        disk_gb=20,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def _resource(db: Session, owner_id: uuid.UUID, vmid: int) -> Resource:
    resource = Resource(
        vmid=vmid,
        user_id=owner_id,
        environment_type="vm",
        created_at=datetime.now(UTC),
    )
    db.add(resource)
    db.commit()
    return resource


def _live(
    *,
    mode: SessionMode = SessionMode.monitor,
    class_id: uuid.UUID | None = None,
    started_by: uuid.UUID | None = None,
    controller: uuid.UUID | None = None,
    vmid: int = 900,
) -> ClassroomSession:
    return ClassroomSession(
        id=uuid.uuid4().hex,
        vmid=vmid,
        mode=mode,
        class_id=class_id or uuid.uuid4(),
        started_by=started_by or uuid.uuid4(),
        controller_user_id=controller,
        subscriber_count=0,
    )


class _FakeManager:
    def __init__(self, sessions: list[ClassroomSession] | None = None) -> None:
        self.sessions = sessions or []
        self.started: list[dict[str, Any]] = []
        self.stopped: list[str] = []
        self.controllers: list[uuid.UUID | None] = []
        self.forget_after_control = False

    def list_sessions(self) -> list[ClassroomSession]:
        return list(self.sessions)

    def get_session(self, session_id: str) -> ClassroomSession | None:
        return next((s for s in self.sessions if s.id == session_id), None)

    def get_session_for_vmid(self, vmid: int) -> ClassroomSession | None:
        return next((s for s in self.sessions if s.vmid == vmid), None)

    async def start_session(self, **kwargs: Any) -> ClassroomSession:
        self.started.append(kwargs)
        live = _live(
            mode=kwargs["mode"],
            class_id=kwargs["class_id"],
            started_by=kwargs["started_by"],
            vmid=kwargs["vmid"],
        )
        self.sessions.append(live)
        return live

    async def stop_session(self, session_id: str) -> None:
        self.stopped.append(session_id)

    async def set_controller(self, session_id: str, user_id: uuid.UUID | None) -> None:
        self.controllers.append(user_id)
        if self.forget_after_control:
            self.sessions = []


class _FakePresence:
    def __init__(self, *, fail: bool = False) -> None:
        self.class_events: list[tuple[uuid.UUID, dict[str, Any]]] = []
        self.user_events: list[tuple[uuid.UUID, dict[str, Any]]] = []
        self.fail = fail

    def online_user_ids_for_class(self, class_id: uuid.UUID) -> set[uuid.UUID]:
        return set()

    async def broadcast_to_class(
        self, class_id: uuid.UUID, event: dict[str, Any]
    ) -> None:
        if self.fail:
            raise RuntimeError("push failed")
        self.class_events.append((class_id, event))

    async def send_to_user(self, user_id: uuid.UUID, event: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("push failed")
        self.user_events.append((user_id, event))


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> _FakeManager:
    fake = _FakeManager()
    monkeypatch.setattr(classroom_service, "vnc_session_manager", fake)
    return fake


@pytest.fixture
def presence(monkeypatch: pytest.MonkeyPatch) -> _FakePresence:
    fake = _FakePresence()
    monkeypatch.setattr(classroom_service, "classroom_presence_hub", fake)
    return fake


# --- 權限檢查 ---------------------------------------------------------------


def test_inactive_class_is_rejected(db: Session) -> None:
    teacher = _user(db, UserRole.teacher)
    teaching_class = _class(db, teacher, status=TeachingClassStatus.archived)
    with pytest.raises(BadRequestError):
        classroom_service.require_can_watch_class(db, teacher, teaching_class.id, 1)


def test_watch_unknown_vmid_is_denied(db: Session) -> None:
    teacher = _user(db, UserRole.teacher)
    teaching_class = _class(db, teacher)
    with pytest.raises(PermissionDeniedError):
        classroom_service.require_can_watch_class(db, teacher, teaching_class.id, 404)


def test_watch_lxc_machine_is_rejected(db: Session) -> None:
    teacher = _user(db, UserRole.teacher)
    student = _user(db, UserRole.student)
    teaching_class = _class(db, teacher, student)
    node = _node(db, teaching_class, resource_type="lxc")
    add_student_machine(
        db, enrollment=_enrollment(db, teaching_class, student.id), node=node, vmid=510
    )
    db.commit()
    with pytest.raises(BadRequestError):
        classroom_service.require_can_watch_class(db, teacher, teaching_class.id, 510)


def test_broadcast_missing_resource_is_not_found(db: Session) -> None:
    teacher = _user(db, UserRole.teacher)
    teaching_class = _class(db, teacher)
    with pytest.raises(NotFoundError):
        classroom_service.require_can_broadcast_class(
            db, teacher, teaching_class.id, 9999
        )


# --- 清單 -------------------------------------------------------------------


def test_list_students_skips_missing_accounts_and_unbuilt_machines(
    db: Session, presence: _FakePresence
) -> None:
    teacher = _user(db, UserRole.teacher)
    student = _user(db, UserRole.student)
    teaching_class = _class(db, teacher, student)
    # 學生帳號已不在的修課紀錄（SQLite 不強制 FK）
    ghost_id = uuid.uuid4()
    db.add(TeachingClassStudent(class_id=teaching_class.id, user_id=ghost_id))
    db.commit()
    enrollment = _enrollment(db, teaching_class, student.id)
    node = _node(db, teaching_class)
    other = _node(db, teaching_class, node_key="server")
    # 還沒有 vmid 的機器要跳過
    add_student_machine(db, enrollment=enrollment, node=node, vmid=None)
    built = add_student_machine(db, enrollment=enrollment, node=other, vmid=520)
    # 節點已不在的機器也要跳過
    third = _node(db, teaching_class, node_key="extra")
    dangling = add_student_machine(db, enrollment=enrollment, node=third, vmid=521)
    dangling.machine_node_id = uuid.uuid4()
    db.add(dangling)
    db.commit()

    rows = classroom_service.list_class_students(
        db,
        teaching_class.id,
        teacher,
        cluster_resources=[{"vmid": None}, {"vmid": 520, "status": "stopped"}],
    )

    assert [row.email for row in rows] == [student.email]
    assert [(vm.vmid, vm.status) for vm in rows[0].vms] == [(built.vmid, "stopped")]
    assert rows[0].online is False


def test_list_broadcast_sources_only_running_qemu(db: Session) -> None:
    teacher = _user(db, UserRole.teacher)
    teaching_class = _class(db, teacher)
    for vmid in (601, 602, 603, 604, 605):
        _resource(db, teacher.id, vmid)

    rows = classroom_service.list_class_broadcast_sources(
        db,
        teaching_class.id,
        teacher,
        cluster_resources=[
            {"vmid": None},
            {"vmid": 601, "type": "lxc", "status": "running"},
            {"vmid": 602, "type": "qemu", "status": "stopped"},
            {"vmid": 603, "type": "qemu", "status": "running", "name": "demo"},
            {"vmid": 604, "status": "running"},
        ],
    )

    assert [(vm.vmid, vm.name, vm.vm_type) for vm in rows] == [
        (603, "demo", "qemu"),
        (604, "VM 604", "qemu"),
    ]


def test_list_sessions_for_admin_and_starter(manager: _FakeManager) -> None:
    starter = User(email="a@example.com", hashed_password="x", role=UserRole.teacher)
    admin = User(
        email="b@example.com",
        hashed_password="x",
        role=UserRole.admin,
        is_superuser=True,
    )
    mine = _live(started_by=starter.id)
    theirs = _live()
    manager.sessions = [mine, theirs]

    assert classroom_service.list_sessions_for(admin) == [mine, theirs]
    assert classroom_service.list_sessions_for(starter) == [mine]


# --- 編排 -------------------------------------------------------------------


async def test_start_broadcast_pushes_live_started(
    db: Session, manager: _FakeManager, presence: _FakePresence
) -> None:
    teacher = _user(db, UserRole.teacher)
    teaching_class = _class(db, teacher)
    _resource(db, teacher.id, 610)

    live = await classroom_service.start_class_broadcast(
        db, teacher, 610, teaching_class.id
    )

    assert live.mode is SessionMode.broadcast
    assert presence.class_events == [
        (
            teaching_class.id,
            {
                "type": "live_started",
                "session_id": live.id,
                "vmid": 610,
                "class_id": str(teaching_class.id),
            },
        )
    ]


async def test_stop_session_not_found(db: Session, manager: _FakeManager) -> None:
    teacher = _user(db, UserRole.teacher)
    with pytest.raises(NotFoundError):
        await classroom_service.stop_session(db, teacher, "missing")


async def test_stop_session_by_other_user_is_denied(
    db: Session, manager: _FakeManager
) -> None:
    teacher = _user(db, UserRole.teacher)
    live = _live()
    manager.sessions = [live]
    with pytest.raises(PermissionDeniedError):
        await classroom_service.stop_session(db, teacher, live.id)
    assert manager.stopped == []


async def test_stop_session_when_class_is_gone(
    db: Session, manager: _FakeManager
) -> None:
    teacher = _user(db, UserRole.teacher)
    live = _live(started_by=teacher.id)
    manager.sessions = [live]
    await classroom_service.stop_session(db, teacher, live.id)
    assert manager.stopped == [live.id]


async def test_set_control_not_found(db: Session, manager: _FakeManager) -> None:
    teacher = _user(db, UserRole.teacher)
    with pytest.raises(NotFoundError):
        await classroom_service.set_control(db, teacher, "missing", "take")


async def test_set_control_rejects_broadcast_session(
    db: Session, manager: _FakeManager
) -> None:
    teacher = _user(db, UserRole.teacher)
    live = _live(mode=SessionMode.broadcast, started_by=teacher.id)
    manager.sessions = [live]
    with pytest.raises(BadRequestError):
        await classroom_service.set_control(db, teacher, live.id, "take")


async def test_set_control_take_and_release_notify_owner(
    db: Session, manager: _FakeManager, presence: _FakePresence
) -> None:
    teacher = _user(db, UserRole.teacher)
    student = _user(db, UserRole.student)
    teaching_class = _class(db, teacher, student)
    _resource(db, student.id, 620)
    live = _live(class_id=teaching_class.id, started_by=teacher.id, vmid=620)
    manager.sessions = [live]

    taken = await classroom_service.set_control(db, teacher, live.id, "take")
    assert taken is live
    manager.forget_after_control = True
    released = await classroom_service.set_control(db, teacher, live.id, "release")

    # session 在操作後消失時回傳原本的快照
    assert released is live
    assert manager.controllers == [teacher.id, None]
    assert [event["type"] for _, event in presence.user_events] == [
        "takeover_started",
        "takeover_stopped",
    ]
    assert {user_id for user_id, _ in presence.user_events} == {student.id}


# --- 結束事件 ---------------------------------------------------------------


def test_lookup_resource_owner_reads_db(
    engine: Any, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _user(db, UserRole.student)
    _resource(db, owner.id, 630)
    monkeypatch.setattr("app.core.db.engine", engine)

    assert classroom_service._lookup_resource_owner(630) == owner.id
    assert classroom_service._lookup_resource_owner(631) is None


def test_lookup_resource_owner_logs_db_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken_session(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(classroom_service, "Session", broken_session)
    with caplog.at_level(logging.ERROR, logger=classroom_service.__name__):
        assert classroom_service._lookup_resource_owner(632) is None
    assert "Failed to look up resource owner" in caplog.text


async def test_session_end_monitor_with_controller_notifies_owner(
    presence: _FakePresence, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id = uuid.uuid4()
    lookups: list[int] = []

    def lookup(vmid: int) -> uuid.UUID | None:
        lookups.append(vmid)
        return owner_id if vmid == 640 else None

    monkeypatch.setattr(classroom_service, "_lookup_resource_owner", lookup)
    await classroom_service._on_session_end(
        _live(vmid=640, controller=uuid.uuid4()), "closed"
    )
    await classroom_service._on_session_end(
        _live(vmid=641, controller=uuid.uuid4()), "closed"
    )
    # 沒有控制者的 monitor 結束不必查擁有者
    await classroom_service._on_session_end(_live(vmid=642), "closed")

    assert lookups == [640, 641]
    assert presence.user_events == [
        (owner_id, presence.user_events[0][1]),
    ]
    assert presence.user_events[0][1]["type"] == "takeover_stopped"


async def test_session_end_push_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        classroom_service, "classroom_presence_hub", _FakePresence(fail=True)
    )
    with caplog.at_level(logging.ERROR, logger=classroom_service.__name__):
        await classroom_service._on_session_end(
            _live(mode=SessionMode.broadcast), "closed"
        )
    assert "Classroom session end event push failed" in caplog.text


async def test_controller_released_notifies_owner(
    presence: _FakePresence, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id = uuid.uuid4()
    monkeypatch.setattr(
        classroom_service,
        "_lookup_resource_owner",
        lambda vmid: owner_id if vmid == 650 else None,
    )
    await classroom_service._on_controller_released(_live(vmid=650))
    await classroom_service._on_controller_released(_live(vmid=651))
    assert [user_id for user_id, _ in presence.user_events] == [owner_id]


async def test_controller_released_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def lookup(_vmid: int) -> uuid.UUID | None:
        raise RuntimeError("boom")

    monkeypatch.setattr(classroom_service, "_lookup_resource_owner", lookup)
    with caplog.at_level(logging.ERROR, logger=classroom_service.__name__):
        await classroom_service._on_controller_released(_live())
    assert "Classroom controller release event push failed" in caplog.text
