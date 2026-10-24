"""管理員維護的使用者群組：建立／改名／刪除、成員加入移出、CSV 名單，以及老師
從群組把學生匯入班級（只複製當下名單、只收學生）。"""

import io
import uuid
from datetime import date, time
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.routes import teaching_classes as class_routes
from app.api.routes import user_groups as group_routes
from app.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models import (
    AuditAction,
    AuditLog,
    TeachingClass,
    TeachingClassStatus,
    TeachingClassStudent,
    User,
    UserGroup,
    UserGroupMember,
    UserRole,
    get_datetime_utc,
)
from app.models.registration_policy import RegistrationPolicy
from app.schemas.teaching_class import StudentGroupImport
from app.schemas.user_group import UserGroupIn
from app.services.user import user_group_service as service

ADMIN = SimpleNamespace(id=uuid.uuid4(), is_superuser=True, role="admin")


@pytest.fixture
def db():
    # CSV 匯入會把 DB 工作丟到 worker thread：同一條記憶體連線要能跨執行緒用
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,  # type: ignore[attr-defined]
            UserGroup.__table__,  # type: ignore[attr-defined]
            UserGroupMember.__table__,  # type: ignore[attr-defined]
            AuditLog.__table__,  # type: ignore[attr-defined]
            TeachingClass.__table__,  # type: ignore[attr-defined]
            TeachingClassStudent.__table__,  # type: ignore[attr-defined]
            RegistrationPolicy.__table__,  # type: ignore[attr-defined]
        ],
    )
    with Session(engine) as session:
        yield session


def _user(db: Session, email: str, role: UserRole = UserRole.student, **kw) -> User:
    user = User(email=email, hashed_password="x", role=role, **kw)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _group(db: Session, name: str = "資管三甲"):
    return service.create_group(
        session=db, body=UserGroupIn(name=name), actor_id=ADMIN.id
    )


def _actions(db: Session) -> list[str]:
    return [row.action for row in db.exec(select(AuditLog))]


# --- 群組本身 ----------------------------------------------------------------


def test_create_rename_delete_and_audit(db: Session) -> None:
    group = _group(db)
    assert (group.name, group.member_count, group.student_count) == ("資管三甲", 0, 0)

    renamed = service.rename_group(
        session=db,
        group_id=group.id,
        body=UserGroupIn(name="資管三乙"),
        actor_id=ADMIN.id,
    )
    assert renamed.name == "資管三乙"

    service.delete_group(session=db, group_id=group.id, actor_id=ADMIN.id)
    assert db.get(UserGroup, group.id) is None
    assert _actions(db) == [
        AuditAction.user_group_create.value,
        AuditAction.user_group_update.value,
        AuditAction.user_group_delete.value,
    ]


def test_group_names_are_unique_ignoring_case(db: Session) -> None:
    _group(db, "CS-A")
    with pytest.raises(ConflictError):
        _group(db, "cs-a")
    other = _group(db, "CS-B")
    with pytest.raises(ConflictError):
        service.rename_group(
            session=db,
            group_id=other.id,
            body=UserGroupIn(name="Cs-A"),
            actor_id=ADMIN.id,
        )


