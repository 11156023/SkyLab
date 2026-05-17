"""Tunnel proxy service — manages STCP proxies on Gateway VM's frpc.

Responsibilities:
1. Register STCP server proxies when VMs are provisioned
2. Rebuild Gateway frpc.toml from DB and reload frpc
3. Provide visitor config data for the desktop client API
"""

import logging
import uuid

from sqlmodel import Session

from app.core.config import settings
from app.models.tunnel_proxy import TunnelProxy
from app.repositories import tunnel_proxy as tp_repo
from app.services.network import gateway_service

logger = logging.getLogger(__name__)

# Port-assignment conventions for the visitor (desktop client) side.
# These map to localhost ports so they don't clash with common services.
_VISITOR_PORT_BASE = {
    "ssh": 60000,   # VM 150 SSH → localhost:60150
    "rdp": 70000,   # VM 150 RDP → localhost:70150
}

_INTERNAL_PORTS = {
    "ssh": 22,
    "rdp": 3389,
}

# Gateway frpc.toml managed-section markers
_MANAGED_BEGIN = "# BEGIN_CAMPUS_CLOUD_TUNNELS"
_MANAGED_END = "# END_CAMPUS_CLOUD_TUNNELS"


# ─── Register / unregister ────────────────────────────────────────────────────


def register_vm(
    *,
    session: Session,
    vmid: int,
    user_id: uuid.UUID,
    vm_type: str,
    ip_address: str | None = None,
) -> list[TunnelProxy]:
    """Create STCP tunnel proxy records for a newly provisioned VM.

    Creates SSH proxy for all VMs; adds RDP proxy for qemu VMs.
    Then syncs the Gateway frpc.toml.
    """
    existing = tp_repo.get_proxies_by_vmid(session=session, vmid=vmid)
    if existing:
        logger.info("VM %d already has %d tunnel proxies, skipping", vmid, len(existing))
        return existing

    services = ["ssh"]
    if vm_type == "qemu":
        services.append("rdp")

    created: list[TunnelProxy] = []
    for svc in services:
        proxy = tp_repo.create_proxy(
            session=session,
            vmid=vmid,
            user_id=user_id,
            service=svc,
            internal_port=_INTERNAL_PORTS[svc],
            proxy_name=f"vm-{vmid}-{svc}",
            visitor_port=_VISITOR_PORT_BASE[svc] + vmid,
            commit=False,
        )
        created.append(proxy)

    session.commit()
    logger.info("Registered %d tunnel proxies for VM %d", len(created), vmid)

    # Best-effort sync to Gateway — don't fail the provisioning if Gateway
    # is unreachable.
    try:
        sync_gateway_frpc(session=session)
    except Exception:
        logger.warning("Failed to sync Gateway frpc after registering VM %d", vmid, exc_info=True)

    return created


def unregister_vm(*, session: Session, vmid: int) -> int:
    """Remove all tunnel proxies for a VM and sync Gateway."""
    count = tp_repo.delete_proxies_by_vmid(session=session, vmid=vmid)
    if count:
        try:
            sync_gateway_frpc(session=session)
        except Exception:
            logger.warning("Failed to sync Gateway frpc after unregistering VM %d", vmid, exc_info=True)
    return count


# ─── Gateway frpc.toml sync ──────────────────────────────────────────────────


def _build_managed_block(proxies: list[TunnelProxy], resource_ips: dict[int, str]) -> str:
    """Build the STCP server-proxy section for Gateway frpc.toml."""
    lines = [_MANAGED_BEGIN, ""]
    for p in proxies:
        ip = resource_ips.get(p.vmid)
        if not ip:
            lines.append(f"# vm-{p.vmid}-{p.service} skipped: no IP address known")
            lines.append("")
            continue
        lines.append("[[proxies]]")
        lines.append(f'name = "{p.proxy_name}"')
        lines.append('type = "stcp"')
        lines.append(f'secretKey = "{p.secret_key}"')
        lines.append(f'localIP = "{ip}"')
        lines.append(f"localPort = {p.internal_port}")
        lines.append("")
    lines.append(_MANAGED_END)
    return "\n".join(lines)


