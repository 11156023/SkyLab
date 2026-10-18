"""Coverage tests for app.services.network.wireguard_service."""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.exceptions import BadRequestError, ConflictError
from app.models.wireguard_peer import WireGuardPeer
from app.services.monitoring import heartbeat_service
from app.services.network import wireguard_service


@pytest.fixture(autouse=True)
def _default_wireguard_vm_subnet(monkeypatch) -> None:
    monkeypatch.setattr(
        wireguard_service.ip_management_service,
        "get_subnet_config",
        lambda _session: None,
    )


def _public_key(seed: int = 1) -> str:
    return base64.b64encode(bytes([seed]) * 32).decode("ascii")


def _peer(**overrides) -> WireGuardPeer:
    values = {
        "user_id": uuid.uuid4(),
        "device_id": "device-1234",
        "public_key": _public_key(1),
        "tunnel_ip": "10.250.0.8",
        "active": True,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    values.update(overrides)
    return WireGuardPeer(**values)


class _Client:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self) -> None:
        self.deleted: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def delete(self, obj) -> None:
        self.deleted.append(obj)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


# ── 網段驗證 ─────────────────────────────────────────────────────────────


def test_client_network_rejects_invalid_and_non_ipv4(monkeypatch) -> None:
    monkeypatch.setattr(
        wireguard_service.settings, "WIREGUARD_CLIENT_SUBNET", "not-a-subnet"
    )
    with pytest.raises(BadRequestError):
        wireguard_service._client_network()

    monkeypatch.setattr(
        wireguard_service.settings, "WIREGUARD_CLIENT_SUBNET", "fd00::/64"
    )
    with pytest.raises(BadRequestError):
        wireguard_service._client_network()

    monkeypatch.setattr(
        wireguard_service.settings, "WIREGUARD_CLIENT_SUBNET", "10.250.0.0/31"
    )
    with pytest.raises(BadRequestError):
        wireguard_service._client_network()


def test_vm_network_rejects_invalid_and_non_ipv4(monkeypatch) -> None:
    monkeypatch.setattr(wireguard_service.settings, "WIREGUARD_VM_SUBNET", "garbage")
    with pytest.raises(BadRequestError):
        wireguard_service._vm_network()

    monkeypatch.setattr(wireguard_service.settings, "WIREGUARD_VM_SUBNET", "fd00::/64")
    with pytest.raises(BadRequestError):
        wireguard_service._vm_network()


def test_allocate_tunnel_ip_pool_exhausted(monkeypatch) -> None:
    monkeypatch.setattr(
        wireguard_service.settings, "WIREGUARD_CLIENT_SUBNET", "10.250.0.0/30"
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo, "list_tunnel_ips", lambda **_: {"10.250.0.2"}
    )
    with pytest.raises(ConflictError):
        wireguard_service._allocate_tunnel_ip(object())


def test_lock_ip_allocation_uses_xact_lock_on_postgresql() -> None:
    executed: list[tuple] = []

    class _PgSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, statement, params):
            executed.append((str(statement), params))

    wireguard_service._lock_ip_allocation(_PgSession())

    assert executed == [
        (
            "SELECT pg_advisory_xact_lock(:lock_id)",
            {"lock_id": wireguard_service._IP_ALLOCATION_LOCK_ID},
        )
    ]


# ── peer 建立 ────────────────────────────────────────────────────────────


def test_get_or_create_peer_retries_integrity_errors(monkeypatch) -> None:
    session = _FakeSession()
    monkeypatch.setattr(
        wireguard_service.peer_repo, "get_by_user_device", lambda **_: None
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo, "get_by_public_key", lambda **_: None
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo, "list_tunnel_ips", lambda **_: set()
    )

    def save(**_kw):
        raise IntegrityError("insert", {}, Exception("duplicate tunnel_ip"))

    monkeypatch.setattr(wireguard_service.peer_repo, "save", save)

    with pytest.raises(ConflictError):
        wireguard_service._get_or_create_peer(
            session=session,
            user_id=uuid.uuid4(),
            device_id="dev",
            public_key=_public_key(4),
        )
    assert session.rollbacks == wireguard_service._ALLOCATION_RETRIES


def test_get_or_create_peer_without_retries_budget(monkeypatch) -> None:
    # _ALLOCATION_RETRIES 為 0 時迴圈一次都不跑，直接走最後的保底錯誤
    monkeypatch.setattr(wireguard_service, "_ALLOCATION_RETRIES", 0)
    with pytest.raises(ConflictError):
        wireguard_service._get_or_create_peer(
            session=object(),
            user_id=uuid.uuid4(),
            device_id="dev",
            public_key=_public_key(4),
        )


