import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from urllib.parse import quote

from sqlmodel import Session

from app.ai.pve_advisor import recommendation_service as advisor_service
from app.core.security import decrypt_value, encrypt_value
from app.exceptions import ProxmoxError
from app.infrastructure.proxmox import get_proxmox_settings
from app.infrastructure.ssh.client import generate_ed25519_keypair
from app.repositories import resource as resource_repo
from app.repositories import vm_request as vm_request_repo
from app.schemas import (
    LXCCreateRequest,
    LXCCreateResponse,
    TemplateSchema,
    VMCreateRequest,
    VMCreateResponse,
    VMTemplateSchema,
)
from app.services.network import (
    firewall_service,
    ip_management_service,
    tunnel_proxy_service,
)
from app.services.proxmox import gpu_service, proxmox_service
from app.services.user import audit_service
from app.services.vm import vm_request_placement_service
from app.utils.hostname import to_punycode_hostname

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def should_start_now(db_request) -> bool:
    if not getattr(db_request, "start_at", None):
        return True

    start_at = db_request.start_at
    end_at = getattr(db_request, "end_at", None)
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=UTC)
    if end_at and end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=UTC)
    if end_at and end_at <= _utc_now():
        return False
    return start_at <= _utc_now()


def _cleanup_failed_resource(node: str, vmid: int, resource_type: str) -> None:
    """Best-effort cleanup for a partially provisioned resource."""
    try:
        try:
            status = proxmox_service.get_status(node, vmid, resource_type)
            if status.get("status") == "running":
                proxmox_service.control(node, vmid, resource_type, "stop")
        except Exception:
            logger.warning("Failed to stop %s %s during cleanup", resource_type, vmid)

        delete_params = {"purge": 1}
        if resource_type == "qemu":
            delete_params["destroy-unreferenced-disks"] = 1
        proxmox_service.delete_resource(node, vmid, resource_type, **delete_params)
        logger.info("Cleaned up partially provisioned %s %s", resource_type, vmid)
    except Exception:
        logger.exception(
            "Failed to clean up partially provisioned %s %s", resource_type, vmid
        )


def cleanup_provisioned_resource(vmid: int) -> None:
    """Find and delete a resource created during a failed approval workflow."""
    resource = proxmox_service.find_resource(vmid)
    _cleanup_failed_resource(resource["node"], vmid, resource["type"])


def _gpu_mapping_nodes(mapping_id: str | None) -> set[str]:
    if not mapping_id:
        return set()

    mapping = gpu_service.get_gpu_mapping(str(mapping_id))
    return {
        str(item.node).strip()
        for item in mapping.maps
        if str(item.node).strip()
    }


