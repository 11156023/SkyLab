import logging
import re
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from urllib.parse import quote

from sqlmodel import Session, select

from app.core.i18n import t
from app.core.security import decrypt_value, encrypt_value
from app.domain.placement import advisor as placement_advisor
from app.exceptions import ProxmoxError
from app.infrastructure.proxmox import get_proxmox_settings_for_node
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
from app.services.vm import placement_support, vm_request_placement_service
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


def _ensure_resource_stopped(
    node: str,
    vmid: int,
    resource_type: str,
    *,
    timeout_seconds: float = 30.0,
) -> None:
    """Stop a resource and wait until Proxmox reports it as stopped."""
    try:
        status = proxmox_service.get_status(node, vmid, resource_type)
    except Exception:
        logger.warning("Failed to fetch %s %s status before stop", resource_type, vmid)
        return

    if status.get("status") != "running":
        return

    proxmox_service.control(node, vmid, resource_type, "stop")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        time.sleep(1)
        try:
            status = proxmox_service.get_status(node, vmid, resource_type)
        except Exception:
            logger.debug(
                "Waiting for %s %s stop: status check failed",
                resource_type,
                vmid,
            )
            continue
        if status.get("status") == "stopped":
            return

    logger.warning(
        "%s %s still appears running after stop timeout", resource_type, vmid
    )
    raise ProxmoxError(f"{resource_type} {vmid} is still running after stop timeout")


def _cleanup_failed_resource(node: str, vmid: int, resource_type: str) -> None:
    """Best-effort cleanup for a partially provisioned resource."""
    try:
        try:
            _ensure_resource_stopped(
                node,
                vmid,
                resource_type,
                timeout_seconds=45.0,
            )
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


# PVE mdev 型別名稱只會是 nvidia-123 / i915-GVTg_V5_4 這類 token，
# 不含逗號、等號或空白（那些字元在 hostpci 字串裡是選項分隔符）
_MDEV_PROFILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _build_gpu_hostpci(mapping_id: str, mdev_profile: str | None) -> str:
    """驗證 GPU 可用額度與 vGPU 規格，回傳 hostpci 設定字串。

    profile 的 creatable 是即時的（NVIDIA 只回報還放得下的規格），
    克隆前這裡是最後一道防線。

    vGPU 卡（profiles 非空）未指定規格時，自動配「最小可建規格」——
    不帶 mdev 的裸 VF 對 NVIDIA vGPU 是不可用的，不能落回 raw passthrough。
    """
    from app.services.proxmox import gpu_service  # noqa: PLC0415

    # hostpci 是逗號分隔的 key=value 字串：mdev 值若含 ',' 或 '=' 就能夾帶
    # romfile/rombar 等額外選項，必須先做格式白名單，再對照 PVE 回報的規格。
    if mdev_profile and not _MDEV_PROFILE_RE.fullmatch(mdev_profile):
        raise ProxmoxError(
            t("provisioning.gpu_mdev_profile_invalid", profile=mdev_profile)
        )

    try:
        gpu_detail = gpu_service.get_gpu_mapping(mapping_id)
        if gpu_detail.available_count <= 0:
            raise ProxmoxError(
                t(
                    "provisioning.gpu_no_available_quota",
                    mapping_id=mapping_id,
                    used=gpu_detail.used_count,
                    capacity=gpu_detail.capacity_count,
                )
            )
        if mdev_profile:
            match = next(
                (p for p in gpu_detail.profiles if p.mdev_type == mdev_profile),
                None,
            )
            # 裸直通卡（profiles 為空）不接受任何 mdev 規格；vGPU 卡則必須命中
            if match is None:
                raise ProxmoxError(
                    t(
                        "provisioning.gpu_profile_not_found",
                        mapping_id=mapping_id,
                        profile=mdev_profile,
                    )
                )
            if match is not None and not match.creatable:
                raise ProxmoxError(
                    t(
                        "provisioning.gpu_profile_not_creatable",
                        profile=match.name or mdev_profile,
                    )
                )
        elif gpu_detail.profiles:
            creatable = [
                p for p in gpu_detail.profiles if p.creatable and p.vram_mb > 0
            ]
            if not creatable:
                raise ProxmoxError(
                    t("provisioning.gpu_memory_full", mapping_id=mapping_id)
                )
            auto = min(creatable, key=lambda p: p.vram_mb)
            logger.info(
                "GPU %s 未指定 vGPU 規格，自動配最小可用規格 %s (%s)",
                mapping_id, auto.name or auto.mdev_type, auto.mdev_type,
            )
            mdev_profile = auto.mdev_type
    except ProxmoxError:
        raise
    except Exception as e:
        logger.error("GPU 可用性檢查失敗 (%s): %s", mapping_id, e)
        raise ProxmoxError(
            t("provisioning.gpu_verification_failed", mapping_id=mapping_id, error=e)
        )

    if mdev_profile:
        return f"mapping={mapping_id},mdev={mdev_profile}"
    return f"mapping={mapping_id}"