# ── ACL 目標 ─────────────────────────────────────────────────────────────


def test_resource_targets_skip_unparseable_ip(monkeypatch) -> None:
    resource = SimpleNamespace(
        vmid=301,
        name="weird",
        is_placeholder=False,
        status="running",
        can_control=True,
        ip_address="not-an-ip",
        start_blocked_reason=None,
        expiry_date=None,
        type="lxc",
    )
    monkeypatch.setattr(
        wireguard_service.resource_service, "list_by_user", lambda **_: [resource]
    )
    monkeypatch.setattr(
        wireguard_service.resource_repo, "get_allocated_ip_address", lambda **_: None
    )

    assert (
        wireguard_service._resource_targets(session=object(), user_id=uuid.uuid4())
        == []
    )


def test_validated_endpoint_rejects_unparseable_values() -> None:
    network = wireguard_service._vm_network()
    with pytest.raises(BadRequestError):
        wireguard_service._validated_endpoint_tuple(
            {"vmid": "abc", "service": "ssh", "host": "10.10.1.1", "port": 22},
            vm_network=network,
        )


# ── Gateway SSH ──────────────────────────────────────────────────────────


def test_gateway_client_requires_configuration(monkeypatch) -> None:
    monkeypatch.setattr(
        wireguard_service.gateway_config_repo, "get_gateway_config", lambda _s: None
    )
    with pytest.raises(BadRequestError):
        wireguard_service._gateway_client(object())


def test_gateway_client_opens_ssh(monkeypatch) -> None:
    config = SimpleNamespace(
        host="gw", ssh_port=2222, ssh_user="root", encrypted_private_key="enc"
    )
    seen: list[tuple] = []
    monkeypatch.setattr(
        wireguard_service.gateway_config_repo, "get_gateway_config", lambda _s: config
    )
    monkeypatch.setattr(
        wireguard_service.gateway_config_repo,
        "get_decrypted_private_key",
        lambda _c: "PEM",
    )
    monkeypatch.setattr(
        wireguard_service.gateway_service,
        "make_client",
        lambda *args: seen.append(args) or "client",
    )

    assert wireguard_service._gateway_client(object()) == (config, "client")
    assert seen == [("gw", 2222, "root", "PEM")]


def test_sync_gateway_peer_removes_old_public_key(monkeypatch) -> None:
    client = _Client()
    scripts: list[str] = []
    monkeypatch.setattr(wireguard_service, "_gateway_client", lambda _s: (None, client))
    monkeypatch.setattr(
        wireguard_service,
        "_run_locked",
        lambda _c, script, _m: scripts.append(script) or _public_key(9),
    )

    result = wireguard_service._sync_gateway_peer(
        session=object(),
        public_key=_public_key(2),
        tunnel_ip="10.250.0.8",
        old_public_key=_public_key(1),
        old_endpoints=[],
        new_endpoints=[],
    )

    assert result == _public_key(9)
    assert f"peer {_public_key(1)} remove" in scripts[0]
    assert client.closed is True


def test_remove_gateway_peer_deletes_peer_and_acl(monkeypatch) -> None:
    client = _Client()
    commands: list[str] = []
    monkeypatch.setattr(wireguard_service, "_gateway_client", lambda _s: (None, client))
    monkeypatch.setattr(
        wireguard_service.gateway_service,
        "exec_checked",
        lambda _c, command, _m: commands.append(command) or "",
    )
    peer = _peer()
    peer.allowed_endpoints = [
        {"vmid": 101, "service": "ssh", "host": "192.168.9.9", "port": 22}
    ]

    wireguard_service._remove_gateway_peer(session=object(), peer=peer)

    assert client.closed is True
    assert "remove" in commands[0]
    # 舊網段的 endpoint 也要能刪（allow_stale_host）
    assert "10.250.0.8 . 192.168.9.9 . 22" in commands[0]


def test_endpoint_host_requires_configuration(monkeypatch) -> None:
    monkeypatch.setattr(
        wireguard_service.gateway_config_repo, "get_gateway_config", lambda _s: None
    )
    monkeypatch.setattr(wireguard_service.settings, "WIREGUARD_ENDPOINT_HOST", "")
    with pytest.raises(BadRequestError):
        wireguard_service._endpoint_host(object())