def _select_request_placement(
    *,
    session: Session,
    db_request,
    placement_request,
    placement_strategy: str,
):
    compatible_nodes = _gpu_mapping_nodes(getattr(db_request, "gpu_mapping_id", None))

    pinned_node = getattr(db_request, "desired_node", None) or getattr(
        db_request, "assigned_node", None
    )
    if pinned_node:
        if compatible_nodes and str(pinned_node) not in compatible_nodes:
            raise ProxmoxError(
                f"Pinned node '{pinned_node}' is not compatible with GPU mapping "
                f"'{getattr(db_request, 'gpu_mapping_id', '')}'."
            )

        nodes, resources = advisor_service._load_cluster_state()
        cpu_overcommit_ratio, disk_overcommit_ratio = (
            vm_request_placement_service.get_overcommit_ratios(session)
        )
        node_capacities = advisor_service._build_node_capacities(
            nodes=nodes,
            resources=resources,
            cpu_overcommit_ratio=cpu_overcommit_ratio,
            disk_overcommit_ratio=disk_overcommit_ratio,
        )
        node_capacities = [
            item for item in node_capacities if item.node == str(pinned_node)
        ]
        effective_resource_type, resource_type_reason = advisor_service._decide_resource_type(
            placement_request
        )
        placement = vm_request_placement_service.CurrentPlacementSelection(
            node=str(pinned_node),
            strategy=placement_strategy,
            plan=vm_request_placement_service.build_plan(
                session=session,
                request=placement_request,
                node_capacities=node_capacities,
                effective_resource_type=effective_resource_type,
                resource_type_reason=resource_type_reason,
                placement_strategy=placement_strategy,
                node_priorities=vm_request_placement_service.get_node_priorities(session),
            ),
        )
        if not placement.plan.feasible or not placement.node:
            reserved_requests = []
            if getattr(db_request, "start_at", None) and getattr(db_request, "end_at", None):
                reserved_requests = [
                    item
                    for item in vm_request_repo.get_approved_vm_requests_overlapping_window(
                        session=session,
                        window_start=db_request.start_at,
                        window_end=db_request.end_at,
                    )
                    if item.id != db_request.id
                ]
            fallback = vm_request_placement_service.select_reserved_target_node(
                session=session,
                db_request=db_request,
                reserved_requests=reserved_requests,
            )
            if fallback.node and fallback.plan.feasible:
                if compatible_nodes and str(fallback.node) not in compatible_nodes:
                    raise ProxmoxError(
                        f"No feasible GPU-compatible node for mapping "
                        f"'{getattr(db_request, 'gpu_mapping_id', '')}' in the selected time window."
                    )
                logger.warning(
                    "Reserved node %s is no longer feasible for request %s; falling back to %s",
                    pinned_node,
                    getattr(db_request, "id", "unknown"),
                    fallback.node,
                )
                return fallback
        return placement

    selection = vm_request_placement_service.select_current_target_node(
        session=session,
        db_request=db_request,
    )
    if compatible_nodes and selection.node and str(selection.node) not in compatible_nodes:
        raise ProxmoxError(
            f"Selected node '{selection.node}' is not compatible with GPU mapping "
            f"'{getattr(db_request, 'gpu_mapping_id', '')}'."
        )
    return selection


def _get_lxc_target_node() -> str:
    return proxmox_service.pick_target_node()


def _get_vm_target_node(template_id: int) -> str:
    template = proxmox_service.find_vm_template(template_id)
    return template["node"]


def _resolve_managed_storage(
    *,
    session: Session,
    node: str,
    resource_type: str,
    requested_storage: str | None,
    disk_gb: int,
    required_content: str,
) -> str:
    preferred_storage = vm_request_placement_service.select_best_storage_name(
        session=session,
        node_name=node,
        resource_type=resource_type,
        disk_gb=disk_gb,
        fallback_storage=requested_storage,
    )
    if preferred_storage is None:
        raise ProxmoxError(
            f"No enabled managed storage is available on node {node} for {resource_type}"
        )
    return proxmox_service.resolve_target_storage(
        node,
        preferred_storage,
        required_content=required_content,
    )


def _dedupe_templates(templates: Iterable[dict]) -> list[dict]:
    unique: dict[str, dict] = {}
    for template in templates:
        volid = template.get("volid")
        if volid:
            unique[volid] = template
    return list(unique.values())


