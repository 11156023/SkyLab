"""wireguard_peer／firewall_layout／user repository 的剩餘分支覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import NotFoundError
from app.models.wireguard_peer import WireGuardPeer
from app.repositories import firewall_layout as layout_repo
from app.repositories import user as user_repo
from app.repositories import wireguard_peer as peer_repo
from app.schemas import UserUpdate


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


NOW = datetime(2099, 1, 1, tzinfo=UTC)


# ── wireguard_peer ─────────────────────────────────────────────────────────


def test_wireguard_peer_queries(session: Session) -> None:
    user_id = uuid.uuid4()
    active = peer_repo.save(
        session=session,
        peer=WireGuardPeer(
            user_id=user_id,
            device_id="laptop",
            public_key="pk-active",
            tunnel_ip="10.99.0.2",
            active=True,
            expires_at=NOW + timedelta(hours=1),
        ),
    )
    expired = peer_repo.save(
        session=session,
        peer=WireGuardPeer(
            user_id=user_id,
            device_id="phone",
            public_key="pk-expired",
            tunnel_ip="10.99.0.3",
            active=True,
            expires_at=NOW - timedelta(hours=1),
        ),
    )
    revoked = peer_repo.save(
        session=session,
        peer=WireGuardPeer(
            user_id=user_id,
            device_id="old",
            public_key="pk-revoked",
            tunnel_ip="10.99.0.4",
            active=False,
            revoked_at=NOW - timedelta(days=10),
        ),
    )

    found = peer_repo.get_by_user_device(
        session=session, user_id=user_id, device_id="laptop"
    )
    assert found is not None and found.id == active.id
    found = peer_repo.get_by_public_key(session=session, public_key="pk-expired")
    assert found is not None and found.id == expired.id
    assert peer_repo.list_tunnel_ips(session=session) == {
        "10.99.0.2",
        "10.99.0.3",
        "10.99.0.4",
    }
    assert [
        p.id for p in peer_repo.list_active_unexpired(session=session, now=NOW)
    ] == [active.id]
    assert [p.id for p in peer_repo.list_expired_active(session=session, now=NOW)] == [
        expired.id
    ]
    assert [
        p.id
        for p in peer_repo.list_revoked_before(
            session=session, cutoff=NOW - timedelta(days=1)
        )
    ] == [revoked.id]


# ── firewall_layout ────────────────────────────────────────────────────────


def test_firewall_layout_upsert_and_reads(session: Session) -> None:
    user_id = uuid.uuid4()
    layout_repo.upsert_layout_batch(
        session=session,
        user_id=user_id,
        nodes=[
            # gateway 一律不帶 vmid
            {"node_type": "gateway", "vmid": 5, "position_x": 1.0, "position_y": 2.0},
            # VM 節點沒有 vmid 或機器不存在 → 略過
            {"node_type": "vm", "position_x": 3.0, "position_y": 4.0},
            {"node_type": "vm", "vmid": 999, "position_x": 3.0, "position_y": 4.0},
        ],
    )
    layout = layout_repo.get_layout(session=session, user_id=user_id)
    assert len(layout) == 1
    assert layout[0].vmid is None and layout[0].position_x == 1.0

    layout_repo.upsert_layout_batch(
        session=session,
        user_id=user_id,
        nodes=[{"node_type": "gateway", "position_x": 7.0, "position_y": 8.0}],
    )
    node = layout_repo.get_node(
        session=session, user_id=user_id, vmid=None, node_type="gateway"
    )
    assert node is not None and (node.position_x, node.position_y) == (7.0, 8.0)
    assert len(layout_repo.get_layout(session=session, user_id=user_id)) == 1


# ── user ───────────────────────────────────────────────────────────────────


def test_update_user_rejects_deleted_user_without_session_get() -> None:
    # 只提供寫入介面的 Session-like adapter（沒有 get）也要擋下已刪除帳號
    adapter = SimpleNamespace(add=lambda _obj: None)
    deleted_user = SimpleNamespace(deleted_at=NOW)
    with pytest.raises(NotFoundError):
        user_repo.update_user(
            session=adapter,  # type: ignore[arg-type]
            db_user=deleted_user,  # type: ignore[arg-type]
            user_in=UserUpdate(full_name="x"),
        )
