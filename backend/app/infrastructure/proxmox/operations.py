"""Centralized Proxmox VE API operations.

Provides a single place for common PVE interactions (resource lookup, config,
control, resize, specs, session ticket, etc.) so that callers no longer
duplicate the same cluster.resources iteration or qemu/lxc dispatch logic.
"""

import logging
from collections.abc import Callable
from typing import Literal

import httpx

from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.infrastructure.proxmox import (
    basic_blocking_task_status,
    get_active_host,
    get_proxmox_api,
    get_proxmox_settings,
    wait_for_task_status,
)

logger = logging.getLogger(__name__)

ResourceType = Literal["qemu", "lxc"]


# ---------------------------------------------------------------------------
# Resource lookup
# ---------------------------------------------------------------------------

def _raw_vms() -> list[dict]:
    """Return all cluster resources of type vm without pool filtering."""
    proxmox = get_proxmox_api()
    return proxmox.cluster.resources.get(type="vm")


def find_resource(vmid: int) -> dict:
    """Find any resource (qemu or lxc) by VMID in the configured pool."""
    pool = get_proxmox_settings().pool_name
    for r in _raw_vms():
        if r["vmid"] == vmid and r.get("pool") == pool:
            return r
    raise NotFoundError(f"Resource {vmid} not found")


def find_lxc(vmid: int) -> dict:
    """Find an LXC container by VMID in the configured pool."""
    pool = get_proxmox_settings().pool_name
    for r in _raw_vms():
        if r["vmid"] == vmid and r["type"] == "lxc" and r.get("pool") == pool:
            return r
    raise NotFoundError(f"LXC container {vmid} not found")


def list_all_resources() -> list[dict]:
    """Return all cluster resources of type vm in the configured pool."""
    pool = get_proxmox_settings().pool_name
    return [r for r in _raw_vms() if r.get("pool") == pool]


def list_nodes() -> list[dict]:
    """Return all cluster nodes."""
    proxmox = get_proxmox_api()
    return proxmox.nodes.get()


def get_available_nodes() -> list[dict]:
    """Return online nodes first, or all nodes if status data is unavailable."""
    nodes = list_nodes()
    online_nodes = [node for node in nodes if node.get("status") == "online"]
    return online_nodes or nodes


def pick_target_node(preferred_node: str | None = None) -> str:
    """Pick a usable target node, preferring an explicitly requested one.

    Priority: preferred_node > settings.default_node > nodes[0]
    """
    nodes = get_available_nodes()
    if not nodes:
        raise ProxmoxError("No Proxmox nodes are available")

    candidate = preferred_node or get_proxmox_settings().default_node
    if candidate:
        for node in nodes:
            node_name = node.get("node") or node.get("name")
            if node_name == candidate:
                return node_name
        logger.warning(
            "Preferred node '%s' not found or offline; falling back to first available node",
            candidate,
        )

    selected = nodes[0].get("node") or nodes[0].get("name")
    if not selected:
        raise ProxmoxError("No usable Proxmox node name was returned")
    return selected


def list_node_storages(node: str) -> list[dict]:
    """Return storages visible on a node."""
    proxmox = get_proxmox_api()
    return proxmox.nodes(node).storage.get()


def _storage_name(storage: dict) -> str | None:
    return storage.get("storage") or storage.get("id")


def _storage_is_enabled(storage: dict) -> bool:
    enabled = storage.get("enabled")
    if enabled is None:
        return storage.get("disable") not in (1, "1", True, "true")
    return enabled not in (0, "0", False, "false")


def _storage_is_active(storage: dict) -> bool:
    active = storage.get("active")
    if active is None:
        return storage.get("status") != "disabled"
    return active not in (0, "0", False, "false")


def _storage_supports_content(storage: dict, required_content: str) -> bool:
    content = storage.get("content")
    if not content:
        return True
    supported = {part.strip() for part in str(content).split(",") if part.strip()}
    return required_content in supported


def resolve_target_storage(
    node: str,
    requested_storage: str | None,
    *,
    required_content: Literal["images", "rootdir"],
) -> str:
    """Pick a usable storage on a node, falling back when the requested one is unavailable."""
    storages = list_node_storages(node)
    compatible = [
        storage
        for storage in storages
        if _storage_is_enabled(storage)
        and _storage_is_active(storage)
        and _storage_supports_content(storage, required_content)
    ]

    if requested_storage:
        for storage in compatible:
            if _storage_name(storage) == requested_storage:
                return requested_storage

        logger.warning(
            "Storage %s is unavailable on node %s for content %s; attempting fallback",
            requested_storage,
            node,
            required_content,
        )

    if compatible:
        fallback = _storage_name(compatible[0])
        if fallback:
            return fallback

    available_names = [
        name
        for storage in storages
        if (name := _storage_name(storage))
    ]
    raise BadRequestError(
        "No enabled Proxmox storage is available on "
        f"node '{node}' for content '{required_content}'. "
        f"Configured/requested storage: '{requested_storage or get_proxmox_settings().data_storage}'. "
        f"Node storages: {', '.join(available_names) if available_names else 'none'}."
    )