def create_lxc(
    *, session: Session, lxc_data: LXCCreateRequest, user_id: uuid.UUID
) -> LXCCreateResponse:
    vmid = proxmox_service.next_vmid()
    target_node = _get_lxc_target_node()
    target_storage = _resolve_managed_storage(
        session=session,
        node=target_node,
        resource_type="lxc",
        requested_storage=lxc_data.storage,
        disk_gb=int(lxc_data.rootfs_size or 8),
        required_content="rootdir",
    )
    # 取得網路配置並分配 IP
    net_cfg = ip_management_service.get_network_config_for_vm(session)
    allocated_ip = ip_management_service.allocate_ip(session, vmid, "lxc")

    created = False
    try:
        # Generate SSH key pair for platform access
        private_key_pem, public_key = generate_ed25519_keypair()

        net0_parts = (
            f"name=eth0,bridge={net_cfg['bridge_name']},"
            f"ip={allocated_ip}/{net_cfg['prefix_len']},"
            f"gw={net_cfg['gateway']},firewall=1"
        )
        config = {
            "vmid": vmid,
            "hostname": to_punycode_hostname(lxc_data.hostname),
            "ostemplate": lxc_data.ostemplate,
            "cores": lxc_data.cores,
            "memory": lxc_data.memory,
            "swap": 512,
            "rootfs": f"{target_storage}:{lxc_data.rootfs_size}",
            "password": lxc_data.password,
            "net0": net0_parts,
            "unprivileged": int(lxc_data.unprivileged),
            "start": int(lxc_data.start),
            "pool": get_proxmox_settings().pool_name,
            "features": "nesting=1",
            "ssh-public-keys": public_key,
        }
        if net_cfg.get("dns_servers"):
            config["nameserver"] = net_cfg["dns_servers"]

        result = proxmox_service.create_lxc(target_node, **config)
        created = True

        firewall_service.setup_default_rules(target_node, vmid, "lxc")

        resource_repo.create_resource(
            session=session,
            vmid=vmid,
            user_id=user_id,
            environment_type=lxc_data.environment_type,
            os_info=lxc_data.os_info,
            expiry_date=lxc_data.expiry_date,
            ssh_private_key_encrypted=encrypt_value(private_key_pem),
            ssh_public_key=public_key,
            service_template_slug=lxc_data.service_template_slug,
            commit=False,
        )

        audit_service.log_action(
            session=session,
            user_id=user_id,
            vmid=vmid,
            action="lxc_create",
            details=(
                f"Created LXC '{lxc_data.hostname}': "
                f"{lxc_data.cores} cores, {lxc_data.memory}MB RAM, "
                f"{lxc_data.rootfs_size}GB disk"
            ),
            commit=False,
        )
        session.commit()

        # Register tunnel proxies (best-effort — don't fail provisioning)
        try:
            tunnel_proxy_service.register_vm(
                session=session,
                vmid=vmid,
                user_id=user_id,
                vm_type="lxc",
            )
        except Exception:
            logger.warning("Failed to register tunnel proxies for LXC %d", vmid, exc_info=True)

        logger.info(f"Created LXC container {vmid}: {lxc_data.hostname}")
        return LXCCreateResponse(
            vmid=vmid,
            upid=result,
            message=f"Container {lxc_data.hostname} created successfully with VMID {vmid}",
        )
    except Exception as e:
        session.rollback()
        # 釋放已分配的 IP
        try:
            with Session(session.get_bind()) as cleanup_session:
                ip_management_service.release_ip(cleanup_session, vmid)
                cleanup_session.commit()
        except Exception:
            logger.warning("Failed to release IP for LXC %d during cleanup", vmid)
        if created:
            try:
                rules = firewall_service.get_vm_firewall_rules(target_node, vmid, "lxc")
                for r in sorted(rules, key=lambda x: x.get("pos", 0), reverse=True):
                    pos = r.get("pos")
                    if pos is not None:
                        try:
                            firewall_service.delete_rule_by_pos(target_node, vmid, "lxc", int(pos))
                        except Exception as fw_err:
                            logger.debug(
                                "LXC %d firewall rule pos=%s cleanup failed: %s",
                                vmid, pos, fw_err,
                            )
            except Exception as fw_err:
                logger.debug(
                    "LXC %d firewall rule listing for cleanup failed: %s",
                    vmid, fw_err,
                )
            _cleanup_failed_resource(target_node, vmid, "lxc")
        logger.error(f"Failed to create LXC container: {e}")
        raise ProxmoxError(f"Failed to create LXC container: {e}")