def _gpu_mapping_nodes(mapping_id: str | None) -> set[str]:
    if not mapping_id:
        return set()

    mapping = gpu_service.get_gpu_mapping(str(mapping_id))
    return {str(item.node).strip() for item in mapping.maps if str(item.node).strip()}


def _template_node_accepts_gpu(plan: dict, template_node: str) -> bool:
    """範本節點能否承接這次建機的 GPU 需求（退回範本節點前的最後把關）。

    不需要 GPU 時一律放行。查詢 mapping 失敗時回 False —— 無法確認就不退回，
    寧可讓建機明確失敗，也不要建出一台掛不上 GPU 的機器。
    """
    mapping_id = plan.get("gpu_mapping_id")
    if not mapping_id:
        return True
    try:
        return str(template_node) in _gpu_mapping_nodes(str(mapping_id))
    except Exception as exc:
        logger.warning(
            "Unable to verify GPU mapping '%s' on template node %s: %s",
            mapping_id,
            template_node,
            exc,
        )
        return False


def _select_request_placement(
    *,
    session: Session,
    db_request,
    placement_request,
    placement_strategy: str,
):
    compatible_nodes = _gpu_mapping_nodes(getattr(db_request, "gpu_mapping_id", None))
    # 模板節點白名單（None = 不受限）：vztmpl 只在部分節點可見、
    # VM 克隆不可跨連線，指定節點與自動選擇都必須落在白名單內。
    template_nodes = placement_support.allowed_template_nodes_for_request(
        placement_request
    )

    def _template_node_error(node: str) -> ProxmoxError:
        template_label = (
            getattr(db_request, "ostemplate", None)
            or getattr(db_request, "template_id", None)
            or "unknown"
        )
        nodes_text = ", ".join(sorted(template_nodes)) or t(
            "provisioning.no_nodes_available"
        )
        return ProxmoxError(
            t(
                "provisioning.template_node_unavailable",
                node=node,
                template=template_label,
                nodes=nodes_text,
            )
        )

    pinned_node = getattr(db_request, "desired_node", None) or getattr(
        db_request, "assigned_node", None
    )
    if pinned_node:
        if compatible_nodes and str(pinned_node) not in compatible_nodes:
            raise ProxmoxError(
                f"Pinned node '{pinned_node}' is not compatible with GPU mapping "
                f"'{getattr(db_request, 'gpu_mapping_id', '')}'."
            )
        if template_nodes is not None and str(pinned_node) not in template_nodes:
            raise _template_node_error(str(pinned_node))

        nodes, resources = placement_advisor._load_cluster_state()
        cpu_overcommit_ratio, disk_overcommit_ratio = (
            vm_request_placement_service.get_overcommit_ratios(session)
        )
        node_capacities = placement_advisor._build_node_capacities(
            nodes=nodes,
            resources=resources,
            cpu_overcommit_ratio=cpu_overcommit_ratio,
            disk_overcommit_ratio=disk_overcommit_ratio,
        )
        node_capacities = [
            item for item in node_capacities if item.node == str(pinned_node)
        ]
        effective_resource_type, resource_type_reason = (
            placement_advisor._decide_resource_type(placement_request)
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
                node_priorities=vm_request_placement_service.get_node_priorities(
                    session
                ),
            ),
        )
        if not placement.plan.feasible or not placement.node:
            reserved_requests = []
            if getattr(db_request, "start_at", None) and getattr(
                db_request, "end_at", None
            ):
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
                if (
                    template_nodes is not None
                    and str(fallback.node) not in template_nodes
                ):
                    raise _template_node_error(str(fallback.node))
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
    if (
        compatible_nodes
        and selection.node
        and str(selection.node) not in compatible_nodes
    ):
        raise ProxmoxError(
            f"Selected node '{selection.node}' is not compatible with GPU mapping "
            f"'{getattr(db_request, 'gpu_mapping_id', '')}'."
        )
    if (
        template_nodes is not None
        and selection.node
        and str(selection.node) not in template_nodes
    ):
        raise _template_node_error(str(selection.node))
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
    *,
    session: Session,
    lxc_data: LXCCreateRequest,
    user_id: uuid.UUID,
    batch_job_id: uuid.UUID | None = None,
    ip_reservation_key: str | None = None,
    target_node: str | None = None,
) -> LXCCreateResponse:
    """建立 LXC。

    ``target_node`` 由呼叫端指定時優先採用 —— 課堂機器以此把整班鎖在同一個
    叢集內（預設的 pick_target_node 會在所有連線間自由挑選）。
    """
    vmid = proxmox_service.next_vmid()
    target_node = target_node or _get_lxc_target_node()
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
    allocated_ip = ip_management_service.allocate_ip(
        session, vmid, "lxc", reservation_key=ip_reservation_key
    )

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
            "pool": get_proxmox_settings_for_node(target_node).pool_name,
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
            batch_job_id=batch_job_id,
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
            logger.warning(
                "Failed to register tunnel proxies for LXC %s", vmid, exc_info=True
            )

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
                ip_management_service.release_ip(
                    cleanup_session,
                    vmid,
                    restore_reservation=bool(ip_reservation_key),
                )
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
                            firewall_service.delete_rule_by_pos(
                                target_node, vmid, "lxc", int(pos)
                            )
                        except Exception as fw_err:
                            logger.debug(
                                "LXC %d firewall rule pos=%s cleanup failed: %s",
                                vmid,
                                pos,
                                fw_err,
                            )
            except Exception as fw_err:
                logger.debug(
                    "LXC %d firewall rule listing for cleanup failed: %s",
                    vmid,
                    fw_err,
                )
            _cleanup_failed_resource(target_node, vmid, "lxc")
        logger.error(f"Failed to create LXC container: {e}")
        raise ProxmoxError(f"Failed to create LXC container: {e}")