def find_vm_template(template_id: int) -> dict:
    """Find a VM template by VMID in the configured pool."""
    pool = get_proxmox_settings().pool_name
    for vm in _raw_vms():
        if (
            vm["vmid"] == template_id
            and vm.get("template") == 1
            and vm.get("pool") == pool
        ):
            return vm
    raise NotFoundError(f"VM template {template_id} not found")


# ---------------------------------------------------------------------------
# Node helper — dispatches qemu / lxc transparently
# ---------------------------------------------------------------------------

def _resource_api(node: str, vmid: int, resource_type: ResourceType):
    """Return the proxmoxer node resource handle (qemu or lxc)."""
    proxmox = get_proxmox_api()
    if resource_type == "qemu":
        return proxmox.nodes(node).qemu(vmid)
    return proxmox.nodes(node).lxc(vmid)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def get_config(node: str, vmid: int, resource_type: ResourceType) -> dict:
    """GET /nodes/{node}/{type}/{vmid}/config"""
    return _resource_api(node, vmid, resource_type).config.get()


def update_config(
    node: str, vmid: int, resource_type: ResourceType, **params
) -> None:
    """PUT /nodes/{node}/{type}/{vmid}/config"""
    _resource_api(node, vmid, resource_type).config.put(**params)


# ---------------------------------------------------------------------------
# Control (start / stop / reboot / shutdown / reset)
# ---------------------------------------------------------------------------

def control(
    node: str, vmid: int, resource_type: ResourceType, action: str
) -> None:
    """Execute a power action on a resource."""
    getattr(_resource_api(node, vmid, resource_type).status, action).post()


def get_status(node: str, vmid: int, resource_type: ResourceType) -> dict:
    """GET /nodes/{node}/{type}/{vmid}/status/current"""
    return _resource_api(node, vmid, resource_type).status.current.get()


# ---------------------------------------------------------------------------
# Disk resize
# ---------------------------------------------------------------------------

def resize_disk(
    node: str,
    vmid: int,
    resource_type: ResourceType,
    disk: str,
    size: str,
) -> None:
    """PUT /nodes/{node}/{type}/{vmid}/resize"""
    _resource_api(node, vmid, resource_type).resize.put(disk=disk, size=size)


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

def list_snapshots(node: str, vmid: int, resource_type: ResourceType) -> list:
    return _resource_api(node, vmid, resource_type).snapshot.get()


def create_snapshot(
    node: str,
    vmid: int,
    resource_type: ResourceType,
    wait_timeout_seconds: float | None = None,
    **params,
) -> str:
    task = _resource_api(node, vmid, resource_type).snapshot.post(**params)
    basic_blocking_task_status(node, task, timeout_seconds=wait_timeout_seconds)
    return task


def delete_snapshot(
    node: str, vmid: int, resource_type: ResourceType, snapname: str
) -> str:
    task = _resource_api(node, vmid, resource_type).snapshot(snapname).delete()
    basic_blocking_task_status(node, task)
    return task


def rollback_snapshot(
    node: str, vmid: int, resource_type: ResourceType, snapname: str
) -> str:
    task = _resource_api(node, vmid, resource_type).snapshot(snapname).rollback.post()
    basic_blocking_task_status(node, task)
    return task


# ---------------------------------------------------------------------------
# RRD stats
# ---------------------------------------------------------------------------

def get_rrd_data(
    node: str, vmid: int, resource_type: ResourceType, timeframe: str
) -> list[dict]:
    return _resource_api(node, vmid, resource_type).rrddata.get(timeframe=timeframe)


def get_node_rrd_data(node: str, timeframe: str) -> list[dict]:
    """GET /nodes/{node}/rrddata"""
    proxmox = get_proxmox_api()
    return proxmox.nodes(node).rrddata.get(timeframe=timeframe)


# ---------------------------------------------------------------------------
# Delete resource
# ---------------------------------------------------------------------------

def delete_resource(
    node: str, vmid: int, resource_type: ResourceType, **params
) -> str:
    task = _resource_api(node, vmid, resource_type).delete(**params)
    basic_blocking_task_status(node, task)
    return task