def create_vm(
    *, session: Session, vm_data: VMCreateRequest, user_id: uuid.UUID
) -> VMCreateResponse:
    new_vmid = proxmox_service.next_vmid()
    target_node = _get_vm_target_node(vm_data.template_id)
    target_storage = _resolve_managed_storage(
        session=session,
        node=target_node,
        resource_type="vm",
        requested_storage=vm_data.storage,
        disk_gb=int(vm_data.disk_size or 20),
        required_content="images",
    )

    # 取得網路配置並分配 IP
    net_cfg = ip_management_service.get_network_config_for_vm(session)
    allocated_ip = ip_management_service.allocate_ip(session, new_vmid, "vm")

    created = False
    try:
        # Generate SSH key pair for platform access
        private_key_pem, public_key = generate_ed25519_keypair()

        clone_config = {
            "newid": new_vmid,
            "name": to_punycode_hostname(vm_data.hostname),
            "full": 1,
            "storage": target_storage,
            "pool": get_proxmox_settings().pool_name,
        }

        result = proxmox_service.clone_vm(target_node, vm_data.template_id, **clone_config)
        created = True

        config_updates = {
            "cores": vm_data.cores,
            "memory": vm_data.memory,
            "ciuser": vm_data.username,
            "cipassword": vm_data.password,
            "sshkeys": quote(public_key, safe=""),
            "ciupgrade": 0,
            "net0": f"virtio,bridge={net_cfg['bridge_name']},firewall=1",
            "ipconfig0": f"ip={allocated_ip}/{net_cfg['prefix_len']},gw={net_cfg['gateway']}",
        }
        if net_cfg.get("dns_servers"):
            config_updates["nameserver"] = net_cfg["dns_servers"]
        gpu_mapping_id = getattr(vm_data, "gpu_mapping_id", None)
        if gpu_mapping_id:
            from app.services.proxmox import gpu_service
            try:
                gpu_detail = gpu_service.get_gpu_mapping(gpu_mapping_id)
                if gpu_detail.available_count <= 0:
                    raise ProxmoxError(
                        f"GPU {gpu_mapping_id} 已無可用插槽 "
                        f"(used={gpu_detail.used_count}/{gpu_detail.device_count})"
                    )
            except ProxmoxError:
                raise
            except Exception as e:
                logger.error("GPU 可用性檢查失敗 (%s): %s", gpu_mapping_id, e)
                raise ProxmoxError(f"無法驗證 GPU '{gpu_mapping_id}'：{e}")
            config_updates["hostpci0"] = f"mapping={gpu_mapping_id}"
        proxmox_service.update_config(target_node, new_vmid, "qemu", **config_updates)

        if vm_data.disk_size:
            proxmox_service.resize_disk(
                target_node, new_vmid, "qemu", "scsi0", f"{vm_data.disk_size}G"
            )

        firewall_service.setup_default_rules(target_node, new_vmid, "qemu")

        if vm_data.start:
            proxmox_service.control(target_node, new_vmid, "qemu", "start")

        resource_repo.create_resource(
            session=session,
            vmid=new_vmid,
            user_id=user_id,
            environment_type=vm_data.environment_type,
            os_info=vm_data.os_info,
            expiry_date=vm_data.expiry_date,
            template_id=vm_data.template_id,
            ssh_private_key_encrypted=encrypt_value(private_key_pem),
            ssh_public_key=public_key,
            service_template_slug=vm_data.service_template_slug,
            commit=False,
        )

        audit_service.log_action(
            session=session,
            user_id=user_id,
            vmid=new_vmid,
            action="vm_create",
            details=(
                f"Created VM '{vm_data.hostname}' from template {vm_data.template_id}: "
                f"{vm_data.cores} cores, {vm_data.memory}MB RAM, "
                f"{vm_data.disk_size or 'default'} disk"
            ),
            commit=False,
        )
        session.commit()

        # Register tunnel proxies (best-effort — don't fail provisioning)
        try:
            tunnel_proxy_service.register_vm(
                session=session,
                vmid=new_vmid,
                user_id=user_id,
                vm_type="qemu",
            )
        except Exception:
            logger.warning("Failed to register tunnel proxies for VM %d", new_vmid, exc_info=True)

        logger.info(f"Created VM {new_vmid} from template {vm_data.template_id}")
        return VMCreateResponse(
            vmid=new_vmid,
            upid=result,
            message=f"VM {vm_data.hostname} created successfully with VMID {new_vmid}",
        )
    except Exception as e:
        session.rollback()
        # 釋放已分配的 IP
        try:
            with Session(session.get_bind()) as cleanup_session:
                ip_management_service.release_ip(cleanup_session, new_vmid)
                cleanup_session.commit()
        except Exception:
            logger.warning("Failed to release IP for VM %d during cleanup", new_vmid)
        if created:
            try:
                rules = firewall_service.get_vm_firewall_rules(target_node, new_vmid, "qemu")
                for r in sorted(rules, key=lambda x: x.get("pos", 0), reverse=True):
                    pos = r.get("pos")
                    if pos is not None:
                        try:
                            firewall_service.delete_rule_by_pos(target_node, new_vmid, "qemu", int(pos))
                        except Exception as fw_err:
                            logger.debug(
                                "VM %d firewall rule pos=%s cleanup failed: %s",
                                new_vmid, pos, fw_err,
                            )
            except Exception as fw_err:
                logger.debug(
                    "VM %d firewall rule listing for cleanup failed: %s",
                    new_vmid, fw_err,
                )
            _cleanup_failed_resource(target_node, new_vmid, "qemu")
        logger.error(f"Failed to create VM: {e}")
        raise ProxmoxError(f"Failed to create VM: {e}")


