"""統一克隆開通服務：所有「從範本開機器」都走這條路徑。

請求端（request_clone）做權限/配額校驗與任務入列；worker 端（run_clone_task）
執行 PVE 克隆：linked clone 優先、失敗自動退 full clone，克隆後重配置
hostname / IP / SSH 金鑰 / 防火牆並寫入 Resource 紀錄。
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any
from urllib.parse import quote

from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.core.permissions import is_admin
from app.core.security import encrypt_value
from app.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.infrastructure.proxmox import get_proxmox_settings
from app.infrastructure.proxmox import operations as proxmox_ops
from app.infrastructure.queue import enqueue_task, report_progress
from app.infrastructure.ssh.client import generate_ed25519_keypair
from app.models import TaskRecord, User, VMTemplate, VMTemplateStatus
from app.repositories import resource as resource_repo
from app.schemas.template import TemplateCloneRequest
from app.services.network import firewall_service, ip_management_service
from app.services.template import template_service
from app.utils.hostname import to_punycode_hostname

logger = logging.getLogger(__name__)

TASK_CLONE = "template.clone"


# ---------------------------------------------------------------------------
# 請求端：校驗 + 入列
# ---------------------------------------------------------------------------

def _build_hostnames(
    base: str | None, template_name: str, count: int
) -> list[str]:
    raw = base or template_name
    hostname = to_punycode_hostname(raw)
    if count == 1:
        return [hostname]
    # 批量時加序號，並保留 63 字元上限
    return [f"{hostname[:59]}-{i + 1:02d}" for i in range(count)]


async def request_clone(
    *,
    session: Session,
    user: User,
    template_id: uuid.UUID,
    data: TemplateCloneRequest,
) -> list[TaskRecord]:
    template = template_service._get_or_404(session, template_id)
    template_service._require_view(session, user, template)
    if template.status != VMTemplateStatus.ready:
        raise ConflictError(
            f"Template is not ready to clone (now: {template.status.value})"
        )

    can_manage = template_service._can_manage(user)
    if data.count > 1 and not can_manage:
        raise PermissionDeniedError("Only teachers and admins can batch clone")

    if not can_manage and not is_admin(user):
        owned = len(
            resource_repo.get_resources_by_user(session=session, user_id=user.id)
        )
        limit = settings.TEMPLATE_CLONE_STUDENT_MAX_INSTANCES
        if owned + data.count > limit:
            raise ConflictError(
                f"Resource quota exceeded: you have {owned} of {limit} instances"
            )

    hostnames = _build_hostnames(data.hostname, template.name, data.count)
    records: list[TaskRecord] = []
    for hostname in hostnames:
        record = await enqueue_task(
            session=session,
            task_type=TASK_CLONE,
            user_id=user.id,
            template_id=template.id,
            payload={
                "template_id": str(template.id),
                "user_id": str(user.id),
                "hostname": hostname,
                "cores": data.cores,
                "memory": data.memory,
                "disk": data.disk,
                "start": data.start,
            },
        )
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# worker 端：克隆 + 重配置（同步，tasks.py 以 to_thread 呼叫）
# ---------------------------------------------------------------------------

def clone_with_fallback(
    *,
    node: str,
    template_vmid: int,
    new_vmid: int,
    hostname: str,
    resource_type: proxmox_ops.ResourceType,
    full_kwargs: dict[str, Any] | None = None,
) -> str:
    """linked clone 優先，失敗退 full clone。回傳實際模式（linked/full）。

    ``full_kwargs`` 只在退 full clone 時併入（例如指定 storage——
    linked clone 必須與範本同 storage，不能帶該參數）。
    """
    pool = get_proxmox_settings().pool_name
    name_key = "hostname" if resource_type == "lxc" else "name"
    clone_fn = (
        proxmox_ops.clone_lxc if resource_type == "lxc" else proxmox_ops.clone_vm
    )
    base_config: dict[str, Any] = {
        "newid": new_vmid,
        name_key: hostname,
        "pool": pool,
    }
    try:
        clone_fn(node, template_vmid, full=0, **base_config)
        return "linked"
    except Exception as exc:
        logger.warning(
            "Linked clone of template %s -> %s failed (%s); falling back to full clone",
            template_vmid,
            new_vmid,
            exc,
        )
        # linked clone 失敗可能留下殘骸，先盡力清掉再以同 VMID full clone
        try:
            from app.services.proxmox import provisioning_service

            provisioning_service.cleanup_provisioned_resource(new_vmid)
        except Exception:
            pass
        clone_fn(node, template_vmid, full=1, **base_config, **(full_kwargs or {}))
        return "full"


def _reconfigure_qemu(
    *,
    node: str,
    vmid: int,
    hostname: str,
    cores: int | None,
    memory: int | None,
    disk: int | None,
    public_key: str,
    net_cfg: dict[str, Any],
    allocated_ip: str,
) -> None:
    config_updates: dict[str, Any] = {
        "name": hostname,
        "sshkeys": quote(public_key, safe=""),
        "ciupgrade": 0,
        "net0": f"virtio,bridge={net_cfg['bridge_name']},firewall=1",
        "ipconfig0": (
            f"ip={allocated_ip}/{net_cfg['prefix_len']},gw={net_cfg['gateway']}"
        ),
    }
    if cores:
        config_updates["cores"] = cores
    if memory:
        config_updates["memory"] = memory
    if net_cfg.get("dns_servers"):
        config_updates["nameserver"] = net_cfg["dns_servers"]
    proxmox_ops.update_config(node, vmid, "qemu", **config_updates)
    if disk:
        proxmox_ops.resize_disk(node, vmid, "qemu", "scsi0", f"{disk}G")


def _reconfigure_lxc(
    *,
    node: str,
    vmid: int,
    hostname: str,
    cores: int | None,
    memory: int | None,
    net_cfg: dict[str, Any],
    allocated_ip: str,
) -> None:
    # LXC 無 cloud-init：SSH 金鑰無法在克隆後注入，登入沿用範本內建憑證
    config_updates: dict[str, Any] = {
        "hostname": hostname,
        "net0": (
            f"name=eth0,bridge={net_cfg['bridge_name']},"
            f"ip={allocated_ip}/{net_cfg['prefix_len']},"
            f"gw={net_cfg['gateway']},firewall=1"
        ),
    }
    if cores:
        config_updates["cores"] = cores
    if memory:
        config_updates["memory"] = memory
    if net_cfg.get("dns_servers"):
        config_updates["nameserver"] = net_cfg["dns_servers"]
    proxmox_ops.update_config(node, vmid, "lxc", **config_updates)


def _parse_expiry(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def run_clone_task(task_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
    """克隆一台：分配 IP → clone（linked→full）→ 重配置 → 防火牆 → Resource 紀錄。

    選用 payload 鍵（batch provision 走同一條路徑時傳入）：
    batch_job_id / environment_type / expiry_date。
    """
    template_id = uuid.UUID(payload["template_id"])
    user_id = uuid.UUID(payload["user_id"])
    hostname = str(payload["hostname"])
    cores = payload.get("cores")
    memory = payload.get("memory")
    disk = payload.get("disk")
    start = bool(payload.get("start", True))
    raw_batch = payload.get("batch_job_id")
    batch_job_id = uuid.UUID(str(raw_batch)) if raw_batch else None
    environment_type = payload.get("environment_type")
    expiry_date = _parse_expiry(payload.get("expiry_date"))

    with Session(engine) as session:
        template = session.get(VMTemplate, template_id)
        if template is None or template.status != VMTemplateStatus.ready:
            raise NotFoundError("Template is missing or not ready")
        template_vmid = template.pve_vmid
        template_name = template.name
        node = template.node
        resource_type: proxmox_ops.ResourceType = (
            "lxc" if template.resource_type == "lxc" else "qemu"
        )
        cores = cores or template.default_cores
        memory = memory or template.default_memory
        disk = disk or template.default_disk

        new_vmid = proxmox_ops.next_vmid()
        net_cfg = ip_management_service.get_network_config_for_vm(session)
        purpose = "lxc" if resource_type == "lxc" else "vm"
        allocated_ip = ip_management_service.allocate_ip(session, new_vmid, purpose)
        # 先提交 IP 分配，避免克隆期間（可能數分鐘）併發任務撞 IP
        session.commit()

    created = False
    clone_mode = "linked"
    try:
        report_progress(task_id, 10)
        clone_mode = clone_with_fallback(
            node=node,
            template_vmid=template_vmid,
            new_vmid=new_vmid,
            hostname=hostname,
            resource_type=resource_type,
        )
        created = True
        report_progress(task_id, 60)

        private_key_pem, public_key = generate_ed25519_keypair()
        if resource_type == "qemu":
            _reconfigure_qemu(
                node=node,
                vmid=new_vmid,
                hostname=hostname,
                cores=cores,
                memory=memory,
                disk=disk,
                public_key=public_key,
                net_cfg=net_cfg,
                allocated_ip=allocated_ip,
            )
        else:
            _reconfigure_lxc(
                node=node,
                vmid=new_vmid,
                hostname=hostname,
                cores=cores,
                memory=memory,
                net_cfg=net_cfg,
                allocated_ip=allocated_ip,
            )
        report_progress(task_id, 75)

        firewall_service.setup_default_rules(node, new_vmid, resource_type)
        if start:
            proxmox_ops.control(node, new_vmid, resource_type, "start")
        report_progress(task_id, 90)

        with Session(engine) as session:
            resource_repo.create_resource(
                session=session,
                vmid=new_vmid,
                user_id=user_id,
                environment_type=environment_type or f"範本 {template_name}",
                expiry_date=expiry_date,
                template_id=template_vmid,
                ssh_private_key_encrypted=(
                    encrypt_value(private_key_pem)
                    if resource_type == "qemu"
                    else None
                ),
                ssh_public_key=public_key if resource_type == "qemu" else None,
                batch_job_id=batch_job_id,
            )
    except Exception:
        # 失敗清理：釋放 IP → 撤防火牆規則 → 刪除半成品
        try:
            with Session(engine) as cleanup_session:
                ip_management_service.release_ip(cleanup_session, new_vmid)
                cleanup_session.commit()
        except Exception:
            logger.warning("Failed to release IP for VMID %d", new_vmid)
        if created:
            try:
                rules = firewall_service.get_vm_firewall_rules(
                    node, new_vmid, resource_type
                )
                for rule in sorted(
                    rules, key=lambda r: r.get("pos", 0), reverse=True
                ):
                    pos = rule.get("pos")
                    if pos is not None:
                        try:
                            firewall_service.delete_rule_by_pos(
                                node, new_vmid, resource_type, int(pos)
                            )
                        except Exception:
                            pass
            except Exception:
                pass
            try:
                from app.services.proxmox import provisioning_service

                provisioning_service.cleanup_provisioned_resource(new_vmid)
            except Exception:
                logger.warning("Failed to clean up half-cloned VMID %d", new_vmid)
        raise

    return {
        "vmid": new_vmid,
        "clone_mode": clone_mode,
        "ip": allocated_ip,
        "hostname": hostname,
    }


__all__ = [
    "TASK_CLONE",
    "clone_with_fallback",
    "request_clone",
    "run_clone_task",
]