def migrate_resource(
    source_node: str,
    target_node: str,
    vmid: int,
    resource_type: ResourceType,
    *,
    online: bool = True,
    with_local_disks: bool = True,
    progress_callback: Callable[[dict], None] | None = None,
) -> str:
    if source_node == target_node:
        raise BadRequestError("Source and target nodes must be different for migration")

    params: dict[str, int | str] = {"target": target_node}
    if resource_type == "qemu":
        if online:
            params["online"] = 1
        if with_local_disks:
            params["with-local-disks"] = 1
    else:
        if online:
            params["restart"] = 1

    task = _resource_api(source_node, vmid, resource_type).migrate.post(**params)
    basic_blocking_task_status(
        source_node,
        task,
        progress_callback=progress_callback,
    )
    return task


# ---------------------------------------------------------------------------
# IP address
# ---------------------------------------------------------------------------

def _is_usable_ipv4(ip: str) -> bool:
    """過濾 loopback、link-local 等不可用的 IPv4 位址"""
    return (
        bool(ip)
        and not ip.startswith("127.")
        and not ip.startswith("169.254.")
        and ip != "0.0.0.0"
    )


def get_ip_address(node: str, vmid: int, resource_type: ResourceType) -> str | None:
    """取得 VM 的 IP 位址，掃描全部網卡（跳過 loopback / link-local）。"""
    proxmox = get_proxmox_api()
    try:
        if resource_type == "lxc":
            interfaces = proxmox.nodes(node).lxc(vmid).interfaces.get()
            for iface in interfaces or []:
                if iface.get("name") == "lo":
                    continue
                inet = iface.get("inet")
                if inet:
                    ip = inet.split("/")[0]
                    if _is_usable_ipv4(ip):
                        return ip
        else:
            try:
                network_info = (
                    proxmox.nodes(node)
                    .qemu(vmid)("agent")("network-get-interfaces")
                    .get()
                )
                if network_info and "result" in network_info:
                    for iface in network_info["result"]:
                        if iface.get("name") == "lo":
                            continue
                        for ip_entry in iface.get("ip-addresses", []):
                            if ip_entry.get("ip-address-type") == "ipv4":
                                ip = ip_entry.get("ip-address", "")
                                if _is_usable_ipv4(ip):
                                    return ip
            except Exception:
                # 單一查詢來源失敗時繼續嘗試下一個
                pass
    except Exception as e:
        logger.debug(f"Failed to get IP for VMID {vmid}: {e}")
    return None


# ---------------------------------------------------------------------------
# Current specs (parsed from config)
# ---------------------------------------------------------------------------

def get_current_specs(node: str, vmid: int, resource_type: ResourceType) -> dict:
    """Returns {"cpu": int|None, "memory": int|None, "disk": int|None}."""
    config = get_config(node, vmid, resource_type)

    current_cpu = config.get("cores") or config.get("cpus")
    current_memory = config.get("memory")
    current_disk = None

    if resource_type == "qemu":
        scsi0 = config.get("scsi0", "")
        if "size=" in scsi0:
            size_str = scsi0.split("size=")[1].split(",")[0].split(")")[0]
            if size_str.endswith("G"):
                current_disk = int(size_str[:-1])
    else:
        rootfs = config.get("rootfs", "")
        if "size=" in rootfs:
            size_str = rootfs.split("size=")[1].split(",")[0]
            if size_str.endswith("G"):
                current_disk = int(size_str[:-1])

    return {"cpu": current_cpu, "memory": current_memory, "disk": current_disk}


# ---------------------------------------------------------------------------
# LXC creation
# ---------------------------------------------------------------------------

def create_lxc(node: str, **config) -> str:
    """Create an LXC container and wait for the task to finish. Returns UPID."""
    proxmox = get_proxmox_api()
    task = proxmox.nodes(node).lxc.create(**config)
    basic_blocking_task_status(node, task)
    return task


# ---------------------------------------------------------------------------
# VM clone + configure
# ---------------------------------------------------------------------------

def clone_vm(node: str, template_id: int, **clone_config) -> str:
    """Clone a VM template and wait. Returns UPID."""
    proxmox = get_proxmox_api()
    task = proxmox.nodes(node).qemu(template_id).clone.post(**clone_config)
    basic_blocking_task_status(node, task)
    return task


def clone_lxc(node: str, template_id: int, **clone_config) -> str:
    """Clone an LXC template and wait. Returns UPID."""
    proxmox = get_proxmox_api()
    task = proxmox.nodes(node).lxc(template_id).clone.post(**clone_config)
    basic_blocking_task_status(node, task)
    return task


