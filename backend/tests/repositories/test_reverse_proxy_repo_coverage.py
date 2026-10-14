"""app.repositories.reverse_proxy 的 CRUD 與網域衝突判斷覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.reverse_proxy_rule import ReverseProxyRule
from app.repositories import reverse_proxy as repo


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _rule(vmid: int, domain: str, port: int = 80) -> ReverseProxyRule:
    return ReverseProxyRule(
        vmid=vmid, vm_ip="10.0.0.5", domain=domain, internal_port=port
    )


def test_crud_and_queries(session: Session) -> None:
    a = repo.create_rule(session, _rule(100, "a.example.com"))
    repo.create_rule(session, _rule(100, "b.example.com", port=8080))
    repo.create_rule(session, _rule(101, "c.example.com"))

    assert len(repo.list_rules(session)) == 3
    assert repo.list_rules_by_vmids(session, []) == []
    assert len(repo.list_rules_by_vmids(session, [100, 101])) == 3
    assert repo.get_rule(session, a.id) is a

    assert repo.is_domain_taken(session, "a.example.com") is True
    assert repo.is_domain_taken(session, "a.example.com", exclude_rule_id=a.id) is False
    assert repo.is_domain_taken(session, "z.example.com") is False

    a.internal_port = 443
    assert repo.update_rule(session, a).internal_port == 443

    deleted = repo.delete_rules_by_vmid_and_port(session, 100, 8080)
    assert [r.domain for r in deleted] == ["b.example.com"]

    repo.delete_rule(session, a)
    assert repo.get_rule(session, a.id) is None

    deleted = repo.delete_rules_by_vmid(session, 101)
    assert [r.domain for r in deleted] == ["c.example.com"]
    assert repo.list_rules(session) == []
