"""子網設定的防護：還有班級／快速練習預留時不能改 CIDR 或刪除；閘道 IP 撞到使用中位址回 409。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, time, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.exceptions import ConflictError
from app.models import (
    IpAllocation,
    QuickPracticeSession,
    SubnetConfig,
    TeachingClass,
    User,
)
from app.models.base import get_datetime_utc
from app.services.network import ip_management_service

BASE = {
    "cidr": "10.20.0.0/24",
    "gateway": "10.20.0.1",
    "bridge_name": "vmbr1",
    "gateway_vm_ip": "10.20.0.2",
}


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            ip_management_service.upsert_subnet_config(db, **BASE)
            yield db
    finally:
        engine.dispose()


def _add_class(db: Session, name: str, code: str) -> TeachingClass:
    teaching_class = TeachingClass(
        owner_id=uuid.uuid4(),
        name=name,
        code=code,
        term="2026-fall",
        start_date=date(2026, 9, 1),
        end_date=date(2027, 1, 31),
        weekday=0,
        start_time=time(9, 0),
        end_time=time(12, 0),
    )
    db.add(teaching_class)
    db.commit()
    return teaching_class


def _reserve_for_class(db: Session, teaching_class: TeachingClass) -> list[str]:
    reserved = ip_management_service.reserve_ips(
        db,
        teaching_class_id=teaching_class.id,
        reservation_keys=[f"{teaching_class.id}:web:{uuid.uuid4()}"],
    )
    db.commit()
    return list(reserved.values())


def _reserve_for_quick_practice(db: Session) -> QuickPracticeSession:
    user = User(email=f"qp-{uuid.uuid4().hex[:8]}@campus.edu", hashed_password="x")
    db.add(user)
    db.commit()
    practice = QuickPracticeSession(
        user_id=user.id,
        environment_version_id=uuid.uuid4(),
        expires_at=get_datetime_utc() + timedelta(hours=2),
    )
    db.add(practice)
    db.commit()
    ip_management_service.reserve_ips(
        db,
        teaching_class_id=None,
        reservation_keys=[f"quick:{practice.id}:web"],
    )
    db.commit()
    return practice


# ── B4：預留 IP 會擋下改 CIDR 與刪除子網設定，並列出是哪個班級 ─────────────


def test_cidr_change_blocked_by_class_reservation_lists_the_class(
    session: Session,
) -> None:
    _reserve_for_class(session, _add_class(session, "網路實務", "NET101"))

    with pytest.raises(ConflictError) as excinfo:
        ip_management_service.upsert_subnet_config(
            session, **{**BASE, "cidr": "10.30.0.0/24",
                        "gateway": "10.30.0.1", "gateway_vm_ip": "10.30.0.2"}
        )

    assert "網路實務" in str(excinfo.value)
    assert "NET101" in str(excinfo.value)
    session.rollback()
    assert session.get(SubnetConfig, 1).cidr == "10.20.0.0/24"


def test_cidr_change_blocked_by_quick_practice_reservation_lists_the_session(
    session: Session,
) -> None:
    practice = _reserve_for_quick_practice(session)

    with pytest.raises(ConflictError) as excinfo:
        ip_management_service.upsert_subnet_config(
            session, **{**BASE, "cidr": "10.30.0.0/24",
                        "gateway": "10.30.0.1", "gateway_vm_ip": "10.30.0.2"}
        )

    assert str(practice.id) in str(excinfo.value)


def test_cidr_change_lists_every_class_holding_reservations_once(
    session: Session,
) -> None:
    first = _add_class(session, "作業系統", "OS201")
    second = _add_class(session, "資料庫", "DB301")
    _reserve_for_class(session, first)
    _reserve_for_class(session, first)
    _reserve_for_class(session, second)

    with pytest.raises(ConflictError) as excinfo:
        ip_management_service.upsert_subnet_config(
            session, **{**BASE, "cidr": "10.30.0.0/24",
                        "gateway": "10.30.0.1", "gateway_vm_ip": "10.30.0.2"}
        )

    message = str(excinfo.value)
    assert message.count("作業系統") == 1
    assert "資料庫" in message


def test_same_cidr_update_still_allowed_with_reservations(session: Session) -> None:
    _reserve_for_class(session, _add_class(session, "網路實務", "NET101"))

    config = ip_management_service.upsert_subnet_config(
        session, **{**BASE, "bridge_name": "vmbr9"}
    )

    assert config.bridge_name == "vmbr9"


def test_cidr_change_allowed_once_reservations_are_released(
    session: Session,
) -> None:
    teaching_class = _add_class(session, "網路實務", "NET101")
    _reserve_for_class(session, teaching_class)
    ip_management_service.release_class_reservations(session, teaching_class.id)
    session.commit()

    config = ip_management_service.upsert_subnet_config(
        session, **{**BASE, "cidr": "10.30.0.0/24",
                    "gateway": "10.30.0.1", "gateway_vm_ip": "10.30.0.2"}
    )

    assert config.cidr == "10.30.0.0/24"


def test_delete_subnet_blocked_by_reservation_lists_the_class(
    session: Session,
) -> None:
    reserved = _reserve_for_class(session, _add_class(session, "網路實務", "NET101"))

    with pytest.raises(ConflictError) as excinfo:
        ip_management_service.delete_subnet_config(session)

    assert "網路實務" in str(excinfo.value)
    session.rollback()
    assert session.get(SubnetConfig, 1) is not None
    remaining = session.exec(
        select(IpAllocation.ip_address).where(IpAllocation.ip_address.in_(reserved))  # type: ignore[attr-defined]
    ).all()
    assert sorted(remaining) == sorted(reserved)


def test_delete_subnet_without_reservations_still_works(session: Session) -> None:
    ip_management_service.delete_subnet_config(session)

    assert session.get(SubnetConfig, 1) is None


# ── B5：改閘道 IP 撞到使用中的位址時回 409 並說是誰在用 ─────────────────────


def test_gateway_change_onto_vm_ip_returns_conflict_naming_the_vmid(
    session: Session,
) -> None:
    vm_ip = ip_management_service.allocate_ip(session, 321, "vm")
    session.commit()

    with pytest.raises(ConflictError) as excinfo:
        ip_management_service.upsert_subnet_config(
            session, **{**BASE, "gateway": vm_ip}
        )

    assert excinfo.value.status_code == 409
    assert "321" in str(excinfo.value)
    session.rollback()
    config = session.get(SubnetConfig, 1)
    assert config.gateway == "10.20.0.1"
    row = session.exec(
        select(IpAllocation).where(IpAllocation.ip_address == vm_ip)
    ).one()
    assert row.vmid == 321


def test_gateway_vm_ip_change_onto_class_reservation_names_the_class(
    session: Session,
) -> None:
    [reserved_ip] = _reserve_for_class(
        session, _add_class(session, "網路實務", "NET101")
    )

    with pytest.raises(ConflictError) as excinfo:
        ip_management_service.upsert_subnet_config(
            session, **{**BASE, "gateway_vm_ip": reserved_ip}
        )

    assert "網路實務" in str(excinfo.value)


def test_swapping_gateway_and_gateway_vm_ip_is_not_a_conflict(
    session: Session,
) -> None:
    config = ip_management_service.upsert_subnet_config(
        session, **{**BASE, "gateway": "10.20.0.2", "gateway_vm_ip": "10.20.0.1"}
    )

    assert (config.gateway, config.gateway_vm_ip) == ("10.20.0.2", "10.20.0.1")
    purposes = dict(
        session.exec(
            select(IpAllocation.ip_address, IpAllocation.purpose).where(
                IpAllocation.purpose.in_(["subnet_gateway", "gateway_vm"])  # type: ignore[attr-defined]
            )
        ).all()
    )
    assert purposes == {"10.20.0.2": "subnet_gateway", "10.20.0.1": "gateway_vm"}


def test_gateway_change_to_free_ip_succeeds(session: Session) -> None:
    ip_management_service.allocate_ip(session, 321, "vm")
    session.commit()

    config = ip_management_service.upsert_subnet_config(
        session, **{**BASE, "gateway": "10.20.0.254"}
    )

    assert config.gateway == "10.20.0.254"
