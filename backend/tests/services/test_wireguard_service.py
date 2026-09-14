from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError, ConflictError
from app.models.wireguard_peer import WireGuardPeer
from app.schemas import ResourcePublic
from app.services.network import wireguard_service


def _public_key(seed: int = 1) -> str:
    return base64.b64encode(bytes([seed]) * 32).decode("ascii")


def test_validate_public_key_rejects_malformed_values() -> None:
    with pytest.raises(BadRequestError, match="Invalid WireGuard public key"):
        wireguard_service._validate_public_key("not-a-key")
    with pytest.raises(BadRequestError, match="Invalid WireGuard public key"):
        wireguard_service._validate_public_key(base64.b64encode(bytes(32)).decode())


def test_allocate_tunnel_ip_reserves_gateway_and_existing_peer(monkeypatch) -> None:
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "list_tunnel_ips",
        lambda **_: {"10.250.0.2"},
    )

    assert wireguard_service._allocate_tunnel_ip(object()) == "10.250.0.3"


def test_resource_targets_are_running_authorized_and_inside_vm_subnet(
    monkeypatch,
) -> None:
    resources = [
        ResourcePublic(
            vmid=101,
            name="linux",
            status="running",
            node="pve1",
            type="lxc",
            ip_address="10.10.1.10",
        ),
        ResourcePublic(
            vmid=102,
            name="windows",
            status="running",
            node="pve1",
            type="qemu",
            ip_address="10.10.1.11",
        ),
        ResourcePublic(
            vmid=103,
            name="stopped",
            status="stopped",
            node="pve1",
            type="qemu",
            ip_address="10.10.1.12",
        ),
        ResourcePublic(
            vmid=104,
            name="outside",
            status="running",
            node="pve1",
            type="qemu",
            ip_address="192.168.1.10",
        ),
    ]
    monkeypatch.setattr(
        wireguard_service.resource_service,
        "list_by_user",
        lambda **_: resources,
    )

    targets = wireguard_service._resource_targets(
        session=object(), user_id=uuid.uuid4()
    )

    assert [(item.vmid, item.service, item.port) for item in targets] == [
        (101, "ssh", 22),
        (102, "ssh", 22),
        (102, "rdp", 3389),
    ]


def test_connect_reuses_device_address_and_activates_only_after_gateway_sync(
    monkeypatch,
) -> None:
    user_id = uuid.uuid4()
    peer = WireGuardPeer(
        user_id=user_id,
        device_id="device-1234",
        public_key=_public_key(1),
        tunnel_ip="10.250.0.8",
        allowed_endpoints=[],
        active=False,
    )
    saved_active_states: list[bool] = []
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "get_by_user_device",
        lambda **_: peer,
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "get_by_public_key",
        lambda **_: None,
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "save",
        lambda **kwargs: (
            saved_active_states.append(kwargs["peer"].active) or kwargs["peer"]
        ),
    )
    monkeypatch.setattr(wireguard_service, "_resource_targets", lambda **_: [])
    monkeypatch.setattr(
        wireguard_service,
        "_sync_gateway_peer",
        lambda **_: _public_key(9),
    )
    monkeypatch.setattr(
        wireguard_service.gateway_config_repo,
        "get_gateway_config",
        lambda _: SimpleNamespace(host="192.168.100.143"),
    )

    response = wireguard_service.connect(
        session=object(),
        user_id=user_id,
        device_id="device-1234",
        public_key=_public_key(2),
    )

    assert response.interface_address == "10.250.0.8/32"
    assert response.endpoint == "192.168.100.143:51821"
    assert response.allowed_ips == ["10.10.0.0/16"]
    assert peer.public_key == _public_key(2)
    assert peer.active is True
    assert saved_active_states == [True]


def test_connect_rejects_public_key_owned_by_another_device(monkeypatch) -> None:
    user_id = uuid.uuid4()
    conflict = WireGuardPeer(
        user_id=uuid.uuid4(),
        device_id="other-device",
        public_key=_public_key(3),
        tunnel_ip="10.250.0.9",
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "get_by_public_key",
        lambda **_: conflict,
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "get_by_user_device",
        lambda **_: None,
    )

    with pytest.raises(ConflictError):
        wireguard_service.connect(
            session=object(),
            user_id=user_id,
            device_id="device-1234",
            public_key=_public_key(3),
        )


def test_local_mode_can_transfer_inactive_peer_on_same_device(monkeypatch) -> None:
    old_user_id = uuid.uuid4()
    new_user_id = uuid.uuid4()
    peer = WireGuardPeer(
        user_id=old_user_id,
        device_id="device-1234",
        public_key=_public_key(3),
        tunnel_ip="10.250.0.9",
        active=False,
    )
    monkeypatch.setattr(
        wireguard_service.settings,
        "WIREGUARD_ALLOW_INACTIVE_PEER_TRANSFER",
        True,
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "get_by_public_key",
        lambda **_: peer,
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "get_by_user_device",
        lambda **_: None,
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "save",
        lambda **kwargs: kwargs["peer"],
    )

    transferred = wireguard_service._get_or_create_peer(
        session=object(),
        user_id=new_user_id,
        device_id="device-1234",
        public_key=_public_key(3),
    )

    assert transferred.id == peer.id
    assert transferred.user_id == new_user_id


