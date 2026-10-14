"""app.repositories.resource_share 的 CRUD 覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.repositories import resource_share as repo


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_share_lifecycle(session: Session) -> None:
    alice = uuid.uuid4()
    bob = uuid.uuid4()
    committed = repo.create_share(
        session=session, vmid=100, user_id=alice, granted_by=None, permission="use"
    )
    flushed = repo.create_share(
        session=session,
        vmid=100,
        user_id=bob,
        granted_by=alice,
        permission="use",
        commit=False,
    )
    session.commit()

    assert {s.id for s in repo.list_shares_for_resource(session=session, vmid=100)} == {
        committed.id,
        flushed.id,
    }
    assert [s.id for s in repo.list_shares_for_user(session=session, user_id=bob)] == [
        flushed.id
    ]
    assert repo.get_share(session=session, vmid=100, user_id=alice) is committed
    assert repo.get_share_by_id(session=session, share_id=flushed.id) is flushed

    repo.delete_share(session=session, share=flushed, commit=False)
    assert repo.get_share(session=session, vmid=100, user_id=bob) is None
    repo.delete_share(session=session, share=committed)
    assert repo.list_shares_for_resource(session=session, vmid=100) == []
