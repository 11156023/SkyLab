"""reserve_ips 的容量判斷與大網段效能回歸測試。

reserve_ips 以前會先把整個 CIDR 的空位全部展開成 list；/8 會建出
一千多萬個字串。改成惰性走訪後，空位足夠時找到就停，空位不足時
走完整個網段，len(available) 才會是真實剩餘數。
"""

import ipaddress
import time

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.exceptions import ConflictError
from app.models import IpAllocation, SubnetConfig
from app.services.network import ip_management_service


@pytest.fixture
def ip_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _subnet(session: Session, cidr: str, gateway: str, gateway_vm_ip: str) -> None:
    session.add(
        SubnetConfig(
            id=1,
            cidr=cidr,
            gateway=gateway,
            bridge_name="vmbr0",
            gateway_vm_ip=gateway_vm_ip,
        )
    )


def _fill_slash24(session: Session, used: int) -> None:
    """/24 共 254 個 host；前 ``used`` 個標成已分配。"""
    _subnet(session, "10.40.0.0/24", "10.40.0.1", "10.40.0.2")
    hosts = list(ipaddress.IPv4Network("10.40.0.0/24").hosts())
    session.add_all(
        IpAllocation(ip_address=str(ip), purpose="vm", vmid=1000 + i)
        for i, ip in enumerate(hosts[:used])
    )
    session.commit()


def _keys(count: int) -> list[str]:
    return [f"quick:capacity:node{i}" for i in range(count)]


def test_reserve_takes_the_remaining_free_addresses(ip_db: Session) -> None:
    _fill_slash24(ip_db, used=250)

    result = ip_management_service.reserve_ips(
        ip_db, teaching_class_id=None, reservation_keys=_keys(4)
    )

    assert set(result) == set(_keys(4))
    assert sorted(result.values()) == sorted(
        ["10.40.0.251", "10.40.0.252", "10.40.0.253", "10.40.0.254"]
    )


def test_reserve_a_subset_of_the_free_addresses(ip_db: Session) -> None:
    _fill_slash24(ip_db, used=250)

    result = ip_management_service.reserve_ips(
        ip_db, teaching_class_id=None, reservation_keys=_keys(3)
    )

    assert len(set(result.values())) == 3


def test_reserve_more_than_free_raises_and_reserves_nothing(ip_db: Session) -> None:
    _fill_slash24(ip_db, used=250)

    with pytest.raises(ConflictError):
        ip_management_service.reserve_ips(
            ip_db, teaching_class_id=None, reservation_keys=_keys(10)
        )

    reserved = ip_db.exec(
        select(IpAllocation).where(
            IpAllocation.reservation_key.is_not(None)  # type: ignore[union-attr]
        )
    ).all()
    assert reserved == []


def test_reserve_on_a_huge_subnet_does_not_enumerate_every_host(
    ip_db: Session,
) -> None:
    _subnet(ip_db, "10.0.0.0/8", "10.0.0.1", "10.0.0.2")
    ip_db.add_all(
        [
            IpAllocation(ip_address="10.0.0.1", purpose="subnet_gateway"),
            IpAllocation(ip_address="10.0.0.2", purpose="gateway_vm"),
        ]
    )
    ip_db.commit()

    started = time.perf_counter()
    result = ip_management_service.reserve_ips(
        ip_db, teaching_class_id=None, reservation_keys=_keys(5)
    )
    elapsed = time.perf_counter() - started

    assert sorted(result.values()) == [f"10.0.0.{n}" for n in range(3, 8)]
    # 展開 /8 的一千六百多萬個 host 要好幾秒；惰性走訪只看前幾個。
    assert elapsed < 1.0
