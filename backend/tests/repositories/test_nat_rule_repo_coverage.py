"""app.repositories.nat_rule 的查詢與刪除分支覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.nat_rule import NatRule
from app.repositories import nat_rule as repo


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _rule(vmid: int, external: int, internal: int = 22, protocol: str = "tcp"):
    return NatRule(
        ssh_host="pve1",
        vmid=vmid,
        vm_ip="10.0.0.5",
        external_port=external,
        internal_port=internal,
        protocol=protocol,
    )


def test_queries(session: Session) -> None:
    r1 = repo.create_rule(session, _rule(100, 20000))
    repo.create_rule(session, _rule(100, 20001, internal=80))
    repo.create_rule(session, _rule(101, 20002, protocol="udp"))

    assert len(repo.list_rules(session)) == 3
    assert len(repo.list_rules_by_vmid(session, 100)) == 2
    assert repo.list_rules_by_vmids(session, []) == []
    assert len(repo.list_rules_by_vmids(session, [100, 101])) == 3
    found = repo.list_rules_by_vmid_and_port(session, 100, 22, "tcp")
    assert [r.id for r in found] == [r1.id]
    assert repo.is_external_port_taken(session, 20000, "tcp") is True
    assert repo.is_external_port_taken(session, 20000, "udp") is False
    assert repo.taken_external_ports(session, "tcp", 20000, 20001) == {20000, 20001}


def test_delete_rule_with_and_without_commit(session: Session) -> None:
    r1 = repo.create_rule(session, _rule(100, 20000))
    r2 = repo.create_rule(session, _rule(100, 20001))

    repo.delete_rule(session, r1, commit=False)
    session.rollback()
    assert len(repo.list_rules(session)) == 2

    repo.delete_rule(session, r2)
    assert [r.external_port for r in repo.list_rules(session)] == [20000]


def test_delete_rules(session: Session) -> None:
    rules = [
        repo.create_rule(session, _rule(100, 20000)),
        repo.create_rule(session, _rule(100, 20001)),
    ]
    assert repo.delete_rules(session, []) == []
    assert repo.delete_rules(session, rules[:1], commit=False) == rules[:1]
    session.rollback()
    assert len(repo.list_rules(session)) == 2
    assert repo.delete_rules(session, rules) == rules
    assert repo.list_rules(session) == []