def test_concurrent_names_differing_only_in_case_are_stopped_by_the_database(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 兩個請求同時送出時都會通過預查；這裡拿掉預查，模擬後到的那個
    _group(db, "CS-A")
    other = _group(db, "CS-B")
    monkeypatch.setattr(service, "_ensure_name_free", lambda *_args, **_kw: None)

    with pytest.raises(ConflictError):
        _group(db, "cs-a")
    with pytest.raises(ConflictError):
        service.rename_group(
            session=db,
            group_id=other.id,
            body=UserGroupIn(name="Cs-A"),
            actor_id=ADMIN.id,
        )
    names = sorted(g.name for g in db.exec(select(UserGroup)).all())
    assert names == ["CS-A", "CS-B"]


def test_renaming_to_the_same_name_is_a_no_op(db: Session) -> None:
    group = _group(db)
    service.rename_group(
        session=db,
        group_id=group.id,
        body=UserGroupIn(name="資管三甲"),
        actor_id=ADMIN.id,
    )
    assert _actions(db) == [AuditAction.user_group_create.value]


def test_blank_names_are_rejected() -> None:
    with pytest.raises(ValidationError):
        UserGroupIn(name="   ")
    assert UserGroupIn(name="  資管三甲 ").name == "資管三甲"


def test_unknown_group_is_not_found(db: Session) -> None:
    with pytest.raises(NotFoundError):
        service.list_members(session=db, group_id=uuid.uuid4())


# --- 成員 --------------------------------------------------------------------


def test_add_members_by_id_and_email(db: Session) -> None:
    group = _group(db)
    alice = _user(db, "alice@ntub.edu.tw")
    bob = _user(db, "Bob@ntub.edu.tw")
    teacher = _user(db, "t@ntub.edu.tw", UserRole.teacher)
    missing_id = uuid.uuid4()

    result = service.add_members(
        session=db,
        group_id=group.id,
        user_ids=[alice.id, missing_id, alice.id],
        emails=[" bob@ntub.edu.tw ", "ghost@ntub.edu.tw", "T@ntub.edu.tw", ""],
        actor_id=ADMIN.id,
    )

    assert result.added == 3
    assert result.not_found == [str(missing_id), "ghost@ntub.edu.tw"]
    assert (result.group.member_count, result.group.student_count) == (3, 2)
    members = service.list_members(session=db, group_id=group.id)
    assert [m.email for m in members.data] == [
        "Bob@ntub.edu.tw",
        "alice@ntub.edu.tw",
        "t@ntub.edu.tw",
    ]
    assert {m.id for m in members.data} == {alice.id, bob.id, teacher.id}

    again = service.add_members(
        session=db, group_id=group.id, user_ids=[alice.id], emails=[], actor_id=ADMIN.id
    )
    assert again.added == 0
    # 沒有實際加入任何人時不留稽核紀錄
    assert _actions(db).count(AuditAction.user_group_member_add.value) == 1


def test_soft_deleted_users_are_not_members(db: Session) -> None:
    group = _group(db)
    gone = _user(db, "gone@ntub.edu.tw")
    alive = _user(db, "alive@ntub.edu.tw")
    service.add_members(
        session=db,
        group_id=group.id,
        user_ids=[gone.id, alive.id],
        emails=[],
        actor_id=ADMIN.id,
    )
    gone.deleted_at = get_datetime_utc()
    gone.is_active = False
    db.add(gone)
    db.commit()

    listed = service.list_groups(session=db).data[0]
    assert (listed.member_count, listed.student_count) == (1, 1)
    assert [u.id for u in service.get_member_users(session=db, group_id=group.id)] == [
        alive.id
    ]
    result = service.add_members(
        session=db,
        group_id=group.id,
        user_ids=[],
        emails=["gone@ntub.edu.tw"],
        actor_id=ADMIN.id,
    )
    assert result.not_found == ["gone@ntub.edu.tw"]


def test_remove_members(db: Session) -> None:
    group = _group(db)
    alice = _user(db, "alice@ntub.edu.tw")
    bob = _user(db, "bob@ntub.edu.tw")
    service.add_members(
        session=db,
        group_id=group.id,
        user_ids=[alice.id, bob.id],
        emails=[],
        actor_id=ADMIN.id,
    )

    result = service.remove_members(
        session=db,
        group_id=group.id,
        user_ids=[alice.id, uuid.uuid4()],
        actor_id=ADMIN.id,
    )

    assert result.removed == 1
    assert result.group.member_count == 1
    assert AuditAction.user_group_member_remove.value in _actions(db)


def test_list_groups_counts_students_per_group(db: Session) -> None:
    a = _group(db, "A")
    b = _group(db, "B")
    _group(db, "C")
    stu = _user(db, "s@ntub.edu.tw")
    teacher = _user(db, "t@ntub.edu.tw", UserRole.teacher)
    service.add_members(
        session=db,
        group_id=a.id,
        user_ids=[stu.id, teacher.id],
        emails=[],
        actor_id=ADMIN.id,
    )
    service.add_members(
        session=db, group_id=b.id, user_ids=[stu.id], emails=[], actor_id=ADMIN.id
    )

    counts = {
        g.name: (g.member_count, g.student_count)
        for g in service.list_groups(session=db).data
    }

    assert counts == {"A": (2, 1), "B": (1, 1), "C": (0, 0)}


def test_deleting_a_group_drops_its_memberships(db: Session) -> None:
    group = _group(db)
    stu = _user(db, "s@ntub.edu.tw")
    service.add_members(
        session=db, group_id=group.id, user_ids=[stu.id], emails=[], actor_id=ADMIN.id
    )
    service.delete_group(session=db, group_id=group.id, actor_id=ADMIN.id)
    assert db.exec(select(UserGroupMember)).all() == []
    assert db.get(User, stu.id) is not None


@pytest.mark.asyncio
async def test_csv_import_uses_the_shared_roster_rules(db: Session) -> None:
    db.add(RegistrationPolicy(id=1, student_email_domain="school.edu.tw"))
    db.commit()
    group = _group(db)
    _user(db, "10000001@school.edu.tw")
    _user(db, "a@x.edu")
    raw = "學號\n10000001\na@x.edu\n99999999\n".encode("cp950")

    result = await group_routes.import_user_group_members(
        group.id, db, ADMIN, UploadFile(file=io.BytesIO(raw), filename="g.csv")
    )

    assert result.added == 2
    assert result.not_found == ["99999999@school.edu.tw"]


@pytest.mark.asyncio
async def test_csv_import_rejects_student_ids_without_a_domain(db: Session) -> None:
    group = _group(db)
    raw = b"10000001\na@x.edu\n10000002\n"

    with pytest.raises(BadRequestError) as caught:
        await group_routes.import_user_group_members(
            group.id, db, ADMIN, UploadFile(file=io.BytesIO(raw), filename="g.csv")
        )

    assert "10000001, 10000002" in caught.value.message
    assert db.exec(select(UserGroupMember)).all() == []


# --- 老師從群組匯入班級 ------------------------------------------------------


def _class(db: Session, owner_id: uuid.UUID, status=TeachingClassStatus.planning):
    item = TeachingClass(
        owner_id=owner_id,
        name="Linux",
        code=f"cls-{uuid.uuid4().hex[:8]}",
        term="115-1",
        start_date=date(2026, 9, 29),
        end_date=date(2026, 9, 29),
        weekday=1,
        start_time=time(0, 0),
        end_time=time(23, 59),
        status=status,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@pytest.fixture
def teacher(db: Session, monkeypatch):
    monkeypatch.setattr(
        class_routes, "_serialize", lambda _session, item: {"id": str(item.id)}
    )
    user = _user(db, "teacher@ntub.edu.tw", UserRole.teacher)
    return SimpleNamespace(id=user.id, is_superuser=False, role="teacher")


def _import(db: Session, item: TeachingClass, group_id: uuid.UUID, user):
    return class_routes.import_group_students(
        item.id, StudentGroupImport(group_id=group_id), db, user
    )


def _roster(db: Session, item: TeachingClass) -> set[uuid.UUID]:
    return {
        row.user_id
        for row in db.exec(
            select(TeachingClassStudent).where(TeachingClassStudent.class_id == item.id)
        )
    }


def test_import_group_adds_only_students_and_skips_existing(
    db: Session, teacher
) -> None:
    group = _group(db)
    s1 = _user(db, "s1@ntub.edu.tw")
    s2 = _user(db, "s2@ntub.edu.tw")
    ta = _user(db, "ta@ntub.edu.tw", UserRole.teacher)
    service.add_members(
        session=db,
        group_id=group.id,
        user_ids=[s1.id, s2.id, ta.id],
        emails=[],
        actor_id=ADMIN.id,
    )
    item = _class(db, teacher.id)
    db.add(TeachingClassStudent(class_id=item.id, user_id=s1.id))
    db.commit()

    result = _import(db, item, group.id, teacher)

    assert result["added"] == 1
    assert result["invalid_role"] == ["ta@ntub.edu.tw"]
    assert _roster(db, item) == {s1.id, s2.id}


def test_import_group_is_a_snapshot(db: Session, teacher) -> None:
    group = _group(db)
    s1 = _user(db, "s1@ntub.edu.tw")
    service.add_members(
        session=db, group_id=group.id, user_ids=[s1.id], emails=[], actor_id=ADMIN.id
    )
    item = _class(db, teacher.id)
    _import(db, item, group.id, teacher)

    later = _user(db, "later@ntub.edu.tw")
    service.add_members(
        session=db, group_id=group.id, user_ids=[later.id], emails=[], actor_id=ADMIN.id
    )
    service.delete_group(session=db, group_id=group.id, actor_id=ADMIN.id)

    assert _roster(db, item) == {s1.id}


def test_import_group_requires_planning_class_and_existing_group(
    db: Session, teacher
) -> None:
    group = _group(db)
    locked = _class(db, teacher.id, TeachingClassStatus.active)
    with pytest.raises(BadRequestError):
        _import(db, locked, group.id, teacher)
    with pytest.raises(NotFoundError):
        _import(db, _class(db, teacher.id), uuid.uuid4(), teacher)


# --- 權限：老師只能列出群組，其餘一律管理員 ----------------------------------


def _dependency_names(dependant) -> set[str]:
    names = set()
    for dependency in dependant.dependencies:
        names.add(getattr(dependency.call, "__name__", ""))
        names |= _dependency_names(dependency)
    return names


def test_only_listing_groups_is_open_to_teachers() -> None:
    from app.main import app
    from tests.utils.routes import iter_api_routes

    routes = {
        (path, method): _dependency_names(route.dependant)
        for path, route in iter_api_routes(app.routes)
        if path.startswith("/api/v1/user-groups")
        for method in route.methods or ()
    }

    teacher_list = routes.pop(("/api/v1/user-groups/", "GET"))
    assert "get_current_instructor_or_admin" in teacher_list
    assert len(routes) == 7
    assert all("get_current_active_superuser" in deps for deps in routes.values())
