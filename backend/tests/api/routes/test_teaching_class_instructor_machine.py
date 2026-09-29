"""班級擁有者也能拿一套和學生相同的機器。

老師的那一列是 status=instructor 的成員：建機、容量、拓樸都照學生的流程走，
但它永遠屬於班級擁有者（管理員代操作也一樣），而且和名單一樣在送出後鎖定。
"""

import uuid
from datetime import date, time
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.routes import teaching_classes as routes
from app.exceptions import BadRequestError
from app.models import (
    INSTRUCTOR_ENROLLMENT_STATUS,
    TeachingClass,
    TeachingClassStatus,
    TeachingClassStudent,
)

TEACHER = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, role="teacher")
ADMIN = SimpleNamespace(id=uuid.uuid4(), is_superuser=True, role="admin")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            TeachingClass.__table__,  # type: ignore[arg-type]
            TeachingClassStudent.__table__,  # type: ignore[arg-type]
        ],
    )
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def _skip_serialize(monkeypatch):
    monkeypatch.setattr(
        routes, "_serialize", lambda _session, item: {"id": str(item.id)}
    )


def _class(db: Session, *, status=TeachingClassStatus.planning) -> TeachingClass:
    item = TeachingClass(
        owner_id=TEACHER.id,
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


def _rows(db: Session, item: TeachingClass) -> list[TeachingClassStudent]:
    return list(
        db.exec(
            select(TeachingClassStudent).where(TeachingClassStudent.class_id == item.id)
        ).all()
    )


def _set(db: Session, item: TeachingClass, enabled: bool, user=TEACHER):
    return routes.set_instructor_machine(
        item.id, routes.InstructorMachineIn(enabled=enabled), db, user
    )


def test_enabling_enrolls_the_owner_as_instructor(db):
    item = _class(db)

    _set(db, item, True)

    (row,) = _rows(db, item)
    assert row.user_id == TEACHER.id
    assert row.status == INSTRUCTOR_ENROLLMENT_STATUS


def test_enabling_twice_keeps_a_single_row(db):
    item = _class(db)

    _set(db, item, True)
    _set(db, item, True)

    assert len(_rows(db, item)) == 1


def test_disabling_removes_only_the_instructor_row(db):
    item = _class(db)
    db.add(TeachingClassStudent(class_id=item.id, user_id=uuid.uuid4()))
    db.commit()
    _set(db, item, True)

    _set(db, item, False)

    (row,) = _rows(db, item)
    assert row.user_id != TEACHER.id
    assert row.status == "active"


def test_admin_enrolls_the_class_owner_not_themselves(db):
    item = _class(db)

    _set(db, item, True, user=ADMIN)

    (row,) = _rows(db, item)
    assert row.user_id == TEACHER.id


@pytest.mark.parametrize(
    "status",
    [
        TeachingClassStatus.pending_review,
        TeachingClassStatus.provisioning,
        TeachingClassStatus.active,
    ],
)
def test_locked_once_the_class_is_submitted(db, status):
    item = _class(db, status=status)

    with pytest.raises(BadRequestError):
        _set(db, item, True)

    assert _rows(db, item) == []
