"""app.repositories.proxmox_connection 的查詢、更新略過、刪除與解密失敗覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import AppError
from app.repositories import proxmox_connection as repo


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _create(session: Session, name: str, **overrides):
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
    }
    payload.update(overrides)
    return repo.create_connection(session, **payload)


def test_get_and_default_resolution(session: Session) -> None:
    assert repo.get_default_connection(session) is None

    b = _create(session, "B")
    a = _create(session, "A", enabled=False)
    assert repo.get_connection(session, b.id) is b
    assert repo.get_connection(session, 9999) is None
    # 沒有標記預設時退回第一筆啟用的連線
    assert repo.get_default_connection(session) is b
    assert [c.name for c in repo.get_all_connections(session)] == ["A", "B"]
    assert [c.name for c in repo.get_all_connections(session, enabled_only=True)] == [
        "B"
    ]

    c = _create(session, "C", is_default=True)
    assert repo.get_default_connection(session) is c
    assert a.is_default is False


def test_update_skips_unknown_and_none_fields(session: Session) -> None:
    conn = _create(session, "A")
    updated = repo.update_connection(
        session,
        conn.id,
        updates={"id": 42, "created_at": None, "host": None, "port": 9006},
    )
    assert updated is not None
    assert updated.id == conn.id
    assert updated.host == "10.0.0.10"
    assert updated.port == 9006


def test_delete_connection(session: Session) -> None:
    conn = _create(session, "A")
    assert repo.delete_connection(session, conn.id) is True
    assert repo.get_connection(session, conn.id) is None
    assert repo.delete_connection(session, conn.id) is False


def test_decrypted_password_and_failure(session: Session) -> None:
    conn = _create(session, "A")
    assert repo.get_decrypted_password(conn) == "secret"
    conn.encrypted_password = "not-a-fernet-token"
    with pytest.raises(AppError) as exc:
        repo.get_decrypted_password(conn)
    assert exc.value.status_code == 400
