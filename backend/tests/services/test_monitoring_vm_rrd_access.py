"""`/monitoring/vms/{vmid}/rrd` 與 `/resources/{vmid}/stats` 同一套權限。

擁有者、班級老師、被分享者、管理員看得到；其他人不行。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, time

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import PermissionDeniedError
from app.models import (
    Resource,
    ResourceShare,
    TeachingClass,
    TeachingClassStatus,
    User,
    UserRole,
)
from app.services.monitoring import monitoring_service

CLASS_VMID = 501
PERSONAL_VMID = 502


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,  # type: ignore[attr-defined]
            TeachingClass.__table__,  # type: ignore[attr-defined]
            Resource.__table__,  # type: ignore[attr-defined]
            ResourceShare.__table__,  # type: ignore[attr-defined]
        ],
    )
    monkeypatch.setattr(
        monitoring_service.proxmox_service,
        "find_resource",
        lambda vmid: {"vmid": vmid, "node": "pve1", "type": "qemu"},
    )
    monkeypatch.setattr(
        monitoring_service.proxmox_service,
        "get_rrd_data",
        lambda node, vmid, rtype, timeframe: [{"time": 1, "cpu": 0.5}],
    )
    with Session(engine) as session:
        yield session
    engine.dispose()


def _user(db: Session, role: UserRole) -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:12]}@example.com", hashed_password="x", role=role
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def people(db: Session) -> dict[str, User]:
    people = {
        "admin": _user(db, UserRole.admin),
        "teacher": _user(db, UserRole.teacher),
        "other_teacher": _user(db, UserRole.teacher),
        "student": _user(db, UserRole.student),
        "sharee": _user(db, UserRole.student),
        "stranger": _user(db, UserRole.student),
    }
    teaching_class = TeachingClass(
        owner_id=people["teacher"].id,
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
    db.add(
        Resource(
            vmid=CLASS_VMID,
            user_id=people["student"].id,
            teaching_class_id=teaching_class.id,
            allocation_scope="teaching_class",
            environment_type="teaching_class",
            created_at=datetime.now(UTC),
        )
    )
    db.add(
        Resource(
            vmid=PERSONAL_VMID,
            user_id=people["student"].id,
            environment_type="vm",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()
    db.add(ResourceShare(resource_vmid=PERSONAL_VMID, user_id=people["sharee"].id))
    db.commit()
    return people


def _rrd(db: Session, user: User, vmid: int) -> list:
    return monitoring_service.get_vm_rrd(
        session=db, vmid=vmid, timeframe="hour", user=user
    )


@pytest.mark.parametrize("who", ["teacher", "student", "admin"])
def test_class_machine_rrd_allowed(db: Session, people: dict[str, User], who: str) -> None:
    assert _rrd(db, people[who], CLASS_VMID) == [{"time": 1, "cpu": 0.5}]


@pytest.mark.parametrize("who", ["other_teacher", "stranger", "sharee"])
def test_class_machine_rrd_denied(db: Session, people: dict[str, User], who: str) -> None:
    with pytest.raises(PermissionDeniedError):
        _rrd(db, people[who], CLASS_VMID)


@pytest.mark.parametrize("who", ["student", "sharee", "admin"])
def test_personal_machine_rrd_allowed(
    db: Session, people: dict[str, User], who: str
) -> None:
    assert _rrd(db, people[who], PERSONAL_VMID) == [{"time": 1, "cpu": 0.5}]


@pytest.mark.parametrize("who", ["teacher", "stranger"])
def test_personal_machine_rrd_denied(
    db: Session, people: dict[str, User], who: str
) -> None:
    with pytest.raises(PermissionDeniedError):
        _rrd(db, people[who], PERSONAL_VMID)