def plan_provision(*, session: Session, db_request) -> dict:
    """Plan a provisioning: resolve placement + storage. Returns a plan dict.

    This reads the DB but does NOT create resources or call Proxmox mutating APIs.
    The caller should commit/close the session before executing the plan.
    """
    new_vmid = proxmox_service.next_vmid()
    placement_request = vm_request_placement_service._to_placement_request(db_request)
    placement_strategy = str(
        db_request.placement_strategy_used
        or vm_request_placement_service.DEFAULT_PLACEMENT_STRATEGY
    )
    placement = _select_request_placement(
        session=session,
        db_request=db_request,
        placement_request=placement_request,
        placement_strategy=placement_strategy,
    )
    if not placement.plan.feasible or not placement.node:
        raise ProxmoxError(
            f"No feasible placement is available for request {getattr(db_request, 'id', 'unknown')}"
        )
    target_node = placement.node
    resource_type = "lxc" if db_request.resource_type == "lxc" else "qemu"

    # Generate SSH key pair for platform access
    private_key_pem, public_key = generate_ed25519_keypair()

    # 取得網路配置並分配 IP（需在 session 中完成）
    net_cfg = ip_management_service.get_network_config_for_vm(session)
    purpose = "lxc" if db_request.resource_type == "lxc" else "vm"
    allocated_ip = ip_management_service.allocate_ip(session, new_vmid, purpose)

    plan: dict = {
        "vmid": new_vmid,
        "target_node": target_node,
        "placement_strategy": placement.strategy,
        "resource_type": resource_type,
        "hostname": db_request.hostname,
        "cores": db_request.cores,
        "memory": db_request.memory,
        "password": decrypt_value(db_request.password),
        "start_immediately": should_start_now(db_request),
        "user_id": db_request.user_id,
        "environment_type": db_request.environment_type,
        "os_info": db_request.os_info,
        "expiry_date": db_request.expiry_date,
        "storage": db_request.storage,
        "ssh_private_key_encrypted": encrypt_value(private_key_pem),
        "ssh_public_key": public_key,
        "allocated_ip": allocated_ip,
        "net_cfg": net_cfg,
    }

    if db_request.resource_type == "lxc":
        plan["target_storage"] = _resolve_managed_storage(
            session=session,
            node=target_node,
            resource_type="lxc",
            requested_storage=db_request.storage,
            disk_gb=int(db_request.rootfs_size or 8),
            required_content="rootdir",
        )
        plan["ostemplate"] = db_request.ostemplate
        plan["rootfs_size"] = db_request.rootfs_size or 8
        plan["unprivileged"] = db_request.unprivileged
    else:
        template = proxmox_service.find_vm_template(db_request.template_id)
        plan["template_id"] = db_request.template_id
        plan["template_node"] = template["node"]
        plan["disk_size"] = db_request.disk_size
        plan["username"] = db_request.username
        if db_request.gpu_mapping_id:
            plan["gpu_mapping_id"] = db_request.gpu_mapping_id
        plan["target_storage"] = _resolve_managed_storage(
            session=session,
            node=target_node,
            resource_type="vm",
            requested_storage=db_request.storage,
            disk_gb=int(db_request.disk_size or 20),
            required_content="images",
        )
        # Pre-resolve fallback storage for cross-node clone failure.
        if target_node != template["node"]:
            plan["fallback_storage"] = _resolve_managed_storage(
                session=session,
                node=template["node"],
                resource_type="vm",
                requested_storage=db_request.storage,
                disk_gb=int(db_request.disk_size or 20),
                required_content="images",
            )

    return plan