def create_vm(
    *,
    session: Session,
    vm_data: VMCreateRequest,
    user_id: uuid.UUID,
    batch_job_id: uuid.UUID | None = None,
    ip_reservation_key: str | None = None,
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
    allocated_ip = ip_management_service.allocate_ip(
        session, new_vmid, "vm", reservation_key=ip_reservation_key
    )

    created = False
    try:
        # Generate SSH key pair for platform access
        private_key_pem, public_key = generate_ed25519_keypair()

        clone_config = {
            "newid": new_vmid,
            "name": to_punycode_hostname(vm_data.hostname),
            "full": 1,
            "storage": target_storage,
            "pool": get_proxmox_settings_for_node(target_node).pool_name,
        }

        result = proxmox_service.clone_vm(
            target_node, vm_data.template_id, **clone_config
        )
        created = True

        config_updates = {
            "cores": vm_data.cores,
            "memory": vm_data.memory,
            "cipassword": vm_data.password,
            "sshkeys": quote(public_key, safe=""),
            "ciupgrade": 0,
            "net0": f"virtio,bridge={net_cfg['bridge_name']},firewall=1",
            "ipconfig0": f"ip={allocated_ip}/{net_cfg['prefix_len']},gw={net_cfg['gateway']}",
        }
        # Windows 範本不帶 username（帳號由 cloudbase-init 設定檔固定）
        if vm_data.username:
            config_updates["ciuser"] = vm_data.username
        if net_cfg.get("dns_servers"):
            config_updates["nameserver"] = net_cfg["dns_servers"]
        gpu_mapping_id = getattr(vm_data, "gpu_mapping_id", None)
        if gpu_mapping_id:
            config_updates["hostpci0"] = _build_gpu_hostpci(
                gpu_mapping_id, getattr(vm_data, "gpu_mdev_profile", None)
            )
        _ensure_resource_stopped(target_node, new_vmid, "qemu")
        proxmox_service.update_config(target_node, new_vmid, "qemu", **config_updates)

        _resize_clone_disk_if_needed(
            target_node, new_vmid, vm_data.template_id, vm_data.disk_size
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
            batch_job_id=batch_job_id,
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
            logger.warning(
                "Failed to register tunnel proxies for VM %d", new_vmid, exc_info=True
            )

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
                ip_management_service.release_ip(
                    cleanup_session,
                    new_vmid,
                    restore_reservation=bool(ip_reservation_key),
                )
                cleanup_session.commit()
        except Exception:
            logger.warning("Failed to release IP for VM %d during cleanup", new_vmid)
        if created:
            try:
                rules = firewall_service.get_vm_firewall_rules(
                    target_node, new_vmid, "qemu"
                )
                for r in sorted(rules, key=lambda x: x.get("pos", 0), reverse=True):
                    pos = r.get("pos")
                    if pos is not None:
                        try:
                            firewall_service.delete_rule_by_pos(
                                target_node, new_vmid, "qemu", int(pos)
                            )
                        except Exception as fw_err:
                            logger.debug(
                                "VM %d firewall rule pos=%s cleanup failed: %s",
                                new_vmid,
                                pos,
                                fw_err,
                            )
            except Exception as fw_err:
                logger.debug(
                    "VM %d firewall rule listing for cleanup failed: %s",
                    new_vmid,
                    fw_err,
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
    ip_reservation_key: str | None = None
    if getattr(db_request, "request_kind", "") == "quick_template":
        # Multi-machine quick-practice reserves every IP in one launch
        # transaction. Resolve the stable key from the request-to-session map
        # so the generic VMRequest schema does not expose infrastructure data.
        from app.models import QuickPracticeSessionMachine  # noqa: PLC0415

        practice_machine = session.exec(
            select(QuickPracticeSessionMachine).where(
                QuickPracticeSessionMachine.vm_request_id == db_request.id
            )
        ).first()
        if practice_machine is not None:
            ip_reservation_key = (
                f"quick:{practice_machine.session_id}:{practice_machine.node_key}"
            )
    allocated_ip = ip_management_service.allocate_ip(
        session,
        new_vmid,
        purpose,
        reservation_key=ip_reservation_key,
    )

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
        "ip_reservation_key": ip_reservation_key,
        "net_cfg": net_cfg,
    }

    if db_request.resource_type == "lxc" and getattr(db_request, "template_id", None):
        # LXC 範本克隆路徑（Course Lab）：linked clone 必須與範本同節點同 storage，
        # 直接以範本節點覆寫 placement 結果（與範本系統 2.0 clone_service 行為一致）。
        from sqlmodel import select as _select  # noqa: PLC0415 — 避免頂層循環相依

        from app.models import VMTemplate  # noqa: PLC0415

        template_row = session.exec(
            _select(VMTemplate).where(VMTemplate.pve_vmid == db_request.template_id)
        ).first()
        if template_row is None:
            raise ProxmoxError(
                f"LXC template VMID {db_request.template_id} is not registered"
            )
        plan["target_node"] = template_row.node
        plan["lxc_clone"] = True
        plan["template_id"] = db_request.template_id
        plan["template_node"] = template_row.node
        # Course Lab 的 password 是佔位隨機值（憑證以範本內烘焙為準），
        # 其餘來源（申請單 / 快速範本）為使用者自訂密碼，克隆後必須套用
        plan["apply_login_password"] = (
            getattr(db_request, "request_kind", "") != "course"
        )
        plan["target_storage"] = _resolve_managed_storage(
            session=session,
            node=template_row.node,
            resource_type="lxc",
            requested_storage=db_request.storage,
            disk_gb=int(db_request.rootfs_size or 8),
            required_content="rootdir",
        )
        plan["rootfs_size"] = db_request.rootfs_size or 8
        plan["template_disk_gb"] = int(template_row.default_disk or 0)
    elif db_request.resource_type == "lxc":
        # 早期防線：vzcreate 前確認目標節點真的看得到這個 vztmpl，
        # 避免 PVE 端 volume does not exist 的晚期失敗（映射整批查詢
        # 失敗時為空 map，此時不擋、交由 PVE 把關）。
        template_node_map = proxmox_service.get_lxc_template_node_map()
        if (
            db_request.ostemplate
            and template_node_map
            and target_node
            not in template_node_map.get(str(db_request.ostemplate), set())
        ):
            nodes_text = ", ".join(
                sorted(template_node_map.get(str(db_request.ostemplate), set()))
            ) or t("provisioning.no_nodes_available")
            raise ProxmoxError(
                t(
                    "provisioning.lxc_template_node_unavailable",
                    node=target_node,
                    template=db_request.ostemplate,
                    nodes=nodes_text,
                )
            )
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
            if getattr(db_request, "gpu_mdev_profile", None):
                plan["gpu_mdev_profile"] = db_request.gpu_mdev_profile
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
    pool_name = get_proxmox_settings_for_node(target_node).pool_name
    created = False
    actual_node = target_node
    net_cfg = plan.get("net_cfg", {})
    allocated_ip = plan.get("allocated_ip")

    try:
        if resource_type == "lxc":
            if not allocated_ip or not net_cfg or not net_cfg.get("bridge_name"):
                raise ProxmoxError(
                    t(
                        "provisioning.lxc_network_incomplete",
                        vmid=new_vmid,
                        allocated_ip=repr(allocated_ip),
                        net_cfg=repr(net_cfg),
                    )
                )
            net0_parts = (
                f"name=eth0,bridge={net_cfg['bridge_name']},"
                f"ip={allocated_ip}/{net_cfg['prefix_len']},"
                f"gw={net_cfg['gateway']},firewall=1"
            )

            if plan.get("lxc_clone"):
                # LXC 範本克隆（linked 優先退 full），克隆後重配置。
                # LXC 無 cloud-init：使用者自訂密碼須待啟動後以 pct exec 設定
                # （_set_lxc_root_password）；Course Lab 憑證以範本內烘焙為準，
                # 不套用（plan["apply_login_password"] = False）。
                from app.services.template import clone_service  # noqa: PLC0415

                clone_service.clone_with_fallback(
                    node=plan["template_node"],
                    template_vmid=plan["template_id"],
                    new_vmid=new_vmid,
                    hostname=hostname,
                    resource_type="lxc",
                    full_kwargs={"storage": plan["target_storage"]},
                )
                created = True
                actual_node = plan["template_node"]
                clone_updates = {
                    "cores": plan["cores"],
                    "memory": plan["memory"],
                    "net0": net0_parts,
                }
                if net_cfg.get("dns_servers"):
                    clone_updates["nameserver"] = net_cfg["dns_servers"]
                proxmox_service.update_config(
                    actual_node, new_vmid, "lxc", **clone_updates
                )
                # 停機狀態下先放大 rootfs，再套防火牆與啟動
                _resize_clone_rootfs_if_needed(
                    actual_node,
                    new_vmid,
                    plan.get("rootfs_size"),
                    int(plan.get("template_disk_gb") or 0),
                )
                firewall_service.setup_default_rules(actual_node, new_vmid, "lxc")
                apply_password = bool(
                    plan.get("apply_login_password") and plan.get("password")
                )
                if plan["start_immediately"]:
                    proxmox_service.control(actual_node, new_vmid, "lxc", "start")
                    if apply_password:
                        clone_service._set_lxc_root_password(
                            actual_node, new_vmid, plan["password"]
                        )
                elif apply_password:
                    logger.warning(
                        "CT %s not started at provision time; custom root "
                        "password not applied (template credentials remain)",
                        new_vmid,
                    )
                logger.info("Provisioned lxc VMID %s on node %s", new_vmid, actual_node)
                return new_vmid, actual_node

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
            if target_node == template_node:
                # 範本系統 2.0 統一克隆路徑：linked clone 優先、失敗退 full
                from app.services.template import clone_service

                clone_service.clone_with_fallback(
                    node=template_node,
                    template_vmid=plan["template_id"],
                    new_vmid=new_vmid,
                    hostname=hostname,
                    resource_type="qemu",
                    full_kwargs={"storage": plan["target_storage"]},
                )
                actual_node = template_node
            else:
                # 跨節點只能 full clone（linked clone 需與範本同 storage）
                clone_config = {
                    "newid": new_vmid,
                    "name": hostname,
                    "full": 1,
                    "storage": plan["target_storage"],
                    "pool": pool_name,
                    "target": target_node,
                }
                try:
                    proxmox_service.clone_vm(
                        template_node,
                        plan["template_id"],
                        **clone_config,
                    )
                    actual_node = target_node
                except Exception as exc:
                    # 退回範本節點會繞過 _select_request_placement 做過的 GPU
                    # 節點相容性檢查：範本節點未必有這張卡。與其建出一台掛不上
                    # GPU 的機器，不如讓這次建機明確失敗。
                    if not _template_node_accepts_gpu(plan, template_node):
                        raise ProxmoxError(
                            t(
                                "provisioning.gpu_fallback_node_incompatible",
                                target_node=target_node,
                                template_node=template_node,
                                mapping_id=plan.get("gpu_mapping_id"),
                            )
                        ) from exc
                    logger.warning(
                        "Cross-node clone failed for VMID %s; falling back to template node %s",
                        new_vmid,
                        template_node,
                    )
                    actual_node = template_node
                    fallback_storage = plan.get(
                        "fallback_storage", plan["target_storage"]
                    )
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
                "cipassword": plan["password"],
                "sshkeys": quote(plan.get("ssh_public_key", ""), safe=""),
                "ciupgrade": 0,
            }
            # Windows 範本不帶 username（帳號由 cloudbase-init 設定檔固定）
            if plan.get("username"):
                config_updates["ciuser"] = plan["username"]
            if allocated_ip and net_cfg and net_cfg.get("bridge_name"):
                config_updates["net0"] = (
                    f"virtio,bridge={net_cfg['bridge_name']},firewall=1"
                )
                config_updates["ipconfig0"] = (
                    f"ip={allocated_ip}/{net_cfg['prefix_len']},gw={net_cfg['gateway']}"
                )
                if net_cfg.get("dns_servers"):
                    config_updates["nameserver"] = net_cfg["dns_servers"]
            else:
                raise ProxmoxError(
                    t("provisioning.vm_network_incomplete", vmid=new_vmid)
                )
            if plan.get("gpu_mapping_id"):
                config_updates["hostpci0"] = _build_gpu_hostpci(
                    plan["gpu_mapping_id"], plan.get("gpu_mdev_profile")
                )
            _ensure_resource_stopped(actual_node, new_vmid, "qemu")
            proxmox_service.update_config(
                actual_node, new_vmid, "qemu", **config_updates
            )

            _resize_clone_disk_if_needed(
                actual_node, new_vmid, plan["template_id"], plan.get("disk_size")
            )

            firewall_service.setup_default_rules(actual_node, new_vmid, "qemu")
            if plan["start_immediately"]:
                proxmox_service.control(actual_node, new_vmid, "qemu", "start")
    except Exception:
        if created:
            # Best-effort: drop any firewall rules created earlier on this VMID
            try:
                rules = firewall_service.get_vm_firewall_rules(
                    actual_node, new_vmid, resource_type
                )
                for rule in sorted(rules, key=lambda r: r.get("pos", 0), reverse=True):
                    pos = rule.get("pos")
                    if pos is not None:
                        try:
                            firewall_service.delete_rule_by_pos(
                                actual_node, new_vmid, resource_type, int(pos)
                            )
                        except Exception as fw_err:
                            logger.debug(
                                "execute_provision rollback: rule pos=%s on VMID %s failed: %s",
                                pos,
                                new_vmid,
                                fw_err,
                            )
            except Exception as fw_err:
                logger.debug(
                    "Firewall cleanup skipped for VMID %s: %s", new_vmid, fw_err
                )
            _cleanup_failed_resource(actual_node, new_vmid, resource_type)
        raise

    logger.info(
        "Provisioned %s VMID %s on node %s", resource_type, new_vmid, actual_node
    )
    return new_vmid, actual_node


def provision_from_request(
    *, session: Session, db_request
) -> tuple[int, str | None, str | None]:
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
        request_id=getattr(db_request, "id", None),
        commit=False,
    )
    return new_vmid, actual_node, plan["placement_strategy"]


