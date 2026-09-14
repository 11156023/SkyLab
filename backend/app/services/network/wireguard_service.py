"""WireGuard control plane for authenticated desktop devices.

The desktop owns its private key. This service persists only public peer
identity, allocates a unique tunnel address, derives resource ACLs from the
existing resource authorization path, and applies fail-closed Gateway rules.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import logging
import shlex
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.core.i18n import t
from app.exceptions import BadRequestError, ConflictError
from app.models.wireguard_peer import WireGuardPeer
from app.repositories import gateway_config as gateway_config_repo
from app.repositories import wireguard_peer as peer_repo
from app.schemas.wireguard import (
    WireGuardConnectionTarget,
    WireGuardConnectResponse,
)
from app.services.network import gateway_service
from app.services.resource import resource_service

_SSH_PORT = 22
_RDP_PORT = 3389
_NFT_FAMILY = "inet"
_NFT_TABLE = "campus_cloud_wg"
_NFT_SET = "allowed_tcp"
_GATEWAY_LOCK = "/run/lock/campus-cloud-wg.lock"
_IP_ALLOCATION_LOCK_ID = 0x534B594C41425747
_ALLOCATION_RETRIES = 3

logger = logging.getLogger(__name__)


class _ReconcileState:
    """上次看到的 Gateway 狀態 id（集中在物件上，避免 global 重新指派）。"""

    last_gateway_state_id: str | None = None


_reconcile_state = _ReconcileState()


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_public_key(value: str) -> str:
    value = value.strip()
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BadRequestError(t("wireguard.invalidPublicKey")) from exc
    if len(decoded) != 32 or decoded == bytes(32):
        raise BadRequestError(t("wireguard.invalidPublicKey"))
    return value


def _client_network() -> ipaddress.IPv4Network:
    try:
        network = ipaddress.ip_network(settings.WIREGUARD_CLIENT_SUBNET, strict=False)
    except ValueError as exc:
        raise BadRequestError(t("wireguard.clientSubnetInvalid")) from exc
    if not isinstance(network, ipaddress.IPv4Network) or network.prefixlen > 30:
        raise BadRequestError(t("wireguard.clientSubnetNotIpv4"))
    return network


def _vm_network() -> ipaddress.IPv4Network:
    try:
        network = ipaddress.ip_network(settings.WIREGUARD_VM_SUBNET, strict=False)
    except ValueError as exc:
        raise BadRequestError(t("wireguard.vmSubnetInvalid")) from exc
    if not isinstance(network, ipaddress.IPv4Network):
        raise BadRequestError(t("wireguard.vmSubnetNotIpv4"))
    return network


def _allocate_tunnel_ip(session: Session) -> str:
    network = _client_network()
    used = peer_repo.list_tunnel_ips(session=session)
    # The first host belongs to the Gateway (10.250.0.1 by default).
    hosts = network.hosts()
    next(hosts, None)
    for candidate in hosts:
        value = str(candidate)
        if value not in used:
            return value
    raise ConflictError(t("wireguard.addressPoolExhausted"))


def _lock_ip_allocation(session: Session) -> None:
    """Serialize address selection on PostgreSQL until the transaction commits."""
    get_bind = getattr(session, "get_bind", None)
    if get_bind is None:
        return
    bind = get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _IP_ALLOCATION_LOCK_ID},
        )


def _get_or_create_peer(
    *, session: Session, user_id: uuid.UUID, device_id: str, public_key: str
) -> WireGuardPeer:
    for attempt in range(_ALLOCATION_RETRIES):
        try:
            _lock_ip_allocation(session)
            peer = peer_repo.get_by_user_device(
                session=session, user_id=user_id, device_id=device_id
            )
            conflict = peer_repo.get_by_public_key(
                session=session, public_key=public_key
            )
            if conflict is not None and (peer is None or conflict.id != peer.id):
                can_transfer = (
                    settings.WIREGUARD_ALLOW_INACTIVE_PEER_TRANSFER
                    and peer is None
                    and not conflict.active
                    and conflict.device_id == device_id
                )
                if not can_transfer:
                    raise ConflictError(
                        t("wireguard.publicKeyBelongsToAnotherDevice")
                    )
                conflict.user_id = user_id
                conflict.updated_at = _now()
                return peer_repo.save(session=session, peer=conflict)
            if peer is not None:
                return peer
            peer = WireGuardPeer(
                user_id=user_id,
                device_id=device_id,
                public_key=public_key,
                tunnel_ip=_allocate_tunnel_ip(session),
                allowed_endpoints=[],
                active=False,
            )
            return peer_repo.save(session=session, peer=peer)
        except IntegrityError:
            session.rollback()
            if attempt + 1 >= _ALLOCATION_RETRIES:
                raise ConflictError(
                    t("wireguard.uniqueAddressAllocationFailed")
                ) from None
    raise ConflictError(t("wireguard.addressAllocationFailed"))


def _resource_targets(
    *, session: Session, user_id: uuid.UUID
) -> list[WireGuardConnectionTarget]:
    vm_network = _vm_network()
    targets: list[WireGuardConnectionTarget] = []
    for resource in resource_service.list_by_user(session=session, user_id=user_id):
        if (
            resource.vmid is None
            or resource.is_placeholder
            or resource.status != "running"
            or not resource.can_control
            or not resource.ip_address
        ):
            continue
        try:
            address = ipaddress.ip_address(resource.ip_address)
        except ValueError:
            continue
        if not isinstance(address, ipaddress.IPv4Address) or address not in vm_network:
            continue
        host = str(address)
        targets.append(
            WireGuardConnectionTarget(
                vmid=resource.vmid,
                name=resource.name,
                service="ssh",
                host=host,
                port=_SSH_PORT,
            )
        )
        if resource.type == "qemu":
            targets.append(
                WireGuardConnectionTarget(
                    vmid=resource.vmid,
                    name=resource.name,
                    service="rdp",
                    host=host,
                    port=_RDP_PORT,
                )
            )
    return targets


def _endpoint_dicts(
    targets: list[WireGuardConnectionTarget],
) -> list[dict[str, object]]:
    return [target.model_dump() for target in targets]


def _validated_endpoint_tuple(endpoint: dict[str, object]) -> tuple[str, str, int]:
    service = str(endpoint.get("service", ""))
    expected_port = (
        _SSH_PORT if service == "ssh" else _RDP_PORT if service == "rdp" else 0
    )
    try:
        vmid = int(endpoint.get("vmid", 0))
        host = ipaddress.ip_address(str(endpoint.get("host", "")))
        port = int(endpoint.get("port", 0))
    except (TypeError, ValueError) as exc:
        raise BadRequestError(t("wireguard.storedAclEndpointInvalid")) from exc
    if (
        vmid <= 0
        or not isinstance(host, ipaddress.IPv4Address)
        or host not in _vm_network()
        or port != expected_port
    ):
        raise BadRequestError(t("wireguard.storedAclEndpointInvalid"))
    return service, str(host), port


def _gateway_client(session: Session):
    config = gateway_config_repo.get_gateway_config(session)
    if config is None or not config.host or not config.encrypted_private_key:
        raise BadRequestError(t("wireguard.gatewayNotConfigured"))
    private_key = gateway_config_repo.get_decrypted_private_key(config)
    client = gateway_service._make_client(  # noqa: SLF001
        config.host,
        config.ssh_port,
        config.ssh_user,
        private_key,
    )
    return config, client


def _run_locked(client, script: str, error_message: str) -> str:
    command = (
        f"flock -w 15 {shlex.quote(_GATEWAY_LOCK)} sh -eu -c {shlex.quote(script)}"
    )
    return gateway_service._exec_checked(  # noqa: SLF001
        client, command, error_message
    )


def _nft_tuple(address: str, endpoint: dict[str, object]) -> str:
    _, host, port = _validated_endpoint_tuple(endpoint)
    return f"{address} . {host} . {port}"


def _sync_gateway_peer(
    *,
    session: Session,
    public_key: str,
    tunnel_ip: str,
    old_public_key: str | None,
    old_endpoints: list[dict[str, object]],
    new_endpoints: list[dict[str, object]],
) -> str:
    address = str(ipaddress.ip_address(tunnel_ip))
    public_key = _validate_public_key(public_key)
    if old_public_key:
        old_public_key = _validate_public_key(old_public_key)

    delete_tuples = {
        _nft_tuple(address, endpoint) for endpoint in [*old_endpoints, *new_endpoints]
    }
    delete_lines = [
        (
            f"nft delete element {_NFT_FAMILY} {_NFT_TABLE} {_NFT_SET} "
            f"{{ {value} }} 2>/dev/null || true"
        )
        for value in sorted(delete_tuples)
    ]
    ttl = max(60, min(settings.WIREGUARD_SESSION_TTL_SECONDS, 86400))
    add_lines = [
        (
            f"nft add element {_NFT_FAMILY} {_NFT_TABLE} {_NFT_SET} "
            f"{{ {_nft_tuple(address, endpoint)} timeout {ttl}s }}"
        )
        for endpoint in new_endpoints
    ]
    cleanup_lines = [
        *[
            (
                f"nft delete element {_NFT_FAMILY} {_NFT_TABLE} {_NFT_SET} "
                f"{{ {_nft_tuple(address, endpoint)} }} 2>/dev/null || true"
            )
            for endpoint in new_endpoints
        ],
        f"wg set {shlex.quote(settings.WIREGUARD_INTERFACE)} peer {shlex.quote(public_key)} remove 2>/dev/null || true",
    ]
    cleanup = "; ".join(cleanup_lines)
    lines = [
        "set -eu",
        f"systemctl is-active --quiet wg-quick@{shlex.quote(settings.WIREGUARD_INTERFACE)}",
        "systemctl is-active --quiet campus-cloud-wg-firewall.service",
        f"trap {shlex.quote(cleanup)} EXIT",
    ]
    if old_public_key and old_public_key != public_key:
        lines.append(
            f"wg set {shlex.quote(settings.WIREGUARD_INTERFACE)} peer {shlex.quote(old_public_key)} remove 2>/dev/null || true"
        )
    lines.extend(
        [
            f"wg set {shlex.quote(settings.WIREGUARD_INTERFACE)} peer {shlex.quote(public_key)} allowed-ips {address}/32",
            *delete_lines,
            *add_lines,
            "trap - EXIT",
            "cat /etc/wireguard/server_public.key",
        ]
    )

    _, client = _gateway_client(session)
    try:
        gateway_public_key = _run_locked(
            client,
            "\n".join(lines),
            t("wireguard.syncPeerFailed"),
        ).strip()
    finally:
        client.close()
    return _validate_public_key(gateway_public_key)


def _remove_gateway_access(
    *,
    session: Session,
    public_key: str,
    tunnel_ip: str,
    endpoints: list[dict[str, object]],
) -> None:
    address = str(ipaddress.ip_address(tunnel_ip))
    public_key = _validate_public_key(public_key)
    lines = [
        "set -eu",
        f"wg set {shlex.quote(settings.WIREGUARD_INTERFACE)} peer {shlex.quote(public_key)} remove 2>/dev/null || true",
    ]
    for endpoint in endpoints:
        lines.append(
            f"nft delete element {_NFT_FAMILY} {_NFT_TABLE} {_NFT_SET} "
            f"{{ {_nft_tuple(address, endpoint)} }} 2>/dev/null || true"
        )
    _, client = _gateway_client(session)
    try:
        _run_locked(
            client,
            "\n".join(lines),
            t("wireguard.revokePeerFailed"),
        )
    finally:
        client.close()


def _remove_gateway_peer(*, session: Session, peer: WireGuardPeer) -> None:
    _remove_gateway_access(
        session=session,
        public_key=peer.public_key,
        tunnel_ip=peer.tunnel_ip,
        endpoints=list(peer.allowed_endpoints or []),
    )


def _format_endpoint(host: str, port: int) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return f"{host}:{port}"
    return f"[{address}]:{port}" if address.version == 6 else f"{address}:{port}"


def _endpoint_host(session: Session) -> str:
    config = gateway_config_repo.get_gateway_config(session)
    host = settings.WIREGUARD_ENDPOINT_HOST.strip() or (config.host if config else "")
    if not host:
        raise BadRequestError(t("wireguard.endpointHostNotConfigured"))
    return host


def _connect_response(
    *,
    peer: WireGuardPeer,
    gateway_public_key: str,
    endpoint_host: str,
    targets: list[WireGuardConnectionTarget],
    ttl: int,
) -> WireGuardConnectResponse:
    return WireGuardConnectResponse(
        interface_name="SkyLab",
        interface_address=f"{peer.tunnel_ip}/32",
        gateway_public_key=gateway_public_key,
        endpoint=_format_endpoint(endpoint_host, settings.WIREGUARD_ENDPOINT_PORT),
        allowed_ips=[str(_vm_network())],
        persistent_keepalive=settings.WIREGUARD_KEEPALIVE_SECONDS,
        expires_in=ttl,
        connections=targets,
    )


def _activate_peer(
    *,
    session: Session,
    peer: WireGuardPeer,
    public_key: str,
    update_last_connected: bool,
) -> WireGuardConnectResponse:
    old_public_key = peer.public_key
    old_endpoints = list(peer.allowed_endpoints or [])
    targets = _resource_targets(session=session, user_id=peer.user_id)
    new_endpoints = _endpoint_dicts(targets)
    endpoint_host = _endpoint_host(session)
    gateway_public_key = _sync_gateway_peer(
        session=session,
        public_key=public_key,
        tunnel_ip=peer.tunnel_ip,
        old_public_key=old_public_key,
        old_endpoints=old_endpoints,
        new_endpoints=new_endpoints,
    )

    now = _now()
    ttl = max(60, min(settings.WIREGUARD_SESSION_TTL_SECONDS, 86400))
    peer.public_key = public_key
    peer.allowed_endpoints = new_endpoints
    peer.active = True
    peer.updated_at = now
    if update_last_connected or peer.last_connected_at is None:
        peer.last_connected_at = now
    peer.expires_at = now + timedelta(seconds=ttl)
    peer.revoked_at = None
    try:
        peer_repo.save(session=session, peer=peer)
    except Exception:
        # Do not leave a usable Gateway ACL when persistence fails after the
        # remote synchronization succeeded. The nft timeout remains a final
        # fail-closed fallback if the compensating SSH operation also fails.
        session.rollback()
        try:
            _remove_gateway_access(
                session=session,
                public_key=public_key,
                tunnel_ip=peer.tunnel_ip,
                endpoints=new_endpoints,
            )
        except Exception:
            logger.exception(
                "Unable to compensate Gateway WireGuard access after DB failure"
            )
        raise

    return _connect_response(
        peer=peer,
        gateway_public_key=gateway_public_key,
        endpoint_host=endpoint_host,
        targets=targets,
        ttl=ttl,
    )


def connect(
    *,
    session: Session,
    user_id: uuid.UUID,
    device_id: str,
    public_key: str,
) -> WireGuardConnectResponse:
    if settings.DESKTOP_TUNNEL_MODE != "wireguard":
        raise BadRequestError(t("wireguard.desktopConnectionsDisabled"))
    public_key = _validate_public_key(public_key)
    peer = _get_or_create_peer(
        session=session,
        user_id=user_id,
        device_id=device_id,
        public_key=public_key,
    )
    return _activate_peer(
        session=session,
        peer=peer,
        public_key=public_key,
        update_last_connected=True,
    )


def refresh(
    *, session: Session, user_id: uuid.UUID, device_id: str
) -> WireGuardConnectResponse:
    if settings.DESKTOP_TUNNEL_MODE != "wireguard":
        raise BadRequestError(t("wireguard.desktopConnectionsDisabled"))
    peer = peer_repo.get_by_user_device(
        session=session, user_id=user_id, device_id=device_id
    )
    now = _now()
    if (
        peer is None
        or not peer.active
        or peer.expires_at is None
        or peer.expires_at <= now
    ):
        raise ConflictError(t("wireguard.sessionInactiveOrExpired"))
    return _activate_peer(
        session=session,
        peer=peer,
        public_key=peer.public_key,
        update_last_connected=False,
    )


def disconnect(*, session: Session, user_id: uuid.UUID, device_id: str) -> bool:
    peer = peer_repo.get_by_user_device(
        session=session, user_id=user_id, device_id=device_id
    )
    if peer is None or not peer.active:
        return True
    _remove_gateway_peer(session=session, peer=peer)
    now = _now()
    peer.active = False
    peer.allowed_endpoints = []
    peer.updated_at = now
    peer.expires_at = now
    peer.revoked_at = now
    peer_repo.save(session=session, peer=peer)
    return True


def _gateway_state_id(session: Session) -> str:
    """Return a token that changes when the Gateway, wg0, or ACL service restarts."""
    _, client = _gateway_client(session)
    command = "\n".join(
        [
            "cat /proc/sys/kernel/random/boot_id",
            (
                "systemctl show -p ActiveEnterTimestampMonotonic --value "
                f"wg-quick@{shlex.quote(settings.WIREGUARD_INTERFACE)}"
            ),
            (
                "systemctl show -p ActiveEnterTimestampMonotonic --value "
                "campus-cloud-wg-firewall.service"
            ),
        ]
    )
    try:
        state_id = gateway_service._exec_checked(  # noqa: SLF001
            client,
            command,
            t("wireguard.inspectStateFailed"),
        ).strip()
    finally:
        client.close()
    if not state_id:
        raise RuntimeError("Gateway VM returned an empty WireGuard state token")
    return state_id


def _mark_peer_inactive(session: Session, peer: WireGuardPeer, now: datetime) -> None:
    peer.active = False
    peer.allowed_endpoints = []
    peer.updated_at = now
    peer.expires_at = now
    peer.revoked_at = now
    peer_repo.save(session=session, peer=peer)


def reconcile_once() -> None:
    """Expire stale leases and replay live peers after Gateway service restarts."""
    now = _now()
    with Session(engine) as session:
        expired = peer_repo.list_expired_active(session=session, now=now)
        for peer in expired:
            try:
                _remove_gateway_peer(session=session, peer=peer)
            except Exception:
                logger.exception(
                    "Unable to remove expired WireGuard peer %s from Gateway",
                    peer.id,
                )
            _mark_peer_inactive(session, peer, now)

        retention_days = max(1, settings.WIREGUARD_REVOKED_RETENTION_DAYS)
        cutoff = now - timedelta(days=retention_days)
        for peer in peer_repo.list_revoked_before(session=session, cutoff=cutoff):
            try:
                # Retry remote cleanup before freeing the unique tunnel address.
                _remove_gateway_peer(session=session, peer=peer)
            except Exception:
                logger.exception(
                    "Keeping revoked WireGuard peer %s because Gateway cleanup failed",
                    peer.id,
                )
                continue
            session.delete(peer)
            session.commit()

        peers = peer_repo.list_active_unexpired(session=session, now=now)
        if not peers:
            return
        try:
            state_id = _gateway_state_id(session)
        except Exception:
            # An unreachable Gateway is different from a rejected peer replay.
            # Keep leases intact so a transient outage can recover next minute.
            logger.exception("Unable to inspect Gateway WireGuard state")
            return
        if state_id == _reconcile_state.last_gateway_state_id:
            return

        logger.info(
            "Gateway WireGuard state changed; replaying %d active peer(s)",
            len(peers),
        )
        for peer in peers:
            endpoints = list(peer.allowed_endpoints or [])
            try:
                _sync_gateway_peer(
                    session=session,
                    public_key=peer.public_key,
                    tunnel_ip=peer.tunnel_ip,
                    old_public_key=peer.public_key,
                    old_endpoints=endpoints,
                    new_endpoints=endpoints,
                )
            except Exception:
                logger.exception(
                    "WireGuard peer %s replay failed; marking it inactive",
                    peer.id,
                )
                _mark_peer_inactive(session, peer, now)
        _reconcile_state.last_gateway_state_id = state_id


async def run_reconciler(stop_event: asyncio.Event) -> None:
    interval = max(10, settings.WIREGUARD_RECONCILE_INTERVAL_SECONDS)
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(reconcile_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("WireGuard reconciliation tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            # 逾時代表這一輪等待結束、進入下一個 reconcile tick
            pass


__all__ = ["connect", "disconnect", "reconcile_once", "refresh", "run_reconciler"]