def execute_provision(plan: dict) -> tuple[int, str]:
    """Execute a provisioning plan — Proxmox-only, NO database session needed.

    Returns (vmid, actual_node).  The caller is responsible for recording the
    result in the database afterwards.
    """
    new_vmid = plan["vmid"]
    target_node = plan["target_node"]
    resource_type = plan["resource_type"]
    hostname = plan["hostname"]
    pool_name = get_proxmox_settings().pool_name
    created = False
    actual_node = target_node
    net_cfg = plan.get("net_cfg", {})
    allocated_ip = plan.get("allocated_ip")

    try:
        if resource_type == "lxc":
            if not allocated_ip or not net_cfg or not net_cfg.get("bridge_name"):
                raise ProxmoxError(
                    f"網路設定不完整，無法建立 LXC VMID={new_vmid}（"
                    f"allocated_ip={allocated_ip!r}, net_cfg={net_cfg!r}）。"
                    "請確認 IP 管理子網設定。"
                )
            net0_parts = (
                f"name=eth0,bridge={net_cfg['bridge_name']},"
                f"ip={allocated_ip}/{net_cfg['prefix_len']},"
                f"gw={net_cfg['gateway']},firewall=1"
            )

            config = {
                "vmid": new_vmid,
                "hostname": plan["hostname"],
                "ostemplate": plan["ostemplate"],
                "cores": plan["cores"],
                "memory": plan["memory"],
                "swap": 512,
                "rootfs": f"{plan['target_storage']}:{plan['rootfs_size']}",
                "password": plan["password"],
                "net0": net0_parts,
                "unprivileged": int(plan["unprivileged"]),
                "start": int(plan["start_immediately"]),
                "pool": pool_name,
                "features": "nesting=1",
                "ssh-public-keys": plan.get("ssh_public_key", ""),
            }
            if net_cfg.get("dns_servers"):
                config["nameserver"] = net_cfg["dns_servers"]
            proxmox_service.create_lxc(target_node, **config)
            created = True
            firewall_service.setup_default_rules(target_node, new_vmid, "lxc")
        else:
            template_node = plan["template_node"]
            clone_config = {
                "newid": new_vmid,
                "name": hostname,
                "full": 1,
                "storage": plan["target_storage"],
                "pool": pool_name,
            }
            if target_node != template_node:
                clone_config["target"] = target_node
            try:
                proxmox_service.clone_vm(
                    template_node,
                    plan["template_id"],
                    **clone_config,
                )
                actual_node = target_node
            except Exception:
                if target_node == template_node:
                    raise
                logger.warning(
                    "Cross-node clone failed for VMID %s; falling back to template node %s",
                    new_vmid,
                    template_node,
                )
                actual_node = template_node
                fallback_storage = plan.get("fallback_storage", plan["target_storage"])
                proxmox_service.clone_vm(
                    template_node,
                    plan["template_id"],
                    newid=new_vmid,
                    name=hostname,
                    full=1,
                    storage=fallback_storage,
                    pool=pool_name,
                )
            created = True

            config_updates = {
                "cores": plan["cores"],
                "memory": plan["memory"],
                "ciuser": plan.get("username"),
                "cipassword": plan["password"],
                "sshkeys": quote(plan.get("ssh_public_key", ""), safe=""),
                "ciupgrade": 0,
            }
            if allocated_ip and net_cfg and net_cfg.get("bridge_name"):
                config_updates["net0"] = f"virtio,bridge={net_cfg['bridge_name']},firewall=1"
                config_updates["ipconfig0"] = f"ip={allocated_ip}/{net_cfg['prefix_len']},gw={net_cfg['gateway']}"
                if net_cfg.get("dns_servers"):
                    config_updates["nameserver"] = net_cfg["dns_servers"]
            else:
                raise ProxmoxError(
                    f"網路設定不完整，無法建立 VM VMID={new_vmid}。"
                    "請確認 IP 管理子網設定。"
                )
            if plan.get("gpu_mapping_id"):
                # Validate GPU availability before assigning
                from app.services.proxmox import gpu_service
                try:
                    gpu_detail = gpu_service.get_gpu_mapping(plan["gpu_mapping_id"])
                    if gpu_detail.available_count <= 0:
                        raise ProxmoxError(
                            f"GPU {plan['gpu_mapping_id']} 已無可用插槽 "
                            f"(used={gpu_detail.used_count}/{gpu_detail.device_count})"
                        )
                except ProxmoxError:
                    raise
                except Exception as e:
                    logger.error("GPU 可用性檢查失敗 (%s): %s", plan["gpu_mapping_id"], e)
                    raise ProxmoxError(f"無法驗證 GPU '{plan['gpu_mapping_id']}'：{e}")
                config_updates["hostpci0"] = f"mapping={plan['gpu_mapping_id']}"
            proxmox_service.update_config(actual_node, new_vmid, "qemu", **config_updates)

            if plan.get("disk_size"):
                proxmox_service.resize_disk(
                    actual_node, new_vmid, "qemu", "scsi0", f"{plan['disk_size']}G"
                )

            firewall_service.setup_default_rules(actual_node, new_vmid, "qemu")
            if plan["start_immediately"]:
                proxmox_service.control(actual_node, new_vmid, "qemu", "start")
    except Exception:
        if created:
            # Best-effort: drop any firewall rules created earlier on this VMID
            try:
                rules = firewall_service.get_vm_firewall_rules(actual_node, new_vmid, resource_type)
                for rule in sorted(rules, key=lambda r: r.get("pos", 0), reverse=True):
                    pos = rule.get("pos")
                    if pos is not None:
                        try:
                            firewall_service.delete_rule_by_pos(actual_node, new_vmid, resource_type, int(pos))
                        except Exception as fw_err:
                            logger.debug(
                                "execute_provision rollback: rule pos=%s on VMID %s failed: %s",
                                pos, new_vmid, fw_err,
                            )
            except Exception as fw_err:
                logger.debug("Firewall cleanup skipped for VMID %s: %s", new_vmid, fw_err)
            _cleanup_failed_resource(actual_node, new_vmid, resource_type)
        raise

    logger.info("Provisioned %s VMID %s on node %s", resource_type, new_vmid, actual_node)
    return new_vmid, actual_node


