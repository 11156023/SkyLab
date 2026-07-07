"""Pair Mode 協作測試（mock VncSessionManager 與群組查詢）。"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import ConflictError, PermissionDeniedError
from app.services.classroom import pair_service
from app.services.classroom.vnc_session_manager import SessionMode

OWNER = SimpleNamespace(id=uuid.uuid4(), email="o@campus.edu")
INVITEE_ID = uuid.uuid4()


class _FakeManager:
    def __init__(self) -> None:
        self.started: list[dict] = []
        self.stopped: list[str] = []

    async def start_session(self, *, vmid, mode, group_id, started_by):
        self.started.append({"vmid": vmid, "mode": mode})
        return SimpleNamespace(
            id=f"sess-{vmid}", vmid=vmid, mode=mode,
            group_id=group_id, started_by=started_by,
            controller_user_id=None, subscriber_count=0,
        )

    async def stop_session(self, session_id, *, reason="ended"):
        self.stopped.append(session_id)


@pytest.fixture(autouse=True)
def clean_state(monkeypatch: pytest.MonkeyPatch):
    pair_service._sessions.clear()
    manager = _FakeManager()
    monkeypatch.setattr(pair_service, "vnc_session_manager", manager)
    monkeypatch.setattr(pair_service, "_share_group", lambda s, a, b: True)
    monkeypatch.setattr(
        pair_service,
        "_get_owned_resource",
        lambda session, user, vmid: SimpleNamespace(vmid=vmid, user_id=user.id),
    )
    monkeypatch.setattr(
        pair_service,
        "_get_active_user",
        lambda session, user_id: SimpleNamespace(id=user_id, is_active=True),
    )
    monkeypatch.setattr(pair_service, "is_admin", lambda user: False)
    yield manager
    pair_service._sessions.clear()


def test_create_pair_starts_session(clean_state: _FakeManager) -> None:
    ps = asyncio.run(
        pair_service.create_pair(
            None, OWNER, vmid=101, invitee_user_id=INVITEE_ID
        )
    )
    assert ps.vmid == 101
    assert ps.owner_id == OWNER.id
    assert ps.invitee_id == INVITEE_ID
    assert clean_state.started[0]["mode"] is SessionMode.pair
    assert pair_service.is_participant(ps.id, INVITEE_ID)
    assert not pair_service.is_participant(ps.id, uuid.uuid4())


def test_create_pair_one_per_vm(clean_state: _FakeManager) -> None:
    asyncio.run(
        pair_service.create_pair(None, OWNER, vmid=101, invitee_user_id=INVITEE_ID)
    )
    with pytest.raises(ConflictError):
        asyncio.run(
            pair_service.create_pair(
                None, OWNER, vmid=101, invitee_user_id=INVITEE_ID
            )
        )


def test_create_pair_requires_same_group(
    clean_state: _FakeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pair_service, "_share_group", lambda s, a, b: False)
    with pytest.raises(PermissionDeniedError):
        asyncio.run(
            pair_service.create_pair(
                None, OWNER, vmid=101, invitee_user_id=INVITEE_ID
            )
        )


def test_list_mine_includes_invited(clean_state: _FakeManager) -> None:
    ps = asyncio.run(
        pair_service.create_pair(None, OWNER, vmid=101, invitee_user_id=INVITEE_ID)
    )
    invitee = SimpleNamespace(id=INVITEE_ID)
    assert [p.id for p in pair_service.list_mine(invitee)] == [ps.id]
    stranger = SimpleNamespace(id=uuid.uuid4())
    assert pair_service.list_mine(stranger) == []


def test_end_pair_owner_only(clean_state: _FakeManager) -> None:
    ps = asyncio.run(
        pair_service.create_pair(None, OWNER, vmid=101, invitee_user_id=INVITEE_ID)
    )
    stranger = SimpleNamespace(id=uuid.uuid4())
    with pytest.raises(PermissionDeniedError):
        asyncio.run(pair_service.end_pair(stranger, ps.id))
    asyncio.run(pair_service.end_pair(OWNER, ps.id))
    assert clean_state.stopped == [ps.id]
