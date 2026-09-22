"""PVE 連線／節點 repository：部分更新與未歸屬節點的認領（記憶體 SQLite，不碰共用庫）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.proxmox_node import ProxmoxNode
from app.repositories import proxmox_connection as conn_repo
from app.repositories import proxmox_node as node_repo


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _create(session: Session, name: str = "機房A", **overrides):
    payload = {
        "name": name,
        "host": "10.0.0.10",
        "port": 8006,
        "user": "root@pam",
        "password": "secret",
        "verify_ssl": False,
        "ca_cert": None,
        "api_timeout": 30,
        "pool_name": "SkyLab",
        "iso_storage": "local",
        "data_storage": "local-lvm",
        "task_check_interval": 2,
        "gateway_ip": "10.0.0.1",
        "local_subnet": "10.0.0.0/24",
        "default_node": "pve1",
        "enabled": True,
        "is_default": True,
    }
    payload.update(overrides)
    return conn_repo.create_connection(session, **payload)


# ── 部分更新 ────────────────────────────────────────────────────────────────


def test_update_connection_only_touches_provided_fields(db: Session) -> None:
    conn = _create(db)
    original_password = conn.encrypted_password

    updated = conn_repo.update_connection(db, conn.id, updates={"enabled": False})

    assert updated is not None
    assert updated.enabled is False
    # 沒帶到的欄位維持原值，不會被 schema 預設值洗掉
    assert updated.host == "10.0.0.10"
    assert updated.pool_name == "SkyLab"
    assert updated.gateway_ip == "10.0.0.1"
    assert updated.encrypted_password == original_password


def test_update_connection_password_and_ca_cert_semantics(db: Session) -> None:
    conn = _create(db, ca_cert="PEM")
    original_password = conn.encrypted_password

    # 帶 None＝不動
    conn_repo.update_connection(
        db, conn.id, updates={"password": None, "ca_cert": None}
    )
    assert conn.encrypted_password == original_password
    assert conn.ca_cert == "PEM"

    # 換密碼、清憑證
    conn_repo.update_connection(
        db, conn.id, updates={"password": "newpass", "ca_cert": ""}
    )
    assert conn.encrypted_password != original_password
    assert conn_repo.get_decrypted_password(conn) == "newpass"
    assert conn.ca_cert is None


def test_update_connection_clears_optional_text_fields(db: Session) -> None:
    conn = _create(db)

    conn_repo.update_connection(
        db, conn.id, updates={"gateway_ip": None, "default_node": ""}
    )

    assert conn.gateway_ip is None
    assert conn.default_node is None
    assert conn.local_subnet == "10.0.0.0/24"  # 沒帶到就不動


def test_update_connection_switches_default_flag(db: Session) -> None:
    first = _create(db, name="機房A")
    second = _create(db, name="機房B", is_default=False)

    conn_repo.update_connection(db, second.id, updates={"is_default": True})

    db.refresh(first)
    assert second.is_default is True
    assert first.is_default is False


def test_update_connection_missing_returns_none(db: Session) -> None:
    assert conn_repo.update_connection(db, 999, updates={"enabled": False}) is None


# ── 節點同步：未歸屬節點的認領 ───────────────────────────────────────────────


def _node_payload(name: str, host: str = "10.0.0.11") -> dict:
    return {"name": name, "host": host, "port": 8006, "is_primary": False}


def test_upsert_nodes_claims_orphan_node_and_keeps_priority(db: Session) -> None:
    conn = _create(db)
    db.add(ProxmoxNode(connection_id=None, name="pve1", host="10.0.0.11", priority=2))
    db.commit()

    saved = node_repo.upsert_nodes(db, [_node_payload("pve1")], connection_id=conn.id)

    assert [n.name for n in saved] == ["pve1"]
    assert saved[0].connection_id == conn.id
    assert saved[0].priority == 2  # 認領不重設使用者設定的優先級


def test_upsert_nodes_keeps_unclaimed_orphans(db: Session) -> None:
    conn = _create(db)
    db.add(ProxmoxNode(connection_id=None, name="old-pve", host="10.9.9.9"))
    db.commit()

    node_repo.upsert_nodes(db, [_node_payload("pve1")], connection_id=conn.id)

    # 別的機房留下的舊節點不屬於這個連線，不該被這次同步刪掉
    assert node_repo.get_node_by_name(db, "old-pve") is not None


def test_upsert_nodes_rejects_name_owned_by_another_connection(db: Session) -> None:
    first = _create(db, name="機房A")
    second = _create(db, name="機房B", host="10.0.1.10", is_default=False)
    node_repo.upsert_nodes(db, [_node_payload("pve1")], connection_id=first.id)

    with pytest.raises(ValueError, match="已被其他連線使用"):
        node_repo.upsert_nodes(db, [_node_payload("pve1")], connection_id=second.id)


def test_upsert_nodes_removes_only_own_disappeared_nodes(db: Session) -> None:
    conn = _create(db)
    node_repo.upsert_nodes(
        db,
        [_node_payload("pve1"), _node_payload("pve2", "10.0.0.12")],
        connection_id=conn.id,
    )

    node_repo.upsert_nodes(db, [_node_payload("pve1")], connection_id=conn.id)

    names = {n.name for n in node_repo.get_all_nodes(db, connection_id=conn.id)}
    assert names == {"pve1"}