def test_activate_peer_logs_when_compensation_fails(monkeypatch) -> None:
    peer = _peer(active=False, expires_at=None)
    session = _FakeSession()
    monkeypatch.setattr(wireguard_service, "_resource_targets", lambda **_: [])
    monkeypatch.setattr(
        wireguard_service, "_sync_gateway_peer", lambda **_: _public_key(9)
    )
    monkeypatch.setattr(
        wireguard_service.gateway_config_repo,
        "get_gateway_config",
        lambda _: SimpleNamespace(host="192.168.100.143"),
    )

    def save(**_kw):
        raise RuntimeError("database unavailable")

    def remove(**_kw):
        raise RuntimeError("gateway unreachable")

    monkeypatch.setattr(wireguard_service.peer_repo, "save", save)
    monkeypatch.setattr(wireguard_service, "_remove_gateway_access", remove)

    with pytest.raises(RuntimeError, match="database unavailable"):
        wireguard_service._activate_peer(
            session=session,
            peer=peer,
            public_key=_public_key(1),
            update_last_connected=False,
        )
    assert session.rollbacks == 1


# ── disconnect ───────────────────────────────────────────────────────────


def test_disconnect_without_active_peer(monkeypatch) -> None:
    monkeypatch.setattr(
        wireguard_service.peer_repo, "get_by_user_device", lambda **_: None
    )
    assert (
        wireguard_service.disconnect(
            session=object(), user_id=uuid.uuid4(), device_id="d"
        )
        is True
    )


def test_disconnect_removes_gateway_peer(monkeypatch) -> None:
    peer = _peer()
    removed: list[WireGuardPeer] = []
    saved: list[WireGuardPeer] = []
    monkeypatch.setattr(
        wireguard_service.peer_repo, "get_by_user_device", lambda **_: peer
    )
    monkeypatch.setattr(
        wireguard_service,
        "_remove_gateway_peer",
        lambda **kw: removed.append(kw["peer"]),
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo, "save", lambda **kw: saved.append(kw["peer"])
    )

    assert (
        wireguard_service.disconnect(
            session=object(), user_id=peer.user_id, device_id="d"
        )
        is True
    )
    assert removed == [peer]
    assert saved == [peer]
    assert peer.active is False


# ── Gateway state id ─────────────────────────────────────────────────────


def test_gateway_state_id_returns_token(monkeypatch) -> None:
    client = _Client()
    monkeypatch.setattr(wireguard_service, "_gateway_client", lambda _s: (None, client))
    monkeypatch.setattr(
        wireguard_service.gateway_service,
        "exec_checked",
        lambda *_a: "boot\n123\n456\n",
    )

    assert wireguard_service._gateway_state_id(object()) == "boot\n123\n456"
    assert client.closed is True


def test_gateway_state_id_rejects_empty_token(monkeypatch) -> None:
    client = _Client()
    monkeypatch.setattr(wireguard_service, "_gateway_client", lambda _s: (None, client))
    monkeypatch.setattr(
        wireguard_service.gateway_service, "exec_checked", lambda *_a: "  \n"
    )

    with pytest.raises(RuntimeError):
        wireguard_service._gateway_state_id(object())
    assert client.closed is True


# ── reconcile_once ───────────────────────────────────────────────────────


def _patch_reconcile(monkeypatch, *, expired, revoked, active) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(wireguard_service, "Session", lambda _engine: session)
    monkeypatch.setattr(
        wireguard_service.peer_repo, "list_expired_active", lambda **_: expired
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo, "list_revoked_before", lambda **_: revoked
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo, "list_active_unexpired", lambda **_: active
    )
    return session


def test_reconcile_expires_and_purges_peers(monkeypatch) -> None:
    expired_peer = _peer()
    revoked_ok = _peer(active=False)
    revoked_fail = _peer(active=False)
    session = _patch_reconcile(
        monkeypatch,
        expired=[expired_peer],
        revoked=[revoked_fail, revoked_ok],
        active=[],
    )
    saved: list[WireGuardPeer] = []
    monkeypatch.setattr(
        wireguard_service.peer_repo, "save", lambda **kw: saved.append(kw["peer"])
    )

    def remove(*, session, peer):
        if peer is expired_peer or peer is revoked_fail:
            raise RuntimeError("gateway down")

    monkeypatch.setattr(wireguard_service, "_remove_gateway_peer", remove)

    wireguard_service.reconcile_once()

    # 過期的就算 Gateway 清不掉也標 inactive；撤銷的清不掉就保留不刪
    assert saved == [expired_peer]
    assert expired_peer.active is False
    assert session.deleted == [revoked_ok]
    assert session.commits == 1


