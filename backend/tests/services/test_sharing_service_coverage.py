"""Coverage tests for app.services.resource.sharing_service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import BadRequestError, NotFoundError
from app.models.resource_share import SHARE_PERMISSION_CONTROL
from app.services.resource import sharing_service as svc

_OWNER_ID = uuid.uuid4()
_TARGET = SimpleNamespace(
    id=uuid.uuid4(), email="target@example.com", full_name="Target", is_active=True
)
_OWNER = SimpleNamespace(
    id=_OWNER_ID, email="owner@example.com", full_name="Owner", is_active=True
)
_ACTOR = SimpleNamespace(id=uuid.uuid4(), email="actor@example.com")


def _share(user_id: uuid.UUID, vmid: int = 150) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        resource_vmid=vmid,
        user_id=user_id,
        permission=SHARE_PERMISSION_CONTROL,
        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )


class _FakeSession:
    def __init__(self, users: dict[uuid.UUID, Any] | None = None) -> None:
        self.users = users or {}
        self.added: list[Any] = []
        self.commits = 0
        self.refreshed: list[Any] = []

    def get(self, _model: Any, key: Any) -> Any:
        return self.users.get(key)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, obj: Any) -> None:
        self.refreshed.append(obj)


@pytest.fixture()
def state(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    st: dict[str, Any] = {
        "resource": SimpleNamespace(
            vmid=150,
            user_id=_OWNER_ID,
            allocation_scope="personal",
            teaching_class_id=None,
        ),
        "users": {_TARGET.email: _TARGET, _OWNER.email: _OWNER},
        "shares": {},
        "created": [],
        "deleted": [],
        "audits": [],
        "cancelled": 0,
        "quota": [],
        "bypass": False,
    }
    monkeypatch.setattr(
        svc.resource_repo, "get_resource_by_vmid", lambda **_kw: st["resource"]
    )
    monkeypatch.setattr(
        svc.user_repo, "get_user_by_email", lambda **kw: st["users"].get(kw["email"])
    )
    monkeypatch.setattr(
        svc.share_repo,
        "get_share",
        lambda **kw: st["shares"].get((kw["vmid"], kw["user_id"])),
    )

    def _create(**kw: Any) -> SimpleNamespace:
        st["created"].append(kw)
        return _share(kw["user_id"], kw["vmid"])

    monkeypatch.setattr(svc.share_repo, "create_share", _create)
    monkeypatch.setattr(
        svc.share_repo, "delete_share", lambda **kw: st["deleted"].append(kw["share"])
    )
    monkeypatch.setattr(
        svc.audit_service, "log_action", lambda **kw: st["audits"].append(kw)
    )
    monkeypatch.setattr(
        svc.spec_request_repo,
        "cancel_open_spec_change_requests_for_vmid",
        lambda **_kw: st["cancelled"],
    )
    monkeypatch.setattr(
        svc.quota_service,
        "check_quota_for_existing_resource",
        lambda _s, uid, info: st["quota"].append((uid, info)),
    )
    monkeypatch.setattr(svc, "can_bypass_resource_ownership", lambda _a: st["bypass"])
    return st


# ── _get_personal_resource / _find_target_user ─────────────────────────────


def test_get_personal_resource_missing(state: dict[str, Any]) -> None:
    state["resource"] = None
    with pytest.raises(NotFoundError):
        svc._get_personal_resource(_FakeSession(), 150)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("scope", "class_id"), [("teaching_class", None), ("personal", uuid.uuid4())]
)
def test_get_personal_resource_rejects_class_machines(
    state: dict[str, Any], scope: str, class_id: Any
) -> None:
    state["resource"].allocation_scope = scope
    state["resource"].teaching_class_id = class_id
    with pytest.raises(BadRequestError):
        svc._get_personal_resource(_FakeSession(), 150)  # type: ignore[arg-type]


def test_find_target_user_missing_or_inactive(state: dict[str, Any]) -> None:
    with pytest.raises(NotFoundError):
        svc._find_target_user(_FakeSession(), "nobody@example.com")  # type: ignore[arg-type]
    state["users"]["off@example.com"] = SimpleNamespace(is_active=False)
    with pytest.raises(NotFoundError):
        svc._find_target_user(_FakeSession(), "off@example.com")  # type: ignore[arg-type]


# ── user_has_share / list_shares ───────────────────────────────────────────


def test_list_shares_maps_known_and_deleted_users(
    state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    gone_id = uuid.uuid4()
    shares = [_share(_TARGET.id), _share(gone_id)]
    monkeypatch.setattr(
        svc.share_repo, "list_shares_for_resource", lambda **_kw: shares
    )
    session = _FakeSession(users={_TARGET.id: _TARGET})
    out = svc.list_shares(session=session, vmid=150)  # type: ignore[arg-type]
    assert out[0].user_email == _TARGET.email
    assert out[0].user_full_name == "Target"
    assert out[1].user_email is None
    assert out[1].user_full_name is None


# ── add_share ──────────────────────────────────────────────────────────────


def test_add_share_creates_control_share(state: dict[str, Any]) -> None:
    session = _FakeSession()
    out = svc.add_share(
        session=session,  # type: ignore[arg-type]
        vmid=150,
        actor=_ACTOR,
        email=_TARGET.email,
    )
    assert out.user_email == _TARGET.email
    assert state["created"][0]["permission"] == SHARE_PERMISSION_CONTROL
    assert state["created"][0]["granted_by"] == _ACTOR.id
    assert state["audits"][0]["action"] == "resource_share_update"
    assert session.commits == 1
    assert len(session.refreshed) == 1


def test_add_share_rejects_owner(state: dict[str, Any]) -> None:
    with pytest.raises(BadRequestError):
        svc.add_share(
            session=_FakeSession(),  # type: ignore[arg-type]
            vmid=150,
            actor=_ACTOR,
            email=_OWNER.email,
        )


def test_add_share_rejects_duplicate(state: dict[str, Any]) -> None:
    state["shares"][(150, _TARGET.id)] = _share(_TARGET.id)
    with pytest.raises(BadRequestError):
        svc.add_share(
            session=_FakeSession(),  # type: ignore[arg-type]
            vmid=150,
            actor=_ACTOR,
            email=_TARGET.email,
        )
    assert state["created"] == []


# ── remove_share ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("found", [None, "other_vm"])
def test_remove_share_not_found(
    state: dict[str, Any], monkeypatch: pytest.MonkeyPatch, found: str | None
) -> None:
    share = None if found is None else _share(_TARGET.id, vmid=999)
    monkeypatch.setattr(svc.share_repo, "get_share_by_id", lambda **_kw: share)
    with pytest.raises(NotFoundError):
        svc.remove_share(
            session=_FakeSession(),  # type: ignore[arg-type]
            vmid=150,
            share_id=uuid.uuid4(),
            actor=_ACTOR,
        )


@pytest.mark.parametrize("user_known", [True, False])
def test_remove_share_deletes_and_audits(
    state: dict[str, Any], monkeypatch: pytest.MonkeyPatch, user_known: bool
) -> None:
    share = _share(_TARGET.id)
    monkeypatch.setattr(svc.share_repo, "get_share_by_id", lambda **_kw: share)
    session = _FakeSession(users={_TARGET.id: _TARGET} if user_known else {})
    svc.remove_share(
        session=session,  # type: ignore[arg-type]
        vmid=150,
        share_id=share.id,
        actor=_ACTOR,
    )
    assert state["deleted"] == [share]
    expected = _TARGET.email if user_known else str(_TARGET.id)
    assert expected in state["audits"][0]["details"]
    assert session.commits == 1


# ── _check_target_quota ────────────────────────────────────────────────────


def test_check_target_quota_live_lookup_failure_counts_machine_only(
    state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_v: int) -> Any:
        raise RuntimeError("pve down")

    monkeypatch.setattr(svc.proxmox_service, "find_resource", _boom)
    svc._check_target_quota(
        _FakeSession(),  # type: ignore[arg-type]
        vmid=150,
        actor=_ACTOR,
        target_id=_TARGET.id,
        resource_info=None,
    )
    assert state["quota"] == [(_TARGET.id, {})]


# ── transfer_ownership ─────────────────────────────────────────────────────


def test_transfer_rejects_current_owner(state: dict[str, Any]) -> None:
    with pytest.raises(BadRequestError):
        svc.transfer_ownership(
            session=_FakeSession(),  # type: ignore[arg-type]
            vmid=150,
            actor=_ACTOR,
            email=_OWNER.email,
            keep_access=False,
        )


def test_transfer_with_keep_access_and_cleanup(state: dict[str, Any]) -> None:
    old_share = _share(_TARGET.id)
    state["shares"][(150, _TARGET.id)] = old_share
    state["cancelled"] = 2
    session = _FakeSession(users={_OWNER_ID: _OWNER})
    out = svc.transfer_ownership(
        session=session,  # type: ignore[arg-type]
        vmid=150,
        actor=_ACTOR,
        email=_TARGET.email,
        keep_access=True,
        resource_info={"maxcpu": 1},
    )
    assert out.new_owner_id == _TARGET.id
    assert state["resource"].user_id == _TARGET.id
    assert state["deleted"] == [old_share]
    assert state["created"][0]["user_id"] == _OWNER_ID
    assert _OWNER.email in state["audits"][0]["details"]
    assert session.commits == 1
