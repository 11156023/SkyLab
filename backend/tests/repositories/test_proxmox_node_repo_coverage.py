"""app.repositories.proxmox_node 的同步、衝突與更新分支覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.proxmox_connection import ProxmoxConnection
from app.models.proxmox_node import ProxmoxNode
from app.repositories import proxmox_node as repo


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _conn(session: Session, name: str) -> ProxmoxConnection:
    conn = ProxmoxConnection(
        name=name,
        host="10.0.0.1",
        port=8006,
        user="root@pam",
        encrypted_password="x",
        verify_ssl=False,
        api_timeout=30,
        pool_name="SkyLab",
        iso_storage="local",
        data_storage="local-lvm",
        task_check_interval=2,
    )
    session.add(conn)
    session.commit()
    session.refresh(conn)
    return conn


def test_connection_names_only_list_owned_nodes(session: Session) -> None:
    conn = _conn(session, "機房A")
    session.add(ProxmoxNode(connection_id=conn.id, name="pve1", host="h1"))
    session.add(ProxmoxNode(connection_id=None, name="legacy", host="h2"))
    session.commit()

    assert repo.get_node_connection_names(session) == {"pve1": "機房A"}
    assert repo.get_node_connection_map(session) == {
        "pve1": (conn.id, "機房A"),
        "legacy": (None, None),
    }


def test_upsert_without_connection_only_touches_unowned_nodes(
    session: Session,
) -> None:
    conn = _conn(session, "A")
    session.add(ProxmoxNode(connection_id=conn.id, name="owned", host="h"))
    session.add(ProxmoxNode(connection_id=None, name="old", host="h"))
    session.commit()

    result = repo.upsert_nodes(
        session, [{"name": "fresh", "host": "10.0.0.9", "is_primary": True}]
    )
    assert [n.name for n in result] == ["fresh"]
    names = {n.name for n in repo.get_all_nodes(session)}
    # 未歸屬的 old 被當成本範圍消失的節點刪掉；其他連線的節點不受影響
    assert names == {"owned", "fresh"}


def test_upsert_duplicate_name_in_same_sync_reuses_pending_node(
    session: Session,
) -> None:
    conn = _conn(session, "A")
    result = repo.upsert_nodes(
        session,
        [
            {"name": "pve1", "host": "10.0.0.1"},
            {"name": "pve1", "host": "10.0.0.2", "port": 8443},
        ],
        connection_id=conn.id,
    )
    assert result[0] is result[1]
    assert result[0].host == "10.0.0.2"
    assert result[0].port == 8443


def test_upsert_refuses_to_claim_orphan_when_name_taken(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _conn(session, "A")
    b = _conn(session, "B")
    session.add(ProxmoxNode(connection_id=None, name="pve1", host="h"))
    session.commit()
    # 名稱有唯一約束，正常資料不會同時存在未歸屬與其他連線的同名節點；
    # 以替身模擬「認領前名稱已被其他連線佔走」的競態
    other = ProxmoxNode(id=999, connection_id=b.id, name="pve1", host="h")
    monkeypatch.setattr(repo, "get_node_by_name", lambda _s, _n: other)
    with pytest.raises(ValueError, match="無法認領未歸屬節點"):
        repo.upsert_nodes(session, [{"name": "pve1", "host": "h"}], connection_id=a.id)


def test_update_node_and_status(session: Session) -> None:
    node = ProxmoxNode(name="pve1", host="h")
    session.add(node)
    session.commit()
    session.refresh(node)

    assert repo.update_node(session, 12345, "h", 8006, 5) is None
    updated = repo.update_node(session, node.id, "new-host", 9000, 2)
    assert updated is not None
    assert (updated.host, updated.port, updated.priority) == ("new-host", 9000, 2)
    assert updated.enabled is True
    updated = repo.update_node(session, node.id, "new-host", 9000, 2, enabled=False)
    assert updated is not None and updated.enabled is False
    assert repo.get_disabled_node_names(session) == {"pve1"}

    repo.update_node_status(session, node.id, False)
    session.refresh(node)
    assert node.is_online is False
    assert node.last_checked is not None
    # 不存在的節點直接略過
    repo.update_node_status(session, 12345, True)