def test_reconcile_keeps_leases_when_gateway_unreachable(monkeypatch) -> None:
    peer = _peer()
    _patch_reconcile(monkeypatch, expired=[], revoked=[], active=[peer])

    def state(_session):
        raise RuntimeError("ssh timeout")

    monkeypatch.setattr(wireguard_service, "_gateway_state_id", state)
    monkeypatch.setattr(
        wireguard_service._reconcile_state, "last_gateway_state_id", "boot-1"
    )

    wireguard_service.reconcile_once()

    assert peer.active is True
    assert wireguard_service._reconcile_state.last_gateway_state_id == "boot-1"


def test_reconcile_skips_unchanged_and_revokes_failed_peer(monkeypatch) -> None:
    unchanged = _peer()
    unchanged.allowed_endpoints = []
    failing = _peer(public_key=_public_key(5), tunnel_ip="10.250.0.9")
    failing.allowed_endpoints = [
        {"vmid": 100, "service": "ssh", "host": "10.10.1.10", "port": 22}
    ]
    _patch_reconcile(monkeypatch, expired=[], revoked=[], active=[unchanged, failing])
    monkeypatch.setattr(wireguard_service, "_gateway_state_id", lambda _s: "boot-1")
    monkeypatch.setattr(
        wireguard_service._reconcile_state, "last_gateway_state_id", "boot-1"
    )
    monkeypatch.setattr(wireguard_service, "_resource_targets", lambda **_: [])
    sync_calls: list[dict] = []

    def sync(**kwargs):
        sync_calls.append(kwargs)
        raise RuntimeError("replay failed")

    def remove(**_kw):
        raise RuntimeError("gateway down")

    saved: list[WireGuardPeer] = []
    monkeypatch.setattr(wireguard_service, "_sync_gateway_peer", sync)
    monkeypatch.setattr(wireguard_service, "_remove_gateway_peer", remove)
    monkeypatch.setattr(
        wireguard_service.peer_repo, "save", lambda **kw: saved.append(kw["peer"])
    )

    wireguard_service.reconcile_once()

    assert [call["public_key"] for call in sync_calls] == [_public_key(5)]
    assert unchanged.active is True
    assert failing.active is False
    assert saved == [failing]


# ── reconcile_tick ───────────────────────────────────────────────────────


# ── run_reconciler ───────────────────────────────────────────────────────


class _Observer:
    def __init__(self, loop: str, *, interval_seconds: int, fail_tick: bool = False):
        self.events: list[tuple] = []
        self.fail_tick = fail_tick

    async def on_start(self, names) -> None:
        self.events.append(("start", tuple(names)))

    async def on_tick(self, *, is_leader: bool) -> None:
        self.events.append(("tick", is_leader))

    async def on_task(self, name, *, ok, duration_seconds, error) -> None:
        if self.fail_tick and not ok:
            raise RuntimeError("redis down")
        self.events.append(("task", name, ok, error is not None))


def _fake_asyncio(results, stop_event: asyncio.Event, rounds: int):
    remaining = list(results)
    waits = {"count": 0}

    async def to_thread(_func):
        outcome = remaining.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def wait_for(coro, timeout):
        coro.close()
        waits["count"] += 1
        if waits["count"] >= rounds:
            stop_event.set()
        raise TimeoutError

    return SimpleNamespace(
        to_thread=to_thread,
        wait_for=wait_for,
        CancelledError=asyncio.CancelledError,
    ), waits


async def test_run_reconciler_tolerates_heartbeat_failure(monkeypatch) -> None:
    def make_observer(loop, *, interval_seconds):
        return _Observer(loop, interval_seconds=interval_seconds, fail_tick=True)

    monkeypatch.setattr(heartbeat_service, "HeartbeatObserver", make_observer)
    stop_event = asyncio.Event()
    fake, waits = _fake_asyncio([RuntimeError("tick failed")], stop_event, rounds=1)
    monkeypatch.setattr(wireguard_service, "asyncio", fake)

    await wireguard_service.run_reconciler(stop_event)

    assert waits["count"] == 1


async def test_run_reconciler_propagates_cancellation(monkeypatch) -> None:
    monkeypatch.setattr(
        heartbeat_service,
        "HeartbeatObserver",
        lambda loop, *, interval_seconds: _Observer(
            loop, interval_seconds=interval_seconds
        ),
    )
    stop_event = asyncio.Event()
    fake, _waits = _fake_asyncio([asyncio.CancelledError()], stop_event, rounds=1)
    monkeypatch.setattr(wireguard_service, "asyncio", fake)

    with pytest.raises(asyncio.CancelledError):
        await wireguard_service.run_reconciler(stop_event)