def sync_gateway_frpc(*, session: Session) -> None:
    """Rebuild the managed section of Gateway frpc.toml and reload frpc."""
    from app.repositories import resource as resource_repo  # noqa: PLC0415

    all_proxies = tp_repo.get_all_proxies(session=session)
    if not all_proxies:
        logger.info("No tunnel proxies in DB; rewriting frpc managed block as empty")

    # Build vmid → IP mapping from resources table + Proxmox fallback
    vmids = list({p.vmid for p in all_proxies})
    resource_ips: dict[int, str] = {}
    missing_ip_vmids: list[int] = []

    for vmid in vmids:
        cached_ip = resource_repo.get_cached_ip_address(session=session, vmid=vmid)
        if cached_ip:
            resource_ips[vmid] = cached_ip
        else:
            missing_ip_vmids.append(vmid)

    # For VMs without cached IP, try Proxmox API
    if missing_ip_vmids:
        try:
            from app.infrastructure.proxmox import (
                operations as pve_ops,  # noqa: PLC0415
            )

            cluster_resources = pve_ops.list_all_resources()
            vmid_info = {
                r["vmid"]: (r.get("node", ""), r.get("type", ""))
                for r in cluster_resources
            }
            for vmid in missing_ip_vmids:
                info = vmid_info.get(vmid)
                if not info:
                    continue
                node, pve_type = info
                vm_type = "lxc" if pve_type == "lxc" else "qemu"
                try:
                    ip = pve_ops.get_ip_address(node, vmid, vm_type)
                    if ip:
                        resource_ips[vmid] = ip
                        # Cache the IP in DB
                        resource_repo.update_ip_address(
                            session=session, vmid=vmid, ip_address=ip
                        )
                        logger.info("Resolved IP for VM %d: %s", vmid, ip)
                except Exception:
                    logger.debug("Could not get IP for VM %d from Proxmox", vmid)
        except Exception:
            logger.warning("Failed to query Proxmox for missing IPs", exc_info=True)

    managed_block = _build_managed_block(all_proxies, resource_ips)

    # Read current frpc.toml
    current_config = gateway_service.read_service_config(session, "frpc")

    # Replace or append managed section
    if _MANAGED_BEGIN in current_config:
        before = current_config[: current_config.index(_MANAGED_BEGIN)]
        after_marker = current_config.find(_MANAGED_END)
        if after_marker != -1:
            after = current_config[after_marker + len(_MANAGED_END) :]
        else:
            after = ""
        new_config = before + managed_block + after
    else:
        new_config = current_config.rstrip() + "\n\n" + managed_block + "\n"

    gateway_service.write_service_config(session, "frpc", new_config)

    # Reload frpc so the new proxies take effect
    ok, msg = gateway_service.control_service(session, "frpc", "reload")
    if not ok:
        # If reload fails (frpc might not support it), try restart
        ok, msg = gateway_service.control_service(session, "frpc", "restart")
    if ok:
        logger.info("Gateway frpc synced: %d proxies", len(all_proxies))
    else:
        logger.warning("Gateway frpc reload/restart failed: %s", msg)


# ─── Desktop client config ───────────────────────────────────────────────────


def get_visitor_config_for_user(
    *, session: Session, user_id: uuid.UUID
) -> dict:
    """Return the data the desktop client needs to build its frpc visitor config.

    Returns a dict with:
      - frpc_config: complete frpc visitor TOML config (ready to write to disk)
      - tunnels: list of {proxy_name, service, vmid, visitor_port, vm_name}
        (metadata only — no secrets)
    """
    from app.repositories import resource as resource_repo  # noqa: PLC0415

    proxies = tp_repo.get_proxies_by_user(session=session, user_id=user_id)

    tunnels = []
    for p in proxies:
        res = resource_repo.get_resource_by_vmid(session=session, vmid=p.vmid)
        tunnels.append({
            "proxy_name": p.proxy_name,
            "service": p.service,
            "vmid": p.vmid,
            "visitor_port": p.visitor_port,
            "vm_name": getattr(res, "environment_type", None) or f"VM-{p.vmid}",
        })

    # Build complete frpc visitor TOML so secrets stay server-side
    frpc_config = _build_visitor_toml(proxies)

    return {
        "frpc_config": frpc_config,
        "tunnels": tunnels,
    }


def _build_visitor_toml(proxies: list[TunnelProxy]) -> str:
    """Build a complete frpc visitor TOML config string."""
    lines = [
        f'serverAddr = "{settings.FRP_SERVER_ADDR}"',
        f"serverPort = {settings.FRP_SERVER_PORT}",
        "",
        'auth.method = "token"',
        f'auth.token = "{settings.FRP_TOKEN}"',
        "",
    ]
    for p in proxies:
        lines.append("[[visitors]]")
        lines.append(f'name = "{p.proxy_name}-visitor"')
        lines.append('type = "stcp"')
        lines.append(f'serverName = "{p.proxy_name}"')
        lines.append(f'secretKey = "{p.secret_key}"')
        lines.append('bindAddr = "127.0.0.1"')
        lines.append(f"bindPort = {p.visitor_port}")
        lines.append("")
    return "\n".join(lines)
