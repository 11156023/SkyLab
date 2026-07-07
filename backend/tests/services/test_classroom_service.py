"""classroom_service 權限與 GET /live 過濾測試（in-memory sqlite）。"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import NotFoundError, PermissionDeniedError
from app.models import Group, GroupMember, Resource, User, UserRole
from app.services.classroom import classroom_service
from app.services.classroom.vnc_session_manager import ClassroomSession, SessionMode


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,  # type: ignore[arg-type]
            Group.__table__,  # type: ignore[arg-type]
            GroupMember.__table__,  # type: ignore[arg-type]
            Resource.__table__,  # type: ignore[arg-type]
        ],
    )
    with Session(engine) as session:
        yield session


def _user(db: Session, role: UserRole, *, superuser: bool = False) -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:12]}@test.local",
        hashed_password="x",
        role=role,
        is_superuser=superuser,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _group(db: Session, owner: User, *members: User) -> Group:
    group = Group(name=f"g-{uuid.uuid4().hex[:8]}", owner_id=owner.id)
    db.add(group)
    db.commit()
    db.refresh(group)
    for member in members:
        db.add(GroupMember(group_id=group.id, user_id=member.id))
    db.commit()
    return group


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


class TestRequireCanWatch:
    def test_teacher_can_watch_own_group_member_vm(self, db: Session) -> None:
        teacher = _user(db, UserRole.teacher)
        student = _user(db, UserRole.student)
        _group(db, teacher, student)
        _resource(db, student, 101)
        resource = classroom_service.require_can_watch(db, teacher, 101)
        assert resource.vmid == 101

    def test_teacher_cannot_watch_other_groups_vm(self, db: Session) -> None:
        teacher = _user(db, UserRole.teacher)
        other_teacher = _user(db, UserRole.teacher)
        student = _user(db, UserRole.student)
        _group(db, other_teacher, student)  # 學生在別的老師的群組
        _resource(db, student, 102)
        with pytest.raises(PermissionDeniedError):
            classroom_service.require_can_watch(db, teacher, 102)

    def test_student_cannot_watch(self, db: Session) -> None:
        student1 = _user(db, UserRole.student)
        student2 = _user(db, UserRole.student)
        _resource(db, student2, 103)
        with pytest.raises(PermissionDeniedError):
            classroom_service.require_can_watch(db, student1, 103)

    def test_admin_can_watch_any(self, db: Session) -> None:
        admin = _user(db, UserRole.admin, superuser=True)
        student = _user(db, UserRole.student)
        _resource(db, student, 104)
        assert classroom_service.require_can_watch(db, admin, 104).vmid == 104

    def test_missing_resource_raises_not_found(self, db: Session) -> None:
        admin = _user(db, UserRole.admin, superuser=True)
        with pytest.raises(NotFoundError):
            classroom_service.require_can_watch(db, admin, 999)


class TestRequireCanBroadcast:
    def test_teacher_broadcasts_own_vm_to_own_group(self, db: Session) -> None:
        teacher = _user(db, UserRole.teacher)
        group = _group(db, teacher)
        _resource(db, teacher, 201)
        classroom_service.require_can_broadcast(db, teacher, 201, group.id)

    def test_student_lacks_monitor_permission(self, db: Session) -> None:
        student = _user(db, UserRole.student)
        group = _group(db, student)
        _resource(db, student, 202)
        with pytest.raises(PermissionDeniedError):
            classroom_service.require_can_broadcast(db, student, 202, group.id)

    def test_teacher_cannot_broadcast_to_others_group(self, db: Session) -> None:
        teacher = _user(db, UserRole.teacher)
        other = _user(db, UserRole.teacher)
        group = _group(db, other)
        _resource(db, teacher, 203)
        with pytest.raises(PermissionDeniedError):
            classroom_service.require_can_broadcast(db, teacher, 203, group.id)

    def test_teacher_cannot_broadcast_someone_elses_vm(self, db: Session) -> None:
        teacher = _user(db, UserRole.teacher)
        student = _user(db, UserRole.student)
        group = _group(db, teacher, student)
        _resource(db, student, 204)
        with pytest.raises(PermissionDeniedError):
            classroom_service.require_can_broadcast(db, teacher, 204, group.id)

    def test_admin_bypasses_ownership(self, db: Session) -> None:
        admin = _user(db, UserRole.admin, superuser=True)
        teacher = _user(db, UserRole.teacher)
        group = _group(db, teacher)
        _resource(db, teacher, 205)
        classroom_service.require_can_broadcast(db, admin, 205, group.id)

    def test_missing_group_raises_not_found(self, db: Session) -> None:
        teacher = _user(db, UserRole.teacher)
        _resource(db, teacher, 206)
        with pytest.raises(NotFoundError):
            classroom_service.require_can_broadcast(db, teacher, 206, uuid.uuid4())

    def test_missing_vm_raises_not_found(self, db: Session) -> None:
        teacher = _user(db, UserRole.teacher)
        group = _group(db, teacher)
        with pytest.raises(NotFoundError):
            classroom_service.require_can_broadcast(db, teacher, 999, group.id)


class TestGroupIdsOfUser:
    def test_member_and_owned_groups(self, db: Session) -> None:
        teacher = _user(db, UserRole.teacher)
        student = _user(db, UserRole.student)
        g_owned = _group(db, teacher, student)
        other_teacher = _user(db, UserRole.teacher)
        g_member = _group(db, other_teacher, teacher)
        assert classroom_service.get_group_ids_of_user(db, teacher.id) == {
            g_owned.id,
            g_member.id,
        }
        assert classroom_service.get_group_ids_of_user(db, student.id) == {g_owned.id}
        assert classroom_service.get_group_ids_of_user(db, _user(db, UserRole.student).id) == set()


class _StubManager:
    def __init__(self, sessions: list[ClassroomSession]) -> None:
        self._sessions = sessions

    def find_broadcast_for_groups(
        self, group_ids: set[uuid.UUID]
    ) -> ClassroomSession | None:
        for session in self._sessions:
            if session.mode is SessionMode.broadcast and session.group_id in group_ids:
                return session
        return None


def _broadcast_session(group_id: uuid.UUID, started_by: uuid.UUID) -> ClassroomSession:
    return ClassroomSession(
        id=uuid.uuid4().hex,
        vmid=300,
        mode=SessionMode.broadcast,
        group_id=group_id,
        started_by=started_by,
        controller_user_id=None,
        subscriber_count=0,
    )


class TestGetLiveForUser:
    def test_student_sees_broadcast_in_own_group(self, db: Session) -> None:
        teacher = _user(db, UserRole.teacher)
        student = _user(db, UserRole.student)
        group = _group(db, teacher, student)
        live = _broadcast_session(group.id, teacher.id)
        manager = _StubManager([live])
        found = classroom_service.get_live_for_user(db, student, manager=manager)
        assert found is not None and found.id == live.id

    def test_student_not_in_group_sees_nothing(self, db: Session) -> None:
        teacher = _user(db, UserRole.teacher)
        outsider = _user(db, UserRole.student)
        group = _group(db, teacher)
        manager = _StubManager([_broadcast_session(group.id, teacher.id)])
        assert classroom_service.get_live_for_user(db, outsider, manager=manager) is None
