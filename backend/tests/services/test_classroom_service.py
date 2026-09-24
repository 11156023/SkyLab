"""正式班級教室服務測試。"""

import uuid
from datetime import UTC, date, datetime, time

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import NotFoundError, PermissionDeniedError
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


def _user(db: Session, role: UserRole, *, superuser: bool = False) -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="x",
        role=role,
        is_superuser=superuser,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _class(db: Session, owner: User, *students: User) -> TeachingClass:
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
    for student in students:
        db.add(TeachingClassStudent(class_id=teaching_class.id, user_id=student.id))
    db.commit()
    return teaching_class


def _resource(db: Session, owner: User, vmid: int) -> Resource:
    resource = Resource(
        vmid=vmid,
        user_id=owner.id,
        environment_type="vm",
        created_at=datetime.now(UTC),
    )
    db.add(resource)
    db.commit()
    return resource


def _student_machine(
    db: Session,
    teaching_class: TeachingClass,
    student: User,
    vmid: int,
) -> None:
    enrollment = db.exec(
        TeachingClassStudent.__table__.select().where(
            TeachingClassStudent.class_id == teaching_class.id,
            TeachingClassStudent.user_id == student.id,
        )
    ).first()
    assert enrollment is not None
    node = TeachingClassMachineNode(
        class_id=teaching_class.id,
        node_key="client",
        source_template_id=uuid.uuid4(),
        name="Client",
        role="student",
        resource_type="qemu",
        cpu=2,
        memory_mb=2048,
        disk_gb=20,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    db.add(
        TeachingClassStudentMachine(
            class_student_id=enrollment.id,
            machine_node_id=node.id,
            vmid=vmid,
            status="completed",
        )
    )
    db.commit()


class TestTeachingClassClassroom:
    def test_teacher_can_watch_class_student_machine(self, db: Session) -> None:
        teacher = _user(db, UserRole.teacher)
        student = _user(db, UserRole.student)
        teaching_class = _class(db, teacher, student)
        _student_machine(db, teaching_class, student, 501)

        machine = classroom_service.require_can_watch_class(
            db, teacher, teaching_class.id, 501
        )
        assert machine.vmid == 501

    def test_teacher_cannot_watch_another_class_machine(self, db: Session) -> None:
        teacher = _user(db, UserRole.teacher)
        other_teacher = _user(db, UserRole.teacher)
        student = _user(db, UserRole.student)
        teaching_class = _class(db, other_teacher, student)
        _student_machine(db, teaching_class, student, 502)

        with pytest.raises(PermissionDeniedError):
            classroom_service.require_can_watch_class(
                db, teacher, teaching_class.id, 502
            )

    def test_lists_fixed_multi_machine_students(self, db: Session) -> None:
        teacher = _user(db, UserRole.teacher)
        student = _user(db, UserRole.student)
        teaching_class = _class(db, teacher, student)
        _student_machine(db, teaching_class, student, 503)

        rows = classroom_service.list_class_students(
            db,
            teaching_class.id,
            teacher,
            cluster_resources=[
                {
                    "vmid": 503,
                    "name": "student-client",
                    "status": "running",
                    "type": "qemu",
                }
            ],
        )

        assert len(rows) == 1
        assert rows[0].email == student.email
        assert [(vm.vmid, vm.name, vm.status) for vm in rows[0].vms] == [
            (503, "Client", "running")
        ]

    def test_teacher_broadcasts_own_vm_to_own_class(self, db: Session) -> None:
        teacher = _user(db, UserRole.teacher)
        teaching_class = _class(db, teacher)
        _resource(db, teacher, 601)
        classroom_service.require_can_broadcast_class(
            db, teacher, teaching_class.id, 601
        )

    def test_missing_class_raises_not_found(self, db: Session) -> None:
        teacher = _user(db, UserRole.teacher)
        _resource(db, teacher, 602)
        with pytest.raises(NotFoundError):
            classroom_service.require_can_broadcast_class(
                db, teacher, uuid.uuid4(), 602
            )


class _StubManager:
    def __init__(self, sessions: list[ClassroomSession]) -> None:
        self._sessions = sessions

    def find_broadcast_for_classes(
        self, class_ids: set[uuid.UUID]
    ) -> ClassroomSession | None:
        return next(
            (
                session
                for session in self._sessions
                if session.mode is SessionMode.broadcast
                and session.class_id in class_ids
            ),
            None,
        )


def _broadcast_session(class_id: uuid.UUID, started_by: uuid.UUID) -> ClassroomSession:
    return ClassroomSession(
        id=uuid.uuid4().hex,
        vmid=700,
        mode=SessionMode.broadcast,
        class_id=class_id,
        started_by=started_by,
        controller_user_id=None,
        subscriber_count=0,
    )


def test_student_sees_broadcast_in_enrolled_class(db: Session) -> None:
    teacher = _user(db, UserRole.teacher)
    student = _user(db, UserRole.student)
    teaching_class = _class(db, teacher, student)
    live = _broadcast_session(teaching_class.id, teacher.id)

    found = classroom_service.get_live_for_user(
        db, student, manager=_StubManager([live])
    )

    assert found is not None and found.id == live.id


def test_archived_class_no_longer_counts_as_mine(db: Session) -> None:
    """課都結束了，舊班級不該再收到直播事件。"""
    teacher = _user(db, UserRole.teacher)
    student = _user(db, UserRole.student)
    teaching_class = _class(db, teacher, student)
    teaching_class.status = TeachingClassStatus.archived
    db.add(teaching_class)
    db.commit()

    assert classroom_service.get_class_ids_of_user(db, student.id) == set()
    assert classroom_service.get_class_ids_of_user(db, teacher.id) == set()


def _monitor_session(class_id: uuid.UUID, started_by: uuid.UUID) -> ClassroomSession:
    return ClassroomSession(
        id=uuid.uuid4().hex,
        vmid=701,
        mode=SessionMode.monitor,
        class_id=class_id,
        started_by=started_by,
        controller_user_id=None,
        subscriber_count=0,
    )


class _StubSessionManager:
    def __init__(self, live: ClassroomSession) -> None:
        self.live = live
        self.controllers: list[uuid.UUID | None] = []

    def get_session(self, session_id: str) -> ClassroomSession | None:
        return self.live if session_id == self.live.id else None

    async def set_controller(
        self, session_id: str, user_id: uuid.UUID | None
    ) -> None:
        self.controllers.append(user_id)


async def test_control_is_refused_once_the_class_changed_hands(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接管權限要每次重問：session 開著時班級可能已經換人帶。"""
    teacher = _user(db, UserRole.teacher)
    successor = _user(db, UserRole.teacher)
    teaching_class = _class(db, teacher)
    live = _monitor_session(teaching_class.id, teacher.id)
    manager = _StubSessionManager(live)
    monkeypatch.setattr(classroom_service, "vnc_session_manager", manager)

    teaching_class.owner_id = successor.id  # 班級轉給別的老師
    db.add(teaching_class)
    db.commit()

    with pytest.raises(PermissionDeniedError):
        await classroom_service.set_control(db, teacher, live.id, "take")
    with pytest.raises(PermissionDeniedError):
        await classroom_service.stop_session(db, teacher, live.id)
    assert manager.controllers == []
