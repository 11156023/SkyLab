"""app.repositories.proxmox_storage 的同步、共享節點與設定更新覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.proxmox_storage import ProxmoxStorage
from app.repositories import proxmox_storage as repo


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _storage(session: Session, node: str, name: str, **kw) -> ProxmoxStorage:
    s = ProxmoxStorage(node_name=node, storage=name, **kw)
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def test_get_all_and_get_storage(session: Session) -> None:
    b = _storage(session, "pve2", "local")
    a = _storage(session, "pve1", "zfs")
    c = _storage(session, "pve1", "local")

    assert [s.id for s in repo.get_all_storages(session)] == [c.id, a.id, b.id]
    assert repo.get_storage(session, a.id).storage == "zfs"
    assert repo.get_storage(session, 999999) is None


def test_get_shared_storage_peers(session: Session) -> None:
    assert repo.get_shared_storage_peers(session, "ceph", set()) == []
    _storage(session, "pve2", "ceph", is_shared=True)
    _storage(session, "pve1", "ceph", is_shared=True)
    _storage(session, "pve3", "ceph", is_shared=True)
    _storage(session, "pve1", "local", is_shared=False)

    peers = repo.get_shared_storage_peers(session, "ceph", {"pve1", "pve2"})
    assert [p.node_name for p in peers] == ["pve1", "pve2"]


def test_upsert_storages_updates_creates_and_deletes(session: Session) -> None:
    kept = _storage(
        session,
        "pve1",
        "local",
        enabled=False,
        speed_tier="hdd",
        user_priority=9,
        total_gb=1.0,
    )
    _storage(session, "pve1", "stale")
    other = _storage(session, "other", "outside")

    result = repo.upsert_storages(
        session,
        [
            {
                "node_name": "pve1",
                "storage": "local",
                "storage_type": "dir",
                "total_gb": 100.0,
                "used_gb": 10.0,
                "avail_gb": 90.0,
                "can_vm": True,
                "can_lxc": True,
                "can_iso": True,
                "can_backup": True,
                "is_shared": False,
                "active": False,
            },
            {"node_name": "pve1", "storage": "new-vm", "can_vm": True},
            {"node_name": "pve1", "storage": "new-iso", "can_iso": True},
        ],
        scope_node_names={"pve1"},
    )

    by_name = {s.storage: s for s in result}
    assert set(by_name) == {"local", "new-vm", "new-iso"}
    local = by_name["local"]
    assert local.id == kept.id
    assert local.storage_type == "dir"
    assert local.total_gb == 100.0
    assert local.active is False
    # user settings are preserved on update
    assert local.enabled is False
    assert local.speed_tier == "hdd"
    assert local.user_priority == 9
    assert by_name["new-vm"].enabled is True
    assert by_name["new-iso"].enabled is False
    assert by_name["new-iso"].speed_tier == "unknown"
    assert by_name["new-iso"].user_priority == 5

    names = {(s.node_name, s.storage) for s in repo.get_all_storages(session)}
    assert ("pve1", "stale") not in names
    # outside the scope is untouched
    assert (other.node_name, other.storage) in names


def test_update_storage_settings_missing_returns_none(session: Session) -> None:
    assert repo.update_storage_settings(session, 424242, True, "ssd", 1) is None


def test_update_storage_settings_appends_self_when_not_in_peers(
    session: Session,
) -> None:
    s1 = _storage(session, "pve1", "ceph", is_shared=True)
    _storage(session, "pve2", "ceph", is_shared=True)

    target, nodes = repo.update_storage_settings(
        session, s1.id, True, "hdd", 3, peer_node_names={"pve2"}
    )

    assert target.id == s1.id
    assert nodes == ["pve1", "pve2"]