def get_lxc_templates() -> list[TemplateSchema]:
    node_map = proxmox_service.get_lxc_template_node_map()
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
            nodes=sorted(node_map.get(str(t["volid"]), set())),
        )
        for t in templates
        if t.get("content") == "vztmpl"
    ]


def _template_disk_gb(template: dict) -> int:
    """cluster resource 的 maxdisk 換算 GB（無條件進位）；缺值回 0。"""
    maxdisk = template.get("maxdisk")
    return -(-int(maxdisk) // (1024**3)) if maxdisk else 0


def template_disk_floor_gb(template_id: int) -> int:
    """未註冊 PVE 範本的磁碟大小（GB）；查不到回 0。

    克隆磁碟只能放大，所以範本大小就是克隆機的下限。呼叫端拿它把申請值
    提高到實際會開出來的大小，避免配額與實體資源對不上。
    """
    try:
        return _template_disk_gb(proxmox_service.find_vm_template(int(template_id)))
    except Exception:
        # 查不到範本磁碟大小時回 0，代表「無下限資訊」，交由 PVE 端把關
        logger.debug(
            "Unable to determine template %s disk size", template_id, exc_info=True
        )
        return 0


def clone_source_disk_gb(session: Session, node) -> int:
    """機器節點磁碟的實際大小，含來源範本下限。

    克隆機不可能小於來源範本（PVE 只能放大不能縮小），所以節點上填的值
    低於範本時，實際開出來的是範本大小。配額、班級容量預檢與申請單都必
    須用這個值，否則會發生「記 20 GB、實際開 40 GB」的超賣。``node`` 是
    課程模板節點或班級機器節點——兩者的來源欄位同形。
    """
    floor = 0
    if node.source_type == "template" and node.source_template_id:
        from app.models import VMTemplate  # noqa: PLC0415

        template = session.get(VMTemplate, node.source_template_id)
        if template is not None:
            floor = int(template.default_disk or 0)
    elif str(node.resource_type).lower() != "lxc" and node.custom_image_ref:
        # 自訂規格的 VM 來源就是一台未註冊的 PVE 範本，下限只能問 PVE。
        try:
            floor = template_disk_floor_gb(int(node.custom_image_ref))
        except ValueError:
            floor = 0
    return max(int(node.disk_gb), floor)


def _resize_clone_disk_if_needed(
    node: str, vmid: int, template_id: int, disk_size: int | None
) -> None:
    """克隆磁碟只能放大：requested <= 範本大小時跳過 resize。

    PVE 對縮小磁碟直接報錯；範本本體大於申請值時（例如範本超過表單
    上限）克隆機天生就是範本大小，跳過即可。範本查不到時照舊 resize，
    交由 PVE 端把關。
    """
    if not disk_size:
        return
    template_gb = template_disk_floor_gb(template_id)
    if template_gb and int(disk_size) <= template_gb:
        logger.info(
            "Skip disk resize for VM %d: requested %dG <= template %dG",
            vmid,
            int(disk_size),
            template_gb,
        )
        return
    proxmox_service.resize_disk(node, vmid, "qemu", "scsi0", f"{int(disk_size)}G")


_LXC_ROOTFS_SIZE_UNITS = {"K": 1 / 1024**2, "M": 1 / 1024, "G": 1.0, "T": 1024.0}


def _lxc_rootfs_gb(node: str, vmid: int) -> int:
    """讀 LXC 目前的 rootfs 大小（GB）；讀不到或格式看不懂回 0。"""
    try:
        rootfs = str(proxmox_service.get_config(node, vmid, "lxc").get("rootfs") or "")
    except Exception:
        logger.debug("Unable to read rootfs size for CT %s", vmid, exc_info=True)
        return 0
    match = re.search(r"size=(\d+(?:\.\d+)?)([KMGT])", rootfs)
    if not match:
        return 0
    return int(float(match.group(1)) * _LXC_ROOTFS_SIZE_UNITS[match.group(2)])


def _resize_clone_rootfs_if_needed(
    node: str, vmid: int, rootfs_size: int | None, template_gb: int
) -> None:
    """LXC 克隆的 rootfs 同樣只能放大：requested <= 範本大小時跳過。

    沒有這一步，從範本克隆的 LXC 會一律停在範本大小，申請單上的磁碟值
    形同虛設，卻照樣計入配額與班級容量。範本大小以註冊資料為準，缺值時
    改讀克隆機自己的 rootfs（克隆是範本的副本，大小相同）；兩者都問不到
    就跳過 —— 不知道下限時貿然 resize，縮小會直接讓整個開機失敗。
    """
    if not rootfs_size:
        return
    floor = template_gb or _lxc_rootfs_gb(node, vmid)
    if not floor:
        logger.warning(
            "Skip rootfs resize for CT %d: template size unknown", vmid
        )
        return
    if int(rootfs_size) <= floor:
        logger.info(
            "Skip rootfs resize for CT %d: requested %dG <= template %dG",
            vmid,
            int(rootfs_size),
            floor,
        )
        return
    proxmox_service.resize_disk(node, vmid, "lxc", "rootfs", f"{int(rootfs_size)}G")


def _template_ostype(vm: dict) -> str | None:
    """讀範本 config 的 ostype；讀不到不阻擋清單（回 None）。"""
    try:
        config = proxmox_service.get_config(vm["node"], int(vm["vmid"]), "qemu")
    except Exception:
        return None
    ostype = config.get("ostype")
    return str(ostype) if ostype else None


def is_windows_template(template_id: int) -> bool:
    """範本 ostype 是否為 Windows（w 開頭）；查不到一律當非 Windows。

    Windows 範本的帳號由 cloudbase-init 設定檔固定，申請單可不帶 username。
    """
    try:
        template = proxmox_service.find_vm_template(template_id)
    except Exception:
        return False
    ostype = _template_ostype(template)
    return bool(ostype and ostype.startswith("w"))


def get_vm_templates() -> list[VMTemplateSchema]:
    """VM 來源用的 PVE 範本清單。

    pool 內的 LXC 範本同樣是 template=1，但它們不能當 VM 來源（ostype 也讀
    不到），所以在這裡就濾掉，避免出現在申請表單的虛擬機清單裡。
    """
    all_vms = [
        vm
        for vm in proxmox_service.get_vm_templates()
        if str(vm.get("type") or "qemu").lower() != "lxc"
    ]
    templates: list[VMTemplateSchema] = []
    for vm in all_vms:
        ostype = _template_ostype(vm)
        # cluster resources 的 maxcpu/maxmem/maxdisk 即範本自身規格
        maxcpu = vm.get("maxcpu")
        maxmem = vm.get("maxmem")
        templates.append(
            VMTemplateSchema(
                vmid=vm["vmid"],
                name=vm["name"],
                node=vm["node"],
                ostype=ostype,
                # PVE 的 Windows ostype（wxp/w2k*/wvista/win7~11）皆以 w 開頭，
                # Linux 為 l24/l26，不衝突
                is_windows=bool(ostype and ostype.startswith("w")),
                cores=int(maxcpu) if maxcpu else None,
                memory_mb=int(maxmem) // (1024 * 1024) if maxmem else None,
                # 磁碟無條件進位：克隆後 resize 只能放大，下限不可低估
                disk_gb=_template_disk_gb(vm) or None,
            )
        )
    return templates