def convert_to_template(
    node: str, vmid: int, resource_type: ResourceType = "qemu"
) -> None:
    """POST /nodes/{node}/{type}/{vmid}/template — 轉為唯讀範本（不可逆）。

    VM 必須處於 stopped 狀態，呼叫端負責先關機。
    """
    _resource_api(node, vmid, resource_type).template.post()


def next_vmid() -> int:
    proxmox = get_proxmox_api()
    return int(proxmox.cluster.nextid.get())


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def get_lxc_templates(node: str) -> list[dict]:
    proxmox = get_proxmox_api()
    return proxmox.nodes(node).storage(get_proxmox_settings().iso_storage).content.get()


def get_vm_templates() -> list[dict]:
    """Return all VM templates in the configured pool."""
    pool = get_proxmox_settings().pool_name
    return [
        vm
        for vm in _raw_vms()
        if vm.get("template") == 1 and vm.get("pool") == pool
    ]


# ---------------------------------------------------------------------------
# Session ticket (for WebSocket auth — password-based, not API token)
# ---------------------------------------------------------------------------

async def get_session_ticket() -> tuple[str, str]:
    """Authenticate via password and return (pve_auth_cookie, csrf_token).

    Proxmox WebSocket endpoints (termproxy, vncproxy) require a session
    ticket obtained via password auth; API tokens are not accepted.
    """
    import ssl as _ssl

    cfg = get_proxmox_settings()
    if cfg.ca_cert:
        # Build a custom SSL context that accepts the PVE self-signed CA cert
        _ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
        _ctx.check_hostname = False
        _ctx.verify_mode = _ssl.CERT_REQUIRED
        _ctx.load_verify_locations(cadata=cfg.ca_cert)
        if hasattr(_ssl, "VERIFY_X509_STRICT"):
            _ctx.verify_flags &= ~_ssl.VERIFY_X509_STRICT
        verify: bool | _ssl.SSLContext = _ctx
    else:
        verify = cfg.verify_ssl

    async with httpx.AsyncClient(verify=verify) as client:
        resp = await client.post(
            f"https://{get_active_host()}:8006/api2/json/access/ticket",
            data={
                "username": cfg.user,
                "password": cfg.password,
            },
        )
        if resp.status_code != 200:
            raise ProxmoxError(
                f"Proxmox session authentication failed: HTTP {resp.status_code}"
            )
        data = resp.json()["data"]
        return data["ticket"], data.get("CSRFPreventionToken", "")


async def get_vnc_ticket_with_session(
    node: str,
    vmid: int,
    pve_auth_cookie: str,
    csrf_token: str,
) -> dict:
    """Get a VM VNC proxy ticket using the same PVE session used for websocket auth."""
    import ssl as _ssl

    cfg = get_proxmox_settings()
    if cfg.ca_cert:
        _ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
        _ctx.check_hostname = False
        _ctx.verify_mode = _ssl.CERT_REQUIRED
        _ctx.load_verify_locations(cadata=cfg.ca_cert)
        if hasattr(_ssl, "VERIFY_X509_STRICT"):
            _ctx.verify_flags &= ~_ssl.VERIFY_X509_STRICT
        verify: bool | _ssl.SSLContext = _ctx
    else:
        verify = cfg.verify_ssl

    headers = {"Cookie": f"PVEAuthCookie={pve_auth_cookie}"}
    if csrf_token:
        headers["CSRFPreventionToken"] = csrf_token

    async with httpx.AsyncClient(verify=verify) as client:
        resp = await client.post(
            f"https://{get_active_host()}:8006/api2/json/nodes/{node}/qemu/{vmid}/vncproxy",
            data={"websocket": 1},
            headers=headers,
        )
        if resp.status_code != 200:
            raise ProxmoxError(
                f"Proxmox VNC ticket creation failed: HTTP {resp.status_code}"
            )
        return resp.json()["data"]


async def wait_task(task_id: str, node: str, check_interval: int | None = None) -> dict:
    return await wait_for_task_status(
        node_name=node,
        task_id=task_id,
        check_interval=check_interval,
    )


# ---------------------------------------------------------------------------
# Console tickets
# ---------------------------------------------------------------------------

def get_terminal_ticket(node: str, vmid: int) -> dict:
    """Get termproxy ticket for an LXC container (port + ticket)."""
    proxmox = get_proxmox_api()
    return proxmox.nodes(node).lxc(vmid).termproxy.post()


def get_vnc_ticket(node: str, vmid: int) -> dict:
    """Get VNC proxy ticket for a VM (port + ticket)."""
    proxmox = get_proxmox_api()
    return proxmox.nodes(node).qemu(vmid).vncproxy.post(websocket=1)