def provision_from_request(*, session: Session, db_request) -> tuple[int, str | None, str | None]:
    """Legacy wrapper: plan + execute in one call (session kept open).

    Prefer plan_provision() + execute_provision() for new code.
    """
    plan = plan_provision(session=session, db_request=db_request)
    try:
        new_vmid, actual_node = execute_provision(plan)
    except Exception:
        session.rollback()
        raise

    # Record resource in DB.
    resource_repo.create_resource(
        session=session,
        vmid=new_vmid,
        user_id=db_request.user_id,
        environment_type=db_request.environment_type,
        os_info=db_request.os_info,
        expiry_date=db_request.expiry_date,
        template_id=getattr(db_request, "template_id", None),
        ssh_private_key_encrypted=plan.get("ssh_private_key_encrypted"),
        ssh_public_key=plan.get("ssh_public_key"),
        service_template_slug=getattr(db_request, "service_template_slug", None),
        commit=False,
    )
    return new_vmid, actual_node, plan["placement_strategy"]


def get_lxc_templates() -> list[TemplateSchema]:
    templates: list[dict] = []
    for node in proxmox_service.get_available_nodes():
        node_name = node.get("node") or node.get("name")
        if not node_name:
            continue
        try:
            templates.extend(proxmox_service.get_lxc_templates(node_name))
        except Exception:
            logger.warning("Failed to load LXC templates from node %s", node_name)

    templates = _dedupe_templates(templates)
    return [
        TemplateSchema(
            volid=t["volid"],
            format=t.get("format", ""),
            size=t.get("size", 0),
        )
        for t in templates
        if t.get("content") == "vztmpl"
    ]


def get_vm_templates() -> list[VMTemplateSchema]:
    all_vms = proxmox_service.get_vm_templates()
    return [
        VMTemplateSchema(vmid=vm["vmid"], name=vm["name"], node=vm["node"])
        for vm in all_vms
    ]
