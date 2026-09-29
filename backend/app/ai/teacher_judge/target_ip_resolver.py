"""Teacher Judge target IP resolution helpers."""

from __future__ import annotations

import logging
from typing import Any, cast

from sqlmodel import Session

from app.infrastructure.proxmox import operations as proxmox_ops
from app.repositories import resource as resource_repo

logger = logging.getLogger(__name__)


def resolve_target_ip_address(
    *,
    session: Session,
    vmid: int,
    live_resource: dict[str, Any] | None = None,
) -> str:
    """Resolve target IP from cache, then Proxmox live state."""
    cached_ip = (
        resource_repo.get_cached_ip_address(session=session, vmid=vmid) or ""
    ).strip()
    if cached_ip:
        return cached_ip

    node = str((live_resource or {}).get("node") or "")
    resource_type = str((live_resource or {}).get("type") or "")
    if not node or resource_type not in {"qemu", "lxc"}:
        try:
            found = proxmox_ops.find_resource(vmid)
        except Exception as exc:
            logger.warning("VMID %s live resource lookup failed: %s", vmid, exc)
            return ""
        node = str(found.get("node") or "")
        resource_type = str(found.get("type") or "")

    if not node or resource_type not in {"qemu", "lxc"}:
        return ""

    try:
        live_ip = proxmox_ops.get_ip_address(
            node,
            vmid,
            cast(proxmox_ops.ResourceType, resource_type),
        )
    except Exception as exc:
        logger.warning("VMID %s live IP lookup failed: %s", vmid, exc)
        return ""

    live_ip = (live_ip or "").strip()
    if not live_ip:
        return ""

    # Writes the live IP back to the cache (rolling back quietly on failure)
    # and returns it unchanged.
    return (
        resource_repo.sync_ip_cache(session=session, vmid=vmid, live_ip=live_ip) or ""
    )
