"""子網設定的主系統 IP：選填、在網段內、保留不配發、不可當發佈目標。"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.exceptions import BadRequestError, ConflictError
from app.models import IpAllocation
from app.schemas.ip_management import SubnetConfigCreate
from app.services.network import ip_management_service as svc
from app.services.network.publish_target_policy import assert_publishable_vm_ip
from app.services.system import setup_service

BASE = {
    "cidr": "10.60.0.0/29",
    "gateway": "10.60.0.1",
    "bridge_name": "vmbr1",
    "gateway_vm_ip": "10.60.0.2",
}


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _platform_rows(db: Session) -> list[IpAllocation]:
    return list(
        db.exec(
            select(IpAllocation).where(IpAllocation.purpose == "platform_host")
        ).all()
    )


def test_platform_ip_is_optional(db: Session) -> None:
    config = svc.upsert_subnet_config(db, **BASE)

    assert config.platform_ip is None
    assert _platform_rows(db) == []


def test_platform_ip_is_reserved_and_skipped_by_allocation(db: Session) -> None:
    config = svc.upsert_subnet_config(db, **BASE, platform_ip="10.60.0.3")

    assert config.platform_ip == "10.60.0.3"
    assert [row.ip_address for row in _platform_rows(db)] == ["10.60.0.3"]
    allocated = [svc.allocate_ip(db, 600 + index, "vm") for index in range(3)]
    assert allocated == ["10.60.0.4", "10.60.0.5", "10.60.0.6"]


def test_clearing_platform_ip_releases_reservation(db: Session) -> None:
    svc.upsert_subnet_config(db, **BASE, platform_ip="10.60.0.3")

    config = svc.upsert_subnet_config(db, **BASE, platform_ip=None)

    assert config.platform_ip is None
    assert _platform_rows(db) == []


def test_changing_platform_ip_moves_reservation(db: Session) -> None:
    svc.upsert_subnet_config(db, **BASE, platform_ip="10.60.0.3")

    svc.upsert_subnet_config(db, **BASE, platform_ip="10.60.0.6")

    assert [row.ip_address for row in _platform_rows(db)] == ["10.60.0.6"]


def test_platform_ip_must_be_inside_subnet(db: Session) -> None:
    with pytest.raises(BadRequestError):
        svc.upsert_subnet_config(db, **BASE, platform_ip="10.61.0.3")


@pytest.mark.parametrize("ip", [BASE["gateway"], BASE["gateway_vm_ip"]])
def test_platform_ip_must_differ_from_gateway_ips(db: Session, ip: str) -> None:
    with pytest.raises(BadRequestError):
        svc.upsert_subnet_config(db, **BASE, platform_ip=ip)


def test_platform_ip_cannot_take_machine_ip(db: Session) -> None:
    svc.upsert_subnet_config(db, **BASE)
    db.add(IpAllocation(ip_address="10.60.0.4", purpose="vm", vmid=100))
    db.commit()

    with pytest.raises(ConflictError):
        svc.upsert_subnet_config(db, **BASE, platform_ip="10.60.0.4")


def test_schema_normalizes_blank_and_rejects_invalid_platform_ip() -> None:
    assert SubnetConfigCreate(**BASE, platform_ip="  ").platform_ip is None
    assert SubnetConfigCreate(**BASE, platform_ip=" 10.60.0.3 ").platform_ip == (
        "10.60.0.3"
    )
    with pytest.raises(ValidationError):
        SubnetConfigCreate(**BASE, platform_ip="10.60.0.300")


def test_platform_ip_cannot_be_published(monkeypatch: pytest.MonkeyPatch) -> None:
    config = SimpleNamespace(
        cidr="10.60.0.0/24",
        gateway="10.60.0.1",
        gateway_vm_ip="10.60.0.2",
        platform_ip="10.60.0.3",
    )
    monkeypatch.setattr(svc, "get_subnet_config", lambda _: config)
    session = SimpleNamespace(exec=lambda _: SimpleNamespace(all=lambda: []))

    assert_publishable_vm_ip(session, "10.60.0.9")
    with pytest.raises(BadRequestError):
        assert_publishable_vm_ip(session, "10.60.0.3")


def test_setup_wizard_subnet_step_saves_platform_ip(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        setup_service,
        "ensure_setup_open",
        lambda *, session: SimpleNamespace(admin_user_id=None),
    )
    monkeypatch.setattr(setup_service.audit_service, "log_action", lambda **_: None)

    result = setup_service.configure_subnet(
        session=db, data=SubnetConfigCreate(**BASE, platform_ip="10.60.0.3")
    )

    assert result.platform_ip == "10.60.0.3"
    assert [row.ip_address for row in _platform_rows(db)] == ["10.60.0.3"]