def test_connect_revokes_gateway_access_when_database_save_fails(monkeypatch) -> None:
    user_id = uuid.uuid4()
    peer = WireGuardPeer(
        user_id=user_id,
        device_id="device-1234",
        public_key=_public_key(1),
        tunnel_ip="10.250.0.8",
        allowed_endpoints=[],
        active=False,
    )

    class FakeSession:
        rolled_back = False

        def rollback(self) -> None:
            self.rolled_back = True

    session = FakeSession()
    cleanup_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "get_by_user_device",
        lambda **_: peer,
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "get_by_public_key",
        lambda **_: None,
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "save",
        lambda **_: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    monkeypatch.setattr(wireguard_service, "_resource_targets", lambda **_: [])
    monkeypatch.setattr(
        wireguard_service,
        "_sync_gateway_peer",
        lambda **_: _public_key(9),
    )
    monkeypatch.setattr(
        wireguard_service,
        "_remove_gateway_access",
        lambda **kwargs: cleanup_calls.append(kwargs),
    )
    monkeypatch.setattr(
        wireguard_service.gateway_config_repo,
        "get_gateway_config",
        lambda _: SimpleNamespace(host="192.168.100.143"),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        wireguard_service.connect(
            session=session,
            user_id=user_id,
            device_id="device-1234",
            public_key=_public_key(2),
        )

    assert session.rolled_back is True
    assert len(cleanup_calls) == 1
    assert cleanup_calls[0]["public_key"] == _public_key(2)
    assert cleanup_calls[0]["tunnel_ip"] == "10.250.0.8"


def test_refresh_rebuilds_targets_and_renews_active_lease(monkeypatch) -> None:
    user_id = uuid.uuid4()
    old_expiry = datetime.now(UTC) + timedelta(minutes=10)
    peer = WireGuardPeer(
        user_id=user_id,
        device_id="device-1234",
        public_key=_public_key(1),
        tunnel_ip="10.250.0.8",
        allowed_endpoints=[],
        active=True,
        expires_at=old_expiry,
    )
    target = wireguard_service.WireGuardConnectionTarget(
        vmid=101,
        name="linux",
        service="ssh",
        host="10.10.1.10",
        port=22,
    )
    sync_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "get_by_user_device",
        lambda **_: peer,
    )
    monkeypatch.setattr(wireguard_service, "_resource_targets", lambda **_: [target])
    monkeypatch.setattr(
        wireguard_service,
        "_sync_gateway_peer",
        lambda **kwargs: sync_calls.append(kwargs) or _public_key(9),
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "save",
        lambda **kwargs: kwargs["peer"],
    )
    monkeypatch.setattr(
        wireguard_service.gateway_config_repo,
        "get_gateway_config",
        lambda _: SimpleNamespace(host="192.168.100.143"),
    )

    response = wireguard_service.refresh(
        session=object(), user_id=user_id, device_id="device-1234"
    )

    assert response.connections == [target]
    assert response.interface_address == "10.250.0.8/32"
    assert peer.expires_at is not None and peer.expires_at > old_expiry
    assert sync_calls[0]["new_endpoints"][0]["host"] == "10.10.1.10"


def test_refresh_rejects_expired_peer(monkeypatch) -> None:
    user_id = uuid.uuid4()
    peer = WireGuardPeer(
        user_id=user_id,
        device_id="device-1234",
        public_key=_public_key(1),
        tunnel_ip="10.250.0.8",
        active=True,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "get_by_user_device",
        lambda **_: peer,
    )

    with pytest.raises(ConflictError, match="inactive or expired"):
        wireguard_service.refresh(
            session=object(), user_id=user_id, device_id="device-1234"
        )


def test_reconcile_marks_peer_inactive_when_gateway_replay_fails(
    monkeypatch,
) -> None:
    peer = WireGuardPeer(
        user_id=uuid.uuid4(),
        device_id="device-1234",
        public_key=_public_key(1),
        tunnel_ip="10.250.0.8",
        allowed_endpoints=[],
        active=True,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    saved: list[WireGuardPeer] = []
    monkeypatch.setattr(wireguard_service, "Session", lambda _: FakeSession())
    monkeypatch.setattr(
        wireguard_service.peer_repo, "list_expired_active", lambda **_: []
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo, "list_revoked_before", lambda **_: []
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "list_active_unexpired",
        lambda **_: [peer],
    )
    monkeypatch.setattr(wireguard_service, "_gateway_state_id", lambda _: "boot-2")
    monkeypatch.setattr(
        wireguard_service,
        "_sync_gateway_peer",
        lambda **_: (_ for _ in ()).throw(RuntimeError("replay failed")),
    )
    monkeypatch.setattr(
        wireguard_service.peer_repo,
        "save",
        lambda **kwargs: saved.append(kwargs["peer"]) or kwargs["peer"],
    )
    monkeypatch.setattr(
        wireguard_service._reconcile_state, "last_gateway_state_id", "boot-1"
    )

    wireguard_service.reconcile_once()

    assert saved == [peer]
    assert peer.active is False
    assert peer.allowed_endpoints == []
    assert peer.revoked_at is not None
