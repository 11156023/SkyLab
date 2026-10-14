"""app.repositories.proxmox_config 的 singleton upsert 覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.proxmox_config import ProxmoxConfig
from app.repositories import proxmox_config as repo


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_upsert_creates_then_updates(session: Session) -> None:
    assert repo.get_proxmox_config(session) is None

    created = repo.upsert_proxmox_config(session, cpu_overcommit_ratio=1.5)
    assert created.id == 1
    assert created.cpu_overcommit_ratio == 1.5
    # 沒帶的欄位維持 model 預設值
    assert created.disk_overcommit_ratio == ProxmoxConfig().disk_overcommit_ratio

    repo.upsert_proxmox_config(session, cpu_overcommit_ratio=2.5)
    stored = repo.get_proxmox_config(session)
    assert stored is not None and stored.cpu_overcommit_ratio == 2.5


@pytest.mark.parametrize("bad", ["no_such_field", "id", "updated_at"])
def test_upsert_rejects_unknown_fields(session: Session, bad: str) -> None:
    with pytest.raises(ValueError, match="unknown proxmox_config field"):
        repo.upsert_proxmox_config(session, **{bad: 1})
